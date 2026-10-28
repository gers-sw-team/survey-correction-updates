#!/usr/bin/env python3
"""Build an ASCII-only GitHub Releases feed for Advanced Installer Updater."""
from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

API_VERSION = "2022-11-28"
INSTALLER_SUFFIXES = (".exe", ".msi", ".msp")
RETRY_STATUSES = {429, 500, 502, 503, 504}


class FeedError(ValueError):
    """The source cannot safely produce a complete updater feed."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_json(data: bytes):
    try:
        return json.loads(data.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise FeedError("The source is not valid UTF-8 JSON") from error


def valid_url(value: object) -> bool:
    if not isinstance(value, str) or any(c.isspace() for c in value):
        return False
    try:
        parts = urlsplit(value)
        _ = parts.port
        return bool(parts.hostname) and not parts.username and not parts.password and (
            parts.scheme == "https"
            or (parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost", "::1"})
        )
    except ValueError:
        return False


def fetch_releases(repository: str, *, api_base="https://api.github.com", token=None,
                   page_size=100, attempts=3, timeout=20):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise FeedError("Repository must have the form owner/name")
    if not valid_url(api_base) or urlsplit(api_base).query or urlsplit(api_base).fragment:
        raise FeedError("API base must be an HTTPS URL or a loopback HTTP URL")
    if urlsplit(api_base).hostname not in {"api.github.com", "127.0.0.1", "localhost", "::1"}:
        raise FeedError("API base must be GitHub or a loopback test server")
    if not 1 <= page_size <= 100:
        raise FeedError("Page size must be between 1 and 100")
    handlers = [NoRedirect()]
    if urlsplit(api_base).hostname in {"127.0.0.1", "localhost", "::1"}:
        handlers.append(ProxyHandler({}))
    opener = build_opener(*handlers)
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": API_VERSION,
               "User-Agent": "survey-correction-updates-feed"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    releases = []
    for page in range(1, 101):
        url = (f"{api_base.rstrip('/')}/repos/{repository}/releases?"
               + urlencode({"per_page": page_size, "page": page}))
        for attempt in range(attempts):
            try:
                with opener.open(Request(url, headers=headers, method="GET"), timeout=timeout) as response:
                    items = read_json(response.read())
                break
            except HTTPError as error:
                if error.code not in RETRY_STATUSES or attempt == attempts - 1:
                    raise FeedError(f"GitHub releases request failed with HTTP {error.code}") from error
            except (URLError, TimeoutError, OSError) as error:
                if attempt == attempts - 1:
                    raise FeedError("GitHub releases request failed; no feed was written") from error
            time.sleep(2 ** attempt)
        if not isinstance(items, list):
            raise FeedError("GitHub releases response must be an array")
        releases.extend(items)
        if len(items) < page_size:
            return releases
    raise FeedError("Pagination exceeded 100 pages; refusing a partial feed")


def prepare_releases(source):
    if not isinstance(source, list):
        raise FeedError("The release source must be an array")
    result = []
    seen_ids, seen_nodes, seen_tags = set(), set(), set()
    for release in source:
        if not isinstance(release, dict):
            raise FeedError("Each release must be an object")
        if type(release.get("draft")) is not bool or type(release.get("prerelease")) is not bool:
            raise FeedError("Release visibility flags must be booleans")
        if release["draft"] or release["prerelease"] or not release.get("published_at"):
            continue
        release_id = release.get("id")
        node_id, tag = release.get("node_id"), release.get("tag_name")
        if type(release_id) is not int or release_id <= 0:
            raise FeedError("Published release has no valid numeric ID")
        if not isinstance(node_id, str) or not node_id or not isinstance(tag, str) or not tag:
            raise FeedError("Published release has no node_id or tag_name")
        if release_id in seen_ids or node_id in seen_nodes or tag in seen_tags:
            raise FeedError("Duplicate release ID, node_id or tag; retry the snapshot")
        name = release.get("name")
        if not isinstance(name, str) or not name.strip():
            raise FeedError(f"Release {tag} has no display name")
        assets = release.get("assets")
        if not isinstance(assets, list) or any(not isinstance(a, dict) for a in assets):
            raise FeedError(f"Release {tag} has no valid asset list")
        installers = [a for a in assets if isinstance(a.get("name"), str)
                      and a["name"].lower().endswith(INSTALLER_SUFFIXES)]
        if not installers:
            raise FeedError(f"Release {tag} has no installer asset")
        required_assets = [installers[0]] + [a for a in assets if a.get("name") == "advinst_update.json"]
        for asset in required_assets:
            if (asset.get("state") != "uploaded" or type(asset.get("size")) is not int
                    or asset["size"] <= 0 or not valid_url(asset.get("browser_download_url"))):
                raise FeedError(f"Release {tag} has an incomplete or invalid updater asset")
        body = release.get("body")
        if body is not None and not isinstance(body, str):
            raise FeedError(f"Release {tag} description must be text or null")
        item = copy.deepcopy(release)
        # AI's Markdown parser also requires CRLF to retain all three sections.
        item["body"] = (body or "").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
        result.append(item)
        seen_ids.add(release_id)
        seen_nodes.add(node_id)
        seen_tags.add(tag)
    if not result:
        raise FeedError("No published stable releases with installers; previous deployment must remain")
    return result


def encode_releases(releases) -> bytes:
    try:
        payload = (json.dumps(releases, ensure_ascii=True, allow_nan=False,
                              sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise FeedError("The release data cannot be serialized as ASCII JSON") from error
    if json.loads(payload) != releases:
        raise FeedError("JSON round-trip verification failed")
    return payload


def atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def build_site(source, output: Path, repository: str):
    releases = prepare_releases(source)
    payload = encode_releases(releases)
    atomic_write(output / "updates.json", payload)
    repo_link = html.escape(f"https://github.com/{repository}/releases", quote=True)
    index = ("<!doctype html><html lang=\"ru\"><meta charset=\"utf-8\">"
             "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
             "<title>Survey Correction — обновления</title><main>"
             "<h1>Survey Correction</h1>"
             f"<p><a href=\"{repo_link}\">Релизы и установщики</a></p>"
             "<p><a href=\"updates.json\">Каталог обновлений</a></p></main></html>\n")
    atomic_write(output / "index.html", index.encode("utf-8"))
    atomic_write(output / ".nojekyll", b"")
    return {"release_count": len(releases), "sha256": hashlib.sha256(payload).hexdigest(),
            "output": str(output / "updates.json"), "ascii_only": payload.isascii()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default="gers-sw-team/survey-correction-updates")
    parser.add_argument("--input", type=Path, help="Read a saved release-array snapshot instead of the API")
    parser.add_argument("--api-base", default="https://api.github.com", help="Override only for a local test API")
    parser.add_argument("--output-dir", type=Path, default=Path("_site"))
    parser.add_argument("--github-output", type=Path, help="Append trusted scalar outputs for GitHub Actions")
    args = parser.parse_args(argv)
    try:
        source = read_json(args.input.read_bytes()) if args.input else fetch_releases(
            args.repository, api_base=args.api_base, token=os.environ.get("GITHUB_TOKEN"))
        summary = build_site(source, args.output_dir, args.repository)
        if args.github_output:
            with args.github_output.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(f"sha256={summary['sha256']}\nrelease_count={summary['release_count']}\n")
        print(json.dumps(summary, ensure_ascii=True))
        return 0
    except (FeedError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())