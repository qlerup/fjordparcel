import io
from PIL import Image
from parcel_map import locate, route_points, render_map


def event(city, day):
    return {'location': city, 'date_iso': f'2026-11-{day:02}T12:00:00Z', 'description': 'På vej'}


def test_city_aliases_country_hints_and_unknown_places():
    assert locate('Aarhus') == locate('Århus, Danmark')
    assert locate('København, DK')[1] == 'DK'
    assert locate('Guangzhou, CN')[1] == 'CN'
    assert locate('Taulov, Danmark')[1] == 'DK'
    assert locate('4600 Køge')[1] == 'DK'
    assert locate('KØGE PAKKETERMINAL')[1] == 'DK'
    assert locate('63741 Aschaffenburg DE')[1] == 'DE'
    assert locate('Danmark') is None
    assert locate('Unknown depot 912384') is None
    assert locate('') is None


def test_route_is_chronological_collapses_repeated_stops_and_keeps_returns():
    events = [event('Aarhus', 7), event('Kolding', 6), event('Kolding', 5), event('Aarhus', 4)]
    assert [p[0] for p in route_points(events)] == ['Århus', 'Kolding', 'Århus']
    assert route_points(list(reversed(events))) == route_points(events)


def test_png_is_real_image_and_unknown_stops_are_skipped():
    assert render_map([event('no such location 123', 1)]) is None
    for events in ([event('Aarhus', 1)], [event('Aarhus', 7), event('Guangzhou, CN', 3)]):
        raw = render_map(events)
        image = Image.open(io.BytesIO(raw))
        assert image.size == (960, 460)
        image.verify()
        assert len(raw) < 525000


def test_map_endpoint_requires_login_and_obeys_hidden_visibility(tmp_path, monkeypatch):
    import app as web
    import storage
    monkeypatch.setattr(storage, 'DATABASE_PATH', str(tmp_path/'test.db'))
    storage.init_db()
    monkeypatch.setattr(web, '_fjordhub_managed', lambda: True)
    web.app.config.update(TESTING=True, SECRET_KEY='test-secret')
    _, shipment = storage.add_shipment('MAP12345')
    with storage.get_connection() as db:
        import json
        db.execute('UPDATE shipments SET events_json=? WHERE id=?', (json.dumps([event('Aarhus', 1)]), shipment['id']))
    client = web.app.test_client()
    url = f"/shipments/{shipment['id']}/map.png"
    assert client.get(url).status_code == 302
    with client.session_transaction() as session:
        session['user_id'] = 'test'
    response = client.get(url)
    assert response.status_code == 200
    assert response.mimetype == 'image/png'
    assert response.headers['Cache-Control'] == 'private, no-store'
    storage.set_shipment_hidden(shipment['id'], 'someone-else')
    assert client.get(url).status_code == 404
    assert client.get('/shipments/999999/map.png').status_code == 404
