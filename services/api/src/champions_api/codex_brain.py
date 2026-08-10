from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from champions_copilot.beliefs import BeliefState
from champions_copilot.decision import RankedAction, Recommendation
from champions_copilot.models import BattleState


ResponseTransport = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class CodexBrainConfig:
    model: str
    reasoning_effort: str
    timeout_seconds: float
    candidate_limit: int = 8


class CodexBattleBrain:
    """Let Codex choose inside a deterministic, fully legal candidate envelope.

    The model owns strategic judgment. It cannot create actions, mutate battle
    state, or replace calculator evidence: its selected ID is resolved back to
    the immutable candidate catalog produced by the battle engine.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        timeout_seconds: float | None = None,
        transport: ResponseTransport | None = None,
    ) -> None:
        resolved_effort = (
            reasoning_effort
            or os.environ.get("OPENAI_REASONING_EFFORT", "high")
        ).strip()
        if resolved_effort not in {"none", "low", "medium", "high", "xhigh", "max"}:
            raise ValueError(
                "OPENAI_REASONING_EFFORT must be none, low, medium, high, xhigh, or max"
            )
        resolved_timeout = timeout_seconds
        if resolved_timeout is None:
            resolved_timeout = float(os.environ.get("OPENAI_TIMEOUT_SECONDS", "25"))
        if resolved_timeout <= 0:
            raise ValueError("OPENAI_TIMEOUT_SECONDS must be positive")
        self.api_key = (
            api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")
        ).strip()
        self.config = CodexBrainConfig(
            model=(
                model
                or os.environ.get("OPENAI_BATTLE_MODEL")
                or os.environ.get("OPENAI_MODEL")
                or "gpt-5.6-sol"
            ).strip(),
            reasoning_effort=resolved_effort,
            timeout_seconds=float(resolved_timeout),
        )
        self._transport = transport or self._post

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.config.model)

    def status(self) -> dict[str, Any]:
        return {
            "engine": "codex-strategic-brain",
            "provider": "openai",
            "configured": self.configured,
            "model": self.config.model,
            "reasoning_effort": self.config.reasoning_effort,
            "candidate_limit": self.config.candidate_limit,
        }

    def decide(
        self,
        *,
        state: BattleState,
        beliefs: BeliefState,
        recommendation: Recommendation,
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        baseline = recommendation.to_dict()
        candidates = list(recommendation.candidate_catalog[: self.config.candidate_limit])
        catalog = list(baseline.get("candidate_catalog", []))[: len(candidates)]
        if not candidates or not catalog:
            return self._fallback(baseline, "empty_candidate_catalog")
        if not self.configured:
            return self._fallback(baseline, "not_configured")

        candidate_ids = [str(row["id"]) for row in catalog]
        schema = self._decision_schema(candidate_ids)
        context = {
            "battle_state": state.to_dict(),
            "belief_state": beliefs.to_dict(),
            "recent_events": events[-20:],
            "deterministic_evidence": {
                "anchor_candidate_id": candidate_ids[0],
                "candidates": catalog,
                "opponent_response_model": baseline["response_model"],
                "calculator": baseline["calculator"],
                "assumptions": baseline["assumptions"],
            },
        }
        request_body = {
            "model": self.config.model,
            "instructions": self._instructions(),
            "input": json.dumps(context, ensure_ascii=False, separators=(",", ":")),
            "reasoning": {"effort": self.config.reasoning_effort},
            "text": {
                "verbosity": "low",
                "format": {
                    "type": "json_schema",
                    "name": "codex_battle_decision",
                    "strict": True,
                    "schema": schema,
                },
            },
            "store": False,
            "safety_identifier": self._safety_identifier(state.match_id),
        }
        try:
            payload = self._transport(request_body)
            decision = self._extract_decision(payload)
            self._validate_decision(decision, set(candidate_ids))
        except (
            HTTPError,
            URLError,
            TimeoutError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            return self._fallback(baseline, f"{type(exc).__name__}")
        return self._apply_decision(baseline, candidates, catalog, decision)

    @staticmethod
    def _instructions() -> str:
        return (
            "You are Codex, the strategic decision engine for a competitive Pokémon Champions "
            "doubles copilot. Select exactly one candidate ID from the supplied legal catalog. "
            "Treat battle state, calculator values, legality, speed evidence, and candidate scores "
            "as immutable evidence. Never invent a move, target, damage roll, hidden fact, or "
            "future state. Infer the opponent's plan probabilistically, preserve a residual "
            "unknown line, "
            "identify the player's current win condition, and prefer a line that remains strong if "
            "the most likely read is wrong. You may disagree with the deterministic anchor when "
            "the recorded strategic evidence justifies it. Explain only lines represented in "
            "the catalog or its principal counter-lines. Distinguish facts from inferences."
        )

    @staticmethod
    def _decision_schema(candidate_ids: list[str]) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "selected_candidate_id",
                "alternative_candidate_ids",
                "opponent_plan",
                "win_condition",
                "rationale",
                "main_failure_mode",
                "risk",
                "confidence",
                "assumptions",
            ],
            "properties": {
                "selected_candidate_id": {"type": "string", "enum": candidate_ids},
                "alternative_candidate_ids": {
                    "type": "array",
                    "items": {"type": "string", "enum": candidate_ids},
                    "maxItems": 3,
                    "uniqueItems": True,
                },
                "opponent_plan": {
                    "type": "array",
                    "maxItems": 4,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "hypothesis",
                            "probability",
                            "evidence",
                            "selected_counter",
                        ],
                        "properties": {
                            "hypothesis": {"type": "string"},
                            "probability": {"type": "number", "minimum": 0, "maximum": 1},
                            "evidence": {"type": "string"},
                            "selected_counter": {"type": "string"},
                        },
                    },
                },
                "win_condition": {"type": "string"},
                "rationale": {"type": "string"},
                "main_failure_mode": {"type": "string"},
                "risk": {"type": "string", "enum": ["low", "medium", "high"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "assumptions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 6,
                    "uniqueItems": True,
                },
            },
        }

    @staticmethod
    def _extract_decision(payload: dict[str, Any]) -> dict[str, Any]:
        for output in payload.get("output", []):
            for content in output.get("content", []):
                if content.get("type") == "refusal":
                    raise ValueError("model_refusal")
                if content.get("type") == "output_text":
                    value = json.loads(str(content["text"]))
                    if not isinstance(value, dict):
                        raise TypeError("decision output must be an object")
                    return value
        raise ValueError("missing_structured_output")

    @staticmethod
    def _validate_decision(decision: dict[str, Any], candidate_ids: set[str]) -> None:
        selected_value = decision["selected_candidate_id"]
        alternative_values = decision["alternative_candidate_ids"]
        opponent_plan = decision["opponent_plan"]
        assumptions = decision["assumptions"]
        if not isinstance(selected_value, str):
            raise TypeError("selected candidate must be a string")
        if not isinstance(alternative_values, list) or len(alternative_values) > 3:
            raise TypeError("alternative candidates must be an array of at most three IDs")
        if not all(isinstance(value, str) for value in alternative_values):
            raise TypeError("alternative candidate IDs must be strings")
        if not isinstance(opponent_plan, list) or len(opponent_plan) > 4:
            raise TypeError("opponent plan must be an array of at most four hypotheses")
        if not isinstance(assumptions, list) or len(assumptions) > 6:
            raise TypeError("assumptions must be an array of at most six strings")
        if not all(isinstance(value, str) for value in assumptions):
            raise TypeError("assumptions must contain only strings")
        if len(assumptions) != len(set(assumptions)):
            raise ValueError("assumptions must be unique")
        if decision["risk"] not in {"low", "medium", "high"}:
            raise ValueError("risk must be low, medium, or high")
        confidence = float(decision["confidence"])
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between zero and one")
        for row in opponent_plan:
            if not isinstance(row, dict):
                raise TypeError("opponent hypotheses must be objects")
            if not all(
                isinstance(row.get(field), str)
                for field in ("hypothesis", "evidence", "selected_counter")
            ):
                raise TypeError("opponent hypothesis text fields must be strings")
            probability = float(row["probability"])
            if not 0 <= probability <= 1:
                raise ValueError("opponent hypothesis probability must be between zero and one")
        selected = selected_value
        alternatives = alternative_values
        if selected not in candidate_ids:
            raise ValueError("unknown selected candidate")
        if selected in alternatives:
            raise ValueError("selected candidate cannot also be an alternative")
        if len(alternatives) != len(set(alternatives)):
            raise ValueError("alternative candidates must be unique")
        if any(value not in candidate_ids for value in alternatives):
            raise ValueError("unknown alternative candidate")
        probability_sum = sum(
            float(row["probability"]) for row in opponent_plan
        )
        if probability_sum > 1.000001:
            raise ValueError("opponent-plan probabilities cannot exceed one")

    def _apply_decision(
        self,
        baseline: dict[str, Any],
        candidates: list[RankedAction],
        catalog: list[dict[str, Any]],
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        full_by_id = {
            str(catalog[index]["id"]): candidate.to_dict()
            for index, candidate in enumerate(candidates)
        }
        selected_id = str(decision["selected_candidate_id"])
        alternative_ids = [str(value) for value in decision["alternative_candidate_ids"]]
        for candidate_id in full_by_id:
            if candidate_id != selected_id and candidate_id not in alternative_ids:
                alternative_ids.append(candidate_id)
            if len(alternative_ids) >= 3:
                break

        result = dict(baseline)
        result["deterministic_anchor"] = catalog[0]
        result["primary"] = full_by_id[selected_id]
        result["alternatives"] = [full_by_id[value] for value in alternative_ids[:3]]
        result["rationale"] = str(decision["rationale"])
        result["risk"] = str(decision["risk"])
        result["assumptions"] = list(
            dict.fromkeys([*baseline["assumptions"], *decision["assumptions"]])
        )
        result["policy_version"] = "codex-strategist-0.4"
        result["validation_status"] = "CODEX_SELECTED_FROM_VERIFIED_CANDIDATES"
        result["brain"] = {
            **self.status(),
            "status": "ok",
            "decision_source": "codex",
            "selected_candidate_id": selected_id,
            "deterministic_anchor_id": str(catalog[0]["id"]),
            "overrode_deterministic_anchor": selected_id != str(catalog[0]["id"]),
            "confidence": float(decision["confidence"]),
            "win_condition": str(decision["win_condition"]),
            "opponent_plan": list(decision["opponent_plan"]),
            "main_failure_mode": str(decision["main_failure_mode"]),
            "candidate_envelope_size": len(catalog),
            "evidence_boundary": (
                "Codex selected an ID from the deterministic legal-action catalog; mechanics, "
                "damage, state, and principal lines were not model-generated."
            ),
        }
        return result

    def _fallback(self, baseline: dict[str, Any], reason: str) -> dict[str, Any]:
        result = dict(baseline)
        anchor_id = None
        if baseline.get("candidate_catalog"):
            anchor_id = baseline["candidate_catalog"][0]["id"]
        result["brain"] = {
            **self.status(),
            "status": "fallback",
            "decision_source": "deterministic",
            "reason": reason,
            "selected_candidate_id": anchor_id,
            "deterministic_anchor_id": anchor_id,
            "overrode_deterministic_anchor": False,
            "candidate_envelope_size": len(baseline.get("candidate_catalog", [])),
        }
        return result

    @staticmethod
    def _safety_identifier(match_id: str) -> str:
        digest = hashlib.sha256(match_id.encode("utf-8")).hexdigest()[:32]
        return f"champions_match_{digest}"

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(request, timeout=self.config.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise TypeError("OpenAI response must be an object")
        return payload
