#!/usr/bin/python3 -I
"""Granted executable boundary. Its stdout is always a small, secret-free JSON result."""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from delivery import DeliveryError, KEY_ENV, validate_refs, validate_url


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def post_event(url, key, refs, timeout=8, _allow_loopback=False):
    validate_url(url, allow_loopback=_allow_loopback)
    validate_refs(refs)
    if not isinstance(key, str) or not key or len(key) > 65536 or "\r" in key or "\n" in key:
        return {"status": "failed", "code": "missing_secret"}
    body = json.dumps(refs, separators=(",", ":"), allow_nan=False).encode()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        with opener.open(request, timeout=timeout) as response:
            # The provider body is intentionally not returned or logged.
            return {"status": "accepted", "code": "http_200"} if response.status == 200 else {"status": "uncertain", "code": "network_uncertain"}
    except urllib.error.HTTPError as error:
        # A server error or request timeout may happen after accepting work.
        # Only explicit client rejection and redirect are safe known failures.
        status = "failed" if 300 <= error.code < 500 and error.code != 408 else "uncertain"
        error.close()
        return {"status": status, "code": "http_rejected" if status == "failed" else "network_uncertain"}
    except (urllib.error.URLError, OSError, TimeoutError):
        return {"status": "uncertain", "code": "network_uncertain"}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise DeliveryError("Invalid worker arguments")


def main():
    try:
        parser = Parser(add_help=False)
        parser.add_argument("--url", required=True)
        parser.add_argument("--refs-json", required=True)
        args = parser.parse_args()
        refs = json.loads(args.refs_json)
        key = os.environ.pop(KEY_ENV, None)
        result = post_event(args.url, key, refs)
    except (DeliveryError, ValueError, TypeError, RecursionError):
        result = {"status": "failed", "code": "invalid_request"}
    except Exception:
        result = {"status": "uncertain", "code": "network_uncertain"}
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result["status"] == "accepted" else 2


if __name__ == "__main__":
    sys.exit(main())
