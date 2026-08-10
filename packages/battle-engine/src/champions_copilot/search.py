from __future__ import annotations

from dataclasses import asdict, dataclass
from time import monotonic
from typing import Any, Protocol, Sequence


class SearchBudgetExhausted(RuntimeError):
    """Raised when no complete search iteration fits inside the budget."""


@dataclass(frozen=True, slots=True)
class WeightedResponse:
    id: str
    probability: float
    payload: Any = None


@dataclass(frozen=True, slots=True)
class ChanceOutcome:
    id: str
    probability: float
    next_state: Any
    immediate_reward: float = 0.0


class SimultaneousGame(Protocol):
    """Adapter boundary between the generic search and battle resolution."""

    def state_key(self, state: Any) -> str: ...

    def terminal_value(self, state: Any) -> float | None: ...

    def evaluate(self, state: Any) -> float: ...

    def player_actions(self, state: Any) -> Sequence[Any]: ...

    def action_label(self, state: Any, action: Any) -> str: ...

    def opponent_responses(
        self, state: Any, action: Any
    ) -> Sequence[WeightedResponse]: ...

    def chance_outcomes(
        self,
        state: Any,
        action: Any,
        response: WeightedResponse,
    ) -> Sequence[ChanceOutcome]: ...


@dataclass(frozen=True, slots=True)
class SearchConfig:
    max_depth: int = 2
    node_budget: int = 50_000
    time_budget_ms: int | None = None
    discount: float = 0.97
    lower_tail_mass: float = 0.20
    expected_weight: float = 0.75
    lower_tail_weight: float = 0.25
    catastrophic_threshold: float = -50.0
    catastrophic_penalty: float = 0.0

    def __post_init__(self) -> None:
        if self.max_depth < 1:
            raise ValueError("max_depth must be at least one")
        if self.node_budget < 1:
            raise ValueError("node_budget must be at least one")
        if self.time_budget_ms is not None and self.time_budget_ms < 1:
            raise ValueError("time_budget_ms must be positive when provided")
        if not 0 < self.discount <= 1:
            raise ValueError("discount must be in (0, 1]")
        if not 0 < self.lower_tail_mass <= 1:
            raise ValueError("lower_tail_mass must be in (0, 1]")
        if self.expected_weight < 0 or self.lower_tail_weight < 0:
            raise ValueError("risk weights cannot be negative")
        if self.expected_weight + self.lower_tail_weight <= 0:
            raise ValueError("at least one risk weight must be positive")
        if self.catastrophic_penalty < 0:
            raise ValueError("catastrophic_penalty cannot be negative")


