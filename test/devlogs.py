#!/usr/bin/env python3
"""Dev-log rendering and HTTP media checks in a disposable habitat."""
import http.client
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import urlencode

sys.dont_write_bytecode = True
repo = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("hive_web_devlogs_test", str(repo / "bin/hive-web"))
spec = importlib.util.spec_from_loader(loader.name, loader)
web = importlib.util.module_from_spec(spec)
loader.exec_module(web)
logs = web.hive_devlogs


class Devlogs(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.bundle = self.root / "artifacts/devlogs/waypoint/saved-searches"
        self.bundle.mkdir(parents=True)
        self.source = self.bundle / "index.md"
        self.source.write_text('''+++
title = "Saved searches survive reload"
summary = "The client keeps the server timestamp."
author = "cedar"
date = 2026-10-11T09:00:00-04:00
commit = "abc1234"
cover = "screen.png"
+++
# Saved searches survive reload

## What changed

**Reload** keeps the saved search visible.

![Saved search screen](screen.png)
![Interaction](clip.mp4)
![Animation](animation.gif)
''')
        self.png = b"\x89PNG\r\n\x1a\n" + b"test fixture"
        (self.bundle / "screen.png").write_bytes(self.png)
        self.video = b"\x00\x00\x00\x18ftypmp42" + bytes(range(100))
        (self.bundle / "clip.mp4").write_bytes(self.video)
        (self.bundle / "animation.gif").write_bytes(b"GIF89a" + b"test fixture")

    def test_article_metadata_body_and_bundled_media(self):
        article = logs.article(self.root, "waypoint/saved-searches")
        self.assertEqual(article["date"], "2026-10-11T13:00:00+00:00")
        self.assertEqual(article["author"], "cedar")
        self.assertEqual(article["commit"], "abc1234")
        self.assertEqual(article["source"], "artifacts/devlogs/waypoint/saved-searches/index.md")
        self.assertEqual(article["url"], "/#devlog=waypoint%2Fsaved-searches")
        self.assertNotIn("<h1>", article["html"])
        self.assertIn("<strong>Reload</strong>", article["html"])
        self.assertIn('type="video/mp4"', article["html"])
        self.assertIn("controls playsinline", article["html"])
        self.assertIn("animation.gif", article["html"])
        self.assertIn('poster="/api/devlog-media?', article["html"])

    def test_plain_markdown_needs_no_metadata(self):
        self.source.write_text("# A simple change\n\nA useful explanation.\n")
        article = logs.article(self.root, "waypoint/saved-searches")
        self.assertEqual(article["title"], "A simple change")
        self.assertEqual(article["summary"], "A useful explanation.")
        self.assertEqual(article["author"], "")
        self.assertIn("A useful explanation.", article["html"])

    def test_feed_sorts_actual_dates_and_isolates_invalid_articles(self):
        other = self.bundle.parent / "earlier"
        other.mkdir()
        (other / "index.md").write_text('+++\ntitle="Earlier"\ndate=2026-10-11T15:00:00+03:00\n+++\nEarlier.')
        invalid = self.root / "artifacts/devlogs/other/broken"
        invalid.mkdir(parents=True)
        (invalid / "index.md").write_text('+++\ntitle = []\n+++\nInvalid.')
        feed = logs.articles(self.root)
        self.assertEqual([a["id"] for a in feed["articles"]], ["waypoint/saved-searches", "waypoint/earlier"])
        self.assertNotIn("html", feed["articles"][0])
        self.assertEqual(feed["errors"][0]["id"], "other/broken")
        self.assertEqual(logs.articles(self.root / "missing"), {"articles": [], "errors": []})

    def test_article_limits_and_invalid_metadata(self):
        for body in ('+++\ntitle="Unclosed"', '+++\ndate="not a date"\n+++\nText',
                     '+++\nauthor=42\n+++\nText', 'x' * (logs.ARTICLE_LIMIT + 1)):
            with self.subTest(body=body[:40]):
                self.source.write_text(body)
                with self.assertRaises(ValueError):
                    logs.article(self.root, "waypoint/saved-searches")

    def test_raw_html_dangerous_links_and_remote_media_are_inert(self):
        body = '''<script>window.pwned=1</script>
<img src=x onerror="window.pwned=2">
[danger](javascript:alert(1))
![remote](https://example.com/tracker.png)
![traversal](../secret.png)
![caption <script>](screen.png)
'''
        rendered = logs.render(self.root, "waypoint/saved-searches", body)
        self.assertNotIn("<script>", rendered)
        self.assertNotIn('<img src=x', rendered)
        self.assertNotIn('href="javascript:', rendered)
        self.assertNotIn('src="https:', rendered)
        self.assertIn("Media unavailable: remote", rendered)
        self.assertIn("caption &lt;script&gt;", rendered)

    def test_paths_and_symlinks_cannot_escape_bundle(self):
        outside = self.root / "secret.png"
        outside.write_bytes(self.png)
        (self.bundle / "escape.png").symlink_to(outside)
        for name in ("../secret.png", "/secret.png", "escape.png", "a/../../secret.png", "a\\b.png", "./screen.png"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                logs.media(self.root, "waypoint/saved-searches", name)
        for name in ("%2e%2e/secret.png", "https://example.com/screen.png", "screen.png?x=1", "screen.png#x"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                logs.local_media(self.root, "waypoint/saved-searches", name)
        for identifier in ("../saved-searches", "waypoint/../secret", "/waypoint/saved-searches", "waypoint/."):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                logs.article(self.root, identifier)
        self.source.unlink()
        self.source.symlink_to(outside)
        with self.assertRaises(ValueError):
            logs.article(self.root, "waypoint/saved-searches")
        external = self.root.parent / (self.root.name + "-outside")
        external.mkdir()
        self.addCleanup(external.rmdir)
        (self.bundle.parent / "escape").symlink_to(external, target_is_directory=True)
        with self.assertRaises(ValueError):
            logs.folder(self.root, "waypoint/escape")

    def test_media_types_and_encoded_names(self):
        for name, signature, mime in (("photo.jpg", b"\xff\xd8\xff", "image/jpeg"),
                                      ("image.webp", b"RIFF1234WEBP", "image/webp"),
                                      ("clip.webm", b"\x1aE\xdf\xa3", "video/webm"),
                                      ("screen copy.png", self.png, "image/png")):
            (self.bundle / name).write_bytes(signature)
            self.assertEqual(logs.media(self.root, "waypoint/saved-searches", name)[1], mime)
        self.assertEqual(logs.local_media(self.root, "waypoint/saved-searches", "screen%20copy.png")[0], "screen copy.png")
        for name, data in (("fake.png", b"<script>alert(1)</script>"), ("image.svg", b"<svg></svg>")):
            (self.bundle / name).write_bytes(data)
            with self.assertRaises(ValueError):
                logs.media(self.root, "waypoint/saved-searches", name)

    def start_server(self):
        root_patch = patch.object(web, "HIVE_ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        def stop():
            server.shutdown()
            server.server_close()
            worker.join()
        self.addCleanup(stop)
        self.port = server.server_port

    def request(self, path, method="GET", headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_http_list_reader_and_media_leave_habitat_unchanged(self):
        self.start_server()
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        status, _, body = self.request("/api/devlogs")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["articles"][0]["id"], "waypoint/saved-searches")
        self.assertEqual(self.request("/api/devlog?id=waypoint%2Fsaved-searches")[0], 200)
        url = logs.media_url("waypoint/saved-searches", "screen.png")
        status, headers, body = self.request(url)
        self.assertEqual((status, body, headers["Content-Type"]), (200, self.png, "image/png"))
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        status, headers, body = self.request(url, "HEAD")
        self.assertEqual((status, body, int(headers["Content-Length"])), (200, b"", len(self.png)))
        for path in ("/api/devlog?id=waypoint%2Fmissing", "/api/devlog?id=..%2Fsecret",
                     "/api/devlog-media?" + urlencode({"id": "waypoint/saved-searches", "path": "../secret.png"})):
            self.assertEqual(self.request(path)[0], 404)
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_http_video_byte_ranges_and_invalid_ranges(self):
        self.start_server()
        url = logs.media_url("waypoint/saved-searches", "clip.mp4")
        size = len(self.video)
        for requested, start, end in (("bytes=4-11", 4, 11), ("bytes=12-", 12, size-1),
                                      ("bytes=-8", size-8, size-1), ("bytes=0-99999", 0, size-1)):
            with self.subTest(range=requested):
                status, headers, body = self.request(url, headers={"Range": requested})
                self.assertEqual(status, 206)
                self.assertEqual(headers["Content-Range"], f"bytes {start}-{end}/{size}")
                self.assertEqual(headers["Accept-Ranges"], "bytes")
                self.assertEqual(headers["Content-Type"], "video/mp4")
                self.assertEqual(body, self.video[start:end+1])
        for requested in ("bytes=-0", "bytes=999999-", "bytes=10-4", "bytes=", "bytes=0-1,4-5"):
            with self.subTest(range=requested):
                status, headers, _ = self.request(url, headers={"Range": requested})
                self.assertEqual(status, 416)
                self.assertEqual(headers["Content-Range"], f"bytes */{size}")


if __name__ == "__main__":
    unittest.main()
