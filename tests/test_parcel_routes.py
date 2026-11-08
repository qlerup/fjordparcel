import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import parcel_routes as routes
import storage


class Queue:
    def __init__(self):
        self.tasks = []

    def submit(self, fn, *args):
        self.tasks.append((fn, args))

    def run(self):
        while self.tasks:
            fn, args = self.tasks.pop(0)
            fn(*args)


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DATABASE_PATH', str(tmp_path / 'routes.db'))
    monkeypatch.setenv('FJORDPARCEL_VALHALLA_URL', 'https://routing.example')
    storage.init_db()
    queue = Queue()
    monkeypatch.setattr(routes, '_workers', queue)
    monkeypatch.setattr(routes, '_slots', threading.BoundedSemaphore(32))
    return queue


ROAD = {'state': 'road', 'coordinates': [[49.4, 11.1], [50, 10], [52.4, 9.7]], 'distance_km': 477.3}
A, B, C = (49.4, 11.1), (52.4, 9.7), (55.7, 12.5)


def test_success_is_shared_persistent_and_directional(cache, monkeypatch):
    calls = []
    monkeypatch.setattr(routes, '_request_route', lambda *args: (calls.append(args) or ROAD, 0))
    assert routes.cached_leg(A, B)['state'] == 'pending'
    assert routes.cached_leg(A, B)['state'] == 'pending'
    assert len(cache.tasks) == 1
    cache.run()
    assert routes.cached_leg(A, B) == ROAD
    # Simulate a restart by replacing the process-local worker queue.
    second_queue = Queue()
    monkeypatch.setattr(routes, '_workers', second_queue)
    assert routes.cached_leg(A, B) == ROAD
    assert second_queue.tasks == []
    routes.cached_leg(B, A)
    routes.cached_leg(B, C)
    assert len(second_queue.tasks) == 2
    second_queue.run()
    assert len(calls) == 3


def test_concurrent_requests_claim_one_job(cache):
    with ThreadPoolExecutor(max_workers=8) as workers:
        results = list(workers.map(lambda _: routes.cached_leg(A, B), range(16)))
    assert all(r['state'] == 'pending' for r in results)
    assert len(cache.tasks) == 1


def test_errors_back_off_and_abandoned_leases_recover(cache, monkeypatch):
    def fail(*args):
        raise OSError('offline')
    monkeypatch.setattr(routes, '_request_route', fail)
    routes.cached_leg(A, B)
    cache.run()
    assert routes.cached_leg(A, B)['state'] == 'unavailable'
    assert not cache.tasks
    with storage.get_connection() as db:
        db.execute('UPDATE parcel_route_cache SET retry_after=0')
    assert routes.cached_leg(A, B)['state'] == 'pending'
    assert len(cache.tasks) == 1
    # An expired job must not overwrite a newer worker's result.
    with storage.get_connection() as db:
        db.execute("UPDATE parcel_route_cache SET lease='new-worker',state='road',result_json=?", (json.dumps(ROAD),))
    cache.run()
    assert routes.cached_leg(A, B) == ROAD


def test_disabled_routing_and_bounded_queue(cache, monkeypatch):
    monkeypatch.setenv('FJORDPARCEL_VALHALLA_URL', '')
    assert routes.cached_leg(A, B)['state'] == 'unavailable'
    assert not cache.tasks
    monkeypatch.setenv('FJORDPARCEL_VALHALLA_URL', 'https://routing.example')
    monkeypatch.setattr(routes, '_slots', threading.BoundedSemaphore(0))
    assert routes.cached_leg(A, B)['state'] == 'pending'
    assert not cache.tasks
    with storage.get_connection() as db:
        assert db.execute('SELECT count(*) FROM parcel_route_cache').fetchone()[0] == 0


def test_request_uses_truck_coordinates_only_and_validates_geometry(monkeypatch):
    sent = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit):
            return json.dumps({'trip': {'legs': [{'shape': {'type': 'LineString',
                'coordinates': [[11.1, 49.4], [9.7, 52.4]]}}], 'summary': {'length': 477.3}}}).encode()
    def request(req, timeout):
        sent.append(json.loads(req.data))
        assert timeout == 12
        return Response()
    monkeypatch.setattr(routes.urllib.request, 'urlopen', request)
    result, retry = routes._request_route('https://routing.example', A, B)
    assert result['coordinates'] == [[49.4, 11.1], [52.4, 9.7]]
    assert retry == 0
    assert sent[0]['costing'] == 'truck'
    assert set(sent[0]) == {'locations', 'costing', 'shape_format', 'directions_options'}
    assert all(set(p) == {'lat', 'lon', 'radius', 'minimum_reachability'} for p in sent[0]['locations'])
    assert routes._decode_shape('??_c`|@_gayB') == [[0, 0], [2, 1]]
    with pytest.raises(ValueError):
        routes._decode_shape('_')


def test_new_tracking_stop_reuses_existing_leg(cache, monkeypatch):
    monkeypatch.setattr(routes, '_request_route', lambda *args: (ROAD, 0))
    def event(city, day):
        return {'location': city, 'date_iso': f'2026-11-{day:02}T12:00:00Z'}
    events = [event('Langenhagen, Germany', 7), event('Nurnberg, Germany', 6)]
    assert routes.route_overview(events)['pending']
    cache.run()
    data = routes.route_overview([event('Kolding', 8)] + events)
    assert data['segments'][0]['state'] == 'road'
    assert data['segments'][1]['state'] == 'pending'
    assert len(cache.tasks) == 1
