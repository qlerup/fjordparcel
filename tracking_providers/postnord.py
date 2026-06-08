from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib import error as urllib_error
from urllib import request as urllib_request

from .common import TrackingLookupResult, normalize_tracking_number

POSTNORD_TRACKING_URL = "https://t.17track.net/da#nums={tracking_number}"
TRACK17_API_URL = "https://t.17track.net/track/restapi"
TRACK17_SIGN = str(os.getenv("TRACK17_SIGN", "") or "").strip()
TRACK17_TIMEZONE_OFFSET = int(str(os.getenv("TRACK17_TIMEZONE_OFFSET", "-480") or "-480"))
DEFAULT_TIMEOUT_SECONDS = int(str(os.getenv("POSTNORD_TRACKING_TIMEOUT", "20") or "20"))

BROWSER_PROFILE_DIR = Path(__file__).parent.parent / ".17track_profile"

DA_TRANSLATIONS = {
    "E-mail notification has been sent to the recipient": "e-mail-meddelelse er sendt til modtageren",
    "A text message notification has been sent to the recipient": "En sms-meddelelse er sendt til modtageren",
    "The shipment item has been dropped off by sender": "forsendelsesvaret er blevet afleveret af afsenderen",
    "Notification sent via APP": "Notifikation sendt via APP",
    "The shipment item has been delivered to a service point": "forsendelsesvaret er blevet leveret til et servicested",
    "The delivery of the shipment item is in progress": "leveringen af forsendelsen er i gang",
    "The shipment item has arrived at the distribution terminal": "forsendelsesvaret er ankommet til distributionsterminalen",
    "The shipment item is under transportation": "forsendelsesvaren er under transport",
    (
        "We have received a notification from your shipper that they are preparing an item for you. "
        "The tracking information will be updated when the parcel is handed over to PostNord"
    ): (
        "Vi har modtaget en meddelelse fra din afsender om, at de forbereder en vare til dig. "
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


def _request_json(
    url: str,
    data: dict[str, Any],
    timeout: int,
    headers: Optional[dict] = None,
) -> dict[str, Any]:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib_request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/json",
            "Origin": "https://t.17track.net",
            "Referer": "https://t.17track.net/da",
            "User-Agent": "Mozilla/5.0 (compatible; fjordparcel-tracking/1.0)",
            **(headers or {}),
        },
    )
    with urllib_request.urlopen(req, timeout=timeout) as response:
        payload = response.read().decode("utf-8", errors="replace")
    try:
        result = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError("17TRACK returnerede ugyldigt JSON.") from exc
    if not isinstance(result, dict):
        raise RuntimeError("17TRACK returnerede et uventet svarformat.")
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
    date_iso, display_date, display_time = _parse_datetime(event.get("time_iso") or "")
    location = _text(event.get("location") or "").replace("Denmark", "Danmark")
    description = _to_danish_text(_text(event.get("description") or ""))
    return {
        "description": description,
        "status": _text(event.get("sub_status") or event.get("stage") or ""),
        "date_iso": date_iso,
        "display_date": display_date,
        "display_time": display_time,
        "location": location,
    }


def _to_danish_text(text: str) -> str:
    clean = _text(text)
    mapped = DA_TRANSLATIONS.get(clean, clean)
    if mapped.startswith("Denmark, "):
        return "Danmark, " + mapped[len("Denmark, "):]
    return mapped.replace("Denmark", "Danmark")


def _parse_tracking_response(data: dict[str, Any], number: str, source: str = "17track-restapi") -> TrackingLookupResult:
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    shipments = data.get("shipments") if isinstance(data.get("shipments"), list) else []
    ok = int(meta.get("code") or 0) == 200
    shipment_entry = next((item for item in shipments if isinstance(item, dict) and int(item.get("code") or 0) == 200), None)
    if not ok or not shipment_entry:
        code = int(meta.get("code") or 0)
        message = "17TRACK fandt ingen forsendelse paa dette nummer."
        if code in {-10, -14}:
            message = "17TRACK kræver browser-login. Kør: python scripts/refresh_sign.py"
        return TrackingLookupResult(
            carrier="PostNord",
            tracking_number=number,
            status="Ikke fundet",
            tracking_url=_tracking_url(number),
            source="17track-restapi",
            error=message,
        )

    shipment = shipment_entry.get("shipment") if isinstance(shipment_entry.get("shipment"), dict) else {}
    tracking = shipment.get("tracking") if isinstance(shipment.get("tracking"), dict) else {}
    providers = tracking.get("providers") if isinstance(tracking.get("providers"), list) else []
    provider = providers[0] if providers and isinstance(providers[0], dict) else {}
    raw_events = provider.get("events") if isinstance(provider.get("events"), list) else []
    events = [_parse_event(item) for item in raw_events if isinstance(item, dict)]
    events_desc = sorted(events, key=lambda item: item.get("date_iso") or "", reverse=True)
    if not events_desc:
        latest_event = shipment.get("latest_event") if isinstance(shipment.get("latest_event"), dict) else {}
        if latest_event:
            events_desc = [_parse_event(latest_event)]

    latest = events_desc[0] if events_desc else {}
    latest_status = shipment.get("latest_status") if isinstance(shipment.get("latest_status"), dict) else {}
    misc = shipment.get("misc_info") if isinstance(shipment.get("misc_info"), dict) else {}
    provider_info = provider.get("provider") if isinstance(provider.get("provider"), dict) else {}
    service_type = _text(misc.get("service_type") or provider.get("service_type") or "")
    provider_name = _text(provider_info.get("name") or "")

    last_text = latest.get("description") or _to_danish_text(_text(latest_status.get("status") or "")) or "Fundet hos 17TRACK"
    status_code = _text(latest_status.get("status") or latest_status.get("sub_status") or "")

    return TrackingLookupResult(
        carrier="PostNord",
        tracking_number=number,
        status=last_text,
        status_code=status_code,
        summary=service_type or provider_name,
        last_event_at=latest.get("date_iso") or "",
        last_event_text=last_text,
        last_event_location=latest.get("location") or "",
        events=events_desc,
        tracking_url=_tracking_url(number),
        source=source,
        error="",
    )


