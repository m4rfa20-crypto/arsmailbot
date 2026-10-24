"""Small urllib JSON/bytes client; credentials never included in errors."""
import json as jsonlib
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler
from urllib.parse import urlencode
from urllib.error import HTTPError
class Response:
    def __init__(self, status, data):
        self.status_code, self.data = status, data
    def json(self): return jsonlib.loads(self.data)
    def raise_for_status(self):
        if self.status_code >= 400: raise RuntimeError('HTTP status ' + str(self.status_code))
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(method, url, headers=None, params=None, json=None, data=None, timeout=45, allow_redirects=True):
    headers = dict(headers or {})
    if params: url += '?' + urlencode(params)
    if json is not None:
        data = jsonlib.dumps(json).encode()
        headers['Content-Type'] = 'application/json'
    if hasattr(data, 'read'): data = data.read()
    try:
        opener = urlopen if allow_redirects else build_opener(NoRedirect()).open
        with opener(Request(url, data=data, headers=headers, method=method), timeout=timeout) as r:
            return Response(r.status, r.read())
    except HTTPError as e:
        return Response(e.code, e.read())
def get(url, **kw): return request('GET', url, **kw)
def post(url, **kw): return request('POST', url, **kw)
def put(url, **kw): return request('PUT', url, **kw)
