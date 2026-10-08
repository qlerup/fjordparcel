"""Bound active browser sessions to current FjordHub app membership."""
import threading
import time
from flask import jsonify, redirect, request, session


class HubAccessUnavailable(Exception):
    pass


class HubAccessSnapshot:
    def __init__(self, call, clock=time.monotonic):
        self.call, self.clock = call, clock
        self.lock = threading.Lock()
        self.expires, self.users = 0, []

    def member(self, identity):
        with self.lock:
            if self.clock() >= self.expires:
                try:
                    result = self.call('/api/hub/apps/users', {}, method='GET')
                except Exception as exc:
                    raise HubAccessUnavailable() from exc
                if not isinstance(result, dict) or result.get('ok') is not True or not isinstance(result.get('items'), list):
                    raise HubAccessUnavailable()
                self.users = result['items']
                self.expires = self.clock() + 5
            uid, username = identity.get('id'), str(identity.get('username') or '').casefold()
            for user in self.users:
                if (uid is not None and user.get('id') == uid) or (uid is None and username and str(user.get('username') or '').casefold() == username):
                    return user
            return None


def install(app, *, managed, subject, revoke):
    snapshot = HubAccessSnapshot(lambda *args, **kwargs: app.extensions['hub_session_call'](*args, **kwargs))
    app.extensions['hub_session_snapshot'] = snapshot

    def denied():
        # Do not carry a negative membership snapshot into a fresh login/regrant.
        snapshot.expires = 0
        session.clear()
        revoke()
        session['hub_access_revoked'] = True
        if request.path.startswith('/api/'):
            return jsonify(ok=False, authenticated=False, error_code='access_revoked',
                           error='Din adgang er blevet fjernet. Du bliver automatisk logget ud.'), 401
        return redirect('/login?access_removed=1')

    @app.before_request
    def enforce_hub_membership():
        endpoint = request.endpoint or ''
        if not managed() or endpoint in {'static', 'api_health', 'health', 'logout'} or endpoint.startswith(('api_share_', 'shared_', 'api_frame_')):
            return None
        identity = subject()
        if identity is None:
            return None
        try:
            member = snapshot.member(identity)
        except HubAccessUnavailable:
            return jsonify(ok=False, error_code='hub_unavailable', error='FjordHub kunne ikke kontaktes. Prøv igen om lidt.'), 503
        if member is None:
            return denied()

    @app.get('/api/auth/access')
    def api_hub_access():
        if session.get('hub_access_revoked') and subject() is None:
            return jsonify(ok=False, authenticated=False, error_code='access_revoked'), 401
        response = jsonify(ok=True, authenticated=subject() is not None)
        response.headers['Cache-Control'] = 'private, no-store'
        return response
