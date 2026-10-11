"""Fixed local browser endpoints for the preferred durable workflow."""
import io
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from PIL import Image

from forge import server


def response(url, *, method='GET', data=None):
    request = urllib.request.Request(url, data=data, method=method,
                                    headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=3) as result:
            return result.status, result.headers.get_content_type(), result.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get_content_type(), error.read()


def test_workflow_http_contract_uses_fixed_model_and_same_origin_previews(monkeypatch):
    buffer = io.BytesIO()
    Image.new('RGBA', (16, 16), (255, 0, 0, 255)).save(buffer, 'PNG')
    png = buffer.getvalue()

    class Jobs:
        submitted = None
        def submit(self, tool, args):
            self.submitted = (tool, args)
            return {'request_id': args['request_id'], 'state': 'starting', 'stage': 'Starting workflow',
                    'log': ['Starting workflow'], 'previews': []}
        def status(self, request_id):
            return {'request_id': request_id, 'state': 'completed', 'stage': 'Ready for visual review',
                    'log': ['Ready for visual review'], 'library_id': 'lib123',
                    'previews': [{'kind': 'Final sprite', 'png': f'/api/workflows/{request_id}/preview/sprite.png'},
                                 {'kind': 'Visual review', 'png': f'/api/workflows/{request_id}/preview/review.png'}],
                    'result': {'library_id': 'lib123', 'status': 'awaiting_visual_review',
                               'qa': {'passed': True}}}
        def cancel(self, request_id):
            return {'request_id': request_id, 'state': 'cancelled', 'stage': 'Stopping workflow',
                    'log': [], 'previews': []}
        def preview(self, request_id, asset):
            assert request_id == 'browser-job'
            if asset != 'sprite.png':
                from forge.workflow_jobs import JobError
                raise JobError('ASSET_NOT_FOUND')
            return png

    jobs = Jobs()
    monkeypatch.setattr(server, 'WORKFLOW_JOBS', jobs)
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{httpd.server_port}'
    try:
        body = json.dumps({'request_id': 'browser-job', 'prompt': 'pink cabinet', 'width': 32,
                           'height': 48, 'project': 'dead-mall', 'checks': ['readable at 1x']}).encode()
        code, _, payload = response(base + '/api/workflows/prepare', method='POST', data=body)
        assert code == 200
        assert json.loads(payload)['state'] == 'starting'
        assert jobs.submitted[0] == 'forge_prepare'
        assert jobs.submitted[1]['request_id'] == 'browser-job'

        code, _, payload = response(base + '/api/workflows/browser-job')
        result = json.loads(payload)
        assert code == 200 and result['state'] == 'completed'
        assert result['result']['png'] == '/api/workflows/browser-job/preview/sprite.png'
        assert result['result']['review_png'] == '/api/workflows/browser-job/preview/review.png'
        assert result['result']['qa'] == {'passed': True}

        code, media, payload = response(base + '/api/workflows/browser-job/preview/sprite.png')
        assert code == 200 and media == 'image/png' and payload == png
        code, _, payload = response(base + '/api/workflows/browser-job/preview/%2E%2E')
        assert code == 400 and json.loads(payload)['error']['code'] == 'ASSET_NOT_FOUND'

        code, _, payload = response(base + '/api/workflows/browser-job/cancel', method='POST', data=b'{}')
        cancelled = json.loads(payload)
        assert code == 200 and cancelled['state'] == 'cancelled'
        assert 'does not guarantee a refund' in cancelled['log'][-1]

        bad = json.dumps({'request_id': 'bad-job', 'prompt': 'cabinet', 'width': 32,
                          'height': 48, 'input_png': '/tmp/secret.png'}).encode()
        code, _, payload = response(base + '/api/workflows/prepare', method='POST', data=bad)
        assert code == 400 and json.loads(payload)['error']['code'] == 'INVALID_REQUEST'
        assert jobs.submitted[1]['request_id'] == 'browser-job'
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(2)
        monkeypatch.setattr(server, 'WORKFLOW_JOBS', None)
