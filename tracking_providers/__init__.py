from __future__ import annotations

from typing import Any, Optional

from .common import TrackingLookupResult
from .bring import fetch_bring_tracking
from .dao import fetch_dao_tracking
from .fedex import fetch_fedex_tracking
from .gls import fetch_gls_tracking
from .track17 import fetch_postnord_tracking, fetch_track17_tracking


def fetch_tracking(
    tracking_number: Any,
    carrier: str = "",
    postal_codes: Optional[list[str]] = None,
    timeout: Optional[int] = None,
) -> TrackingLookupResult:
    return fetch_track17_tracking(tracking_number, carrier=carrier, postal_codes=postal_codes, timeout=timeout)



__all__ = [
    "TrackingLookupResult",
    "fetch_tracking",
    "fetch_bring_tracking",
    "fetch_dao_tracking",
    "fetch_fedex_tracking",
    "fetch_gls_tracking",
    "fetch_postnord_tracking",
]
