#!/usr/bin/env python3
"""An address's balance as spendable and locked (the explorer's address page).

    python3 -m pytest pool/test_address_spendable.py -q
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

POOL = Path(__file__).resolve().parent
_cfg = POOL / "config.json"
_made_cfg = not _cfg.exists() and "server" not in sys.modules
if _made_cfg:
    _cfg.write_text(json.dumps({"public_url": "https://pool.lazarus-xbt.xyz"}))
if "server" not in sys.modules:
    os.environ["POOL_DB"] = str(Path(tempfile.mkdtemp(prefix="spendable-")) / "pool.sqlite")
sys.path.insert(0, str(POOL))
try:
    import server
finally:
    if _made_cfg:
        _cfg.unlink()


def utxo(txid, value, height=None):
    st = {"confirmed": True, "block_height": height} if height else {"confirmed": False}
    return {"txid": txid, "vout": 0, "value": value, "status": st}


class Spendable(unittest.TestCase):
    def test_lock_by_the_height_the_coin_was_mined_at(self):
        self.assertEqual(server.coinbase_lock_blocks(973_439), 100)
        self.assertEqual(server.coinbase_lock_blocks(973_440), 6_480)
        self.assertEqual(server.coinbase_lock_blocks(979_918), 6_480)
        self.assertEqual(server.coinbase_lock_blocks(979_919), 25_920)

    def test_a_coin_is_spendable_when_the_next_block_may_spend_it(self):
        tip = 983_000
        utxos = [
            utxo("pay", 500, tip),  # a payment in the tip block: spendable
            utxo("old", 1_000, 970_000),  # mined before the long lock
            utxo("d45-open", 2_000, tip + 1 - 6_480),  # 45-day lock, spendable in the next block
            utxo("d45-shut", 4_000, tip + 2 - 6_480),  # one block short
            utxo("d180", 8_000, 980_000),  # 180-day lock
            utxo("gone", 16_000, 979_000),  # the node no longer has it
            utxo("mempool", 32_000),  # unconfirmed
        ]
        cb = {(u["txid"], 0): u["txid"] != "pay" for u in utxos if u["txid"] not in ("gone", "mempool")}
        got = server.split_address_balance(utxos, cb, tip)
        self.assertEqual(got["spendable_sats"], 500 + 1_000 + 2_000)
        self.assertEqual(got["locked_sats"], 4_000 + 8_000)
        self.assertEqual(got["unconfirmed_sats"], 32_000)
        self.assertEqual((got["utxos"], got["locked_utxos"]), (6, 2))
        self.assertEqual(got["next_unlock_height"], tip + 2)
        self.assertEqual(got["last_unlock_height"], 980_000 + 25_920)

    def test_nothing_locked_has_no_unlock_height(self):
        got = server.split_address_balance([utxo("pay", 7, 900_000)], {("pay", 0): False}, 983_000)
        self.assertEqual((got["spendable_sats"], got["locked_sats"], got["next_unlock_height"]), (7, 0, None))

    def test_only_an_address_is_asked_about(self):
        self.assertTrue(server._CHAIN_ADDRESS_RE.match("bc1qt5praystcdle0nq04e3h02yjszha82uzhww85x6972lcy40k4eyqz9jfaq"))
        for bad in ("", "abc", "bc1q/../x" + "a" * 20, "a" * 101, "bc1q" + "a" * 30 + "?x=1"):
            self.assertFalse(server._CHAIN_ADDRESS_RE.match(bad), bad)


if __name__ == "__main__":
    unittest.main()
