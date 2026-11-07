"""Transactional parcel notifications, delivered through FjordHub's bot outbox."""
import hashlib
import json
import time


def init(db):
    db.execute('''CREATE TABLE IF NOT EXISTS parcel_notifications (
        event_id TEXT PRIMARY KEY, shipment_id INTEGER NOT NULL, payload TEXT NOT NULL,
        delivered INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0)''')


def event_key(event):
    # Translation changes are not new carrier events.
    from tracking_text import localize_description
    return [event.get('date_iso') or '', event.get('status') or localize_description(event.get('description')) or '',
            event.get('location') or '']


def enqueue(db, shipment, kind, event=None, events=None):
    shipment = dict(shipment)
    if shipment.get('hidden_by'):
        return
    event = event or {}
    identity = [shipment['id'], shipment['created_at'], kind, event_key(event) if event else []]
    ident = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    payload = dict(type=kind, event_id=ident, tracking_number=shipment['tracking_number'],
                   label=shipment.get('label') or '', carrier=shipment.get('carrier') or '',
                   description=event.get('description') or '', occurred_at=event.get('date_iso') or '',
                   location=event.get('location') or '')
    if events is not None:
        payload['_map_events'] = events
    db.execute('INSERT OR IGNORE INTO parcel_notifications(event_id,shipment_id,payload) VALUES(?,?,?)',
               (ident, shipment['id'], json.dumps(payload)))


def new_events(db, previous, updated, events):
    old = json.loads(previous['events_json'] or '[]')
    known = {json.dumps(event_key(e)) for e in old if isinstance(e, dict)}
    if not old and previous['last_event_at']:
        # Existing installations: do not announce their old latest event on upgrade.
        known.add(json.dumps(event_key(dict(date_iso=previous['last_event_at'],
            status=previous['status_code'], description=previous['last_event_text'],
            location=previous['last_event_location']))))
    candidates = [e for e in events if isinstance(e, dict) and e.get('description')]
    if not old:
        # Initial lookup reports the latest event, not the parcel's entire history.
        candidates = candidates[:1]
    for event in reversed(candidates):
        if json.dumps(event_key(event)) not in known:
            enqueue(db, updated, 'tracking_event', event, events=events[events.index(event):])


def deliver_one(send):
    from storage import get_connection
    now = time.time()
    with get_connection() as db:
        row = db.execute('SELECT * FROM parcel_notifications WHERE delivered=0 AND next_attempt<=? ORDER BY rowid LIMIT 1', (now,)).fetchone()
        if not row:
            return False
        if not db.execute('UPDATE parcel_notifications SET next_attempt=? WHERE event_id=? AND delivered=0 AND next_attempt<=?',
                          (now+60,row['event_id'],now)).rowcount:
            return False
        shipment = db.execute('SELECT hidden_by, events_json FROM shipments WHERE id=?',(row['shipment_id'],)).fetchone()
        if not shipment or shipment['hidden_by']:
            db.execute('UPDATE parcel_notifications SET delivered=1 WHERE event_id=?',(row['event_id'],))
            return False
    try:
        payload = json.loads(row['payload'])
        from tracking_text import localize_description
        payload['description'] = localize_description(payload.get('description'))
        map_events = payload.pop('_map_events', None)
        if payload.get('type') == 'tracking_event':
            try:
                import base64
                from parcel_map import render_map
                events = map_events if map_events is not None else json.loads(shipment['events_json'] or '[]')
                # A delayed notification must not reveal later movements.
                cutoff = payload.get('occurred_at')
                if cutoff and map_events is None:
                    from datetime import datetime, timezone
                    def event_time(value):
                        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
                        return date.replace(tzinfo=date.tzinfo or timezone.utc)
                    events = [e for e in events if e.get('date_iso') and event_time(e['date_iso']) <= event_time(cutoff)]
                image = render_map(events)
                if image:
                    payload['map_png'] = base64.b64encode(image).decode('ascii')
            except Exception:
                __import__('logging').getLogger(__name__).exception('Could not render parcel notification map')
        result = send('/api/hub/apps/notifications', payload)
    except Exception:
        return False
    if result.get('ok') and (result.get('queued') or result.get('duplicate')):
        with get_connection() as db:
            db.execute('UPDATE parcel_notifications SET delivered=1 WHERE event_id=?',(row['event_id'],))
        return True
    return False
