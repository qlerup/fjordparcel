import json
import pytest
import storage
import parcel_notifications as notifications
from tracking_providers import TrackingLookupResult

@pytest.fixture
def db(tmp_path,monkeypatch):
    monkeypatch.setattr(storage,'DATABASE_PATH',str(tmp_path/'parcel.db'))
    storage.init_db()


def queued():
    with storage.get_connection() as db:
        return [dict(r) for r in db.execute('SELECT * FROM parcel_notifications')]


def test_new_parcel_once_and_events_survive_retries(db,monkeypatch):
    _,shipment=storage.add_shipment('ABC12345',label='Sko')
    storage.add_shipment('ABC12345')
    assert len(queued())==1
    event={'date_iso':'2026-11-06T12:00:00Z','description':'På vej','status':'InTransit','location':'Danmark'}
    monkeypatch.setattr(storage,'fetch_tracking',lambda number,**kw:TrackingLookupResult(tracking_number=number,
        carrier='DHL',status='På vej',source='17track-api',events=[event],last_event_at=event['date_iso'],last_event_text=event['description']))
    storage.refresh_shipment_tracking(shipment['id'])
    storage.refresh_shipment_tracking(shipment['id'])
    event['description']='Under transport' # Translation alone is not a new event.
    storage.refresh_shipment_tracking(shipment['id'])
    assert len(queued())==2
    assert not notifications.deliver_one(lambda *a:{'ok':True,'queued':False})
    assert not any(r['delivered'] for r in queued())
    with storage.get_connection() as con:con.execute('UPDATE parcel_notifications SET next_attempt=0')
    calls=[]
    def send(path,event):calls.append(event);return {'ok':True,'queued':True}
    assert notifications.deliver_one(send)
    assert notifications.deliver_one(send)
    assert not notifications.deliver_one(send)
    assert [e['type'] for e in calls]==['tracking_added','tracking_event']


def test_outage_and_hidden_parcels_do_not_send(db):
    _,shipment=storage.add_shipment('ABC12345')
    def offline(*a):raise OSError('offline')
    assert not notifications.deliver_one(offline)
    storage.set_shipment_hidden(shipment['id'],'owner')
    with storage.get_connection() as con:con.execute('UPDATE parcel_notifications SET next_attempt=0')
    assert not notifications.deliver_one(lambda *a:pytest.fail('Hidden parcel sent'))
    assert queued()[0]['delivered']


def test_initial_history_only_notifies_latest_and_existing_history_is_not_replayed(db,monkeypatch):
    _,shipment=storage.add_shipment('ABC12345')
    events=[{'date_iso':f'2026-11-0{day}T12:00:00Z','description':'På vej','status':'InTransit'} for day in (6,5,4)]
    monkeypatch.setattr(storage,'fetch_tracking',lambda number,**kw:TrackingLookupResult(tracking_number=number,carrier='DHL',source='17track-api',events=events))
    storage.refresh_shipment_tracking(shipment['id'])
    assert len(queued())==2
    with storage.get_connection() as con:con.execute('DELETE FROM parcel_notifications')
    storage.refresh_shipment_tracking(shipment['id'])
    assert not queued()
    events.insert(0,{'date_iso':'2026-11-07T12:00:00Z','description':'Leveret','status':'Delivered'})
    storage.refresh_shipment_tracking(shipment['id'])
    assert len(queued())==1
