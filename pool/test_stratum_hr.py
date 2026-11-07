#!/usr/bin/env python3
"""House-stratum rate per address (`_update_gateway_hr`) and the public-stratum share of the
datum/stratum split (`_path_hashrate`).

    python3 pool/test_stratum_hr.py

Imports server.py the way test_earned_24h.py does (a throwaway DB, and a config.json only if
none exists).
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
TMP = Path(tempfile.mkdtemp(prefix="stratum-hr-"))
if "server" not in sys.modules:
    os.environ["POOL_DB"] = str(TMP / "pool.sqlite")
    os.environ["POOL_UI_NO_WRITE"] = "1"
sys.path.insert(0, str(POOL))
try:
    import server
finally:
    if _made_cfg:
        _cfg.unlink()

A = "bc1qmixedminer"
T0 = 1_800_000_000
STEP = 30
# diff per poll per session for ~24 TH/s: 24e12 * 30 s / 2**32
PER_POLL = int(24e12 * STEP / 2**32)


def session(host, diff, addr=A, worker="001"):
    return {"address": addr, "worker": worker, "host": host, "via": "stratum", "diff_acc": diff}


def rate_ghs(poll_rows):
    """Feed polls 30 s apart; return the last poll's rate for A."""
    out = {}
    for i, rows in enumerate(poll_rows):
        out = server._update_gateway_hr(rows, T0 + i * STEP)
    return out.get(A, 0.0)


class GatewayRate(unittest.TestCase):
    def setUp(self):
        server._gw_diff_hist.clear()
        server._gw_last_diff.clear()
        server._gw_session_diff.clear()
        server._gw_cum_diff.clear()

    def three_sessions(self, n):
        """Three long-lived ~24 TH/s sessions, each holding a ~31M diff running total."""
        base = 31_000_000
        return [session(f"5.188.33.83:{p}", base + n * PER_POLL) for p in (12596, 28318, 20178)]

    def test_steady_sessions(self):
        polls = [self.three_sessions(n) for n in range(12)]
        self.assertAlmostEqual(rate_ghs(polls), 72_000, delta=500)

    def test_session_missing_from_one_scrape_is_not_counted_twice(self):
        polls = [self.three_sessions(n) for n in range(12)]
        polls[6] = polls[6][:1]  # two sessions absent from one scrape, back on the next
        self.assertAlmostEqual(rate_ghs(polls), 72_000, delta=500)

    def test_reconnect_does_not_credit_the_new_sessions_total(self):
        polls = [self.three_sessions(n) for n in range(6)]
        for n in range(6, 12):
            rows = self.three_sessions(n)[:2]
            rows.append(session("5.188.33.83:40000", 1_000 + (n - 6) * PER_POLL))
            polls.append(rows)
        self.assertLess(rate_ghs(polls), 75_000)

    def test_mixed_miner_split_uses_the_stratum_rate(self):
        polls = [self.three_sessions(n) for n in range(12)]
        polls[6] = []
        gw = rate_ghs(polls)
        row = {"address": A, "via": "both", "credited_hr_ghs": 1_430_000.0, "gateway_hr_ghs": gw}
        datum, stratum, _, _ = server._path_hashrate([row])
        self.assertAlmostEqual(stratum, 72_000, delta=500)
        self.assertAlmostEqual(datum + stratum, 1_430_000.0)


class PathSplit(unittest.TestCase):
    """The rows merge_prime_online hands _path_hashrate for an address on both paths."""

    def setUp(self):
        server.state["gateway_hr"] = {A: 72_000.0}

    def tearDown(self):
        server.state.pop("gateway_hr", None)

    def test_stratum_rows_then_datum_side_row(self):
        rows = [
            {"address": A, "via": "stratum", "credited_hr_ghs": 1_430_000.0, "host": "5.188.33.83:12596"},
            {"address": A, "via": "stratum", "credited_hr_ghs": 1_430_000.0, "host": "5.188.33.83:28318"},
            {"address": A, "via": "prime", "ua": "DATUM gateway", "credited_hr_ghs": 1_430_000.0,
             "gateway_hr_ghs": 72_000.0},
            {"address": "bc1qplainstratum", "via": "stratum", "credited_hr_ghs": 50_000.0},
            {"address": "bc1qplaindatum", "via": "prime", "credited_hr_ghs": 900_000.0},
        ]
        datum, stratum, n_datum, n_stratum = server._path_hashrate(rows)
        self.assertAlmostEqual(stratum, 122_000.0)
        self.assertAlmostEqual(datum, 1_358_000.0 + 900_000.0)
        self.assertEqual((n_datum, n_stratum), (2, 2))

    def test_stratum_rows_without_gateway_rate_on_the_row(self):
        rows = [
            {"address": A, "via": "stratum", "credited_hr_ghs": 1_430_000.0},
            {"address": A, "via": "prime", "credited_hr_ghs": 1_430_000.0},
        ]
        datum, stratum, _, _ = server._path_hashrate(rows)
        self.assertAlmostEqual(stratum, 72_000.0)
        self.assertAlmostEqual(datum, 1_358_000.0)


if __name__ == "__main__":
    unittest.main()
