"""Supporters: addresses that tip the pool, and what the pool gives them back.

A supporter is a payout address whose tips over the last SUPPORTER_DAYS days come to at least
SUPPORTER_MIN_PERCENT of what it earned in the pool's blocks over those days, all its machines
together, and to at least SUPPORTER_MIN_TIP_SATS. The figures are read from primed's block log:
what each block gave an address is its output plus the change in its balance, and its tip is
the block's `tips` entry. Nothing about a supporter's payout differs from anyone else's.

Supporters get two things here:

  * worker alerts by email (a machine stopped hashing, or came back);
  * their per-block earnings as a CSV file.

Email addresses live in their own small SQLite file (`alerts.sqlite` beside the pool
database), are never part of any API answer, and are deleted on unsubscribe.

The server passes in everything this module reads from the pool (block records, live workers);
it imports nothing from the server, so it is tested on its own.
"""
import csv
import hashlib
import io
import os
import re
import secrets
import smtplib
import sqlite3
import ssl
import threading
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

SUPPORTER_DAYS = 7
SUPPORTER_MIN_PERCENT = 0.5
SUPPORTER_MIN_TIP_SATS = 1_000

# A worker is reported offline once it has not been live for this long, and at most this many
# mails go to one subscription in a day. A Prime restart drops every session for a minute or
# two, well inside the wait.
OFFLINE_AFTER_S = 15 * 60
MAX_MAILS_PER_DAY = 12
# An unconfirmed subscription is forgotten after this long.
CONFIRM_WITHIN_S = 48 * 3600
MAX_SUBSCRIPTIONS_PER_ADDRESS = 3
# Sign-up attempts: per requester and per target mailbox, in a rolling hour.
MAX_ATTEMPTS_PER_HOUR = 5

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)+$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{20,64}$")


# ---------------------------------------------------------------- who is a supporter


def block_earnings(rec):
    """{address: (earned_sats, tip_sats, paid_sats)} for one block record.

    `earned` is what the block gave the address after its tip: its output (`split`) plus the
    change in its balance (`carry_delta`, negative when the output included balance from
    earlier blocks). `paid` is the output itself."""
    out = {}
    for addr, sats in rec.get("split") or []:
        e = out.setdefault(addr, [0, 0, 0])
        e[0] += int(sats)
        e[2] += int(sats)
    for addr, delta in rec.get("carry_delta") or []:
        out.setdefault(addr, [0, 0, 0])[0] += int(delta)
    for addr, sats in rec.get("tips") or []:
        out.setdefault(addr, [0, 0, 0])[1] += int(sats)
    return {a: (max(0, e[0]), max(0, e[1]), e[2]) for a, e in out.items()}


def _counts(rec):
    return not str(rec.get("kind") or "").startswith("orphan")


def supporter_table(records, now, days=SUPPORTER_DAYS):
    """{address: {tip_sats, earned_sats, percent, supporter}} over the blocks of the last `days`.

    Only addresses that tipped at all are in it. `percent` is the tip as a share of what the
    address earned before the tip, two decimals."""
    since = int(now) - days * 86400
    tot = {}
    for rec in records:
        if not _counts(rec) or int(rec.get("ts") or 0) < since:
            continue
        for addr, (earned, tip, _paid) in block_earnings(rec).items():
            t = tot.setdefault(addr, [0, 0])
            t[0] += earned
            t[1] += tip
    out = {}
    for addr, (earned, tip) in tot.items():
        if tip <= 0:
            continue
        pct = 100.0 * tip / (earned + tip) if earned + tip > 0 else 0.0
        out[addr] = {
            "tip_sats": tip,
            "earned_sats": earned,
            "percent": round(pct, 2),
            "supporter": pct >= SUPPORTER_MIN_PERCENT and tip >= SUPPORTER_MIN_TIP_SATS,
        }
    return out


def public_status(row):
    """What the API says about one address's standing; `row` is its supporter_table entry or None."""
    row = row or {}
    return {
        "supporter": bool(row.get("supporter")),
        "tip_percent_7d": float(row.get("percent") or 0.0),
        "tip_sats_7d": int(row.get("tip_sats") or 0),
        "min_percent": SUPPORTER_MIN_PERCENT,
        "min_tip_sats": SUPPORTER_MIN_TIP_SATS,
        "days": SUPPORTER_DAYS,
    }