def _build_request_payload(number: str, sign: str) -> dict[str, Any]:
    return {
        "data": [{"num": number, "fc": 0, "sc": 0}],
        "guid": "",
        "timeZoneOffset": TRACK17_TIMEZONE_OFFSET,
        "sign": sign,
    }


def _try_direct_api(number: str, sign: str, timeout: int) -> Optional[dict[str, Any]]:
    if not sign:
        return None
    try:
        payload = _build_request_payload(number, sign)
        data = _request_json(TRACK17_API_URL, payload, timeout)
        code = int((data.get("meta") or {}).get("code") or 0)
        if code == 200:
            return data
        if code in {-10, -14}:
            return None
    except Exception:
        pass
    return None


def _save_sign_to_env(sign: str) -> None:
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        return
    content = env_path.read_text(encoding="utf-8")
    if "TRACK17_SIGN" in content:
        content = re.sub(r"^TRACK17_SIGN=.*$", f"TRACK17_SIGN={sign}", content, flags=re.MULTILINE)
    else:
        content = content.rstrip("\n") + f"\nTRACK17_SIGN={sign}\n"
    env_path.write_text(content, encoding="utf-8")


def _fetch_via_playwright(number: str, timeout: int) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("playwright er ikke installeret. Kør: pip install playwright && playwright install chromium") from exc

    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(BROWSER_PROFILE_DIR),
            headless=True,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            locale="da-DK",
            viewport={"width": 1280, "height": 900},
        )

        result: dict[str, Any] = {}
        captcha_triggered = False
        new_sign: list[str] = []

        def on_response(response: Any) -> None:
            nonlocal captcha_triggered
            if "captcha/generate" in response.url and response.status == 200:
                captcha_triggered = True
            if "restapi" in response.url and response.status == 200:
                try:
                    data = json.loads(response.text())
                    if int((data.get("meta") or {}).get("code") or 0) == 200:
                        result["data"] = data
                        try:
                            req_body = json.loads(response.request.post_data or "{}")
                            sign = str(req_body.get("sign") or "")
                            if sign:
                                new_sign[:] = [sign]
                        except Exception:
                            pass
                except Exception:
                    pass

        page = ctx.new_page()
        page.on("request", on_request)
        page.on("response", on_response)

        page.goto(
            f"https://t.17track.net/en#nums={number}",
            wait_until="domcontentloaded",
            timeout=timeout * 1000,
        )

        try:
            page.click('button:has-text("Consent")', timeout=3000)
        except Exception:
            pass

        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass

        if new_sign:
            _save_sign_to_env(new_sign[0])

        ctx.close()

    if result.get("data"):
        return result["data"]

    if captcha_triggered:
        raise RuntimeError(
            "17TRACK kræver CAPTCHA-verificering. "
            "Kør scripts/refresh_sign.py for at forny sign'et."
        )

    raise RuntimeError("17TRACK returnerede ingen sporingsdata.")


def fetch_postnord_tracking(
    tracking_number: Any,
    postal_codes: Optional[list[str]] = None,
    timeout: Optional[int] = None,
) -> TrackingLookupResult:
    number = normalize_tracking_number(tracking_number)
    timeout_seconds = int(timeout or DEFAULT_TIMEOUT_SECONDS or 20)

    sign = TRACK17_SIGN
    try:
        fast_data = _try_direct_api(number, sign, timeout_seconds)
        if fast_data is not None:
            return _parse_tracking_response(fast_data, number, source="17track-restapi")
    except Exception:
        pass

    try:
        data = _fetch_via_playwright(number, timeout_seconds)
        return _parse_tracking_response(data, number, source="17track-browser")
    except urllib_error.HTTPError as exc:
        status = "Ikke fundet" if int(exc.code or 0) == 404 else "Fejl ved opdatering"
        return TrackingLookupResult(
            carrier="PostNord",
            tracking_number=number,
            status=status,
            tracking_url=_tracking_url(number),
            source="17track-browser",
            error=f"17TRACK svarede med HTTP {int(exc.code or 0)}",
        )
    except Exception as exc:
        return TrackingLookupResult(
            carrier="PostNord",
            tracking_number=number,
            status="Fejl ved opdatering",
            tracking_url=_tracking_url(number),
            source="17track-browser",
            error=str(exc)[:260],
        )
