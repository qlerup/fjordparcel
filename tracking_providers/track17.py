from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime
from typing import Any, Optional
from urllib import error as urllib_error
from urllib import request as urllib_request

from .common import TrackingLookupResult, normalize_tracking_number

POSTNORD_TRACKING_URL = "https://t.17track.net/da#nums={tracking_number}"
TRACK17_API_BASE = "https://api.17track.net/track/v2.2"
TRACK17_API_KEY = str(os.getenv("TRACK17_API_KEY", "") or "").strip()
DEFAULT_TIMEOUT_SECONDS = int(str(os.getenv("POSTNORD_TRACKING_TIMEOUT", "20") or "20"))

DA_TRANSLATIONS = {
    "E-mail notification has been sent to the recipient": "e-mail-meddelelse er sendt til modtageren",
    "A text message notification has been sent to the recipient": "En sms-meddelelse er sendt til modtageren",
    "The shipment item has been dropped off by sender": "Forsendelsen er afleveret af afsender",
    "Notification sent via APP": "Notifikation sendt via APP",
    "The shipment item has been delivered to a service point": "Forsendelsen er leveret til et udleveringssted",
    "The delivery of the shipment item is in progress": "Forsendelsen er under levering",
    "The shipment item has arrived at the distribution terminal": "Forsendelsen er ankommet til distributionscentret",
    "The shipment item is under transportation": "Forsendelsen er under transport",
    (
        "We have received a notification from your shipper that they are preparing an item for you. "
        "The tracking information will be updated when the parcel is handed over to PostNord"
    ): (
        "Vi har modtaget besked om, at din afsender forbereder en forsendelse. "
        "Sporingsoplysningerne opdateres, når pakken afleveres til PostNord"
    ),
}


def _text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\s+([.,:;!?])", r"\1", text)


def _tracking_url(number: str) -> str:
    return POSTNORD_TRACKING_URL.format(tracking_number=number)


def _parse_datetime(value: Any) -> tuple[str, str, str]:
    raw = _text(value)
    if not raw:
        return "", "", ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.isoformat(timespec="seconds"), parsed.strftime("%d-%m-%Y"), parsed.strftime("%H:%M")
    except ValueError:
        return raw, raw[:10], ""


def _to_danish_text(text: str) -> str:
    clean = _text(text)
    mapped = DA_TRANSLATIONS.get(clean, clean)
    if mapped.startswith("Denmark, "):
        return "Danmark, " + mapped[len("Denmark, "):]
    return mapped.replace("Denmark", "Danmark")


def _parse_event(event: dict[str, Any]) -> dict[str, Any]:
    date_iso, display_date, display_time = _parse_datetime(event.get("time_iso") or "")
    location = _text(event.get("location") or "").replace("Denmark", "Danmark")
    translation = event.get("description_translation")
    translated = translation.get("description") if isinstance(translation, dict) and translation.get("lang") == "da" else ""
    description = _text(translated) or _to_danish_text(_text(event.get("description") or ""))
    return {
        "description": description,
        "status": _text(event.get("sub_status") or event.get("stage") or ""),
        "date_iso": date_iso,
        "display_date": display_date,
        "display_time": display_time,
        "location": location,
    }


def _get_api_key() -> str:
    try:
        from app_config import load_track17_settings
        return load_track17_settings().get("api_key", "") or TRACK17_API_KEY
    except Exception:
        return TRACK17_API_KEY


def _api_request(endpoint: str, payload: list[Any], api_key: str, timeout: int) -> dict[str, Any]:
    url = f"{TRACK17_API_BASE}/{endpoint}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib_request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "17token": api_key,
            "Content-Type": "application/json",
        },
    )
    with urllib_request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("17TRACK API returnerede ugyldigt JSON.") from exc
    if not isinstance(result, dict):
        raise RuntimeError("17TRACK API returnerede et uventet svarformat.")
    return result


