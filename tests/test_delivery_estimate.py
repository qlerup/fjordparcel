import pytest

from delivery_estimate import display_estimate, normalize_estimate
from tracking_providers import track17, TrackingLookupResult
import storage


@pytest.mark.parametrize('raw,expected', [
    ({'from': '2026-11-09', 'to': '2026-11-10'}, '09-11-2026 – 10-11-2026'),
    ({'from': '2026-11-10T08:00:00+01:00', 'to': '2026-11-10T16:00:00+01:00'}, '10-11-2026'),
    ({'from': None, 'to': '2026-11-10T12:15:00-05:00'}, 'Senest 10-11-2026'),
    ({'from': '2026-11-09', 'to': None}, 'Tidligst 09-11-2026'),
])
def test_delivery_windows_and_one_sided_carrier_estimates(raw, expected):
    estimate = normalize_estimate({**raw, 'source': 'Official'})
    assert display_estimate(estimate['from'], estimate['to'], estimate['source']) == {
        'label': expected, 'source': 'Fragtfirmaets estimat'}


@pytest.mark.parametrize('raw', [None, [], {}, {'from': 'no date', 'to': 123},
                                {'to': '2026-02-30'}, {'from': '2026-11-10', 'to': '2026-11-09'}])
def test_missing_and_invalid_estimates_are_not_invented(raw):
    estimate = normalize_estimate(raw)
    assert display_estimate(estimate['from'], estimate['to'], estimate['source']) is None


def test_track17_maps_actual_documented_time_metrics():
    data = {'data': {'accepted': [{'number': 'ABC12345', 'track_info': {
        'latest_status': {'status': 'InTransit'}, 'time_metrics': {
            'estimated_delivery_date': {'source': 'Official', 'from': None,
                                        'to': '2026-11-10T12:15:00-05:00'}}}}]}}
    result = track17._parse_response(data, 'ABC12345', 'UPS')
    assert result.estimated_delivery_from == ''
    assert result.estimated_delivery_to == '2026-11-10T12:15:00-05:00'
    assert result.as_dict()['estimated_delivery_source'] == 'Official'
    data['data']['accepted'][0]['track_info']['time_metrics'] = None
    assert track17._parse_response(data, 'ABC12345').estimated_delivery_to == ''


def test_delivery_estimate_persists_and_handles_outages_and_withdrawals(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DATABASE_PATH', str(tmp_path/'parcels.db'))
    storage.init_db()
    _, shipment = storage.add_shipment('ABC12345', carrier='UPS')
    result = TrackingLookupResult(carrier='UPS', tracking_number='ABC12345', status='InTransit',
        source='17track-api', estimated_delivery_to='2026-11-10', estimated_delivery_source='Official')
    monkeypatch.setattr(storage, 'fetch_tracking', lambda *args, **kwargs: result)
    item = storage.refresh_shipment_tracking(shipment['id'])
    assert item['delivery_estimate']['label'] == 'Senest 10-11-2026'
    assert item['show_delivery_estimate']
    # Initializing an existing installation preserves both parcels and estimates.
    storage.init_db()
    assert storage.get_shipment(shipment['id'])['delivery_estimate'] == item['delivery_estimate']
    result.error = 'API unavailable'
    result.estimated_delivery_to = ''
    assert storage.refresh_shipment_tracking(shipment['id'])['delivery_estimate'] == item['delivery_estimate']
    result.error = ''
    assert storage.refresh_shipment_tracking(shipment['id'])['delivery_estimate'] is None
    result.estimated_delivery_to = '2026-11-10'
    result.status_code = 'Delivered'
    assert not storage.refresh_shipment_tracking(shipment['id'])['show_delivery_estimate']


def test_eta_migration_adds_columns_to_old_installation_without_losing_data(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DATABASE_PATH', str(tmp_path/'legacy.db'))
    storage.init_db()
    _, shipment = storage.add_shipment('ABC12345', carrier='UPS')
    with storage.get_connection() as db:
        for field in ('estimated_delivery_from', 'estimated_delivery_to', 'estimated_delivery_source'):
            db.execute(f'ALTER TABLE shipments DROP COLUMN {field}')
    storage.init_db()
    item = storage.get_shipment(shipment['id'])
    assert item['tracking_number'] == 'ABC12345'
    assert item['estimated_delivery_to'] == '' and item['delivery_estimate'] is None
