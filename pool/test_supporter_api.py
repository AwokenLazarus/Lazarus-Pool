#!/usr/bin/env python3
"""The supporter routes over HTTP: export, the alert sign-up post, confirm and unsubscribe.

    python3 -m pytest pool/test_supporter_api.py -q
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

POOL = Path(__file__).resolve().parent
_cfg = POOL / "config.json"
_made_cfg = not _cfg.exists() and "server" not in sys.modules
if _made_cfg:
    _cfg.write_text(json.dumps({"public_url": "https://pool.lazarus-xbt.xyz"}))
if "server" not in sys.modules:
    os.environ["POOL_DB"] = str(Path(tempfile.mkdtemp(prefix="supapi-")) / "pool.sqlite")
sys.path.insert(0, str(POOL))
try:
    import server
finally:
    if _made_cfg:
        _cfg.unlink()
import supporters

TIPPER = "bc1qtttttttttttttttttttttttttttttttttttttt"
OTHER = "bc1qoooooooooooooooooooooooooooooooooooooo"


class Mail:
    ready = True

    def __init__(self):
        self.sent = []

    def send(self, to, subject, body, unsubscribe_url=None):
        self.sent.append((to, subject, body))
        return True


def call(port, path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=None if body is None else json.dumps(body).encode(), headers={"X-Lazarus-Client": "9.9.9.9"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.headers.get("Content-Type"), r.headers.get("Cache-Control"), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type"), e.headers.get("Cache-Control"), e.read().decode()


class Routes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        d = Path(tempfile.mkdtemp(prefix="supapi-"))
        log = d / "blocks.jsonl"
        now = int(time.time())
        log.write_text(json.dumps({"height": 5, "ts": now - 60, "hash": "aa", "kind": "split", "split": [[TIPPER, 990_000], [OTHER, 1_000_000]], "carry_delta": [], "tips": [[TIPPER, 10_000]]}) + "\n")
        cls.keep = (server.BLOCKS_LOG, server._alerts)
        server.BLOCKS_LOG = log
        cls.mail = Mail()
        server._alerts = supporters.Alerts(d / "alerts.sqlite", cls.mail, "https://pool.example")
        cls.httpd = server.PoolHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        server.BLOCKS_LOG, server._alerts = cls.keep

    def test_export_is_for_supporters(self):
        code, ctype, _, body = call(self.port, f"/api/export/{TIPPER}.csv")
        self.assertEqual((code, ctype), (200, "text/csv; charset=utf-8"))
        self.assertEqual(body.strip().split("\n")[1].split(",")[3:], ["split", "990000", "10000", "990000", "0"])
        code, _, _, body = call(self.port, f"/api/export/{OTHER}.csv")
        self.assertEqual(code, 403)
        self.assertFalse(json.loads(body)["support"]["supporter"])
        self.assertEqual(call(self.port, "/api/export/../etc.csv")[0], 404)

    def test_an_address_the_config_names_is_a_supporter(self):
        keep = server.SUPPORTERS_ALWAYS
        server.SUPPORTERS_ALWAYS = frozenset([OTHER])
        try:
            self.assertIn(OTHER, server.supporter_addresses())
            self.assertTrue(server.supporter_status(OTHER)["supporter"])
            self.assertFalse(server.supporter_status("bc1qnobody")["supporter"])
        finally:
            server.SUPPORTERS_ALWAYS = keep

    def test_alert_links_are_answered_when_every_slot_is_taken(self):
        held = 0
        while server._inflight.acquire(blocking=False):
            held += 1
        try:
            self.assertEqual(call(self.port, f"/api/export/{TIPPER}.csv")[0], 503)
            self.assertIn("no longer valid", call(self.port, "/api/alerts/confirm?t=" + "z" * 32)[3])
        finally:
            for _ in range(held):
                server._inflight.release()

    def test_the_board_lists_supporters_and_not_the_pools_own(self):
        code, _, _, body = call(self.port, "/api/supporters")
        doc = json.loads(body)
        self.assertEqual((code, doc["count"], doc["days"], doc["min_percent"]), (200, 1, 7, 0.5))
        self.assertEqual(doc["supporters"], [{"address": TIPPER, "tip_sats_7d": 10_000, "tip_percent_7d": 1.0}])
        keep = server.SUPPORTERS_ALWAYS
        server.SUPPORTERS_ALWAYS = frozenset([TIPPER, OTHER])
        try:
            self.assertEqual(server.supporter_board()["supporters"], [])
        finally:
            server.SUPPORTERS_ALWAYS = keep

    def test_rows_are_marked(self):
        doc = server.stamp_supporters({"online": [{"address": TIPPER}, {"address": OTHER}], "n": 2})
        self.assertEqual([r["supporter"] for r in doc["online"]], [True, False])

    def test_sign_up_confirm_unsubscribe(self):
        code, _, cache, body = call(self.port, "/api/alerts/subscribe", {"address": OTHER, "email": "a@example.org"})
        self.assertEqual((code, json.loads(body)["code"], cache), (403, "not_supporter", "no-store"))
        self.assertEqual(call(self.port, "/api/alerts/subscribe", {"address": TIPPER, "email": "nope"})[0], 400)
        self.assertEqual(call(self.port, "/api/alerts/subscribe", {"address": "x", "email": "a@example.org"})[0], 400)
        self.assertEqual(call(self.port, "/api/other", {})[0], 404)
        code, _, _, body = call(self.port, "/api/alerts/subscribe", {"address": TIPPER, "email": "a@example.org"})
        self.assertEqual((code, json.loads(body)), (200, {"ok": True, "code": "sent"}))
        token = self.mail.sent[-1][2].split("confirm?t=")[1].split()[0]
        code, ctype, cache, body = call(self.port, "/api/alerts/confirm?t=" + token)
        self.assertEqual((code, ctype, cache), (200, "text/html; charset=utf-8", "no-store"))
        self.assertIn("Worker alerts are on", body)
        self.assertIn("no longer valid", call(self.port, "/api/alerts/confirm?t=" + "z" * 32)[3])
        self.assertEqual(server._alerts.subscribed_addresses(), [TIPPER])
        self.assertIn("Worker alerts are off", call(self.port, "/api/alerts/unsubscribe?t=" + token)[3])
        self.assertEqual(server._alerts.subscribed_addresses(), [])
        self.assertEqual(json.loads(call(self.port, "/api/alerts/unsubscribe?t=" + token, {})[3]), {"ok": False})


if __name__ == "__main__":
    unittest.main()
