# -*- coding: utf-8 -*-
"""Bollinger Leverage live log analyzer + optional Report-tab Sheet push."""
from __future__ import annotations

import json
import math
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from config import BOLLINGER_ROOT, IS_VERCEL, OUTPUTS

SHEET_KEY = "1yu_2Vjt2pKOYa70z5yt9phErRIelc7qx7Mp-na1DW5Q"
SHEET_GID = "197876950"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_KEY}/edit#gid={SHEET_GID}"
SHEET_TAB = "Report"

KST_RE = re.compile(r"KST now: (\d{4}-\d{2}-\d{2})")
TOTAL_RE = re.compile(r"'TotalMoney': ([\d.]+)")
REMAIN_RE = re.compile(r"'RemainMoney': ([\d.]+)")
HOLD_RE = re.compile(r"Bollinger 보유: (\d+)/5")
BUY_RE = re.compile(r"매수체결\s+(\d{6})\s+x(\d+)\s+@\s+([\d,]+)")
SELL_RE = re.compile(
    r"매도주문\s+(\d{6})\s+(\S+)\s+현재가\s+([\d,]+)\s+\(진입\s+([\d,]+)"
)
PENDING_RE = re.compile(r"예약매수 유지 (\d+)건:\s*([0-9, ]+)")


def parse_equity(text: str) -> list[dict[str, Any]]:
    days: list[str] = []
    day_lines: dict[str, list[str]] = defaultdict(list)
    current = None
    for line in text.splitlines():
        match = KST_RE.search(line)
        if match:
            current = match.group(1)
            if current not in days:
                days.append(current)
        if current:
            day_lines[current].append(line)

    rows: list[dict[str, Any]] = []
    for day in days:
        blob = "\n".join(day_lines[day])
        totals = [float(x) for x in TOTAL_RE.findall(blob)]
        remains = [float(x) for x in REMAIN_RE.findall(blob)]
        holds = HOLD_RE.findall(blob)
        if not totals:
            continue
        rows.append(
            {
                "date": day,
                "equity": totals[-1],
                "cash": remains[-1] if remains else None,
                "positions": int(holds[-1]) if holds else 0,
            }
        )
    if not rows:
        raise ValueError("로그에서 TotalMoney를 찾지 못함")
    return rows


