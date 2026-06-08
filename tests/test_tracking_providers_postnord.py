from tracking_providers import postnord


def test_encode_tracking_id_matches_known_parcelsapp_payload():
    encoded = postnord._encode_tracking_id("00073215400595127740")
    expected = "|||\x05\x01\x00}\x03\x02||\x03\x07\x03}\x00\x05\x05\x02|"

    assert encoded == expected


def test_parse_tracking_response_maps_events_and_sender():
    payload = {
        "status": "in_transit",
        "attributes": [
            {"title": "Sender", "value": "Proshop a/s"},
            {"title": "Tracking number", "value": "00073215400568030824"},
        ],
        "states": [
            {
                "date": "2026-06-06T09:22:10+02:00",
                "state": "in_transit",
                "text": "Package arrived at sorting facility",
                "location": "Taastrup",
            },
            {
                "date": "2026-06-08T11:56:40+02:00",
                "state": "ready",
                "text": "Package arrived at pickup point",
                "location": "Naestved",
            },
        ],
    }

    result = postnord._parse_tracking_response(payload, "00073215400568030824")

    assert result.carrier == "PostNord"
    assert result.source == "parcelsapp"
    assert result.summary == "Proshop a/s"
    assert result.status == "Package arrived at pickup point"
    assert result.status_code == "in_transit"
    assert result.last_event_location == "Naestved"
    assert result.last_event_at == "2026-06-08T11:56:40+02:00"
    assert len(result.events) == 2
    assert result.events[0]["description"] == "Package arrived at pickup point"


def test_parse_tracking_response_handles_no_data_error():
    result = postnord._parse_tracking_response({"error": "NO_DATA"}, "00073215400568030824")

    assert result.status == "Ikke fundet"
    assert "Ingen haendelser" in result.error
