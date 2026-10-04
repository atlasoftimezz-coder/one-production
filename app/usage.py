"""Usage ledger (plan R2) in data_dir/usage.sqlite.

    calls       one row per reply: account, project, model, tokens, cost, outcome, and the
                provider's own utilisation at that moment (Claude reports it exactly)
    switches    every hand-over from one account to another, with the handoff file
    limit_hits  every usage-limit error, with what our estimate said at that moment - the
                data that tells us whether an estimate can be trusted
"""
from __future__ import annotations

import csv
import io
import sqlite3
import statistics
import threading
import time
from pathlib import Path

RANGES = {"5h": 5 * 3600, "today": None, "7d": 7 * 86400, "month": None}

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, account TEXT NOT NULL, provider TEXT NOT NULL,
  project TEXT, model TEXT, in_tok INTEGER DEFAULT 0, out_tok INTEGER DEFAULT 0,
  cache_read INTEGER DEFAULT 0, cache_write INTEGER DEFAULT 0, cost_usd REAL,
  outcome TEXT NOT NULL, err_kind TEXT, util_5h REAL, util_7d REAL);
CREATE INDEX IF NOT EXISTS calls_acc_ts ON calls(account, ts);
CREATE TABLE IF NOT EXISTS switches (
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, project TEXT, from_acc TEXT, to_acc TEXT,
  trigger TEXT, usage_at_switch REAL, handoff TEXT);
