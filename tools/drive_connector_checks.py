"""Browser checks for the Drive connector UI, using only simulated account responses.

The existing storage API tests cover PKCE, token storage and file transfer separately.
This walk checks user actions, popup fallback, validation, errors and modal navigation.
"""
import json
import os
from pathlib import Path


def check_drive_connector(js, wait_for, check, shot, send, out):
    def preview(name):
        shot(name)
        if directory := os.environ.get('ONEPROD_UI_PREVIEW_DIR'):
            target = Path(directory)
            target.mkdir(parents=True, exist_ok=True)
            (target / f'{name}.png').write_bytes((out / f'{name}.png').read_bytes())

    js(r"""{
      const mock = window.driveUiMock = {
        originalFetch: window.fetch, originalOpen: window.open, calls: [], failBrowse: false,
        drive: {client_id: '', folder: 'One Production', auto: false, email: '', has_secret: false, connected: false}
      };
      const reply = (body, status = 200) => new Response(JSON.stringify(body), {status, headers: {'Content-Type': 'application/json'}});
      window.open = () => null; // exercise popup-blocker fallback without opening an external tab
      window.fetch = async (input, options = {}) => {
        const url = new URL(typeof input === 'string' ? input : input.url, location.origin);
        const path = url.pathname, method = options.method || 'GET';
        const isDrive = path === '/api/storage' || path.startsWith('/api/storage/drive')
          || (path === '/api/storage/browse' && url.searchParams.get('location') === 'drive')
          || /^\/api\/projects\/[^/]+\/storage\/import$/.test(path);
        if (!isDrive) return mock.originalFetch.call(window, input, options);
        const body = options.body ? JSON.parse(options.body) : null;
        mock.calls.push({path, method, body});
        if (path === '/api/storage') return reply({internal: {path: '', auto: false}, drive: mock.drive, jobs: []});
        if (path === '/api/storage/drive' && method === 'PUT') {
          Object.assign(mock.drive, {client_id: body.client_id, folder: body.folder, auto: body.auto,
            has_secret: !!body.client_secret || mock.drive.has_secret});
          return reply({drive: mock.drive});
        }
        if (path === '/api/storage/drive' && method === 'DELETE') {
          mock.drive.connected = false; mock.drive.email = ''; return reply({drive: mock.drive});
        }
        if (path === '/api/storage/drive/connect') return reply({url: 'https://accounts.example.test/signin', state: 'ui-test'});
        if (path === '/api/storage/drive/connect/ui-test') {
          mock.drive.connected = true; mock.drive.email = 'creator@example.test'; return reply({connected: true});
        }
        if (path === '/api/storage/browse') {
          if (!mock.drive.connected) return reply({detail: 'Connect Google Drive first.'}, 400);
          if (mock.failBrowse) return reply({detail: 'Google Drive needs connecting again (the sign-in was revoked or expired).'}, 400);
          const folder = url.searchParams.get('where');
          const items = folder === 'competitors' ? [
            {id: 'video-1', name: 'Competitor video.mp4', size: 12000000},
            {id: 'transcript-1', name: 'Competitor transcript.srt', size: 1000}
          ] : [
            {id: 'competitors', name: 'Competitors', folder: true},
            {id: 'doc-1', name: 'Google Doc transcript', google_doc: true}
          ];
          return reply({items});
        }
        if (path.endsWith('/storage/import')) return reply({imported: ['imports/Competitor video.mp4', 'imports/Competitor transcript.srt'], skipped: []});
        return reply({detail: 'Unexpected simulated request'}, 400);
      };
    }""")
    try:
        js("document.querySelector('#mcpbtn').click()")
        check("Drive card shows disconnected account", wait_for("document.querySelector('#mcp_drive_state').textContent === 'Not connected'"))
        check("Drive actions need a connected account", js("document.querySelector('#mcp_drive_test').disabled && document.querySelector('#mcp_drive_browse').disabled"))
        js("document.querySelector('#mcp_drive_connect').click()")
        check("Drive setup opens from MCP", wait_for("document.querySelector('#stormodal').classList.contains('drive-focused') && document.querySelector('#stor_drive_setup').open"))
        check("Drive opens its own browser", js("document.querySelector('#stor_loc').value === 'drive' && getComputedStyle(document.querySelector('#stor_int')).display === 'none'"))

        client_file = out / 'drive-client.json'
        doc = send('DOM.getDocument')['root']['nodeId']
        input_id = send('DOM.querySelector', nodeId=doc, selector='#stor_client_json')['nodeId']
        client_file.write_text(json.dumps({'web': {'client_id': 'wrong-client'}}), encoding='utf-8')
        send('DOM.setFileInputFiles', nodeId=input_id, files=[str(client_file)])
        check("Web OAuth clients are rejected clearly", wait_for("document.querySelector('#stor_drive_result').textContent.includes('Desktop app OAuth client JSON')"))
        client_file.write_text(json.dumps({'installed': {'client_id': 'ui-client.apps.googleusercontent.com', 'client_secret': 'fake-ui-secret'}}), encoding='utf-8')
        send('DOM.setFileInputFiles', nodeId=input_id, files=[str(client_file)])
        check("Desktop OAuth JSON fills fields locally", wait_for("document.querySelector('#stor_cid').value === 'ui-client.apps.googleusercontent.com' && document.querySelector('#stor_secret').value === 'fake-ui-secret'"))
        check("Client JSON is not saved until requested", js("!driveUiMock.calls.some(c => c.method === 'PUT') && document.querySelector('#stor_secret').type === 'password'"))
        preview('google-drive-setup')
        viewport = js('({width: innerWidth, height: innerHeight})')
        send('Emulation.setDeviceMetricsOverride', width=560, height=900, deviceScaleFactor=1, mobile=False)
        check("Drive setup fits a narrow window", js("document.querySelector('#stormodal .modal').scrollWidth <= document.querySelector('#stormodal .modal').clientWidth + 1"))
        preview('google-drive-narrow')
        send('Emulation.setDeviceMetricsOverride', width=viewport['width'], height=viewport['height'], deviceScaleFactor=1, mobile=False)

        js("document.querySelector('#stor_connect').click()")
        check("Sign-in has a fallback when popups are blocked", wait_for("!document.querySelector('#stor_signin_fallback').hidden && document.querySelector('#stor_signin_link').href === 'https://accounts.example.test/signin'"))
        check("Drive shows connected email after sign-in", wait_for("document.querySelector('#stor_drv_state').textContent.includes('creator@example.test')"))
        check("Saved secret is cleared from the form", js("document.querySelector('#stor_secret').value === ''"))
        check("Google Docs import limit is visible", js("document.querySelector('[data-pick=doc-1]').disabled"))
        js("document.querySelector('[data-open=competitors]').click()")
        check("Drive folders can be opened", wait_for("document.querySelector('#stor_where').textContent.includes('Competitors') && !!document.querySelector('[data-pick=video-1]')"))
        js("document.querySelector('[data-pick=video-1]').click(); document.querySelector('[data-pick=transcript-1]').click()")
        check("Selected files update the import button", js("document.querySelector('#stor_import').textContent === 'Import selected (2)' && !document.querySelector('#stor_import').disabled"))
        preview('google-drive-connected')
        js("document.querySelector('#stor_import').click()")
        check("Drive imports show a result", wait_for("document.querySelector('#stor_feedback').textContent.includes('Imported 2 files')"))
        check("Import sends selected Drive IDs", js("driveUiMock.calls.some(c => c.path.endsWith('/storage/import') && c.body.location === 'drive' && c.body.ids.join() === 'video-1,transcript-1')"))
        check("Import selection resets after success", wait_for("document.querySelector('#stor_import').disabled"))

        js("document.querySelector('#storclose').click()")
        check("Closing Drive returns to the MCP card", wait_for("document.querySelector('#mcpmodal').classList.contains('open') && document.querySelector('#mcp_drive_state').textContent === 'Connected'"))
        js("driveUiMock.failBrowse = true; document.querySelector('#mcp_drive_test').click()")
        check("Revoked access is shown without claiming success", wait_for("document.querySelector('#mcp_drive_result').classList.contains('error') && document.querySelector('#mcp_drive_result').textContent.includes('connecting again')"))
        check("Failed connection tests can be retried", wait_for("!document.querySelector('#mcp_drive_test').disabled"))
        js("driveUiMock.failBrowse = false; document.querySelector('#mcp_drive_test').click()")
        check("Retry verifies file access", wait_for("document.querySelector('#mcp_drive_result').textContent.includes('Connection verified')"))
        preview('google-drive-card')

        js("document.querySelector('#mcp_drive_browse').click()")
        check("Import files opens Drive directly", wait_for("document.querySelector('#stormodal').classList.contains('open') && document.querySelector('#stor_loc').value === 'drive'"))
        js("document.querySelector('#stor_disconnect').click()")
        check("Disconnect clears file selection and account", wait_for("document.querySelector('#stor_drv_state').textContent.includes('not connected') && document.querySelector('#stor_import').disabled && !document.querySelector('[data-pick]')"))
        js("document.querySelector('#storclose').click()")
        check("Disconnect updates the MCP card", wait_for("document.querySelector('#mcp_drive_state').textContent === 'Not connected' && document.querySelector('#mcp_drive_browse').disabled"))
        js("document.querySelector('#mcpclose').click()")
    finally:
        js("window.fetch = driveUiMock.originalFetch; window.open = driveUiMock.originalOpen; delete window.driveUiMock")
