import pytest
import app as app_module
import storage
from tracking_providers import TrackingLookupResult

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(storage,'DATABASE_PATH',str(tmp_path/'test.db'))
    storage.init_db()
    monkeypatch.setattr(app_module,'_fjordhub_managed',lambda:True)
    app_module.app.config.update(TESTING=True,SECRET_KEY='test-secret')
    monkeypatch.setattr(storage,'fetch_tracking',lambda number,**kw:TrackingLookupResult(
        tracking_number=number,carrier='DHL',status='In transit',source='17track-api'))
    client=app_module.app.test_client()
    with client.session_transaction() as session:
        session['user_id']='test';session['role']='admin'
    return client


def test_manual_add_lookup_duplicate_and_validation(client):
    assert b'Tilf' in client.get('/').data
    assert client.post('/shipments',data={'tracking_number':'ABC 12345','label':'Sko'}).status_code==302
    items=storage.list_shipments()
    assert len(items)==1 and items[0]['tracking_number']=='ABC12345'
    assert items[0]['tracking_source']=='17track-api' and items[0]['label']=='Sko'
    client.post('/shipments',data={'tracking_number':'ABC12345'})
    assert len(storage.list_shipments())==1
    client.post('/shipments',data={'tracking_number':'<bad>'})
    assert len(storage.list_shipments())==1


def test_api_outage_preserves_last_known_events(client,monkeypatch):
    client.post('/shipments',data={'tracking_number':'ABC12345'})
    item=storage.list_shipments()[0]
    monkeypatch.setattr(storage,'fetch_tracking',lambda *a,**kw:TrackingLookupResult(
        tracking_number='ABC12345',carrier='DHL',status='Error',error='Quota exhausted'))
    updated=storage.refresh_shipment_tracking(item['id'])
    assert updated['status']==item['status']
    assert updated['tracking_error']=='Quota exhausted'
