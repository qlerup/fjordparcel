"""Offline browser verification of route maps at phone and desktop sizes."""
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
    import json
    from playwright.sync_api import sync_playwright, expect
    module._fjordhub_managed = lambda: True
    module.connected_accounts = lambda: []
    module._FJORDHUB_URL = ''
    module._FJORDHUB_API_KEY = ''
    module.app.config.update(TESTING=True, SECRET_KEY='browser-test')
    _, shipment = storage.add_shipment('MAP12345', label='Pakkens rejse')
    events = [dict(location=city, description='Under transport', date_iso=f'2026-11-{day:02}T12:00:00Z')
              for city,day in [('Aarhus',7),('Kolding',6),('Kolding',5),('Hamburg, DE',4)]]
    with storage.get_connection() as db:
        db.execute('UPDATE shipments SET events_json=?,status=? WHERE id=?', (json.dumps(events),'På vej',shipment['id']))
    client = module.app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 'tester'
        session['role'] = 'admin'
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/usr/bin/chromium', args=['--no-sandbox'])
        page = browser.new_page()
        errors, requests = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        def route(request):
            from urllib.parse import urlsplit
            url = urlsplit(request.request.url)
            if url.path.endswith('/map.png'):
                requests.append(url.path)
            response = client.open(url.path, method=request.request.method, follow_redirects=True)
            request.fulfill(status=response.status_code, body=response.data, headers=dict(response.headers))
        page.route('http://parcel.test/**', route)
        for width in (390,1440):
            page.set_viewport_size({'width':width,'height':900})
            page.goto('http://parcel.test/')
            count = len(requests)
            expect(page.locator('[data-shipment-details]')).to_be_hidden()
            assert len(requests) == count
            page.locator('[data-shipment-toggle]').click()
            image = page.locator('[data-map-url]')
            expect(image).to_be_visible()
            page.wait_for_function("document.querySelector('[data-map-url]').naturalWidth === 960")
            assert len(requests) == count+1
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=f'/tmp/parcel-map-{width}.png', full_page=True)
            page.locator('[data-shipment-toggle]').click()
            page.locator('[data-shipment-toggle]').click()
            assert len(requests) == count+1
        with storage.get_connection() as db:
            db.execute('UPDATE shipments SET events_json=?', (json.dumps([dict(location='Unknown depot 123',description='På vej')]),))
        page.reload()
        page.locator('[data-shipment-toggle]').click()
        expect(page.locator('[data-map-status]')).to_contain_text('ingen genkendelige byer')
        assert not errors, errors
        browser.close()
    print('PASS: lazy map, mobile/desktop, repeat toggles, no overflow, unknown-city state')
