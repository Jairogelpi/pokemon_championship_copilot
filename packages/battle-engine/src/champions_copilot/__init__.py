"""Deterministic domain engine for Pokémon Champions Battle Copilot."""

from .beliefs import BeliefState
from .decision import Recommendation, recommend_actions, recommend_team_preview
from .events import BattleEvent, EventValidationError, apply_event, replay
from .models import BattleState, FieldState, PokemonState, SideState
from .search import (
    ChanceOutcome,
    EndgameCycleDetected,
    ExhaustiveEndgameResult,
    ExhaustiveEndgameSolver,
    ExhaustiveEndgameUnavailable,
    RiskAwareExpectiminimax,
    SearchBudgetExhausted,
    SearchConfig,
    SearchResult,
    WeightedResponse,
)
from .team import PLAYER_TEAM, create_match

__all__ = [
    "BattleEvent",
    "BattleState",
    "BeliefState",
    "ChanceOutcome",
    "EndgameCycleDetected",
    "EventValidationError",
    "ExhaustiveEndgameResult",
    "ExhaustiveEndgameSolver",
    "ExhaustiveEndgameUnavailable",
    "FieldState",
    "PLAYER_TEAM",
    "PokemonState",
    "Recommendation",
    "RiskAwareExpectiminimax",
    "SearchBudgetExhausted",
    "SearchConfig",
    "SearchResult",
    "SideState",
    "WeightedResponse",
    "apply_event",
    "create_match",
    "recommend_actions",
    "recommend_team_preview",
    "replay",
]
