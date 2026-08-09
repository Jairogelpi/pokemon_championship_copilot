from __future__ import annotations

from dataclasses import dataclass
from math import floor


@dataclass(frozen=True, slots=True)
class DamageRange:
    minimum: int
    maximum: int
    rolls: tuple[int, ...]

    def to_dict(self) -> dict[str, object]:
        return {"minimum": self.minimum, "maximum": self.maximum, "rolls": list(self.rolls)}


def stage_multiplier(stage: int) -> float:
    stage = max(-6, min(6, stage))
    if stage >= 0:
        return (2 + stage) / 2
    return 2 / (2 - stage)


def calculate_damage_range(
    *,
    level: int,
    power: int,
    attack: int,
    defense: int,
    modifier: float = 1.0,
) -> DamageRange:
    if level <= 0 or power <= 0 or attack <= 0 or defense <= 0 or modifier < 0:
        raise ValueError("damage inputs must be positive")
    base = floor(floor(floor((2 * level / 5) + 2) * power * attack / defense) / 50) + 2
    rolls = tuple(max(1, floor(base * modifier * random_roll / 100)) for random_roll in range(85, 101))
    return DamageRange(minimum=min(rolls), maximum=max(rolls), rolls=rolls)


def effective_speed(
    *,
    raw_speed: int,
    stage: int = 0,
    tailwind: bool = False,
    paralysis: bool = False,
    ability_or_item_modifier: float = 1.0,
) -> int:
    if raw_speed <= 0 or ability_or_item_modifier <= 0:
        raise ValueError("speed inputs must be positive")
    value = raw_speed * stage_multiplier(stage)
    if tailwind:
        value *= 2
    if paralysis:
        value *= 0.5
    value *= ability_or_item_modifier
    return max(1, floor(value))
