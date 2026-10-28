import contextlib
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_updates as feed
import verify_updates


def release(number=1, body="## Features\n- Добавлено автосохранение: ёж, Ёлка.\n"):
    return {"id": number, "node_id": f"RE_test_{number}", "tag_name": f"1.17.{number}",
            "name": f"Survey Correction — выпуск {number}", "draft": False, "prerelease": False,
            "published_at": "2026-10-29T00:00:00Z", "body": body,
            "author": {"login": "maintainer", "name": "Инженер"},
            "assets": [{"id": number * 10, "name": f"Survey.Correction-Setup-1.17.{number}.exe",
                        "size": 190824144, "state": "uploaded", "digest": "sha256:" + "a" * 64,
                        "browser_download_url": f"https://github.com/test/repo/releases/download/1.17.{number}/setup.exe"}],
            "future_api_field": {"preserve": [1, True, None]}}


@contextlib.contextmanager
def server(respond):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append({"path": self.path, "method": self.command,
                             "authorization": self.headers.get("Authorization")})
            status, body, headers = respond(self.path, len(requests))
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", requests
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)


class SerializationTests(unittest.TestCase):
    def test_ascii_transport_preserves_unicode_and_every_other_field(self):
        source = [release(body="## Features\r\n- Ёж и русский текст 🧭\r\n")]
        before = copy.deepcopy(source)
        data = feed.encode_releases(feed.prepare_releases(source))
        self.assertTrue(data.isascii())
        self.assertIn(b"\\u0401", data)
        self.assertIn(b"\\ud83e\\udded", data)
        self.assertEqual(json.loads(data), source)
        self.assertEqual(source, before)

    def test_only_body_line_endings_are_normalized(self):
        source = release(body="## Features\n- Один\r\n## Enhancements\r- Два\n")
        result = feed.prepare_releases([source])[0]
        self.assertEqual(result["body"], "## Features\r\n- Один\r\n## Enhancements\r\n- Два\r\n")
        self.assertEqual({k: v for k, v in result.items() if k != "body"},
                         {k: v for k, v in source.items() if k != "body"})

    def test_drafts_prereleases_and_unpublished_are_excluded(self):
        source = [release(n) for n in range(1, 5)]
        source[1]["draft"] = True
        source[2]["prerelease"] = True
        source[3]["published_at"] = None
        self.assertEqual([r["id"] for r in feed.prepare_releases(source)], [1])

    def test_duplicate_identity_is_rejected(self):
        for key in ("id", "node_id", "tag_name"):
            with self.subTest(key=key):
                first, second = release(1), release(2)
                second[key] = first[key]
                with self.assertRaisesRegex(feed.FeedError, "Duplicate"):
                    feed.prepare_releases([first, second])

    def test_incomplete_installer_is_rejected(self):
        for update in ({"state": "starter"}, {"size": 0}, {"size": True},
                       {"browser_download_url": "http://untrusted.example/setup.exe"},
                       {"browser_download_url": "file:///C:/setup.exe"}):
            with self.subTest(update=update):
                item = release()
                item["assets"][0].update(update)
                with self.assertRaisesRegex(feed.FeedError, "incomplete or invalid"):
                    feed.prepare_releases([item])

    def test_optional_detection_asset_is_preserved_and_checked(self):
        item = release(body="Description\r\n")
        item["assets"].append({"name": "advinst_update.json", "size": 42,
                               "state": "uploaded", "browser_download_url": "https://example.org/detection.json"})
        self.assertEqual(feed.prepare_releases([item]), [item])
        item["assets"][1]["state"] = "starter"
        with self.assertRaises(feed.FeedError):
            feed.prepare_releases([item])

    def test_invalid_source_never_replaces_existing_feed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing = root / "updates.json"
            existing.write_bytes(b"previous successful deployment")
            for source in ({"error": "API failure"}, [], [dict(release(), assets=[])]):
                with self.subTest(source=source), self.assertRaises(feed.FeedError):
                    feed.build_site(source, root, "test/repo")
                self.assertEqual(existing.read_bytes(), b"previous successful deployment")

    def test_nondeterministic_or_invalid_number_is_not_published(self):
        item = release()
        item["unexpected_number"] = float("nan")
        with self.assertRaises(feed.FeedError):
            feed.encode_releases(feed.prepare_releases([item]))

    def test_site_is_deterministic_and_digest_matches_exact_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = feed.build_site([release()], root, "test/repo")
            data = (root / "updates.json").read_bytes()
            second = feed.build_site([release()], root, "test/repo")
            self.assertEqual(first, second)
            self.assertEqual(first["sha256"], hashlib.sha256(data).hexdigest())
            self.assertTrue((root / "index.html").is_file())
            self.assertTrue((root / ".nojekyll").is_file())

    def test_visibility_flags_must_not_be_truthy_strings(self):
        item = release()
        item["draft"] = "false"
        with self.assertRaises(feed.FeedError):
            feed.prepare_releases([item])


