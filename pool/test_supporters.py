#!/usr/bin/env python3
"""Supporters: who qualifies, the earnings file, and the email alerts.

    python3 -m pytest pool/test_supporters.py -q
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import supporters as S

NOW = 1_795_600_000
A, B, C = "bc1qaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bc1qbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "bc1qcccccccccccccccccccccccccccccccccccccc"


def block(height, ts, split=(), carry=(), tips=(), kind="split"):
    return {"height": height, "ts": ts, "hash": f"{height:064x}", "kind": kind,
            "split": [list(x) for x in split], "carry_delta": [list(x) for x in carry], "tips": [list(x) for x in tips]}


class Standing(unittest.TestCase):
    def test_earned_is_the_output_plus_the_balance_change(self):
        rec = block(1, NOW, split=[(A, 1_000_000)], carry=[(A, -400_000), (B, 5_000)], tips=[(A, 6_000)])
        got = S.block_earnings(rec)
        self.assertEqual(got[A], (600_000, 6_000, 1_000_000))  # 400k of the output was older balance
        self.assertEqual(got[B], (5_000, 0, 0))  # under the floor: earned, not paid

    def test_half_a_percent_over_seven_days_all_machines_together(self):
        recs = [
            block(1, NOW - 86400, split=[(A, 990_000), (B, 995_100), (C, 100_000)], tips=[(A, 10_000), (B, 4_900), (C, 900)]),
            block(2, NOW - 2 * 86400, split=[(A, 990_000), (B, 1_000_000)], tips=[(A, 10_000)]),
            # too old to count, and an orphan never counts
            block(3, NOW - 8 * 86400, split=[(B, 1)], tips=[(B, 1_000_000)]),
            block(4, NOW - 3600, split=[(B, 1)], tips=[(B, 1_000_000)], kind="orphan-stale"),
        ]
        t = S.supporter_table(recs, NOW)
        self.assertEqual((t[A]["percent"], t[A]["supporter"]), (1.0, True))
        self.assertEqual((t[B]["tip_sats"], t[B]["percent"], t[B]["supporter"]), (4_900, 0.24, False))  # one machine of several tipping
        self.assertEqual((t[C]["percent"], t[C]["supporter"]), (0.89, False))  # over 0.5% but under 1,000 sats
        self.assertFalse(S.public_status(None)["supporter"])
        self.assertEqual(S.public_status(t[A])["tip_sats_7d"], 20_000)

    def test_the_earnings_file(self):
        recs = [
            block(11, NOW, split=[(A, 500)], carry=[(A, -100)], tips=[(A, 4)]),
            block(10, NOW - 60, carry=[(A, 100)]),
            block(12, NOW, split=[(B, 9)]),
            block(13, NOW, split=[(A, 9)], kind="orphan"),
        ]
        rows = S.earnings_csv(recs, A).strip().split("\n")
        self.assertEqual(rows[0], ",".join(S.EXPORT_COLUMNS))
        self.assertEqual([r.split(",")[0] for r in rows[1:]], ["10", "11"])
        self.assertEqual(rows[2].split(",")[3:], ["split", "400", "4", "500", "-100"])


class FakeMailer:
    ready = True

    def __init__(self):
        self.sent = []

    def send(self, to, subject, body, unsubscribe_url=None):
        self.sent.append((to, subject, body))
        return True


class AlertsCase(unittest.TestCase):
    def setUp(self):
        self.t = NOW
        self.mail = FakeMailer()
        self.a = S.Alerts(Path(tempfile.mkdtemp(prefix="alerts-")) / "alerts.sqlite", self.mail, "https://pool.example", now=lambda: self.t)

    def token(self):
        return self.mail.sent[-1][2].split("confirm?t=")[1].split()[0]

    def on(self, address=A, email="m@example.org"):
        self.assertEqual(self.a.subscribe(address, email, "1.2.3.4", True), (True, "sent"))
        self.assertEqual(self.a.confirm(self.token()), address)

    def test_sign_up_needs_a_supporter_a_real_mailbox_and_the_link(self):
        self.assertEqual(self.a.subscribe(A, "m@example.org", "ip", False), (False, "not_supporter"))
        for bad in ("", "x", "a@b", "a b@c.de", "a@b.c\nBcc: x@y.zz", "<a@b.cd>"):
            self.assertEqual(self.a.subscribe(A, bad, "ip", True), (False, "bad_email"), bad)
        self.assertEqual(self.mail.sent, [])
        self.assertEqual(self.a.subscribe(A, "m@example.org", "ip", True), (True, "sent"))
        self.assertEqual(self.a.subscribed_addresses(), [])  # nothing until the link is opened
        self.assertIsNone(self.a.confirm("x" * 32))
        self.assertEqual(self.a.confirm(self.token()), A)
        self.assertEqual(self.a.subscribed_addresses(), [A])
        # asking again says the same and mails nothing new
        n = len(self.mail.sent)
        self.assertEqual(self.a.subscribe(A, "M@example.org", "ip2", True), (True, "sent"))
        self.assertEqual(len(self.mail.sent), n)

    def test_a_stale_link_and_the_limits(self):
        self.a.subscribe(A, "m@example.org", "ip", True)
        tok = self.token()
        self.t += S.CONFIRM_WITHIN_S + 1
        self.assertIsNone(self.a.confirm(tok))
        for i in range(S.MAX_ATTEMPTS_PER_HOUR):
            self.a.subscribe(A, f"u{i}@example.org", "same-ip", True)
        self.assertEqual(self.a.subscribe(A, "last@example.org", "same-ip", True), (False, "slow_down"))
        # three per address, whoever asks
        self.t += 3601
        self.assertEqual(self.a.subscribe(A, "four@example.org", "other", True), (False, "full"))

    def test_offline_after_the_wait_then_back_once_each(self):
        self.on()
        sup = {A}
        self.assertEqual(self.a.check({A: {"rig1", "rig2"}}, sup), 0)
        self.t += S.OFFLINE_AFTER_S - 60
        self.assertEqual(self.a.check({A: {"rig1"}}, sup), 0)  # not yet
        self.t += 60
        self.assertEqual(self.a.check({A: {"rig1"}}, sup), 1)
        to, subject, body = self.mail.sent[-1]
        self.assertEqual((to, subject), ("m@example.org", "Lazarus Pool: rig2 is offline"))
        self.assertIn("unsubscribe?t=", body)
        self.t += 3600
        self.assertEqual(self.a.check({A: {"rig1"}}, sup), 0)  # told once
        self.assertEqual(self.a.check({A: {"rig1", "rig2"}}, sup), 1)
        self.assertEqual(self.mail.sent[-1][1], "Lazarus Pool: rig2 is back")
        self.assertEqual(self.a.check({A: {"rig1", "rig2"}}, sup), 0)

    def test_no_mail_to_an_address_that_stopped_tipping_or_unsubscribed(self):
        self.on()
        self.a.check({A: {"rig1"}}, {A})
        self.t += S.OFFLINE_AFTER_S + 1
        n = len(self.mail.sent)
        self.assertEqual(self.a.check({A: set()}, set()), 0)
        # a supporter again: starts from what is live, with no mail about the gap
        self.assertEqual(self.a.check({A: set()}, {A}), 0)
        self.assertEqual(len(self.mail.sent), n)
        tok = self.mail.sent[0][2].split("confirm?t=")[1].split()[0]
        self.assertEqual(self.a.unsubscribe(tok), A)
        self.assertEqual(self.a.subscribed_addresses(), [])
        self.assertIsNone(self.a.unsubscribe(tok))

    def test_a_daily_cap(self):
        self.on()
        sup = {A}
        for i in range(S.MAX_MAILS_PER_DAY + 3):
            self.a.check({A: {"rig1"}}, sup)
            self.t += S.OFFLINE_AFTER_S + 1
            self.a.check({A: set()}, sup)
            self.t += 60
        alerts = [m for m in self.mail.sent if "Confirm" not in m[1]]
        self.assertEqual(len(alerts), S.MAX_MAILS_PER_DAY)

    def test_the_mailbox_file_is_private(self):
        self.assertEqual(Path(self.a.path).stat().st_mode & 0o777, 0o600)

    def test_without_mail_settings_nothing_is_offered(self):
        a = S.Alerts(Path(tempfile.mkdtemp(prefix="alerts-")) / "a.sqlite", S.Mailer(S.read_mail_env("/nonexistent")), "https://pool.example")
        self.assertFalse(a.mailer.ready)
        self.assertEqual(a.subscribe(A, "m@example.org", "ip", True), (False, "unavailable"))


if __name__ == "__main__":
    unittest.main()
