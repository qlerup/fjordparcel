import json
import pytest
from tracking_text import localize_description
from tracking_providers.track17 import _parse_event


@pytest.mark.parametrize('original,translation,expected', [
    ('On the Way, Arrived at Facility', 'På vejen ankom jeg til faciliteten', 'Pakken er ankommet til en terminal'),
    ('We have your Package, Arrived at Facility', 'Vi har din pakke, ankommet til faciliteten', 'Pakken er ankommet til en terminal'),
    ('Picked up', 'Tog', 'Pakken er hentet hos afsenderen'),
    ('Departed FedEx hub', 'Afgået fra FedEx-hub', 'Pakken har forladt FedEx’ sorteringscenter'),
    ('Arrived at FedEx hub', "Ankommet til FedEx' hub", 'Pakken er ankommet til FedEx’ sorteringscenter'),
    ('Departed from Facility', 'Afgik fra faciliteten', 'Pakken har forladt terminalen'),
    ('  ARRIVED AT FACILITY. ', '', 'Pakken er ankommet til en terminal'),
])
def test_known_events_use_clear_danish(original, translation, expected):
    event = _parse_event({'description':original, 'description_translation':{'lang':'da','description':translation}})
    assert event['description'] == expected
    assert localize_description(event['description']) == expected
    assert event['description_original']


def test_unknown_details_are_preserved_instead_of_using_broad_status():
    detail = 'On the Way, Delivery delayed due to a customs inspection'
    translation = 'Leveringen er forsinket på grund af toldkontrol'
    assert localize_description(detail,translation) == translation
    assert localize_description(detail) == detail
    event = _parse_event({'description': detail, 'sub_status':'InTransit_Other'})
    assert event['description'] == detail


def test_label_does_not_claim_the_carrier_has_received_the_parcel():
    text = localize_description('Label Created, Shipper created a label, UPS has not received the package yet.')
    assert 'UPS har endnu ikke modtaget pakken' in text


def test_existing_saved_history_is_corrected_without_database_changes():
    import storage
    raw = dict(events_json=json.dumps([{'description':'På vejen ankom jeg til faciliteten'}]),
               tracking_source='17track-api', tracking_number='ABC12345', status='På vejen ankom jeg til faciliteten',
               last_event_text='På vejen ankom jeg til faciliteten')
    result = storage.row_to_dict(raw)
    assert result['status'] == result['last_event_text'] == result['events'][0]['description'] == 'Pakken er ankommet til en terminal'
    assert raw['status'] == 'På vejen ankom jeg til faciliteten'


def test_existing_history_can_use_preserved_original_for_a_better_translation():
    assert localize_description('Picked up', 'Vilkårlig maskinoversættelse') == 'Pakken er hentet hos afsenderen'


def test_translation_change_matches_legacy_latest_event_without_status():
    from parcel_notifications import new_events
    class CaptureDB:
        def execute(self, *args):
            pytest.fail('Translation-only change queued a notification')
    previous = dict(events_json='[]', last_event_at='2026-11-07T07:52:00Z',
                    status_code='', last_event_text='På vejen ankom jeg til faciliteten',
                    last_event_location='Langenhagen')
    new_events(CaptureDB(), previous, {}, [dict(date_iso=previous['last_event_at'],
        description='Pakken er ankommet til en terminal', location='Langenhagen')])
