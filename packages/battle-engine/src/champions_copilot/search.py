from __future__ import annotations

from dataclasses import asdict, dataclass
from time import monotonic
from typing import Any, Protocol, Sequence


class SearchBudgetExhausted(RuntimeError):
    """Raised when no complete search iteration fits inside the budget."""


class ExhaustiveEndgameUnavailable(RuntimeError):
    """Raised when a terminal tablebase cannot be closed without approximation."""


class EndgameCycleDetected(ExhaustiveEndgameUnavailable):
    """Raised when the exact reachable graph contains an unresolved cycle."""


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


@dataclass(frozen=True, slots=True)
class EndgameValue:
    expected_utility: float
    win_probability: float
    draw_probability: float
    loss_probability: float
    principal_line: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "expected_utility": round(self.expected_utility, 6),
            "win_probability": round(self.win_probability, 9),
            "draw_probability": round(self.draw_probability, 9),
            "loss_probability": round(self.loss_probability, 9),
            "principal_line": list(self.principal_line),
        }


@dataclass(frozen=True, slots=True)
class EndgameActionEvaluation:
    action: str
    value: EndgameValue
    worst_response: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "worst_response": self.worst_response,
            **self.value.to_dict(),
        }


@dataclass(slots=True)
class ExhaustiveEndgameStats:
    states_closed: int = 0
    terminal_states: int = 0
    transposition_hits: int = 0
    chance_branches: int = 0
    maximum_stack_depth: int = 0
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ExhaustiveEndgameResult:
    best: EndgameActionEvaluation
    alternatives: tuple[EndgameActionEvaluation, ...]
    stats: ExhaustiveEndgameStats

    def to_dict(self) -> dict[str, Any]:
        return {
            "exhaustive_claim": True,
            "solution_semantics": (
                "all legal player actions, all legal opponent responses, and every "
                "supported stochastic branch were closed to a terminal state"
            ),
            "best": self.best.to_dict(),
            "alternatives": [row.to_dict() for row in self.alternatives],
            "stats": self.stats.to_dict(),
        }