def _parse_response(data: dict[str, Any], number: str, carrier: str = "PostNord") -> TrackingLookupResult:
    accepted = (data.get("data") or {}).get("accepted") or []
    entry = next(
        (item for item in accepted if isinstance(item, dict) and item.get("number") == number),
        None,
    )

    if not entry:
        return TrackingLookupResult(
            carrier=carrier,
            tracking_number=number,
            status="Ikke fundet",
            tracking_url=_tracking_url(number),
            source="17track-api",
            error="17TRACK fandt ingen forsendelse på dette nummer.",
        )

    track_info = entry.get("track_info") if isinstance(entry.get("track_info"), dict) else {}
    if not track_info or (not track_info.get("latest_event") and (track_info.get("latest_status") or {}).get("status") in (None, "NotFound")):
        return _pending(number, carrier)
    latest_status = track_info.get("latest_status") if isinstance(track_info.get("latest_status"), dict) else {}
    latest_event_raw = track_info.get("latest_event") if isinstance(track_info.get("latest_event"), dict) else {}
    tracking = track_info.get("tracking") if isinstance(track_info.get("tracking"), dict) else {}
    providers = tracking.get("providers") if isinstance(tracking.get("providers"), list) else []
    provider = providers[0] if providers and isinstance(providers[0], dict) else {}
    raw_events = [event for p in providers if isinstance(p, dict) for event in (p.get("events") or [])]

    events = [_parse_event(item) for item in raw_events if isinstance(item, dict)]
    events_desc = sorted(events, key=lambda item: item.get("date_iso") or "", reverse=True)
    if not events_desc and latest_event_raw:
        events_desc = [_parse_event(latest_event_raw)]

    latest = events_desc[0] if events_desc else {}
    status_code = _text(latest_status.get("status") or latest_status.get("sub_status") or "")
    last_text = (
        latest.get("description")
        or _to_danish_text(_text(latest_status.get("status") or ""))
        or "Fundet hos 17TRACK"
    )

    misc = track_info.get("misc_info") if isinstance(track_info.get("misc_info"), dict) else {}
    provider_info = provider.get("provider") if isinstance(provider.get("provider"), dict) else {}
    service_type = _text(misc.get("service_type") or provider.get("service_type") or "")
    provider_name = _text(provider_info.get("name") or "")
    detected_carrier = provider_name or carrier or "Ukendt"
    for canonical in ("PostNord", "GLS", "DAO", "Bring", "FedEx", "DHL", "UPS"):
        if canonical.lower() in provider_name.lower():
            detected_carrier = canonical
            break

    return TrackingLookupResult(
        carrier=detected_carrier,
        tracking_number=number,
        status=last_text,
        status_code=status_code,
        summary=service_type or provider_name,
        last_event_at=latest.get("date_iso") or "",
        last_event_text=last_text,
        last_event_location=latest.get("location") or "",
        events=events_desc,
        tracking_url=_tracking_url(number),
        source="17track-api",
        error="",
    )


def _pending(number, carrier):
    return TrackingLookupResult(carrier=carrier or "Ukendt", tracking_number=number,
        status="Afventer 17TRACK", summary="17TRACK henter oplysninger fra fragtfirmaet. Vi prøver automatisk igen.",
        tracking_url=_tracking_url(number), source="17track-api")


def _response_error(data, number):
    if data.get("code", 0) != 0:
        return data.get("code"), "17TRACK afviste forespørgslen."
    rejected = (data.get("data") or {}).get("rejected") or []
    for item in rejected:
        if item.get("number") == number:
            error = item.get("error") or {}
            return error.get("code"), "17TRACK afviste trackingnummeret."
    return None, ""


def fetch_track17_tracking(tracking_number: Any, carrier: str = "", postal_codes=None, timeout=None):
    number = normalize_tracking_number(tracking_number)
    # A first lookup can involve read + register + read; stay below the
    # web worker's 30-second request timeout even if 17TRACK is slow.
    timeout_seconds = max(1, min(8, int(timeout or DEFAULT_TIMEOUT_SECONDS or 20)))
    api_key = _get_api_key()
    def failure(message):
        return TrackingLookupResult(carrier=carrier or "Ukendt", tracking_number=number,
            status="Fejl ved opdatering", tracking_url=_tracking_url(number), source="17track-api", error=message)
    if not api_key:
        return failure("17TRACK API-nøgle mangler. Gå til Indstillinger → Fragtfirmaer.")
    try:
        # Read existing registrations first; do not repeatedly register numbers.
        result = _api_request("gettrackinfo", [{"number":number}], api_key, timeout_seconds)
        code, error = _response_error(result, number)
        if code == -18019902:
            registration = _api_request("register", [{"number":number,"lang":"da"}], api_key, timeout_seconds)
            code, error = _response_error(registration, number)
            if code in (None, -18019901):
                # Registration only starts 17TRACK's lookup; fetch immediately.
                result = _api_request("gettrackinfo", [{"number":number}], api_key, timeout_seconds)
                code, error = _response_error(result, number)
                if code == -18019902:
                    return _pending(number, carrier)
        if code == -18019909:
            return _pending(number, carrier)
        if code is not None:
            messages = {-18019908:"17TRACK-kvoten er opbrugt.", -18019907:"17TRACKs daglige grænse er nået.",
                -18019903:"17TRACK kan ikke identificere fragtfirmaet for nummeret.",
                -18010002:"17TRACK API-nøglen er ugyldig."}
            return failure(messages.get(code, error) + f" (kode {code})")
        return _parse_response(result, number, carrier=carrier)
    except urllib_error.HTTPError as exc:
        return failure(f"17TRACK API svarede med HTTP {int(exc.code or 0)}")
    except (OSError, ValueError, RuntimeError):
        return failure("17TRACK kunne ikke kontaktes eller returnerede et ugyldigt svar. Prøv igen senere.")


def fetch_postnord_tracking(tracking_number, postal_codes=None, timeout=None):
    return fetch_track17_tracking(tracking_number, carrier="PostNord", postal_codes=postal_codes, timeout=timeout)
