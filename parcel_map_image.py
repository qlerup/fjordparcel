"""Screenshots of the same Leaflet renderer and road geometry used in the app."""
import hashlib
import io
import json
import mimetypes
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit

from parcel_routes import route_overview

ROOT = Path(__file__).parent
ORIGIN = 'https://fjordparcel-map.invalid'
_lock = threading.Lock()

HTML = '''<!doctype html><html lang="da"><meta charset="utf-8">
<link rel="stylesheet" href="/static/vendor/leaflet/leaflet.css">
<link rel="stylesheet" href="/static/vendor/maplibre/maplibre-gl.css">
<link rel="stylesheet" href="/static/styles.css">
<style>html,body{margin:0;padding:0;width:100vw;height:100vh;background:white}
.parcel-map{margin:0;border:0;border-radius:0}.parcel-map-canvas{height:100vh;width:100vw}
body{font-family:system-ui,Arial,sans-serif}</style>
<figure class="parcel-map" data-parcel-map><button data-map-expand hidden></button>
<p data-map-status hidden style="display:none"></p>
<div class="parcel-map-canvas" data-map-url="/route.json"></div></figure>
<script src="/static/vendor/leaflet/leaflet.js"></script>
<script src="/static/vendor/maplibre/maplibre-gl.js"></script>
<script src="/static/vendor/maplibre/leaflet-maplibre-gl.js"></script>
<script src="/static/parcel-map.js"></script></html>'''


def _screenshot(data):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        options = dict(headless=True, timeout=10000, args=['--no-sandbox', '--use-angle=swiftshader',
                                           '--enable-unsafe-swiftshader'])
        executable = os.getenv('FJORDPARCEL_MAP_BROWSER')
        if executable:
            options['executable_path'] = executable
        browser = playwright.chromium.launch(**options)
        try:
            page = browser.new_page(viewport={'width':960, 'height':460}, device_scale_factor=1,
                user_agent='FjordParcel/1.0 (+https://github.com/qlerup/fjordparcel)')
            def resource(route):
                url = urlsplit(route.request.url)
                if url.hostname == 'fjordparcel-map.invalid':
                    if url.path == '/':
                        route.fulfill(body=HTML, content_type='text/html')
                    elif url.path.startswith('/static/'):
                        path = (ROOT / url.path.lstrip('/')).resolve()
                        if not path.is_relative_to((ROOT/'static').resolve()) or not path.is_file():
                            route.abort()
                        else:
                            route.fulfill(body=path.read_bytes(), content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')
                    else:
                        route.abort()
                elif url.scheme == 'https' and url.hostname == 'tiles.openfreemap.org':
                    route.continue_()
                else:
                    # No carrier data, tracking links or arbitrary URLs reach the browser.
                    route.abort()
            page.route('**/*', resource)
            page.goto(ORIGIN, wait_until='load', timeout=10000)
            size = page.evaluate('(data) => window.FjordParcelMap.frame(data)', data)
            page.set_viewport_size(size)
            page.evaluate('(data) => window.FjordParcelMap.snapshot(document.querySelector("figure"),data)', data)
            image = page.locator('[data-map-url]').screenshot(type='png', timeout=5000)
            if len(image) > 500000:
                from PIL import Image
                result = io.BytesIO()
                Image.open(io.BytesIO(image)).quantize(colors=256).save(result, format='PNG', optimize=True)
                image = result.getvalue()
                if len(image) > 500000:
                    result = io.BytesIO()
                    Image.open(io.BytesIO(image)).quantize(colors=128).save(result, format='PNG', optimize=True)
                    image = result.getvalue()
            return image
        finally:
            browser.close()


def render_map_image(events):
    data = route_overview(events)
    if not data['points']:
        return None
    # Road calculations are shared with the app and persisted across restarts.
    # Let the notification queue retry if a new road leg is not ready yet.
    deadline = time.monotonic() + 8
    while data['pending'] and time.monotonic() < deadline:
        time.sleep(.2)
        data = route_overview(events)
    if data['pending']:
        raise RuntimeError('The road route is still being calculated')
    from storage import DATABASE_PATH
    directory = Path(DATABASE_PATH).resolve().parent / 'map-images'
    directory.mkdir(parents=True, exist_ok=True)
    version = HTML.encode() + (ROOT/'static/parcel-map.js').read_bytes() + (ROOT/'static/styles.css').read_bytes()
    key = hashlib.sha256(version + json.dumps(data, sort_keys=True).encode()).hexdigest()
    path = directory / (key + '.png')
    with _lock:
        if path.exists():
            return path.read_bytes()
        image = _screenshot(data)
        temporary = path.with_suffix('.tmp-' + str(os.getpid()))
        temporary.write_bytes(image)
        temporary.replace(path)
        for old in sorted(directory.glob('*.png'), key=lambda f:f.stat().st_mtime, reverse=True)[256:]:
            old.unlink(missing_ok=True)
        return image
