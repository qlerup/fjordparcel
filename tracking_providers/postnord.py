from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime
from typing import Any, Optional
from http import cookiejar
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from .common import TrackingLookupResult, normalize_tracking_number

POSTNORD_TRACKING_URL = "https://parcelsapp.com/en/tracking/{tracking_number}"
PARCELSAPP_API_URL = "https://parcelsapp.com/api/v2/parcels"
DEFAULT_TIMEOUT_SECONDS = int(str(os.getenv("POSTNORD_TRACKING_TIMEOUT", "20") or "20"))


def _text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\s+([.,:;!?])", r"\1", text)


def _tracking_url(number: str) -> str:
    return POSTNORD_TRACKING_URL.format(tracking_number=urllib_parse.quote(number, safe=""))


def _request_text(
    url: str,
    timeout: int,
    headers: Optional[dict] = None,
    opener: Optional[urllib_request.OpenerDirector] = None,
) -> str:
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
    opener_obj = opener or urllib_request.build_opener()
    with opener_obj.open(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _request_json(
    url: str,
    data: dict[str, Any],
    timeout: int,
    headers: Optional[dict] = None,
    opener: Optional[urllib_request.OpenerDirector] = None,
) -> dict[str, Any]:
    body = urllib_parse.urlencode(data).encode("utf-8")
    req = urllib_request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": "Mozilla/5.0 (compatible; fjordparcel-tracking/1.0)",
            **(headers or {}),
        },
    )
    opener_obj = opener or urllib_request.build_opener()
    with opener_obj.open(req, timeout=timeout) as response:
        payload = response.read().decode("utf-8", errors="replace")
    try:
        result = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError("ParcelsApp returnerede ugyldigt JSON.") from exc
    if not isinstance(result, dict):
        raise RuntimeError("ParcelsApp returnerede et uventet svarformat.")
    return result


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
    date_iso, display_date, display_time = _parse_datetime(event.get("date") or event.get("eventTime") or "")
    location = _text(
        event.get("location")
        or event.get("locationName")
        or event.get("city")
        or event.get("country")
        or ""
    )
    description = _text(event.get("text") or event.get("description") or event.get("status") or event.get("state") or "")
    return {
        "description": description,
        "status": _text(event.get("status") or event.get("state") or ""),
        "date_iso": date_iso,
        "display_date": display_date,
        "display_time": display_time,
        "location": location,
    }


def _parse_tracking_response(data: dict[str, Any], number: str) -> TrackingLookupResult:
    states_raw = data.get("states") if isinstance(data.get("states"), list) else []
    events = [_parse_event(item) for item in states_raw if isinstance(item, dict)]
    if not events:
        error_code = _text(data.get("error") or "")
        message = "ParcelsApp fandt ingen forsendelse paa dette nummer."
        if error_code == "NO_DATA":
            message = "Ingen haendelser fundet endnu hos ParcelsApp."
        return TrackingLookupResult(
            carrier="PostNord",
            tracking_number=number,
            status="Ikke fundet",
            tracking_url=_tracking_url(number),
            source="parcelsapp",
            error=message,
        )

    events_desc = sorted(events, key=lambda item: item.get("date_iso") or "", reverse=True)
    latest = events_desc[0]
    attributes = data.get("attributes") if isinstance(data.get("attributes"), list) else []
    sender = ""
    for attr in attributes:
        if not isinstance(attr, dict):
            continue
        title = _text(attr.get("title") or "").lower()
        if title in {"sender", "afsender", "from"}:
            sender = _text(attr.get("value") or "")
            if sender:
                break

    last_text = latest.get("description") or _text(data.get("status") or "") or "Fundet hos PostNord"
    status_code = _text(data.get("status") or "")

    return TrackingLookupResult(
        carrier="PostNord",
        tracking_number=number,
        status=last_text,
        status_code=status_code,
        summary=sender,
        last_event_at=latest.get("date_iso") or "",
        last_event_text=last_text,
        last_event_location=latest.get("location") or "",
        events=events_desc,
        tracking_url=_tracking_url(number),
        source="parcelsapp",
        error="",
    )


def _extract_csrf_token(text: str) -> str:
    match = re.search(r'name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']', text, re.IGNORECASE)
    if not match:
        raise RuntimeError("ParcelsApp-side mangler CSRF-token.")
    return match.group(1).strip()


def _encode_tracking_id(number: str) -> str:
    # ParcelsApp client obfuscates each character before POSTing trackingId.
    return "".join(chr((ord(ch) + 76) % 126) for ch in number)


def _fetch_parcelsapp_tracking(number: str, timeout: int) -> dict[str, Any]:
    cookie_handler = urllib_request.HTTPCookieProcessor(cookiejar.CookieJar())
    opener = urllib_request.build_opener(cookie_handler)

    tracking_page_url = _tracking_url(number)
    page = _request_text(tracking_page_url, timeout, opener=opener)
    csrf_token = _extract_csrf_token(page)

    payload = {
        "trackingId": _encode_tracking_id(number),
        "carrier": "Auto-Detect",
        "language": "en",
        "country": "Unknown",
        "platform": "web-desktop",
        "wd": "false",
        "c": "true",
        "p": "0",
        "l": "2",
        "se": "fjordparcel",
    }
    return _request_json(
        PARCELSAPP_API_URL,
        payload,
        timeout,
        headers={
            "X-CSRF-Token": csrf_token,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": tracking_page_url,
        },
        opener=opener,
    )


def fetch_postnord_tracking(
    tracking_number: Any,
    postal_codes: Optional[list[str]] = None,
    timeout: Optional[int] = None,
) -> TrackingLookupResult:
    number = normalize_tracking_number(tracking_number)
    timeout_seconds = int(timeout or DEFAULT_TIMEOUT_SECONDS or 20)

    try:
        data = _fetch_parcelsapp_tracking(number, timeout_seconds)
        return _parse_tracking_response(data, number)
    except urllib_error.HTTPError as exc:
        status = "Ikke fundet" if int(exc.code or 0) == 404 else "Fejl ved opdatering"
        return TrackingLookupResult(
            carrier="PostNord",
            tracking_number=number,
            status=status,
            tracking_url=_tracking_url(number),
            source="parcelsapp",
            error=f"ParcelsApp svarede med HTTP {int(exc.code or 0)}",
        )
    except Exception as exc:
        return TrackingLookupResult(
            carrier="PostNord",
            tracking_number=number,
            status="Fejl ved opdatering",
            tracking_url=_tracking_url(number),
            source="parcelsapp",
            error=str(exc)[:260],
        )
