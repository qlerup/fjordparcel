"""Persistent truck routes from Valhalla. Only city coordinates leave the server."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
import logging
import math
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid

from parcel_map import route_points

log = logging.getLogger(__name__)
DEFAULT_URL = 'https://valhalla1.openstreetmap.de'
_workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix='parcel-route')
_slots = threading.BoundedSemaphore(32)
LEASE_SECONDS = 300
MAX_LEGS = 64


def init(db):
    db.execute('''CREATE TABLE IF NOT EXISTS parcel_route_cache (
        key TEXT PRIMARY KEY, state TEXT NOT NULL, result_json TEXT,
        retry_after REAL NOT NULL, lease TEXT NOT NULL
    )''')


@contextmanager
def _connection(path):
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def _decode_shape(shape):
    """Valhalla's default polyline uses six decimal places."""
    result, offset, lat, lon = [], 0, 0, 0
    while offset < len(shape):
        deltas = []
        for _ in range(2):
            value, shift = 0, 0
            while True:
                if offset >= len(shape) or shift > 30:
                    raise ValueError('Truncated route shape')
                part = ord(shape[offset]) - 63
                offset += 1
                if not 0 <= part <= 63:
                    raise ValueError('Invalid route encoding')
                value |= (part & 31) << shift
                shift += 5
                if part < 32:
                    break
            deltas.append(~(value >> 1) if value & 1 else value >> 1)
        lat += deltas[0]
        lon += deltas[1]
        result.append([lon / 1e6, lat / 1e6])
        if len(result) > 50000:
            raise ValueError('Route shape too large')
    return result


def _request_route(endpoint, start, end):
    payload = {
        'locations': [{'lat': p[0], 'lon': p[1], 'radius': 5000,
                       'minimum_reachability': 1} for p in (start, end)],
        'costing': 'truck', 'shape_format': 'polyline6',
        'directions_options': {'units': 'kilometers', 'directions_type': 'none'},
    }
    req = urllib.request.Request(endpoint + '/route', data=json.dumps(payload).encode(),
                                 headers={'Content-Type': 'application/json',
                                          'User-Agent': 'FjordParcel/1.0 (parcel route overview)'})
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            raw = response.read(4_000_001)
            if len(raw) > 4_000_000:
                raise ValueError('Route response too large')
            data = json.loads(raw)
    except urllib.error.HTTPError as exc:
        if exc.code == 400:
            data = json.loads(exc.read(16000))
            if data.get('error_code') in (170, 171, 172, 442):
                return {'state': 'unavailable'}, 86400
        raise
    legs = data.get('trip', {}).get('legs', [])
    if not legs:
        raise ValueError('Missing route geometry')
    coordinates = []
    for leg in legs:
        shape = leg.get('shape')
        if isinstance(shape, str):
            points = _decode_shape(shape)
        elif isinstance(shape, dict) and shape.get('type') == 'LineString':
            points = shape.get('coordinates', [])
        else:
            raise ValueError('Invalid route shape')
        for p in points:
            if (not isinstance(p, list) or len(p) != 2 or
                    any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in p) or
                    not -180 <= p[0] <= 180 or not -90 <= p[1] <= 90):
                raise ValueError('Invalid route coordinate')
            coordinates.append([p[1], p[0]])
    if not 2 <= len(coordinates) <= 50000:
        raise ValueError('Invalid route length')
    distance = data.get('trip', {}).get('summary', {}).get('length')
    if isinstance(distance, bool) or not isinstance(distance, (int, float)) or not math.isfinite(distance) or distance < 0:
        raise ValueError('Invalid route distance')
    return {'state': 'road', 'coordinates': coordinates, 'distance_km': round(distance, 1)}, 0


def _calculate(path, key, lease, endpoint, start, end):
    try:
        try:
            result, retry = _request_route(endpoint, start, end)
        except Exception:
            log.warning('Truck route calculation failed; retrying after one hour', exc_info=True)
            result, retry = {'state': 'unavailable'}, 3600
        with _connection(path) as db:
            db.execute('''UPDATE parcel_route_cache SET state=?,result_json=?,retry_after=?
                          WHERE key=? AND lease=?''',
                       (result['state'], json.dumps(result), time.time() + retry if retry else 0, key, lease))
    finally:
        _slots.release()


def cached_leg(start, end):
    import storage
    endpoint = os.environ.get('FJORDPARCEL_VALHALLA_URL', DEFAULT_URL).strip().rstrip('/')
    if not endpoint:
        return {'state': 'unavailable'}
    start, end = tuple(start), tuple(end)
    # Successful routes never expire. Direction, engine and profile identify a leg.
    key = hashlib.sha256(json.dumps([1, endpoint, 'truck', start, end]).encode()).hexdigest()
    path, now, lease = storage.DATABASE_PATH, time.time(), uuid.uuid4().hex
    claimed = False
    try:
        with _connection(path) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM parcel_route_cache WHERE key=?', (key,)).fetchone()
            if row and row['state'] == 'road':
                return json.loads(row['result_json'])
            if row and row['retry_after'] > now:
                return json.loads(row['result_json']) if row['result_json'] else {'state': 'pending'}
            if not _slots.acquire(blocking=False):
                return {'state': 'pending'}
            claimed = True
            db.execute('''INSERT INTO parcel_route_cache VALUES (?, 'pending', NULL, ?, ?)
                          ON CONFLICT(key) DO UPDATE SET state='pending',result_json=NULL,
                          retry_after=excluded.retry_after,lease=excluded.lease''',
                       (key, now + LEASE_SECONDS, lease))
        _workers.submit(_calculate, path, key, lease, endpoint, start, end)
    except Exception:
        if claimed:
            _slots.release()
        raise
    return {'state': 'pending'}


def route_overview(events):
    route = route_points(events)
    points = [{'name': p[0], 'country': p[1], 'lat': p[2], 'lon': p[3]} for p in route]
    segments = []
    for index, (start, end) in enumerate(zip(route, route[1:])):
        leg = cached_leg(start[2:], end[2:]) if index < MAX_LEGS else {'state': 'unavailable'}
        segments.append({'from': index, 'to': index + 1, **leg})
    return {'points': points, 'segments': segments,
            'pending': any(s['state'] == 'pending' for s in segments)}
