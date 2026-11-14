"""Trade journal in SQLite: every order event and every closed round trip."""
from __future__ import annotations

import csv
import json
import sqlite3

from . import config as C

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT, mode TEXT, symbol TEXT, strategy TEXT,
  ts_open REAL, ts_close REAL, qty REAL, entry REAL, exit REAL, fees REAL,
  pnl REAL, pnl_pct REAL, r_multiple REAL, initial_stop REAL,
  entry_reason TEXT, exit_reason TEXT, notes TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, mode TEXT, kind TEXT, symbol TEXT, payload TEXT);
"""


def _db():
    con = sqlite3.connect(C.home() / "journal.db")
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def event(mode: str, kind: str, symbol: str = "", **payload) -> None:
    with _db() as con:
        con.execute("INSERT INTO events (ts, mode, kind, symbol, payload) VALUES (?,?,?,?,?)",
                    (C.now(), mode, kind, symbol, json.dumps(payload, default=str)))


def record_trade(mode, symbol, strategy, ts_open, qty, entry, exit_, fees, initial_stop,
                 entry_reason="", exit_reason="") -> dict:
    pnl = (exit_ - entry) * qty - fees
    risk = (entry - initial_stop) * qty if initial_stop and initial_stop < entry else None
    row = {"mode": mode, "symbol": symbol, "strategy": strategy, "ts_open": ts_open, "ts_close": C.now(),
           "qty": qty, "entry": entry, "exit": exit_, "fees": fees, "pnl": pnl,
           "pnl_pct": pnl / (entry * qty) * 100 if entry * qty else 0.0,
           "r_multiple": pnl / risk if risk else None, "initial_stop": initial_stop,
           "entry_reason": entry_reason, "exit_reason": exit_reason}
    with _db() as con:
        cur = con.execute(f"INSERT INTO trades ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
                          tuple(row.values()))
        row["id"] = cur.lastrowid
    return row


def trades(mode: str | None = None, symbol: str | None = None, limit: int = 0) -> list[dict]:
    q, a = "SELECT * FROM trades WHERE 1=1", []
    if mode:
        q, a = q + " AND mode=?", a + [mode]
    if symbol:
        q, a = q + " AND symbol=?", a + [symbol]
    q += " ORDER BY ts_close DESC" + (f" LIMIT {int(limit)}" if limit else "")
    with _db() as con:
        return [dict(r) for r in con.execute(q, a)]


def events(limit: int = 50, kind: str | None = None) -> list[dict]:
    q, a = "SELECT * FROM events", []
    if kind:
        q, a = q + " WHERE kind=?", [kind]
    with _db() as con:
        rows = [dict(r) for r in con.execute(q + f" ORDER BY id DESC LIMIT {int(limit)}", a)]
    for r in rows:
        r["payload"] = json.loads(r["payload"])
    return rows


def add_note(trade_id: int, note: str) -> bool:
    with _db() as con:
        cur = con.execute("UPDATE trades SET notes = trim(notes || ' ' || ?) WHERE id=?", (note, trade_id))
        return cur.rowcount > 0


def _summ(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"trades": 0}
    pnl = [r["pnl"] for r in rows]
    wins, losses = [p for p in pnl if p > 0], [p for p in pnl if p <= 0]
    rs = [r["r_multiple"] for r in rows if r["r_multiple"] is not None]
    gl = -sum(losses)
    return {"trades": n, "win_rate_pct": round(len(wins) / n * 100, 1), "net_pnl": round(sum(pnl), 2),
            "profit_factor": round(sum(wins) / gl, 2) if gl > 0 else None,
            "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
            "expectancy": round(sum(pnl) / n, 2),
            "avg_r": round(sum(rs) / len(rs), 2) if rs else None,
            "fees": round(sum(r["fees"] for r in rows), 2)}


def stats(mode: str | None = None) -> dict:
    rows = list(reversed(trades(mode)))  # chronological
    out = {"mode": mode or "all", "overall": _summ(rows)}
    if not rows:
        return out
    streak = worst = cur = 0
    for r in rows:
        cur = cur + 1 if r["pnl"] <= 0 else 0
        worst = max(worst, cur)
    streak = cur
    out["current_losing_streak"], out["longest_losing_streak"] = streak, worst
    for key in ("symbol", "strategy", "exit_reason"):
        groups: dict[str, list] = {}
        for r in rows:
            groups.setdefault(r[key] or "unknown", []).append(r)
        out[f"by_{key}"] = {k: _summ(v) for k, v in groups.items()}
    hold = [(r["ts_close"] - r["ts_open"]) / 3600 for r in rows if r["ts_open"]]
    out["avg_hold_hours"] = round(sum(hold) / len(hold), 1) if hold else None
    flags = []
    o = out["overall"]
    if o["trades"] < 30:
        flags.append(f"Only {o['trades']} trades: too few to judge the edge (aim for 30+).")
    if o["profit_factor"] is not None and o["profit_factor"] < 1:
        flags.append("Profit factor below 1: the system is losing money net of fees.")
    if o["avg_r"] is not None and o["avg_r"] < 0:
        flags.append("Average R is negative: losses are larger than planned or winners are cut early.")
    if worst >= 5:
        flags.append(f"Losing streak of {worst}: check whether the market regime changed.")
    if o["net_pnl"] and o["fees"] > abs(o["net_pnl"]):
        flags.append("Fees exceed net P&L: trade less often or use limit orders.")
    out["flags"] = flags
    return out


def export_csv(path: str, mode: str | None = None) -> int:
    rows = trades(mode)
    if not rows:
        return 0
    with open(path, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)
    return len(rows)
