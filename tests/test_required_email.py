from flask import Flask, session
from hub_session_guard import install


def test_required_email_gates_existing_session_and_updates_only_its_hub_identity():
    app = Flask(__name__)
    app.secret_key = 'synthetic-test-only'
    member = {'id':77, 'username':'member', 'email_required':False}
    calls = []
    def call(path, payload=None, method='GET'):
        if path.endswith('/users'): return {'ok':True, 'items':[dict(member)]}
        calls.append((path, payload))
        if payload['email'] == 'invalid': return {'ok':True, 'accepted':False, 'error':'Invalid email'}
        member['email_required'] = False
        return {'ok':True, 'accepted':True, 'user':dict(member)}
    app.extensions['hub_session_call'] = call
    install(app, managed=lambda:True, subject=lambda:({'id':session['uid']} if session.get('uid') else None), revoke=lambda:None)
    app.add_url_rule('/', 'index', lambda:'private content')
    app.add_url_rule('/api/private', 'private', lambda:{'private':True}, methods=['GET','POST'])
    client = app.test_client()
    with client.session_transaction() as state: state['uid'] = 77
    assert client.get('/api/private').status_code == 200
    member['email_required'] = True
    blocked = client.get('/api/auth/access?fresh=1')
    assert blocked.status_code == 403 and blocked.json['error_code'] == 'email_update_required'
    token = blocked.json['csrf_token']
    assert client.get('/api/private').status_code == 403
    assert b'private content' not in client.get('/').data
    assert b'hub-session.js' in client.get('/').data
    assert client.post('/api/auth/email',json={'email':'new@example.test'}).status_code == 403
    headers = {'X-CSRF-Token':token,'Origin':'http://localhost'}
    assert client.post('/api/auth/email',json={'email':'new@example.test'},headers={**headers,'Origin':'https://evil.test'}).status_code == 403
    assert client.post('/api/auth/email',json={'email':'invalid'},headers=headers).json['accepted'] is False
    assert client.get('/api/auth/access').status_code == 403
    assert client.post('/api/auth/email',json={'email':'new@example.test','id':999},headers=headers).json['accepted'] is True
    assert calls[-1] == ('/api/hub/apps/users/77/email', {'email':'new@example.test'})
    assert client.get('/api/private').status_code == 200
    with client.session_transaction() as state: assert state['uid'] == 77
    anonymous = app.test_client()
    assert anonymous.post('/api/auth/email',json={'email':'new@example.test'},headers=headers).status_code == 401
