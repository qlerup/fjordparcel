from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime
from typing import Any, Optional
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from .common import TrackingLookupResult, normalize_tracking_number

POSTNORD_TRACKING_URL = "https://www.postnord.dk/varktojer/track-trace/?shipmentId={tracking_number}"
POSTNORD_API_URL = "https://api2.postnord.com/rest/shipment/v5/trackandtrace/findByIdentifier.json"
POSTNORD_API_KEY = str(os.getenv("POSTNORD_API_KEY", "") or "").strip()
DEFAULT_TIMEOUT_SECONDS = int(str(os.getenv("POSTNORD_TRACKING_TIMEOUT", "20") or "20"))


def _text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\s+([.,:;!?])", r"\1", text)


def _tracking_url(number: str) -> str:
    return POSTNORD_TRACKING_URL.format(tracking_number=urllib_parse.quote(number, safe=""))


def _request_text(url: str, timeout: int, headers: Optional[dict] = None) -> str:
    req = urllib_request.Request(
        url,
        method="GET",
        headers={
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "da-DK,da;q=0.9,en;q=0.7",
            "User-Agent": "Mozilla/5.0 (compatible; fjordparcel-tracking/1.0)",
            **(headers or {}),
        },
    )
    with urllib_request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _parse_datetime(value: Any) -> tuple[str, str, str]:
    raw = _text(value)
    if not raw:
        return "", "", ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.isoformat(timespec="seconds"), parsed.strftime("%d-%m-%Y"), parsed.strftime("%H:%M")
    except ValueError:
        return raw, raw[:10], ""


def _parse_event(event: dict[str, Any]) -> dict[str, Any]:
    date_iso, display_date, display_time = _parse_datetime(
        event.get("eventTime") or event.get("dateTime") or ""
    )
    location_obj = event.get("location") or {}
    if isinstance(location_obj, dict):
        location = _text(location_obj.get("displayName") or location_obj.get("name") or "")
    else:
        location = _text(location_obj)
    return {
        "description": _text(event.get("eventDescription") or event.get("description") or ""),
        "status": _text(event.get("eventCode") or event.get("status") or ""),
        "date_iso": date_iso,
        "display_date": display_date,
        "display_time": display_time,
        "location": location,
    }


def _parse_tracking_response(data: dict[str, Any], number: str) -> TrackingLookupResult:
    response = data.get("TrackingInformationResponse") or {}
    shipments = response.get("shipments") or []
    if not shipments or not isinstance(shipments[0], dict):
        return TrackingLookupResult(
            carrier="PostNord",
            tracking_number=number,
            status="Ikke fundet",
            tracking_url=_tracking_url(number),
            source="postnord-api",
            error="PostNord fandt ingen forsendelse paa dette nummer.",
        )

    shipment = shipments[0]
    items = shipment.get("items") or []
    item = items[0] if items and isinstance(items[0], dict) else {}

    raw_events = item.get("events") or shipment.get("events") or []
    events = [_parse_event(e) for e in raw_events if isinstance(e, dict)]
    # PostNord returns events oldest-first — reverse so latest is first
    events_desc = list(reversed(events))
    latest = events_desc[0] if events_desc else {}

    status_header = _text((shipment.get("statusText") or {}).get("header") or "")
    last_text = latest.get("description") or status_header or "Fundet hos PostNord"

    service_point = (item.get("deliveryPoint") or item.get("pickupPoint") or {})
    if isinstance(service_point, dict):
        location_name = _text(service_point.get("name") or "")
        city = _text(service_point.get("city") or "")
        summary = f"{location_name}, {city}".strip(", ") if city else location_name
    else:
        summary = ""

    return TrackingLookupResult(
        carrier="PostNord",
        tracking_number=number,
        status=last_text,
        status_code=_text(shipment.get("status") or item.get("status") or ""),
        summary=summary,
        last_event_at=latest.get("date_iso") or "",
        last_event_text=last_text,
        last_event_location=latest.get("location") or "",
        events=events_desc,
        tracking_url=_tracking_url(number),
        source="postnord-api",
        error="",
    )


def _fetch_via_api(number: str, timeout: int) -> dict[str, Any]:
    query = urllib_parse.urlencode({"apikey": POSTNORD_API_KEY, "id": number, "locale": "da"})
    raw = _request_text(f"{POSTNORD_API_URL}?{query}", timeout, headers={"Accept": "application/json"})
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("PostNord API returnerede ugyldigt JSON.") from exc


def _extract_tracking_response_from_text(text: str) -> dict[str, Any]:
    # Search for the PostNord API response structure embedded anywhere in page/RSC payload
    for match in re.finditer(r'\{[^{}]*"TrackingInformationResponse"[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL):
        try:
            candidate = json.loads(match.group(0))
            if isinstance(candidate, dict) and "TrackingInformationResponse" in candidate:
                return candidate
        except json.JSONDecodeError:
            continue

    # Try __NEXT_DATA__ script tag
    nd_match = re.search(r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', text, re.DOTALL)
    if nd_match:
        try:
            nd = json.loads(nd_match.group(1))
            # Walk props looking for tracking response
            props = nd.get("props", {}).get("pageProps", {})
            for val in _walk_dicts(props):
                if isinstance(val, dict) and "TrackingInformationResponse" in val:
                    return val
        except (json.JSONDecodeError, Exception):
            pass

    # Try RSC streaming chunks (Next.js App Router, like Bring)
    for chunk_match in re.finditer(r'streamController\.enqueue\((\"(?:\\.|[^\"\\])*\")\)', text):
        try:
            encoded = json.loads(chunk_match.group(1)).strip()
            if not encoded.startswith("["):
                continue
            values = json.loads(encoded)
            chunk_text = json.dumps(values)
            if "TrackingInformationResponse" in chunk_text:
                for val in _walk_dicts(values):
                    if isinstance(val, dict) and "TrackingInformationResponse" in val:
                        return val
        except (json.JSONDecodeError, Exception):
            continue

    raise RuntimeError("Ingen tracking-data fundet i PostNord-siden.")


def _walk_dicts(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk_dicts(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_dicts(item)


def fetch_postnord_tracking(
    tracking_number: Any,
    postal_codes: Optional[list[str]] = None,
    timeout: Optional[int] = None,
) -> TrackingLookupResult:
    number = normalize_tracking_number(tracking_number)
    timeout_seconds = int(timeout or DEFAULT_TIMEOUT_SECONDS or 20)

    if POSTNORD_API_KEY:
        try:
            data = _fetch_via_api(number, timeout_seconds)
            return _parse_tracking_response(data, number)
        except Exception as exc:
            return TrackingLookupResult(
                carrier="PostNord",
                tracking_number=number,
                status="Fejl ved opdatering",
                tracking_url=_tracking_url(number),
                source="postnord-api",
                error=str(exc)[:260],
            )

    # No API key — PostNord blocks server-side requests, so scraping won't work.
    return TrackingLookupResult(
        carrier="PostNord",
        tracking_number=number,
        status="API-nøgle mangler",
        tracking_url=_tracking_url(number),
        source="postnord-api",
        error=(
            "PostNord kræver en gratis API-nøgle for automatisk opdatering. "
            "Opret konto på developer.postnord.com, kopiér nøglen, "
            "og tilføj POSTNORD_API_KEY=din-nøgle til .env-filen."
        ),
    )
