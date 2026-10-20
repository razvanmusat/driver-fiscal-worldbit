"""Client HTTP minimal (stdlib) către driverele locale. Ocolește proxy-ul sistemului și nu trimite header Origin."""
import http.client
import json
import socket
import urllib.error
import urllib.request

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class HttpError(Exception):
    pass


class Refused(HttpError):
    """Conexiune refuzată: serverul nu rulează, deci cererea sigur nu a ajuns."""


class TimedOut(HttpError):
    pass


class NetworkError(HttpError):
    pass


def request(method, url, body=None, headers=None, timeout=10):
    """Întoarce (status_http, json_sau_None, text). Ridică HttpError doar la probleme de rețea."""
    data = None
    hdrs = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with _opener.open(req, timeout=timeout) as resp:
            status = resp.status
            text = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status = e.code
        text = e.read().decode("utf-8", "replace")
    except urllib.error.URLError as e:
        reason = e.reason
        if isinstance(reason, ConnectionRefusedError):
            raise Refused(str(reason)) from e
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise TimedOut(str(reason)) from e
        raise NetworkError(str(reason)) from e
    except (socket.timeout, TimeoutError) as e:
        raise TimedOut(str(e)) from e
    except (ConnectionError, http.client.HTTPException, OSError) as e:
        raise NetworkError(str(e)) from e
    try:
        parsed = json.loads(text) if text else None
    except ValueError:
        parsed = None
    return status, parsed, text
