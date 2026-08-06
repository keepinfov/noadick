from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from services.economy_metrics import simulate_growth


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministic noadick growth simulation")
    parser.add_argument("--trials", type=int, default=5_000)
    parser.add_argument("--seed", type=int, default=20260804)
    args = parser.parse_args()
    if args.trials < 100:
        parser.error("--trials must be at least 100")

    results = [
        simulate_growth(days, initial, trials=args.trials, seed=args.seed + initial + days)
        for days in (30, 90, 365)
        for initial in (0, 50, 200)
    ]
    print(json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
