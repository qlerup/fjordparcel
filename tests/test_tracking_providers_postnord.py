import pytest
from tracking_providers import track17, fetch_tracking


def response(number='ABC12345'):
    return {'code':0,'data':{'accepted':[{'number':number,'track_info':{
        'latest_status':{'status':'InTransit'},
        'tracking':{'providers':[{'provider':{'name':'GLS'},'events':[{
            'time_iso':'2026-11-06T10:00:00Z','description':'In transit','location':'Denmark'}]}]}}}]}}


@pytest.mark.parametrize('carrier',['GLS','DAO','Bring','PostNord','FedEx','Unknown'])
def test_all_carriers_use_same_api(monkeypatch,carrier):
    calls=[]
    monkeypatch.setattr(track17,'_get_api_key',lambda:'secret-test-key')
    def request(endpoint,payload,key,timeout):
        calls.append((endpoint,payload));return response()
    monkeypatch.setattr(track17,'_api_request',request)
    result=fetch_tracking('ABC12345',carrier=carrier)
    assert result.source=='17track-api' and result.carrier=='GLS'
    assert result.status_code=='InTransit' and len(result.events)==1
    assert calls==[('gettrackinfo',[{'number':'ABC12345'}])]


def test_register_only_unregistered_number(monkeypatch):
    monkeypatch.setattr(track17,'_get_api_key',lambda:'test')
    calls=[]
    def request(endpoint,payload,*args):
        calls.append(endpoint)
        if endpoint=='gettrackinfo':return {'data':{'rejected':[{'number':'ABC12345','error':{'code':-18019902}}]}}
        return {'code':0,'data':{'accepted':[{'number':'ABC12345'}]}}
    monkeypatch.setattr(track17,'_api_request',request)
    result=fetch_tracking('ABC12345')
    assert calls==['gettrackinfo','register','gettrackinfo']
    assert result.status=='Afventer 17TRACK' and not result.error


@pytest.mark.parametrize('code',[-18019908,-18010002,-18019903])
def test_api_errors_are_reported(monkeypatch,code):
    monkeypatch.setattr(track17,'_get_api_key',lambda:'test')
    monkeypatch.setattr(track17,'_api_request',lambda *a:{'data':{'rejected':[{'number':'ABC12345','error':{'code':code}}]}})
    assert str(code) in fetch_tracking('ABC12345').error


def test_missing_key_does_not_call_any_provider(monkeypatch):
    monkeypatch.setattr(track17,'_get_api_key',lambda:'')
    monkeypatch.setattr(track17,'_api_request',lambda *a:pytest.fail('Unexpected request'))
    assert 'API-nøgle mangler' in fetch_tracking('ABC12345').error


def test_response_never_uses_another_parcel():
    assert track17._parse_response(response('OTHER123'),'ABC12345').error


def test_registration_fetches_ready_events_immediately(monkeypatch):
    monkeypatch.setattr(track17,'_get_api_key',lambda:'test')
    responses=iter([{'data':{'rejected':[{'number':'ABC12345','error':{'code':-18019902}}]}},
                    {'code':0,'data':{'accepted':[{'number':'ABC12345'}]}}, response()])
    monkeypatch.setattr(track17,'_api_request',lambda *a:next(responses))
    result=fetch_tracking('ABC12345')
    assert result.events and result.status_code=='InTransit'


def test_empty_accepted_tracking_is_pending():
    result=track17._parse_response({'data':{'accepted':[{'number':'ABC12345','track_info':None}]}},'ABC12345')
    assert result.status=='Afventer 17TRACK' and not result.error


@pytest.mark.parametrize('latest_only',[False,True])
def test_danish_translation_used_in_summary_and_events(latest_only):
    data=response()
    info=data['data']['accepted'][0]['track_info']
    event=info['tracking']['providers'][0]['events'][0]
    event['description_translation']={'lang':'da','description':'Pakken er under transport'}
    if latest_only:
        info['latest_event']=event
        info['tracking']['providers']=[]
    result=track17._parse_response(data,'ABC12345')
    assert result.status==result.last_event_text=='Pakken er under transport'
    assert result.events[0]['description']=='Pakken er under transport'


@pytest.mark.parametrize('translation',[None,{}, {'lang':'da','description':''}, {'lang':'de','description':'Unterwegs'}])
def test_missing_danish_translation_preserves_original(translation):
    event=track17._parse_event({'description':'Original event','description_translation':translation})
    assert event['description']=='Original event'
