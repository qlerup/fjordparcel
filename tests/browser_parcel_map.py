"""Browser verification of interactive maps; optional live Valhalla smoke test."""
import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory() as folder:
    os.environ['DATABASE_PATH'] = str(Path(folder)/'test.db')
    os.environ['FJORDPARCEL_AUTOMATION_ENABLED'] = '0'
    import app as module
    import storage
    import parcel_routes
    import json
    from playwright.sync_api import sync_playwright, expect
    module._fjordhub_managed = lambda: True
    module.connected_accounts = lambda: []
    module._FJORDHUB_URL = ''
    module._FJORDHUB_API_KEY = ''
    module.app.config.update(TESTING=True, SECRET_KEY='browser-test')
    _, shipment = storage.add_shipment('MAP12345', label='Pakkens rejse')
    events = [dict(location=city, description='Under transport', date_iso=f'2026-11-{day:02}T12:00:00Z')
              for city,day in [('Langenhagen, Germany',7),('Nurnberg, Germany',6),('Nurnberg, Germany',5)]]
    road_calls = []
    request_route = parcel_routes._request_route
    def calculate(endpoint, start, end):
        road_calls.append((start, end))
        if os.environ.get('FJORDPARCEL_BROWSER_LIVE_ROUTE') == '1':
            return request_route(endpoint, start, end)
        return {'state': 'road', 'coordinates': [start, [50.5, 10.2], end], 'distance_km': 477.3}, 0
    parcel_routes._request_route = calculate
    with storage.get_connection() as db:
        db.execute('UPDATE shipments SET events_json=?,status=?,last_event_text=?,status_code=? WHERE id=?',
                   (json.dumps(events),'Pakken er ankommet til en terminal','Pakken er ankommet til en terminal','InTransit',shipment['id']))
    client = module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 'tester'
        session['role'] = 'admin'
    with sync_playwright() as p:
        executable = os.environ.get('CHROMIUM_EXECUTABLE')
        browser = p.chromium.launch(**({'executable_path': executable} if executable else {}), args=['--no-sandbox'])
        page = browser.new_page()
        errors, requests = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        def route(request):
            from urllib.parse import urlsplit
            url = urlsplit(request.request.url)
            if url.path.endswith('/route.json'):
                requests.append(url.path)
            response = client.open(url.path, method=request.request.method, follow_redirects=True)
            request.fulfill(status=response.status_code, body=response.data, headers=dict(response.headers))
        page.route('http://parcel.test/**', route)
        for width in (390,1440):
            page.set_viewport_size({'width':width,'height':900})
            page.goto('http://parcel.test/')
            expect(page.locator('.group-head').filter(has_text='Klar til afhentning')).to_have_count(0)
            expect(page.locator('.group-head').filter(has_text='På vej')).to_contain_text('(1)')
            count = len(requests)
            expect(page.locator('[data-shipment-details]')).to_be_hidden()
            assert len(requests) == count
            page.locator('[data-shipment-toggle]').click()
            canvas = page.locator('[data-map-url]')
            expect(canvas).to_be_visible()
            expect(page.locator('[data-map-status]')).to_contain_text('477 km', timeout=30000)
            expect(page.locator('.parcel-map-label')).to_have_count(2)
            expect(page.locator('.parcel-map-label').last).to_contain_text('senest')
            settled_count = len(requests)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.locator('.parcel-map').scroll_into_view_if_needed()
            page.wait_for_timeout(700)
            output = Path(os.environ.get('FJORDPARCEL_BROWSER_OUTPUT', tempfile.gettempdir()))
            output.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(output/f'parcel-map-{width}.png'), full_page=True)
            page.locator('[data-map-expand]').click()
            expect(page.locator('[data-map-expand]')).to_have_attribute('aria-pressed','true')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.locator('[data-map-expand]').click()
            page.locator('[data-shipment-toggle]').click()
            page.locator('[data-shipment-toggle]').click()
            assert len(requests) == settled_count
        assert len(road_calls) == 1, road_calls
        # A transport connection without road coverage stays explicitly dashed.
        with storage.get_connection() as db:
            db.execute("UPDATE parcel_route_cache SET state='unavailable',result_json=?,retry_after=?",
                       (json.dumps({'state':'unavailable'}), 9999999999))
        page.reload()
        page.locator('[data-shipment-toggle]').click()
        expect(page.locator('[data-map-status]')).to_contain_text('Ingen sammenhængende lastbilrute')
        assert page.locator('path[stroke-dasharray]').count() >= 1
        with storage.get_connection() as db:
            db.execute('UPDATE shipments SET events_json=?', (json.dumps([dict(location='Unknown depot 123',description='På vej')]),))
        page.reload()
        page.locator('[data-shipment-toggle]').click()
        expect(page.locator('[data-map-status]')).to_contain_text('ingen genkendelige byer')
        # Failed requests can be retried by reopening the parcel.
        page.route('**/route.json', lambda r: r.fulfill(status=503,body='offline'))
        page.reload()
        page.locator('[data-shipment-toggle]').click()
        expect(page.locator('[data-map-status]')).to_contain_text('Kortet kunne ikke hentes')
        page.unroute('**/route.json')
        page.locator('[data-shipment-toggle]').click()
        page.locator('[data-shipment-toggle]').click()
        expect(page.locator('[data-map-status]')).to_contain_text('ingen genkendelige byer')
        assert not errors, errors
        browser.close()
    print('PASS: truck route, persistent cache, lazy map, mobile/desktop, expand, repeats, fallback, errors, no overflow')
    parcel_routes._workers.shutdown(wait=True)
    db.close()
    import gc
    gc.collect()
