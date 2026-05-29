import random
import time

import texts

DISEASE_CHANCE = 0.03


class Disease:
    def __init__(
        self,
        d_id: str,
        name: str,
        days: int,
        growth_mod: float,
        duel_mod: float,
        infect_chance: float,
        catch_message: str,
        is_buff: bool = False,
    ) -> None:
        self.id = d_id
        self.name = name
        self.days = days
        self.growth_mod = growth_mod
        self.duel_mod = duel_mod
        self.infect_chance = infect_chance
        self.catch_message = catch_message
        self.is_buff = is_buff


def _d(
    d_id: str,
    days: int,
    growth_mod: float,
    duel_mod: float,
    infect_chance: float,
    is_buff: bool = False,
) -> "Disease":
    name, catch_message = texts.DISEASE_TEXT[d_id]
    return Disease(
        d_id=d_id,
        name=name,
        days=days,
        growth_mod=growth_mod,
        duel_mod=duel_mod,
        infect_chance=infect_chance,
        catch_message=catch_message,
        is_buff=is_buff,
    )


DISEASES = [
    _d("syphilis", days=3, growth_mod=0.5, duel_mod=-0.15, infect_chance=0.60),
    _d("fracture", days=2, growth_mod=0.0, duel_mod=-0.10, infect_chance=0.0),
    _d("fungus", days=1, growth_mod=1.0, duel_mod=0.0, infect_chance=0.40),
    _d("valgus", days=2, growth_mod=0.7, duel_mod=-0.05, infect_chance=0.0),
    _d("piercing", days=3, growth_mod=1.10, duel_mod=0.05, infect_chance=0.0, is_buff=True),
    _d("gonorrhea", days=3, growth_mod=0.6, duel_mod=-0.10, infect_chance=0.50),
]

DISEASE_BY_ID = {d.id: d for d in DISEASES}


def roll_infection() -> Disease | None:
    if random.random() < DISEASE_CHANCE:
        return random.choice(DISEASES)
    return None


def check_expire(player: dict) -> bool:
    disease = player.get("disease")
    if not disease:
        return False
    d = DISEASE_BY_ID.get(disease["id"])
    if not d:
        player.pop("disease", None)
        return True
    elapsed = (time.time() - disease["caught_at"]) / 86400
    if elapsed >= d.days:
        player.pop("disease", None)
        return True
    return False


def apply_growth_mod(player: dict, delta: int) -> int:
    disease = player.get("disease")
    if not disease:
        return delta
    d = DISEASE_BY_ID.get(disease["id"])
    if not d:
        return delta
    return max(0, int(delta * d.growth_mod))


def apply_duel_mod(player: dict, chance: float) -> float:
    disease = player.get("disease")
    if not disease:
        return chance
    d = DISEASE_BY_ID.get(disease["id"])
    if not d:
        return chance
    return chance + d.duel_mod


def try_infect(source: dict, target: dict) -> str | None:
    disease = source.get("disease")
    if not disease:
        return None
    d = DISEASE_BY_ID.get(disease["id"])
    if not d or d.infect_chance <= 0:
        return None
    if random.random() < d.infect_chance:
        d2 = DISEASE_BY_ID.get(target.get("disease", {}).get("id"))
        if d2 and d2.is_buff:
            return None
        target["disease"] = {"id": d.id, "caught_at": int(time.time())}
        return d.catch_message
    return None


def disease_tag(player: dict) -> str:
    disease = player.get("disease")
    if not disease:
        return ""
    d = DISEASE_BY_ID.get(disease["id"])
    if not d:
        return ""
    days_left = d.days - int((time.time() - disease["caught_at"]) / 86400)
    return texts.disease_tag_text(d.name, max(0, days_left))
