from __future__ import annotations

import os


def admin_ids() -> set[int]:
    raw = os.environ.get("ADMIN_IDS", "")
    ids: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            ids.add(int(part))
    return ids


def is_global_admin(user_id: int) -> bool:
    return user_id in admin_ids()
