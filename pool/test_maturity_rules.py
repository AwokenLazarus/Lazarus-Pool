#!/usr/bin/env python3
"""Coinbase maturity on the site: Knots #419's window and Knots 29.4.3's 180-day rule (#468).

    python3 pool/test_maturity_rules.py

Imports server.py the way test_earned_24h.py does (a throwaway DB, and a config.json only if none
exists). Heights and depths are mainnet's, from v29.4.3.knots20260508:
- coinbases below 973,440 need 100 under consensus, 6,480 to be relayed;
- 973,440..979,918 need 6,480 (consensus and relay), each from its own block, no cliff in practice;
- 979,919 on need 25,920 (consensus and relay), each from its own block.
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
TMP = Path(tempfile.mkdtemp(prefix="maturity-"))
if "server" not in sys.modules:
    os.environ["POOL_DB"] = str(TMP / "pool.sqlite")
    os.environ["POOL_UI_NO_WRITE"] = "1"
sys.path.insert(0, str(POOL))
try:
    import server
finally:
    if _made_cfg:
        _cfg.unlink()


class MaturityRules(unittest.TestCase):
    def test_window_coins_each_wait_6480_from_their_own_block(self):
        for h in (973440, 975000, 979918):
            self.assertEqual(server.maturity_confs(h), 6481, h)
            self.assertEqual(server.mature_height(h), h + 6480, h)
            self.assertEqual(server.payable_height(h), h + 6480, h)

    def test_from_979919_coins_wait_180_days(self):
        for h in (979919, 979920, 980019, 986399, 1000000):
            self.assertEqual(server.maturity_confs(h), 25921, h)
            self.assertEqual(server.mature_height(h), h + 25920, h)
            self.assertEqual(server.payable_height(h), h + 25920, h)

    def test_boundary(self):
        self.assertEqual(server.payable_height(979918), 986398)
        self.assertEqual(server.payable_height(979919), 1005839)
        self.assertEqual(server.payable_height(979920), 1005840)

    def test_before_the_window_consensus_100_relay_6480(self):
        self.assertEqual(server.maturity_confs(973439), 100)
        self.assertEqual(server.mature_height(973439), 973539)
        self.assertEqual(server.payable_height(973439), 973439 + 6480)

    def test_current_figure_switches_when_blocks_are_mined_under_the_new_rule(self):
        self.assertEqual(server.current_maturity_confs(979000), 6481)
        self.assertEqual(server.current_maturity_confs(979917), 6481)
        # the next block, 979,919, is the first under the 180-day rule
        self.assertEqual(server.current_maturity_confs(979918), 25921)
        self.assertEqual(server.current_maturity_confs(990000), 25921)

    def test_payout_status(self):
        self.assertEqual(server.payout_status_for_height(979919, 979919 + 25919), "immature")
        self.assertEqual(server.payout_status_for_height(979919, 979919 + 25920), "paid")
        self.assertEqual(server.payout_status_for_height(979918, 979918 + 6480), "paid")


if __name__ == "__main__":
    unittest.main()