class HttpTests(unittest.TestCase):
    def test_token_is_not_sent_to_an_unrelated_api_host(self):
        with mock.patch.object(feed, "build_opener") as opener:
            with self.assertRaisesRegex(feed.FeedError, "GitHub or a loopback"):
                feed.fetch_releases("test/repo", api_base="https://unrelated.example", token="test-only-token")
            opener.assert_not_called()

    def test_pagination_downloads_all_pages_using_only_get(self):
        items = [release(n) for n in range(1, 6)]
        def respond(path, count):
            query = parse_qs(urlsplit(path).query)
            page, size = int(query["page"][0]), int(query["per_page"][0])
            return 200, json.dumps(items[(page-1)*size:page*size]).encode(), {}
        with server(respond) as (base, requests):
            self.assertEqual(feed.fetch_releases("test/repo", api_base=base, page_size=2, token="test-only-token"), items)
            self.assertEqual(len(requests), 3)
            self.assertTrue(all(r["method"] == "GET" for r in requests))
            self.assertTrue(all(r["authorization"] == "Bearer test-only-token" for r in requests))

    def test_transient_server_error_is_retried(self):
        def respond(path, count):
            return (503, b"temporarily unavailable", {}) if count == 1 else (200, json.dumps([release()]).encode(), {})
        with server(respond) as (base, requests), mock.patch.object(feed.time, "sleep"):
            self.assertEqual(len(feed.fetch_releases("test/repo", api_base=base)), 1)
            self.assertEqual(len(requests), 2)

    def test_api_error_object_is_not_an_empty_feed(self):
        with server(lambda path, count: (200, b'{"message":"bad request"}', {})) as (base, _):
            with self.assertRaisesRegex(feed.FeedError, "array"):
                feed.fetch_releases("test/repo", api_base=base)

    def test_redirect_cannot_forward_an_authorization_header(self):
        with server(lambda path, count: (302, b"", {"Location": "/capture"})) as (base, requests):
            with self.assertRaisesRegex(feed.FeedError, "HTTP 302"):
                feed.fetch_releases("test/repo", api_base=base, token="test-only-token")
            self.assertEqual(len(requests), 1)
            self.assertNotEqual(requests[0]["path"], "/capture")

    def test_nonretryable_http_error_fails_immediately(self):
        with server(lambda path, count: (404, b"not found", {})) as (base, requests):
            with self.assertRaisesRegex(feed.FeedError, "HTTP 404"):
                feed.fetch_releases("test/repo", api_base=base)
            self.assertEqual(len(requests), 1)

    def test_invalid_json_fails_without_a_partial_result(self):
        with server(lambda path, count: (200, b"not json", {})) as (base, _):
            with self.assertRaisesRegex(feed.FeedError, "JSON"):
                feed.fetch_releases("test/repo", api_base=base)

    def test_published_feed_matches_build_and_is_ascii(self):
        payload = feed.encode_releases(feed.prepare_releases([release()]))
        digest = hashlib.sha256(payload).hexdigest()
        with server(lambda path, count: (200, payload, {})) as (base, requests):
            result = verify_updates.verify(base + "/updates.json", digest, attempts=1)
            self.assertEqual(result["sha256"], digest)
            self.assertIn("feed_sha256=", requests[0]["path"])

    def test_stale_or_non_ascii_published_feed_is_rejected(self):
        for data in (b"[]", json.dumps([release()], ensure_ascii=False).encode("utf-8")):
            with self.subTest(data=data), server(lambda path, count: (200, data, {})) as (base, _):
                with self.assertRaises(ValueError):
                    verify_updates.verify(base + "/updates.json", "a" * 64, attempts=1)


if __name__ == "__main__":
    unittest.main()