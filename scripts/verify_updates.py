#!/usr/bin/env python3
"""Verify that Pages serves the exact ASCII updater feed built by this run."""
import argparse
import hashlib
import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


def verify(url, expected_sha256, *, attempts=10, delay=3):
    if len(expected_sha256) != 64 or any(c not in "0123456789abcdef" for c in expected_sha256):
        raise ValueError("Expected SHA-256 must be 64 lowercase hexadecimal characters")
    parts = urlsplit(url)
    if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname == "127.0.0.1"):
        raise ValueError("Feed URL must use HTTPS or loopback HTTP")
    query = parse_qsl(parts.query, keep_blank_values=True) + [("feed_sha256", expected_sha256)]
    request_url = urlunsplit(parts._replace(query=urlencode(query)))
    last_error = "Feed not available"
    for attempt in range(attempts):
        try:
            request = Request(request_url, headers={"Cache-Control": "no-cache", "User-Agent": "survey-correction-feed-check"})
            with urlopen(request, timeout=10) as response:
                data = response.read()
            if not data.isascii():
                raise ValueError("Published feed contains non-ASCII bytes")
            releases = json.loads(data)
            if not isinstance(releases, list) or not releases:
                raise ValueError("Published feed is not a nonempty release array")
            actual = hashlib.sha256(data).hexdigest()
            if actual != expected_sha256:
                raise ValueError("Published feed does not yet match this build")
            return {"url": url, "sha256": actual, "release_count": len(releases), "ascii_only": True}
        except (HTTPError, URLError, OSError, ValueError) as error:
            last_error = str(error)
            if attempt + 1 < attempts:
                time.sleep(delay)
    raise ValueError(last_error)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.url, args.sha256), ensure_ascii=True))
        return 0
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())