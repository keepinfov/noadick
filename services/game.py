"""Pure game mechanics, independent of Telegram, reusable by handlers/tests."""
from __future__ import annotations

import random

# (range, weight) pairs for the daily /dick size delta.
WEIGHTED_RANGES = [
    ((-179, -178), 0.0001),
    ((-10, -6), 0.03),
    ((-5, -1), 0.3),
    ((1, 7), 0.6),
    ((8, 14), 0.07),
]


def roll_delta() -> int:
    ranges, weights = zip(*WEIGHTED_RANGES)
    (lo, hi) = random.choices(ranges, weights=weights, k=1)[0]
    return random.randint(lo, hi)
