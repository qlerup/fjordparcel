"""Exercise real auth ordering without touching installed data or automation."""
import os
from pathlib import Path
import subprocess
import sys


def test_active_session_revocation_and_outage(tmp_path):
    env = {**os.environ, 'DATABASE_PATH': str(tmp_path / 'parcel.db'),
           'INSTALL_STATE_PATH': str(tmp_path / 'install.json'), 'FJORDPARCEL_AUTOMATION_ENABLED':'0',
           'FJORDHUB_URL':'http://hub.test', 'FJORDHUB_API_KEY':'fixture-key', 'FJORDHUB_APP_ID':'fjordparcel'}
    script = '''
import app
app._hub_api = lambda *a, **kw: {'ok':True, 'items':[{'id':77, 'username':'Alice'}]}
client = app.app.test_client()
assert b'hub-session.js' in client.get('/login').data
with client.session_transaction() as session:
    session.update(user_id='Alice', user_name='Alice', role='user', hub_user_id=77)
assert client.get('/api/auth/access').json['authenticated'] is True
snapshot = app.app.extensions['hub_session_snapshot']
snapshot.expires = 0
app._hub_api = lambda *a, **kw: {'ok':False}
assert client.get('/api/auth/access').status_code == 503
with client.session_transaction() as session:
    assert session['user_id'] == 'Alice'
app._hub_api = lambda *a, **kw: {'ok':True, 'items':[]}
denied = client.get('/api/auth/access')
assert denied.status_code == 401 and denied.json['error_code'] == 'access_revoked'
assert client.get('/api/auth/access').json['error_code'] == 'access_revoked'
assert client.get('/', follow_redirects=False).status_code == 302
'''
    result = subprocess.run([sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[1],
                            env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
