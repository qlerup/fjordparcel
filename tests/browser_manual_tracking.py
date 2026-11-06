"""Offline browser check of the real manual tracking form and POST handler."""
import os, tempfile
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory() as folder:
    os.environ['DATABASE_PATH']=str(Path(folder)/'test.db')
    os.environ['FJORDPARCEL_AUTOMATION_ENABLED']='0'
    import app as module
    import storage
    from tracking_providers import TrackingLookupResult
    from playwright.sync_api import sync_playwright, expect
    module._fjordhub_managed=lambda:True
    module.connected_accounts=lambda:[]
    module._FJORDHUB_URL=''
    module._FJORDHUB_API_KEY=''
    module.app.config.update(TESTING=True,SECRET_KEY='browser-test')
    storage.fetch_tracking=lambda number,**kw:TrackingLookupResult(tracking_number=number,carrier='DHL',status='Afventer 17TRACK',source='17track-api')
    client=module.app.test_client()
    with client.session_transaction() as session:
        session['user_id']='tester';session['role']='admin'
    with sync_playwright() as p:
        browser=p.chromium.launch()
        page=browser.new_page()
        errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        def route(r):
            from urllib.parse import urlsplit
            url=urlsplit(r.request.url)
            response=client.open(url.path+('?' +url.query if url.query else ''),method=r.request.method,data=r.request.post_data,content_type=r.request.headers.get('content-type'),follow_redirects=True)
            r.fulfill(status=response.status_code,body=response.data,headers=dict(response.headers))
        page.route('http://parcel.test/**',route)
        for width in (390,1440):
            page.set_viewport_size({'width':width,'height':900})
            page.goto('http://parcel.test/')
            expect(page.get_by_role('heading',name='Tilføj trackingnummer')).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=f'/tmp/parcel-manual-{width}.png',full_page=True)
        page.locator('[name=tracking_number]').fill('ABC12345')
        page.locator('[name=label]').fill('Testpakke')
        page.get_by_role('button',name='Tilføj pakke',exact=True).click()
        expect(page.locator('.shipment-item')).to_contain_text('ABC12345')
        assert len(storage.list_shipments())==1
        assert not errors,errors
        browser.close()
    print('PASS: manual form, mobile/desktop, real submit and saved result')
