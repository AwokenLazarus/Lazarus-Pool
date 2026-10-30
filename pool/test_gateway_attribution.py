#!/usr/bin/env python3
"""Worker gateway attribution when one payout address sits behind several gateway keys (issue #109).

    python3 pool/test_gateway_attribution.py

Imports server.py read-only the way dev/serve.py does (a throwaway DB, and a config.json only if
none exists), so it runs anywhere the tree is checked out.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

POOL = Path(__file__).resolve().parent
_cfg = POOL / "config.json"
_made_cfg = not _cfg.exists()
if _made_cfg:
    _cfg.write_text(json.dumps({"public_url": "https://pool.lazarus-xbt.xyz"}))
os.environ.setdefault("POOL_DB", str(Path(tempfile.mkdtemp(prefix="gwa-")) / "pool.sqlite"))
os.environ["POOL_UI_NO_WRITE"] = "1"
sys.path.insert(0, str(POOL))
try:
    import server  # noqa: E402
finally:
    if _made_cfg:
        _cfg.unlink()

ADDR = "bc1qexampleaddress0000000000000000000000000"
LIVE, OLD, OTHER = "02f0da315500062a", "50852a491cba5cee", "1111222233334444"


def client(gw, offline, tag=""):
    return {"identity": ADDR, "gateway": gw, "offline": offline, "secondary_tag": tag, "fee_path": "datum"}


class GatewayAttribution(unittest.TestCase):
    def names(self, clients, tags=()):
        server.state["prime_meta"] = {"clients": clients}
        rows = [{"gateway": g, "tag": t, "identity": ADDR, "height": h} for g, t, h in tags]
        real_db = server.db
        server.db = lambda sql, *a, **k: rows if "gateway_tags" in sql else real_db(sql, *a, **k)
        try:
            return server.gateway_names_by_address()
        finally:
            server.db = real_db

    def test_offline_record_is_not_connected_and_does_not_win(self):
        # The offline record comes last, as in the API on 2026-10-30.
        for order in ([client(LIVE, False, "rig"), client(OLD, True)], [client(OLD, True), client(LIVE, False, "rig")]):
            who = self.names(order)[ADDR]
            self.assertEqual((who["gateway"], who["connected"]), (LIVE, True), order)
            self.assertFalse(who["gateways"][OLD]["connected"])
            self.assertTrue(who["gateways"][LIVE]["connected"])

    def test_only_offline_records_are_not_connected(self):
        who = self.names([client(OLD, True)])[ADDR]
        self.assertEqual((who["gateway"], who["connected"]), (OLD, False))

    def test_worker_keeps_its_own_gateway_and_state(self):
        names = self.names([client(LIVE, False, "rig"), client(OLD, True)])
        workers = [{"address": ADDR, "worker": "a", "gateway": LIVE, "online": True, "gateway_name": ""}]
        server.stamp_gateway_names(workers, names)
        self.assertEqual((workers[0]["gateway"], workers[0]["gateway_connected"], workers[0]["gateway_name"]), (LIVE, True, "rig"))

    def test_two_live_gateways_keep_distinct_workers(self):
        names = self.names([client(LIVE, False, "east"), client(OTHER, False, "west")])
        workers = [{"address": ADDR, "worker": "a", "gateway": LIVE, "online": True},
                   {"address": ADDR, "worker": "b", "gateway": OTHER, "online": True}]
        server.stamp_gateway_names(workers, names)
        self.assertEqual([(w["gateway"], w["gateway_name"], w["gateway_connected"]) for w in workers],
                         [(LIVE, "east", True), (OTHER, "west", True)])

    def test_row_without_gateway_takes_the_live_address_entry(self):
        names = self.names([client(OLD, True), client(LIVE, False, "rig")])
        row = {"address": ADDR, "gateway_name": ""}
        server.stamp_gateway_names([row], names)
        self.assertEqual((row["gateway"], row["gateway_connected"]), (LIVE, True))

    def test_historical_tag_only_address_is_not_connected(self):
        who = self.names([], tags=[(OLD, "oldtag", 979000)])[ADDR]
        self.assertEqual((who["gateway"], who["name"], who["connected"]), (OLD, "oldtag", False))


if __name__ == "__main__":
    unittest.main()
