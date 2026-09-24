# -*- coding: utf-8 -*-
"""OKX 3in1 live log analyze + optional Google Sheet push."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from config import COIN_ROOT, OUTPUTS

_DATA = COIN_ROOT / "data"
if str(_DATA) not in sys.path:
    sys.path.insert(0, str(_DATA))

from push_live_to_3in1_sheet import (  # noqa: E402
    SHEET_KEY,
    apply_cashflow_adjust,
    parse_live_rows,
)


def _extract_trade_notes(lines: list[str], limit: int = 20) -> list[str]:
    """Latest day-open / signal / reconcile notes (not pinned to one date)."""
    notes: list[str] = []
    interesting = (
        "day-open pass:",
        "진입:",
        "청산:",
        "피라미딩",
        "스킵:",
        "entry skip",
        "reconcile:",
        "15m protect:",
        "signal pass",
        "PENDING",
        "MarketClose",
        "51169",
    )
    for line in lines:
        s = line.strip()
        if any(k in s for k in interesting):
            notes.append(s[:200])
    return notes[-limit:]


def analyze_text(text: str) -> dict:
    lines = text.splitlines()
    rows, cash_events = apply_cashflow_adjust(parse_live_rows(lines))
    if not rows:
        raise ValueError("DRY_RUN=False live ticks not found in log")

    hold = [r for r in rows if r["n_pos"] > 0]
    if not hold:
        raise ValueError("no holding ticks (n_pos > 0) in log")

    h0 = hold[0]["adj_equity"]
    peak = h0
    mdd = 0.0
    mdd_at = hold[0]["utc"]
    mdd_eq = h0
    for r in hold:
        e = r["adj_equity"]
        if e > peak:
            peak = e
        dd = e / peak - 1.0
        if dd < mdd:
            mdd = dd
            mdd_at = r["utc"]
            mdd_eq = e

    hold_ret = hold[-1]["adj_equity"] / h0 - 1.0

    series: list[dict] = []
    peak2 = None
    for idx, r in enumerate(rows):
        keep = (
            idx % 4 == 0
            or idx == len(rows) - 1
            or r["utc"].endswith((":00:03", ":00:04", ":00:05"))
        )
        if not keep:
            continue
        e = r["adj_equity"]
        if r["n_pos"] == 0:
            dd_pct = 0.0
            ret_pct = 0.0
        else:
            if peak2 is None:
                peak2 = e
            if e > peak2:
                peak2 = e
            dd_pct = (e / peak2 - 1.0) * 100
            ret_pct = (e / h0 - 1.0) * 100
        series.append(
            {
                "utc": r["utc"],
                "ret_pct": round(ret_pct, 3),
                "dd_pct": round(dd_pct, 3),
                "adj_equity": r["adj_equity"],
                "equity": round(r["equity"], 2),
                "free": round(r["free"], 2),
                "n_pos": r["n_pos"],
                "pos": list(r["pos"]) if r["pos"] else [],
            }
        )
    series = sorted({s["utc"]: s for s in series}.values(), key=lambda x: x["utc"])

    daily = {}
    for r in rows:
        daily[r["utc"][:10]] = {
            "utc_day": r["utc"][:10],
            "equity": round(r["equity"], 2),
            "adj_equity": r["adj_equity"],
            "free": round(r["free"], 2),
            "n_pos": r["n_pos"],
            "pos": list(r["pos"]) if r["pos"] else [],
        }

    summary = {
        "asof": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "range_utc": f"{rows[0]['utc']} ~ {rows[-1]['utc']}",
        "hold_start_equity": round(hold[0]["equity"], 2),
        "latest_adj_equity": hold[-1]["adj_equity"],
        "hold_ret_pct": round(hold_ret * 100, 2),
        "mdd_pct": round(mdd * 100, 2),
        "mdd_at": mdd_at,
        "mdd_eq": round(mdd_eq, 2),
        "n_pos": hold[-1]["n_pos"],
        "positions": list(hold[-1]["pos"]) if hold[-1]["pos"] else [],
        "cash_events": cash_events,
        "n_ticks": len(rows),
        "n_hold_ticks": len(hold),
    }
    return {
        "summary": summary,
        "series": series,
        "daily": [daily[k] for k in sorted(daily)],
        "trade_notes": _extract_trade_notes(lines),
        "sheet_url": f"https://docs.google.com/spreadsheets/d/{SHEET_KEY}/edit#gid=1193052549",
    }


def save_local(result: dict, log_path: Path) -> Path:
    out_dir = OUTPUTS / "coin"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # Keep project canonical live log in sync
    dest = COIN_ROOT / "run.log"
    if log_path.resolve() != dest.resolve():
        dest.write_bytes(log_path.read_bytes())
    return out_dir / "summary.json"


def push_sheet() -> dict:
    """Run the existing publisher against project run.log."""
    import push_live_ui_to_3in1_sheet as ui

    ui.main()
    return {
        "ok": True,
        "sheet_url": f"https://docs.google.com/spreadsheets/d/{SHEET_KEY}/edit#gid=1193052549",
    }


def run(log_path: Path, push: bool = False) -> dict:
    text = log_path.read_text(encoding="utf-8", errors="replace")
    result = analyze_text(text)
    saved = save_local(result, log_path)
    result["saved_to"] = str(saved)
    result["project_log"] = str(COIN_ROOT / "run.log")
    if push:
        result["sheet"] = push_sheet()
    else:
        result["sheet"] = {"ok": False, "skipped": True}
    return result