class ExhaustiveEndgameSolver:
    """Fail-closed terminal expectiminimax for finite, fully observed games.

    Opponent responses are adversarial choices; stochastic outcomes retain their
    exact probability. A reachable cycle, unsupported transition, missing legal
    action, or resource cutoff invalidates the whole tablebase.
    """

    def __init__(
        self,
        *,
        max_states: int = 100_000,
        max_chance_branches: int = 1_000_000,
        time_budget_ms: int | None = 30_000,
    ) -> None:
        if max_states < 1 or max_chance_branches < 1:
            raise ValueError("exhaustive endgame limits must be positive")
        if time_budget_ms is not None and time_budget_ms < 1:
            raise ValueError("time_budget_ms must be positive when provided")
        self.max_states = max_states
        self.max_chance_branches = max_chance_branches
        self.time_budget_ms = time_budget_ms
        self.stats = ExhaustiveEndgameStats()
        self._cache: dict[str, EndgameValue] = {}
        self._visiting: set[str] = set()
        self._started_at = 0.0
        self._deadline: float | None = None

    def solve(self, game: SimultaneousGame, state: Any) -> ExhaustiveEndgameResult:
        if game.terminal_value(state) is not None:
            raise ValueError("cannot solve an endgame from a terminal state")
        self.stats = ExhaustiveEndgameStats()
        self._cache = {}
        self._visiting = set()
        self._started_at = monotonic()
        self._deadline = (
            self._started_at + self.time_budget_ms / 1000
            if self.time_budget_ms is not None
            else None
        )
        root_key = game.state_key(state)
        self._visiting.add(root_key)
        try:
            evaluations = self._action_evaluations(game, state, stack_depth=0)
        finally:
            self._visiting.remove(root_key)
        self.stats.states_closed += 1
        evaluations.sort(
            key=lambda row: (
                -row.value.expected_utility,
                -row.value.win_probability,
                row.value.loss_probability,
                row.action,
            )
        )
        self.stats.elapsed_ms = round((monotonic() - self._started_at) * 1000, 3)
        return ExhaustiveEndgameResult(
            best=evaluations[0],
            alternatives=tuple(evaluations[1:]),
            stats=self.stats,
        )

    def _state_value(
        self,
        game: SimultaneousGame,
        state: Any,
        *,
        stack_depth: int,
    ) -> EndgameValue:
        self._check_limits()
        key = game.state_key(state)
        cached = self._cache.get(key)
        if cached is not None:
            self.stats.transposition_hits += 1
            return cached
        terminal = game.terminal_value(state)
        if terminal is not None:
            self.stats.terminal_states += 1
            value = float(terminal)
            result = EndgameValue(
                expected_utility=value,
                win_probability=1.0 if value > 0 else 0.0,
                draw_probability=1.0 if value == 0 else 0.0,
                loss_probability=1.0 if value < 0 else 0.0,
            )
            self._cache[key] = result
            return result
        if key in self._visiting:
            raise EndgameCycleDetected(
                "reachable endgame cycle requires PP/cycle game solving"
            )
        if self.stats.states_closed >= self.max_states:
            raise ExhaustiveEndgameUnavailable(
                f"state limit exceeded before terminal closure ({self.max_states})"
            )
        self._visiting.add(key)
        self.stats.maximum_stack_depth = max(
            self.stats.maximum_stack_depth, stack_depth
        )
        try:
            evaluations = self._action_evaluations(
                game, state, stack_depth=stack_depth
            )
            best = min(
                evaluations,
                key=lambda row: (
                    -row.value.expected_utility,
                    -row.value.win_probability,
                    row.value.loss_probability,
                    row.action,
                ),
            )
            result = best.value
            self._cache[key] = result
            self.stats.states_closed += 1
            return result
        finally:
            self._visiting.remove(key)

    def _action_evaluations(
        self,
        game: SimultaneousGame,
        state: Any,
        *,
        stack_depth: int,
    ) -> list[EndgameActionEvaluation]:
        actions = sorted(
            game.player_actions(state),
            key=lambda action: game.action_label(state, action),
        )
        if not actions:
            raise ExhaustiveEndgameUnavailable(
                "nonterminal state has no enumerated player action"
            )
        return [
            self._evaluate_action(game, state, action, stack_depth=stack_depth)
            for action in actions
        ]

    def _evaluate_action(
        self,
        game: SimultaneousGame,
        state: Any,
        action: Any,
        *,
        stack_depth: int,
    ) -> EndgameActionEvaluation:
        responses = sorted(
            game.opponent_responses(state, action), key=lambda response: response.id
        )
        if not responses:
            raise ExhaustiveEndgameUnavailable(
                "nonterminal state has no enumerated opponent response"
            )
        response_values: list[tuple[WeightedResponse, EndgameValue]] = []
        for response in responses:
            outcomes = _normalized_probabilities(
                game.chance_outcomes(state, action, response),
                label=f"exact chance outcomes for {response.id}",
            )
            self.stats.chance_branches += len(outcomes)
            if self.stats.chance_branches > self.max_chance_branches:
                raise ExhaustiveEndgameUnavailable(
                    "chance-branch limit exceeded before terminal closure "
                    f"({self.max_chance_branches})"
                )
            expected = 0.0
            win = 0.0
            draw = 0.0
            loss = 0.0
            representative: tuple[float, ChanceOutcome, EndgameValue] | None = None
            for outcome, probability in outcomes:
                continuation = self._state_value(
                    game,
                    outcome.next_state,
                    stack_depth=stack_depth + 1,
                )
                branch_utility = float(outcome.immediate_reward) + continuation.expected_utility
                expected += probability * branch_utility
                win += probability * continuation.win_probability
                draw += probability * continuation.draw_probability
                loss += probability * continuation.loss_probability
                row = (branch_utility, outcome, continuation)
                if representative is None or (row[0], row[1].id) < (
                    representative[0], representative[1].id
                ):
                    representative = row
            assert representative is not None
            branch_utility, outcome, continuation = representative
            step = {
                "action": game.action_label(state, action),
                "response": response.id,
                "outcome": outcome.id,
                "branch_probability": round(
                    next(
                        probability
                        for candidate, probability in outcomes
                        if candidate is outcome
                    ),
                    9,
                ),
                "branch_utility": round(branch_utility, 6),
            }
            response_values.append(
                (
                    response,
                    EndgameValue(
                        expected_utility=expected,
                        win_probability=win,
                        draw_probability=draw,
                        loss_probability=loss,
                        principal_line=(step, *continuation.principal_line),
                    ),
                )
            )
        response, value = min(
            response_values,
            key=lambda row: (
                row[1].expected_utility,
                row[1].win_probability,
                -row[1].loss_probability,
                row[0].id,
            ),
        )
        return EndgameActionEvaluation(
            action=game.action_label(state, action),
            value=value,
            worst_response=response.id,
        )

    def _check_limits(self) -> None:
        if self._deadline is not None and monotonic() >= self._deadline:
            raise ExhaustiveEndgameUnavailable(
                "time limit exceeded before terminal closure"
            )
