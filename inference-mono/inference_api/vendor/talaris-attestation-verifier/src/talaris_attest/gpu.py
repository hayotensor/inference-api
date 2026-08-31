"""GPU UUID canonical hash — byte-exact mirror of the producer's hash_gpu_uuids.

Single source of truth: ``tee_wrapper.engine.commands.hash_gpu_uuids``. Canonicalization:

    items = sorted({u.strip() for u in uuids if u and u.strip()})
    None when empty, else sha256(json.dumps(items, separators=(",", ":"))).

The cross-check in verify.py recomputes this over doc.gpu_uuids and requires it to equal
doc.gpu_hash, AND requires set(doc.gpu_uuids) to be a subset of the UUIDs the NVIDIA
evidence attested.
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional


def hash_gpu_uuids(uuids: Optional[list[str]]) -> Optional[str]:
    """Canonical sha256 over the sorted, de-duplicated, stripped GPU UUID set, or None."""
    items = sorted({u.strip() for u in (uuids or []) if u and u.strip()})
    if not items:
        return None
    canonical = json.dumps(items, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