CREATE TABLE IF NOT EXISTS limit_hits (
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, account TEXT NOT NULL, message TEXT, est_pct REAL);
"""


def range_start(name: str, now: float | None = None) -> float:
    now = now or time.time()
    lt = time.localtime(now)
    if name == "today":
        return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    if name == "month":
        return time.mktime((lt.tm_year, lt.tm_mon, 1, 0, 0, 0, 0, 0, -1))
    if name in RANGES:
        return now - RANGES[name]
    raise ValueError("unknown range")


class Ledger:
    def __init__(self, data_dir: Path):
        self.path = data_dir / "usage.sqlite"
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    # ---------------------------------------------------------------- writes
    def record_call(self, *, account: str, provider: str, project: str | None, usage=None,
                    outcome: str = "ok", err_kind: str | None = None, windows: dict | None = None,
                    ts: float | None = None) -> None:
        w = windows or {}
        u5 = (w.get("five_hour") or {}).get("utilization")
        u7 = (w.get("seven_day") or {}).get("utilization")
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO calls (ts, account, provider, project, model, in_tok, out_tok, cache_read,"
                      " cache_write, cost_usd, outcome, err_kind, util_5h, util_7d) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (ts or time.time(), account, provider, project,
                       getattr(usage, "model", None), getattr(usage, "input_tokens", 0) or 0,
                       getattr(usage, "output_tokens", 0) or 0, getattr(usage, "cache_read_tokens", 0) or 0,
                       getattr(usage, "cache_write_tokens", 0) or 0, getattr(usage, "cost_usd", None),
                       outcome, err_kind, u5, u7))

    def record_switch(self, *, project: str, from_acc: str, to_acc: str | None, trigger: str,
                      usage_at_switch: float | None, handoff: str | None) -> None:
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO switches (ts, project, from_acc, to_acc, trigger, usage_at_switch, handoff)"
                      " VALUES (?,?,?,?,?,?,?)", (time.time(), project, from_acc, to_acc, trigger,
                                                   usage_at_switch, handoff))

    def record_limit_hit(self, account: str, message: str, est_pct: float | None) -> None:
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO limit_hits (ts, account, message, est_pct) VALUES (?,?,?,?)",
                      (time.time(), account, message[:500], est_pct))

    # ---------------------------------------------------------------- reads
    def tokens_since(self, account: str, since: float) -> int:
        with self._conn() as c:
            r = c.execute("SELECT COALESCE(SUM(in_tok + out_tok), 0) FROM calls WHERE account=? AND ts>=?",
                          (account, since)).fetchone()
        return int(r[0])

    def summary(self, range_name: str) -> dict:
        since = range_start(range_name)
        with self._conn() as c:
            per_acc = {r["account"]: dict(r) for r in c.execute(
                "SELECT account, COUNT(*) AS requests, SUM(in_tok) AS in_tok, SUM(out_tok) AS out_tok,"
                " SUM(cache_read) AS cache_read, SUM(cost_usd) AS cost_usd, MAX(ts) AS last_used,"
                " SUM(outcome != 'ok') AS errors FROM calls WHERE ts>=? GROUP BY account", (since,))}
            by_project = [dict(r) for r in c.execute(
                "SELECT project, COUNT(*) AS requests, SUM(in_tok + out_tok) AS tokens, SUM(cost_usd) AS cost_usd"
                " FROM calls WHERE ts>=? GROUP BY project ORDER BY tokens DESC", (since,))]
            by_model = [dict(r) for r in c.execute(
                "SELECT COALESCE(model, provider) AS model, COUNT(*) AS requests, SUM(in_tok + out_tok) AS tokens,"
                " SUM(cost_usd) AS cost_usd FROM calls WHERE ts>=? GROUP BY 1 ORDER BY tokens DESC", (since,))]
            totals = dict(c.execute(
                "SELECT COUNT(*) AS requests, COALESCE(SUM(in_tok),0) AS in_tok, COALESCE(SUM(out_tok),0) AS out_tok,"
                " SUM(cost_usd) AS cost_usd FROM calls WHERE ts>=?", (since,)).fetchone())
        return {"range": range_name, "since": since, "accounts": per_acc, "by_project": by_project,
                "by_model": by_model, "totals": totals}

    def switches(self, limit: int = 50) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM switches ORDER BY ts DESC LIMIT ?", (limit,))]

    def limit_hits(self, account: str | None = None, since: float = 0) -> list[dict]:
        q, args = "SELECT * FROM limit_hits WHERE ts>=?", [since]
        if account:
            q += " AND account=?"
            args.append(account)
        with self._conn() as c:
            return [dict(r) for r in c.execute(q + " ORDER BY ts DESC", args)]

    def estimate_accuracy(self, account: str) -> dict | None:
        """Plan R2 target: over 7 days, the estimate at each real limit hit should read
        90-110 (median), and never below 70. Otherwise the estimate is flagged unreliable."""
        hits = [h["est_pct"] for h in self.limit_hits(account, time.time() - 7 * 86400) if h["est_pct"] is not None]
        if not hits:
            return None
        med = statistics.median(hits)
        ok = 90 <= med <= 110 and min(hits) >= 70
        return {"hits": len(hits), "median_est_at_hit": round(med, 1), "min_est_at_hit": round(min(hits), 1),
                "reliable": ok}

    def export_csv(self, range_name: str, redact, accounts: list[dict] | None = None) -> str:
        """Two tables, Excel-friendly: every account's usage as shown on the usage page (so the
        export is useful even before any message was sent), then one row per message."""
        since = range_start(range_name)
        stamp = lambda ts: time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else ""
        clean = lambda row: [redact(str(v)) if isinstance(v, str) else ("" if v is None else v) for v in row]
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["One Production usage", f"range: {range_name}", f"from {stamp(since)}",
                    f"exported {stamp(time.time())}"])
        w.writerow([])
        w.writerow(["ACCOUNTS"])
        acols = ["label", "provider", "email", "plan", "status", "used_now_pct", "measured", "session_5h_pct",
                 "session_resets", "week_pct", "week_resets", "requests", "in_tok", "out_tok", "cost_usd", "last_used"]
        w.writerow(acols)
        for a in accounts or []:
            w.writerow(clean([a.get(k) if k not in ("session_resets", "week_resets", "last_used")
                              else stamp(a.get(k)) for k in acols]))
        w.writerow([])
        w.writerow(["MESSAGES"])
        cols = ["ts", "account", "provider", "project", "model", "in_tok", "out_tok", "cache_read",
                "cache_write", "cost_usd", "outcome", "err_kind", "util_5h", "util_7d"]
        w.writerow(["time"] + cols[1:])
        n = 0
        with self._conn() as c:
            for r in c.execute(f"SELECT {', '.join(cols)} FROM calls WHERE ts>=? ORDER BY ts", (since,)):
                w.writerow(clean([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["ts"]))]
                                 + [r[k] for k in cols[1:]]))
                n += 1
        if not n:
            w.writerow([f"No messages were sent through One Production in this range ({range_name})."])
        return buf.getvalue()