@dataclass(frozen=True, slots=True)
class PrincipalStep:
    depth_remaining: int
    action: str
    response: str
    outcome: str
    branch_probability: float
    immediate_reward: float
    continuation_value: float
    total_value: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ActionEvaluation:
    action: str
    expected_value: float
    lower_tail_value: float
    catastrophic_probability: float
    robust_value: float
    principal_line: tuple[PrincipalStep, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["principal_line"] = [step.to_dict() for step in self.principal_line]
        return value


@dataclass(slots=True)
class SearchStats:
    requested_depth: int
    completed_depth: int = 0
    nodes_expanded: int = 0
    leaf_evaluations: int = 0
    terminal_evaluations: int = 0
    cache_hits: int = 0
    cutoff_reason: str | None = None
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SearchResult:
    best: ActionEvaluation
    alternatives: tuple[ActionEvaluation, ...]
    stats: SearchStats

    def to_dict(self) -> dict[str, Any]:
        return {
            "best": self.best.to_dict(),
            "alternatives": [alternative.to_dict() for alternative in self.alternatives],
            "stats": self.stats.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class _NodeValue:
    value: float
    principal_line: tuple[PrincipalStep, ...] = ()


@dataclass(frozen=True, slots=True)
class _ResponseValue:
    response: WeightedResponse
    response_probability: float
    value: float
    representative: ChanceOutcome
    representative_probability: float
    continuation: _NodeValue
    total_value: float


class _Cutoff(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _normalized_probabilities(
    values: Sequence[Any],
    *,
    label: str,
) -> list[tuple[Any, float]]:
    if not values:
        raise ValueError(f"{label} cannot be empty")
    probabilities = [float(value.probability) for value in values]
    if any(probability < 0 for probability in probabilities):
        raise ValueError(f"{label} cannot contain negative probability")
    total = sum(probabilities)
    if total <= 0:
        raise ValueError(f"{label} must contain positive probability mass")
    return [(value, probability / total) for value, probability in zip(values, probabilities)]


def _weighted_lower_tail(values: list[tuple[float, float]], mass: float) -> float:
    remaining = mass
    total = 0.0
    for probability, value in sorted(values, key=lambda row: row[1]):
        included = min(remaining, probability)
        total += included * value
        remaining -= included
        if remaining <= 1e-12:
            break
    used = mass - remaining
    return total / used if used > 0 else 0.0


class RiskAwareExpectiminimax:
    """Iterative-deepening search for simultaneous, uncertain decisions.

    A depth unit is one complete player decision followed by an opponent
    response and a chance transition. Only fully completed depths are returned.
    The node budget is deterministic; the optional wall-clock budget is an
    operational safeguard and may produce a shallower result.
    """

    def __init__(self, config: SearchConfig | None = None) -> None:
        self.config = config or SearchConfig()
        self.stats = SearchStats(requested_depth=self.config.max_depth)
        self._cache: dict[tuple[str, int], _NodeValue] = {}
        self._started_at = 0.0
        self._deadline: float | None = None

    def search(self, game: SimultaneousGame, state: Any) -> SearchResult:
        self.stats = SearchStats(requested_depth=self.config.max_depth)
        self._cache = {}
        self._started_at = monotonic()
        self._deadline = (
            self._started_at + self.config.time_budget_ms / 1000
            if self.config.time_budget_ms is not None
            else None
        )
        completed: tuple[ActionEvaluation, ...] | None = None
        for depth in range(1, self.config.max_depth + 1):
            try:
                current = self._rank_root(game, state, depth)
            except _Cutoff as cutoff:
                self.stats.cutoff_reason = cutoff.reason
                break
            completed = current
            self.stats.completed_depth = depth
        self.stats.elapsed_ms = round((monotonic() - self._started_at) * 1000, 3)
        if completed is None:
            raise SearchBudgetExhausted(
                self.stats.cutoff_reason or "search budget exhausted before depth one"
            )
        return SearchResult(
            best=completed[0],
            alternatives=completed[1:],
            stats=self.stats,
        )

    def _rank_root(
        self,
        game: SimultaneousGame,
        state: Any,
        depth: int,
    ) -> tuple[ActionEvaluation, ...]:
        terminal = game.terminal_value(state)
        if terminal is not None:
            raise ValueError("cannot search from a terminal state")
        self._consume_node()
        actions = sorted(
            game.player_actions(state),
            key=lambda action: game.action_label(state, action),
        )
        if not actions:
            raise ValueError("no player actions are available")
        ranked = [self._evaluate_action(game, state, action, depth) for action in actions]
        ranked.sort(key=lambda row: (-row.robust_value, row.action))
        return tuple(ranked)

    def _state_value(
        self,
        game: SimultaneousGame,
        state: Any,
        depth: int,
    ) -> _NodeValue:
        key = (game.state_key(state), depth)
        self._check_time_budget()
        cached = self._cache.get(key)
        if cached is not None:
            self.stats.cache_hits += 1
            return cached
        self._consume_node()
        terminal = game.terminal_value(state)
        if terminal is not None:
            self.stats.terminal_evaluations += 1
            result = _NodeValue(float(terminal))
            self._cache[key] = result
            return result
        if depth == 0:
            self.stats.leaf_evaluations += 1
            result = _NodeValue(float(game.evaluate(state)))
            self._cache[key] = result
            return result
        actions = sorted(
            game.player_actions(state),
            key=lambda action: game.action_label(state, action),
        )
        if not actions:
            self.stats.leaf_evaluations += 1
            result = _NodeValue(float(game.evaluate(state)))
            self._cache[key] = result
            return result
        evaluations = [
            self._evaluate_action(game, state, action, depth) for action in actions
        ]
        best = min(evaluations, key=lambda row: (-row.robust_value, row.action))
        result = _NodeValue(best.robust_value, best.principal_line)
        self._cache[key] = result
        return result

    def _evaluate_action(
        self,
        game: SimultaneousGame,
        state: Any,
        action: Any,
        depth: int,
    ) -> ActionEvaluation:
        responses = _normalized_probabilities(
            game.opponent_responses(state, action),
            label="opponent responses",
        )
        response_values: list[_ResponseValue] = []
        branch_values: list[tuple[float, float]] = []
        for response, response_probability in responses:
            self._check_time_budget()
            outcomes = _normalized_probabilities(
                game.chance_outcomes(state, action, response),
                label=f"chance outcomes for response {response.id}",
            )
            total = 0.0
            branches: list[tuple[float, ChanceOutcome, _NodeValue, float]] = []
            for outcome, outcome_probability in outcomes:
                self._check_time_budget()
                depth_cost_method = getattr(game, "transition_depth_cost", None)
                depth_cost = (
                    int(depth_cost_method(state, action, response, outcome))
                    if depth_cost_method is not None
                    else 1
                )
                if depth_cost not in {0, 1}:
                    raise ValueError("transition depth cost must be zero or one")
                continuation = self._state_value(
                    game, outcome.next_state, depth - depth_cost
                )
                branch_value = float(outcome.immediate_reward) + (
                    (self.config.discount**depth_cost) * continuation.value
                )
                total += outcome_probability * branch_value
                branches.append(
                    (outcome_probability, outcome, continuation, branch_value)
                )
                branch_values.append(
                    (response_probability * outcome_probability, branch_value)
                )
            representative_probability, representative, continuation, branch_value = min(
                branches,
                key=lambda row: (row[3], -row[0], row[1].id),
            )
            response_values.append(
                _ResponseValue(
                    response=response,
                    response_probability=response_probability,
                    value=total,
                    representative=representative,
                    representative_probability=representative_probability,
                    continuation=continuation,
                    total_value=branch_value,
                )
            )
        expected = sum(probability * value for probability, value in branch_values)
        lower_tail = _weighted_lower_tail(
            branch_values,
            self.config.lower_tail_mass,
        )
        catastrophic = sum(
            probability
            for probability, value in branch_values
            if value <= self.config.catastrophic_threshold
        )
        weight_total = self.config.expected_weight + self.config.lower_tail_weight
        robust = (
            self.config.expected_weight * expected
            + self.config.lower_tail_weight * lower_tail
        ) / weight_total
        robust -= self.config.catastrophic_penalty * catastrophic

        worst = min(
            response_values,
            key=lambda row: (row.value, -row.response.probability, row.response.id),
        )
        step = PrincipalStep(
            depth_remaining=depth,
            action=game.action_label(state, action),
            response=worst.response.id,
            outcome=worst.representative.id,
            branch_probability=round(
                worst.response_probability * worst.representative_probability,
                6,
            ),
            immediate_reward=round(float(worst.representative.immediate_reward), 6),
            continuation_value=round(worst.continuation.value, 6),
            total_value=round(worst.total_value, 6),
        )
        return ActionEvaluation(
            action=game.action_label(state, action),
            expected_value=round(expected, 6),
            lower_tail_value=round(lower_tail, 6),
            catastrophic_probability=round(catastrophic, 6),
            robust_value=round(robust, 6),
            principal_line=(step, *worst.continuation.principal_line),
        )

    def _consume_node(self) -> None:
        if self.stats.nodes_expanded >= self.config.node_budget:
            raise _Cutoff("node_budget")
        self._check_time_budget()
        self.stats.nodes_expanded += 1

    def _check_time_budget(self) -> None:
        if self._deadline is not None and monotonic() >= self._deadline:
            raise _Cutoff("time_budget")
