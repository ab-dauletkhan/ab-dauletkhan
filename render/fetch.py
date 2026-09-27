import datetime as dt
import json
import time
import urllib.error
import urllib.request


def request(url, headers, body=None):
    data = json.dumps(body).encode() if body is not None else None
    extra = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers={**headers, **extra})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp), resp.headers
        except urllib.error.HTTPError as err:
            limited = err.code == 403 and (err.headers.get("retry-after") or err.headers.get("x-ratelimit-remaining") == "0")
            if err.code not in (429, 500, 502, 503, 504) and not limited:
                raise
            time.sleep(int(err.headers.get("retry-after") or 2**attempt))
        except (urllib.error.URLError, TimeoutError):
            time.sleep(2**attempt)
    raise RuntimeError(f"{url} kept failing")


def parse_time(stamp):
    return dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
