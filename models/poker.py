"""Pure no-limit Texas Hold'em mechanics.

The module deliberately has no Telegram or database dependencies.  A hand is a
JSON-serialisable dictionary so an in-progress deal can be persisted after
every action and resumed after a process restart.
"""

from __future__ import annotations

import secrets
from itertools import combinations
from typing import Any

RANKS = "23456789TJQKA"
SUITS = "cdhs"
SUIT_SYMBOLS = {"c": "♣", "d": "♦", "h": "♥", "s": "♠"}
CATEGORY_NAMES = (
    "старшая карта",
    "пара",
    "две пары",
    "тройка",
    "стрит",
    "флеш",
    "фулл-хаус",
    "каре",
    "стрит-флеш",
)


class PokerError(ValueError):
    """A rejected game action with a stable machine-readable reason."""


def fresh_deck() -> list[str]:
    deck = [rank + suit for rank in RANKS for suit in SUITS]
    secrets.SystemRandom().shuffle(deck)
    return deck


def card_text(card: str) -> str:
    rank, suit = card
    return f"{rank}{SUIT_SYMBOLS[suit]}"


def cards_text(cards: list[str]) -> str:
    return " ".join(card_text(card) for card in cards)


def _five_score(cards: tuple[str, ...]) -> tuple[int, ...]:
    values = sorted((RANKS.index(card[0]) + 2 for card in cards), reverse=True)
    counts: dict[int, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1

    unique = sorted(counts, reverse=True)
    straight_high = 0
    straight_values = unique[:]
    if 14 in straight_values:
        straight_values.append(1)
    run = 1
    for previous, current in zip(straight_values, straight_values[1:], strict=False):
        if previous - current == 1:
            run += 1
            if run >= 5:
                straight_high = previous + 3
                break
        else:
            run = 1

    flush = len({card[1] for card in cards}) == 1
    groups = sorted(((count, value) for value, count in counts.items()), reverse=True)
    if flush and straight_high:
        return (8, straight_high)
    if groups[0][0] == 4:
        four = groups[0][1]
        kicker = max(value for value in values if value != four)
        return (7, four, kicker)
    triples = sorted((value for value, count in counts.items() if count == 3), reverse=True)
    pairs = sorted((value for value, count in counts.items() if count == 2), reverse=True)
    if triples and (pairs or len(triples) > 1):
        pair = pairs[0] if pairs else triples[1]
        return (6, triples[0], pair)
    if flush:
        return (5, *values)
    if straight_high:
        return (4, straight_high)
    if triples:
        kickers = sorted((v for v in values if v != triples[0]), reverse=True)[:2]
        return (3, triples[0], *kickers)
    if len(pairs) >= 2:
        high, low = pairs[:2]
        kicker = max(v for v in values if v not in {high, low})
        return (2, high, low, kicker)
    if pairs:
        kickers = sorted((v for v in values if v != pairs[0]), reverse=True)[:3]
        return (1, pairs[0], *kickers)
    return (0, *values)


def evaluate(cards: list[str]) -> tuple[int, ...]:
    """Return a comparable score for five to seven cards."""
    if not 5 <= len(cards) <= 7:
        raise PokerError("bad_card_count")
    return max(_five_score(combo) for combo in combinations(cards, 5))


def hand_name(score: tuple[int, ...]) -> str:
    return CATEGORY_NAMES[score[0]]


def _ordered_uids(state: dict[str, Any], *, after_seat: int) -> list[str]:
    seats = sorted(
        ((int(uid), int(data["seat"])) for uid, data in state["players"].items()),
        key=lambda item: item[1],
    )
    if not seats:
        return []
    greater = [str(uid) for uid, seat in seats if seat > after_seat]
    lower = [str(uid) for uid, seat in seats if seat <= after_seat]
    return greater + lower


def _next_from(state: dict[str, Any], after_uid: str, candidates: set[str]) -> str | None:
    if not candidates:
        return None
    after_seat = int(state["players"][after_uid]["seat"])
    for uid in _ordered_uids(state, after_seat=after_seat):
        if uid in candidates:
            return uid
    return None


def _eligible_to_act(state: dict[str, Any]) -> set[str]:
    return {
        uid
        for uid, player in state["players"].items()
        if not player["folded"] and not player["all_in"] and player["stack"] > 0
    }


def _pay(player: dict[str, Any], amount: int) -> int:
    paid = min(max(0, amount), int(player["stack"]))
    player["stack"] -= paid
    player["street_bet"] += paid
    player["total_bet"] += paid
    if player["stack"] == 0:
        player["all_in"] = True
    return paid


def start_hand(
    seats: list[dict[str, Any]],
    *,
    small_blind: int,
    big_blind: int,
    dealer_seat: int | None = None,
    deck: list[str] | None = None,
) -> dict[str, Any]:
    """Create a hand from ready seats and post blinds.

    ``seats`` items require ``user_id``, ``seat``, ``name`` and ``stack``.
    The returned state owns the stacks until settlement.
    """
    live = sorted((seat for seat in seats if int(seat["stack"]) > 0), key=lambda s: s["seat"])
    if not 2 <= len(live) <= 6:
        raise PokerError("need_two_to_six")
    if small_blind < 1 or big_blind < small_blind:
        raise PokerError("bad_blinds")

    seat_numbers = [int(seat["seat"]) for seat in live]
    if dealer_seat is None:
        dealer = seat_numbers[0]
    else:
        dealer = next((seat for seat in seat_numbers if seat > dealer_seat), seat_numbers[0])

    state: dict[str, Any] = {
        "street": "preflop",
        "deck": list(deck) if deck is not None else fresh_deck(),
        "board": [],
        "dealer_seat": dealer,
        "small_blind": small_blind,
        "big_blind": big_blind,
        "current_bet": 0,
        "min_raise": big_blind,
        "current_uid": None,
        "needs_action": [],
        "last_action_bet": {},
        "players": {},
        "finished": False,
        "result": None,
        "last_action": "",
    }
    for seat in live:
        uid = str(int(seat["user_id"]))
        state["players"][uid] = {
            "seat": int(seat["seat"]),
            "name": str(seat["name"]),
            "stack": int(seat["stack"]),
            "cards": [],
            "folded": False,
            "all_in": False,
            "street_bet": 0,
            "total_bet": 0,
        }

    order = _ordered_uids(state, after_seat=dealer)
    if len(live) == 2:
        dealer_uid = next(uid for uid, p in state["players"].items() if p["seat"] == dealer)
        small_uid = dealer_uid
        big_uid = next(uid for uid in order if uid != dealer_uid)
    else:
        small_uid, big_uid = order[0], order[1]

    # Deal one card around twice, starting left of the dealer.
    for _ in range(2):
        for uid in order:
            state["players"][uid]["cards"].append(state["deck"].pop())

    _pay(state["players"][small_uid], small_blind)
    _pay(state["players"][big_uid], big_blind)
    state["current_bet"] = max(
        state["players"][small_uid]["street_bet"],
        state["players"][big_uid]["street_bet"],
    )
    state["small_uid"] = small_uid
    state["big_uid"] = big_uid
    needs = _eligible_to_act(state)
    state["needs_action"] = sorted(needs)
    first = _next_from(state, big_uid, needs)
    state["current_uid"] = first
    if first is None:
        _finish_betting(state)
    return state


def legal_actions(state: dict[str, Any], user_id: int | str) -> dict[str, Any]:
    uid = str(user_id)
    if state["finished"] or state["current_uid"] != uid:
        return {"turn": False}
    player = state["players"][uid]
    to_call = max(0, int(state["current_bet"]) - int(player["street_bet"]))
    max_total = int(player["street_bet"]) + int(player["stack"])
    last_seen = state["last_action_bet"].get(uid)
    raise_reopened = last_seen is None or int(state["current_bet"]) - int(last_seen) >= int(
        state["min_raise"]
    )
    can_raise = max_total > int(state["current_bet"]) and raise_reopened
    min_to = int(state["current_bet"]) + int(state["min_raise"])
    if min_to > max_total:
        min_to = max_total
    return {
        "turn": True,
        "fold": True,
        "check": to_call == 0,
        "call": min(to_call, int(player["stack"])),
        "to_call": to_call,
        "can_raise": can_raise,
        "min_raise_to": min_to if can_raise else None,
        "max_raise_to": max_total if can_raise else None,
        "pot": sum(int(p["total_bet"]) for p in state["players"].values()),
    }


def apply_action(
    state: dict[str, Any], user_id: int | str, action: str, amount: int | None = None
) -> dict[str, Any]:
    uid = str(user_id)
    legal = legal_actions(state, uid)
    if not legal.get("turn"):
        raise PokerError("not_your_turn")
    player = state["players"][uid]
    name = player["name"]
    needs = set(state["needs_action"])

    if action == "fold":
        player["folded"] = True
        needs.discard(uid)
        state["last_action"] = (
            f"{name} увидел две картонки, обосрался и утащил остатки достоинства в пас."
        )
    elif action == "check":
        if not legal["check"]:
            raise PokerError("cannot_check")
        needs.discard(uid)
        state["last_action_bet"][uid] = int(state["current_bet"])
        state["last_action"] = f"{name} чекнул: денег не внёс, мысли тоже. Редкая стабильность."
    elif action == "call":
        if legal["to_call"] <= 0:
            raise PokerError("nothing_to_call")
        paid = _pay(player, int(legal["to_call"]))
        needs.discard(uid)
        state["last_action_bet"][uid] = int(state["current_bet"])
        state["last_action"] = (
            f"{name} наскрёб {paid} см на колл и теперь изображает человека с планом."
        )
    elif action == "raise":
        if amount is None:
            raise PokerError("amount_required")
        raise_to = int(amount)
        max_to = int(player["street_bet"]) + int(player["stack"])
        if not legal["can_raise"] or raise_to <= int(state["current_bet"]):
            raise PokerError("cannot_raise")
        if raise_to > max_to:
            raise PokerError("not_enough_stack")
        increment = raise_to - int(state["current_bet"])
        full_raise = increment >= int(state["min_raise"])
        if not full_raise and raise_to != max_to:
            raise PokerError("raise_too_small")
        _pay(player, raise_to - int(player["street_bet"]))
        state["current_bet"] = raise_to
        if full_raise:
            state["min_raise"] = increment
            needs = _eligible_to_act(state) - {uid}
        else:
            needs.discard(uid)
            needs |= {
                other_uid
                for other_uid in _eligible_to_act(state)
                if int(state["players"][other_uid]["street_bet"]) < raise_to
            }
        state["last_action_bet"][uid] = raise_to
        state["last_action"] = (
            f"{name} вывалил все {raise_to} см на стол. Мозг закончился раньше стека."
            if player["all_in"]
            else f"{name} задрал ставку до {raise_to} см, компенсируя размер громкостью."
        )
    else:
        raise PokerError("unknown_action")

    state["needs_action"] = sorted(needs & _eligible_to_act(state))
    _continue_after_action(state, uid)
    return state


def _continue_after_action(state: dict[str, Any], actor_uid: str) -> None:
    remaining = [uid for uid, p in state["players"].items() if not p["folded"]]
    if len(remaining) == 1:
        _settle(state, showdown=False)
        return
    needs = set(state["needs_action"])
    if needs:
        state["current_uid"] = _next_from(state, actor_uid, needs)
        return
    _finish_betting(state)


def _finish_betting(state: dict[str, Any]) -> None:
    if state["street"] == "river":
        _settle(state, showdown=True)
        return

    reveals = {"preflop": 3, "flop": 1, "turn": 1}
    next_street = {"preflop": "flop", "flop": "turn", "turn": "river"}
    for _ in range(reveals[state["street"]]):
        state["board"].append(state["deck"].pop())
    state["street"] = next_street[state["street"]]
    state["current_bet"] = 0
    state["min_raise"] = int(state["big_blind"])
    state["last_action_bet"] = {}
    for player in state["players"].values():
        player["street_bet"] = 0

    can_act = _eligible_to_act(state)
    if len(can_act) <= 1:
        # No betting decisions remain: deal the board out and settle.
        while state["street"] != "river":
            for _ in range(reveals[state["street"]]):
                state["board"].append(state["deck"].pop())
            state["street"] = next_street[state["street"]]
        _settle(state, showdown=True)
        return
    state["needs_action"] = sorted(can_act)
    ordered = _ordered_uids(state, after_seat=int(state["dealer_seat"]))
    state["current_uid"] = next(uid for uid in ordered if uid in can_act)


def _pot_layers(state: dict[str, Any]) -> list[dict[str, Any]]:
    contributions = {
        uid: int(player["total_bet"])
        for uid, player in state["players"].items()
        if int(player["total_bet"]) > 0
    }
    levels = sorted(set(contributions.values()))
    previous = 0
    pots: list[dict[str, Any]] = []
    for level in levels:
        involved = [uid for uid, amount in contributions.items() if amount >= level]
        amount = (level - previous) * len(involved)
        eligible = [uid for uid in involved if not state["players"][uid]["folded"]]
        if amount:
            pots.append(
                {
                    "amount": amount,
                    "eligible": involved if len(involved) == 1 else eligible,
                    "uncalled": len(involved) == 1,
                }
            )
        previous = level
    return pots


def _winner_order(state: dict[str, Any], winners: list[str]) -> list[str]:
    order = _ordered_uids(state, after_seat=int(state["dealer_seat"]))
    return [uid for uid in order if uid in winners]


def _settle(state: dict[str, Any], *, showdown: bool) -> None:
    pots_out: list[dict[str, Any]] = []
    total_rake = 0
    scores: dict[str, tuple[int, ...]] = {}
    if showdown:
        for uid, player in state["players"].items():
            if not player["folded"]:
                scores[uid] = evaluate(player["cards"] + state["board"])

    for pot in _pot_layers(state):
        eligible = pot["eligible"]
        if showdown:
            best = max(scores[uid] for uid in eligible)
            winners = [uid for uid in eligible if scores[uid] == best]
        else:
            winners = eligible
        rake = pot["amount"] // 20 if len(state["board"]) >= 3 and not pot.get("uncalled") else 0
        distributable = pot["amount"] - rake
        total_rake += rake
        ordered = _winner_order(state, winners)
        share, odd = divmod(distributable, len(ordered))
        payouts: dict[str, int] = {}
        for index, uid in enumerate(ordered):
            won = share + (1 if index < odd else 0)
            state["players"][uid]["stack"] += won
            payouts[uid] = won
        pots_out.append(
            {
                "gross": pot["amount"],
                "rake": rake,
                "payouts": payouts,
                "eligible": eligible,
            }
        )

    state["finished"] = True
    state["current_uid"] = None
    state["needs_action"] = []
    state["result"] = {
        "showdown": showdown,
        "pots": pots_out,
        "rake": total_rake,
        "scores": {uid: list(score) for uid, score in scores.items()},
    }
