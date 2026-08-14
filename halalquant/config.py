"""Library-wide settings (API key and related)."""

from __future__ import annotations

import os
from typing import Optional

# Set from user code: ``import halalquant as hq; hq.api_key = "..."``
api_key: Optional[str] = None


def resolve_api_key(explicit: Optional[str] = None) -> Optional[str]:
    """
    Resolve the FMP key in order: explicit argument, module setting, env var.
    """
    if explicit:
        return explicit
    if api_key:
        return api_key
    return os.getenv("FMP_API_KEY")