# ---------------------------------------------------------------- export


EXPORT_COLUMNS = ("height", "time_utc", "block_hash", "block_kind", "earned_sats", "tip_sats", "paid_in_block_sats", "balance_change_sats")


def earnings_csv(records, address):
    """The address's line in every pool block that touched it, oldest first, as CSV text."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(EXPORT_COLUMNS)
    for rec in sorted((r for r in records if _counts(r)), key=lambda r: (int(r.get("height") or 0), int(r.get("ts") or 0))):
        row = block_earnings(rec).get(address)
        if row is None:
            continue
        earned, tip, paid = row
        delta = sum(int(d) for a, d in rec.get("carry_delta") or [] if a == address)
        ts = int(rec.get("ts") or 0)
        w.writerow([
            int(rec.get("height") or 0),
            time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts)) if ts else "",
            rec.get("hash") or "",
            rec.get("kind") or "",
            earned, tip, paid, delta,
        ])
    return buf.getvalue()


# ---------------------------------------------------------------- mail


class Mailer:
    """Sends through an SMTP submission server (STARTTLS). `conf` is
    {host, port, user, token, from_addr, from_name}; without a host nothing is sent."""

    def __init__(self, conf):
        self.conf = dict(conf or {})

    @property
    def ready(self):
        c = self.conf
        return bool(c.get("host") and c.get("user") and c.get("token"))

    def send(self, to, subject, body, unsubscribe_url=None):
        if not self.ready:
            return False
        c = self.conf
        sender = c.get("from_addr") or c["user"]
        msg = EmailMessage()
        msg["From"] = f'{c.get("from_name") or "Lazarus Pool"} <{sender}>'
        msg["To"] = to
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=False)
        msg["Message-ID"] = make_msgid(domain=sender.split("@", 1)[-1])
        msg["Auto-Submitted"] = "auto-generated"
        if unsubscribe_url:
            msg["List-Unsubscribe"] = f"<{unsubscribe_url}>"
            msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        msg.set_content(body)
        try:
            with smtplib.SMTP(c["host"], int(c.get("port") or 587), timeout=20) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(c["user"], c["token"])
                s.send_message(msg)
            return True
        except (OSError, smtplib.SMTPException):
            return False


def read_mail_env(path):
    """SMTP settings from a KEY=value file (the hub's /etc/lazarus/pool-mail.env). Names:
    POOL_SMTP_HOST, POOL_SMTP_PORT, POOL_SMTP_USER, POOL_SMTP_TOKEN, POOL_MAIL_FROM,
    POOL_MAIL_FROM_NAME. A missing file is an empty configuration."""
    env = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        return {}
    return {
        "host": env.get("POOL_SMTP_HOST", ""),
        "port": env.get("POOL_SMTP_PORT", "587"),
        "user": env.get("POOL_SMTP_USER", ""),
        "token": env.get("POOL_SMTP_TOKEN", ""),
        "from_addr": env.get("POOL_MAIL_FROM", ""),
        "from_name": env.get("POOL_MAIL_FROM_NAME", ""),
    }


# ---------------------------------------------------------------- subscriptions


def valid_email(s):
    return isinstance(s, str) and len(s) <= 254 and bool(_EMAIL_RE.match(s))


def _hash(s):
    return hashlib.sha256(s.encode()).hexdigest()[:24]


class Alerts:
    """Email subscriptions and the worker state they are told about."""

    def __init__(self, path, mailer, site_url, now=time.time):
        self.path = str(path)
        self.mailer = mailer
        self.site = site_url.rstrip("/")
        self.now = now
        self._lock = threading.Lock()
        # Mailboxes: the file is the service user's alone (SQLite gives its -wal and -shm the
        # same mode).
        if not os.path.exists(self.path):
            os.close(os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600))
        self._c = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self._c.row_factory = sqlite3.Row
        self._c.execute("PRAGMA journal_mode=WAL")
        self._c.executescript(
            """
            CREATE TABLE IF NOT EXISTS subs (
              token TEXT PRIMARY KEY, address TEXT NOT NULL, email TEXT NOT NULL,
              created_ts INTEGER NOT NULL, confirmed_ts INTEGER,
              sent_day TEXT, sent_n INTEGER NOT NULL DEFAULT 0,
              UNIQUE (address, email)
            );
            CREATE TABLE IF NOT EXISTS workers (
              address TEXT NOT NULL, worker TEXT NOT NULL,
              last_live_ts INTEGER NOT NULL, down INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY (address, worker)
            );
            CREATE TABLE IF NOT EXISTS attempts (ts INTEGER NOT NULL, who TEXT NOT NULL);
            """
        )
        self._c.commit()

    # -- sign-up

    def _allowed(self, who):
        now = int(self.now())
        self._c.execute("DELETE FROM attempts WHERE ts < ?", (now - 3600,))
        n = self._c.execute("SELECT COUNT(*) FROM attempts WHERE who = ?", (who,)).fetchone()[0]
        if n >= MAX_ATTEMPTS_PER_HOUR:
            return False
        self._c.execute("INSERT INTO attempts (ts, who) VALUES (?, ?)", (now, who))
        return True

    def subscribe(self, address, email, requester, is_supporter):
        """(ok, code). Sends the confirmation mail; the subscription does nothing until its
        link is opened. Codes: sent, not_supporter, bad_email, unavailable, slow_down, full."""
        if not self.mailer.ready:
            return False, "unavailable"
        email = (email or "").strip()
        if not valid_email(email):
            return False, "bad_email"
        if not is_supporter:
            return False, "not_supporter"
        with self._lock:
            now = int(self.now())
            ok = self._allowed("ip:" + _hash(requester or "")) and self._allowed("to:" + _hash(email.lower()))
            self._c.commit()
            if not ok:
                return False, "slow_down"
            self._c.execute("DELETE FROM subs WHERE confirmed_ts IS NULL AND created_ts < ?", (now - CONFIRM_WITHIN_S,))
            row = self._c.execute("SELECT token, confirmed_ts FROM subs WHERE address = ? AND email = ? COLLATE NOCASE", (address, email)).fetchone()
            if row and row["confirmed_ts"]:
                # Already on. Say the same thing as for a new one: the answer must not tell a
                # stranger whether a mailbox is subscribed.
                return True, "sent"
            if row:
                token = row["token"]
            else:
                n = self._c.execute("SELECT COUNT(*) FROM subs WHERE address = ?", (address,)).fetchone()[0]
                if n >= MAX_SUBSCRIPTIONS_PER_ADDRESS:
                    return False, "full"
                token = secrets.token_urlsafe(24)
                self._c.execute("INSERT INTO subs (token, address, email, created_ts) VALUES (?, ?, ?, ?)", (token, address, email, now))
            self._c.commit()
        body = (
            "Someone asked Lazarus Pool to send worker alerts for this payout address to you:\n\n"
            f"  {address}\n\n"
            "To turn the alerts on, open this link:\n\n"
            f"  {self.site}/api/alerts/confirm?t={token}\n\n"
            "If this was not you, do nothing: no alerts are sent until the link is opened, and\n"
            "this request is forgotten in two days.\n\n"
            "Lazarus Pool\n"
            f"{self.site}\n"
        )
        if not self.mailer.send(email, "Confirm worker alerts for your Lazarus Pool address", body):
            return False, "unavailable"
        return True, "sent"

    def confirm(self, token):
        """The address now alerted, or None for a link that is unknown or too old."""
        if not _TOKEN_RE.match(token or ""):
            return None
        with self._lock:
            now = int(self.now())
            row = self._c.execute("SELECT address, confirmed_ts, created_ts FROM subs WHERE token = ?", (token,)).fetchone()
            if not row or (not row["confirmed_ts"] and row["created_ts"] < now - CONFIRM_WITHIN_S):
                return None
            if not row["confirmed_ts"]:
                self._c.execute("UPDATE subs SET confirmed_ts = ? WHERE token = ?", (now, token))
                self._c.commit()
            return row["address"]

    def unsubscribe(self, token):
        """The address no longer alerted, or None. The row, and the mailbox with it, is deleted."""
        if not _TOKEN_RE.match(token or ""):
            return None
        with self._lock:
            row = self._c.execute("SELECT address FROM subs WHERE token = ?", (token,)).fetchone()
            if not row:
                return None
            self._c.execute("DELETE FROM subs WHERE token = ?", (token,))
            if not self._c.execute("SELECT 1 FROM subs WHERE address = ?", (row["address"],)).fetchone():
                self._c.execute("DELETE FROM workers WHERE address = ?", (row["address"],))
            self._c.commit()
            return row["address"]

    def subscribed_addresses(self):
        with self._lock:
            return [r[0] for r in self._c.execute("SELECT DISTINCT address FROM subs WHERE confirmed_ts IS NOT NULL")]

    # -- the check

    def check(self, live_by_address, supporters):
        """One pass. `live_by_address` maps each subscribed address to the set of its worker
        names hashing now ("" for work with no worker name); `supporters` is the set of
        addresses with supporter standing. Returns the number of mails sent.

        An address that is no longer a supporter is not mailed and its worker state is left
        to age: when it qualifies again it starts from what is live then."""
        now = int(self.now())
        sent = 0
        for address in self.subscribed_addresses():
            if address not in supporters:
                with self._lock:
                    self._c.execute("DELETE FROM workers WHERE address = ?", (address,))
                    self._c.commit()
                continue
            live = set(live_by_address.get(address) or ())
            went_down, came_back = [], []
            with self._lock:
                known = {r["worker"]: r for r in self._c.execute("SELECT worker, last_live_ts, down FROM workers WHERE address = ?", (address,))}
                for w in live:
                    if w in known and known[w]["down"]:
                        came_back.append(w)
                    self._c.execute(
                        "INSERT INTO workers (address, worker, last_live_ts, down) VALUES (?, ?, ?, 0) "
                        "ON CONFLICT (address, worker) DO UPDATE SET last_live_ts = excluded.last_live_ts, down = 0",
                        (address, w, now),
                    )
                for w, r in known.items():
                    if w in live:
                        continue
                    if not r["down"] and now - r["last_live_ts"] >= OFFLINE_AFTER_S:
                        went_down.append((w, r["last_live_ts"]))
                        self._c.execute("UPDATE workers SET down = 1 WHERE address = ? AND worker = ?", (address, w))
                    elif r["down"] and now - r["last_live_ts"] >= 30 * 86400:
                        self._c.execute("DELETE FROM workers WHERE address = ? AND worker = ?", (address, w))
                self._c.commit()
            if went_down or came_back:
                sent += self._tell(address, went_down, came_back, len(live), now)
        return sent

    def _tell(self, address, went_down, came_back, live_n, now):
        def label(w):
            return w or "(no worker name)"

        lines = []
        if went_down:
            lines.append("Stopped hashing:")
            for w, last in sorted(went_down):
                lines.append(f"  {label(w)}   last share {time.strftime('%Y-%m-%d %H:%M', time.gmtime(last))} UTC")
            lines.append("")
        if came_back:
            lines.append("Hashing again:")
            lines.extend(f"  {label(w)}" for w in sorted(came_back))
            lines.append("")
        lines.append(f"Workers hashing now: {live_n}")
        if went_down and came_back:
            subject = f"Lazarus Pool: {len(went_down)} worker(s) offline, {len(came_back)} back"
        elif went_down:
            one = label(went_down[0][0])
            subject = f"Lazarus Pool: {one} is offline" if len(went_down) == 1 else f"Lazarus Pool: {len(went_down)} workers offline"
        else:
            one = label(came_back[0])
            subject = f"Lazarus Pool: {one} is back" if len(came_back) == 1 else f"Lazarus Pool: {len(came_back)} workers back"
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        sent = 0
        with self._lock:
            subs = [dict(r) for r in self._c.execute("SELECT token, email, sent_day, sent_n FROM subs WHERE address = ? AND confirmed_ts IS NOT NULL", (address,))]
        for s in subs:
            n = s["sent_n"] if s["sent_day"] == day else 0
            if n >= MAX_MAILS_PER_DAY:
                continue
            unsub = f"{self.site}/api/alerts/unsubscribe?t={s['token']}"
            body = "\n".join(
                [f"Payout address {address}", ""]
                + lines
                + ["", f"Your stats: {self.site}/miner/{address}", "", f"Stop these alerts: {unsub}", ""]
            )
            if n == MAX_MAILS_PER_DAY - 1:
                body += f"\nThis is alert {MAX_MAILS_PER_DAY} today, the most sent in one day; the next comes tomorrow (UTC).\n"
            if self.mailer.send(s["email"], subject, body, unsubscribe_url=unsub):
                sent += 1
                with self._lock:
                    self._c.execute("UPDATE subs SET sent_day = ?, sent_n = ? WHERE token = ?", (day, n + 1, s["token"]))
                    self._c.commit()
        return sent