def parse_trades(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current = None
    for line in text.splitlines():
        match = KST_RE.search(line)
        if match:
            current = match.group(1)
        if not current:
            continue
        buy = BUY_RE.search(line)
        if buy:
            events.append(
                {
                    "date": current,
                    "side": "BUY",
                    "code": buy.group(1),
                    "shares": int(buy.group(2)),
                    "price": float(buy.group(3).replace(",", "")),
                    "reason": "buy",
                }
            )
            continue
        sell = SELL_RE.search(line)
        if sell:
            events.append(
                {
                    "date": current,
                    "side": "SELL",
                    "code": sell.group(1),
                    "shares": None,
                    "price": float(sell.group(3).replace(",", "")),
                    "entry_price": float(sell.group(4).replace(",", "")),
                    "reason": sell.group(2),
                }
            )
    return events


def parse_pending(text: str) -> list[str]:
    last = None
    for line in text.splitlines():
        match = PENDING_RE.search(line)
        if match:
            last = [code.strip() for code in match.group(2).split(",") if code.strip()]
    return last or []


def _trim_after_capital_reset(
    rows: list[dict[str, Any]], drop_threshold: float = -0.25
) -> tuple[list[dict[str, Any]], str | None]:
    reset_idx = None
    for i in range(1, len(rows)):
        prev = rows[i - 1]["equity"]
        if prev <= 0:
            continue
        change = rows[i]["equity"] / prev - 1.0
        if change <= drop_threshold:
            reset_idx = i
    if reset_idx is None:
        return rows, None
    prev_val = rows[reset_idx - 1]["equity"] if reset_idx > 0 else float("nan")
    new_val = rows[reset_idx]["equity"]
    note = (
        f"자본변동 감지 {rows[reset_idx]['date']} "
        f"(전일 {prev_val:,.0f} → {new_val:,.0f}) — 이후 구간만 성과 산출"
    )
    return rows[reset_idx:], note


def compute_summary(
    rows: list[dict[str, Any]], trades: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    start_val = float(rows[0]["equity"])
    end_val = float(rows[-1]["equity"])
    start_dt = datetime.strptime(rows[0]["date"], "%Y-%m-%d")
    end_dt = datetime.strptime(rows[-1]["date"], "%Y-%m-%d")
    days = max((end_dt - start_dt).days, 1)
    years = days / 365.25
    total_return = end_val / start_val - 1.0 if start_val else 0.0
    if years > 0 and start_val > 0 and end_val > 0:
        cagr = (end_val / start_val) ** (1.0 / years) - 1.0
    else:
        cagr = 0.0

    peak = start_val
    mdd = 0.0
    mdd_date = rows[0]["date"]
    series: list[dict[str, Any]] = []
    for row in rows:
        equity = float(row["equity"])
        if equity > peak:
            peak = equity
        dd = equity / peak - 1.0 if peak else 0.0
        if dd < mdd:
            mdd = dd
            mdd_date = row["date"]
        series.append(
            {
                "utc": row["date"],
                "ret_pct": round((equity / start_val - 1.0) * 100, 3) if start_val else 0.0,
                "dd_pct": round(dd * 100, 3),
                "equity": round(equity, 0),
                "cash": row.get("cash"),
                "n_pos": row.get("positions", 0),
            }
        )

    rets: list[float] = []
    for i in range(1, len(rows)):
        prev = rows[i - 1]["equity"]
        if prev > 0:
            rets.append(rows[i]["equity"] / prev - 1.0)
    if len(rets) >= 2:
        mean = sum(rets) / len(rets)
        var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
        std = math.sqrt(var)
        sharpe = (mean / std * math.sqrt(252)) if std > 0 else 0.0
    else:
        sharpe = 0.0

    buys = [t for t in trades if t["side"] == "BUY"]
    sells = [t for t in trades if t["side"] == "SELL"]
    summary = {
        "source": "live_log",
        "start_date": rows[0]["date"],
        "end_date": rows[-1]["date"],
        "calendar_days": int(days),
        "trading_days": len(rows),
        "initial_equity": round(start_val, 0),
        "final_equity": round(end_val, 0),
        "latest_cash": rows[-1].get("cash"),
        "latest_positions": rows[-1].get("positions", 0),
        "total_return_pct": round(total_return * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "mdd_pct": round(mdd * 100, 2),
        "mdd_date": mdd_date,
        "sharpe": round(sharpe, 2),
        "trade_count": len(buys),
        "buy_count": len(buys),
        "sell_signal_count": len(sells),
    }
    return summary, series


def _sync_project_log(log_path: Path) -> Path:
    dest = BOLLINGER_ROOT / "Bollinger_Lev.log"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if log_path.resolve() != dest.resolve():
        dest.write_bytes(log_path.read_bytes())
    return dest


def push_sheet(log_path: Path | None = None) -> dict:
    if IS_VERCEL:
        raise RuntimeError(
            "Bollinger Google Sheet push is not available on Vercel yet; "
            "service-account credentials are local-only."
        )
    script = BOLLINGER_ROOT / "google_sheets" / "run_sheet_live_report.py"
    if not script.is_file():
        raise RuntimeError(f"Sheet publisher missing: {script}")
    log = log_path or (BOLLINGER_ROOT / "Bollinger_Lev.log")
    proc = subprocess.run(
        [sys.executable, str(script), "--force", "--log", str(log)],
        cwd=str(BOLLINGER_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-2000:]
        raise RuntimeError(detail or f"Sheet push failed (exit {proc.returncode})")
    return {
        "ok": True,
        "sheet_url": SHEET_URL,
        "sheet_tab": SHEET_TAB,
        "publisher_log": (proc.stdout or "")[-1500:],
    }


def run(log_path: Path, push: bool = False) -> dict:
    text = log_path.read_text(encoding="utf-8", errors="replace")
    equity_full = parse_equity(text)
    equity, capital_note = _trim_after_capital_reset(equity_full)
    trades = [t for t in parse_trades(text) if t["date"] >= equity[0]["date"]]
    summary, series = compute_summary(equity, trades)
    summary["capital_note"] = capital_note
    summary["log_full_start"] = equity_full[0]["date"]
    summary["log_full_end"] = equity_full[-1]["date"]
    pending = parse_pending(text)

    dest = _sync_project_log(log_path)
    hub_out = OUTPUTS / "bollinger"
    hub_out.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": summary,
        "series": series,
        "trades": trades[-40:],
        "pending_buys": pending,
        "n_buys": summary["buy_count"],
        "n_sells": summary["sell_signal_count"],
        "saved_to": str(hub_out / "summary.json"),
        "project_log": str(dest),
        "sheet_url": SHEET_URL,
        "sheet_tab": SHEET_TAB,
    }
    (hub_out / "summary.json").write_text(
        json.dumps(
            {
                "summary": summary,
                "n_buys": summary["buy_count"],
                "n_sells": summary["sell_signal_count"],
                "pending_buys": pending,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    if push:
        payload["sheet"] = push_sheet(dest)
    else:
        payload["sheet"] = {"ok": False, "skipped": True}
    return payload
