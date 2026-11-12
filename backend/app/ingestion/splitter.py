from __future__ import annotations

import hashlib
import math


def assign_dataset_split(seed_key: str, dev_ratio: float = 0.5) -> str:
    """Deterministically assign a case to DEV or HOLDOUT using SHA-256."""
    if math.isnan(dev_ratio) or dev_ratio < 0.0 or dev_ratio > 1.0:
        raise ValueError(f"dev_ratio 必须在 [0.0, 1.0] 区间内，当前值: {dev_ratio}")
    if dev_ratio <= 0.0:
        return "HOLDOUT"
    if dev_ratio >= 1.0:
        return "DEV"

    digest = hashlib.sha256(seed_key.encode("utf-8")).digest()
    bucket_u64 = int.from_bytes(digest[:8], byteorder="big", signed=False)
    unit_val = bucket_u64 / float(1 << 64)
    return "DEV" if unit_val < dev_ratio else "HOLDOUT"
