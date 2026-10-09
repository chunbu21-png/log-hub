# -*- coding: utf-8 -*-
"""OKX Strategy D live log analyzer + optional StrategyD Sheet push."""
from __future__ import annotations

import ast
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config import IS_VERCEL, OUTPUTS, STRATEGYD_ROOT

SHEET_KEY = "1hdwoh-Tc5LDVOsOcaJnNx00vKkqNOiU3E_ikW45yKiQ"
SHEET_GID = "78563573"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_KEY}/edit#gid={SHEET_GID}"
SHEET_TAB = "StrategyD"
KST = timezone(timedelta(hours=9))
UTC_RE = re.compile(r"UTC\s+(\d+)\s+(\d+)")
FREE_RE = re.compile(r"free[~≈?]?\s*([\d.]+)")
EQUITY_RE = re.compile(r"equity[~≈]?\s*([\d.]+)")
POS_DONE_RE = re.compile(r"positions=\s*(\[.*?\])\s*pending_entries=")


def _parse_positions(done_line: str) -> list[str]:
    match = POS_DONE_RE.search(done_line)
    if not match:
        return []
    try:
        raw = ast.literal_eval(match.group(1))
        return [str(item) for item in raw]
    except Exception:
        return []


def _assign_utc_datetimes(runs: list[dict], mtime: datetime) -> None:
    if not runs:
        return
    mtime_kst = mtime.replace(tzinfo=KST)
    anchor_utc = mtime_kst.astimezone(timezone.utc)
    last = runs[-1]
    current = datetime(
        anchor_utc.year,
        anchor_utc.month,
        anchor_utc.day,
        last["utc_h"],
        last["utc_m"],
        tzinfo=timezone.utc,
    )
    if current > anchor_utc + timedelta(minutes=5):
        current -= timedelta(days=1)
    runs[-1]["dt"] = current
    for index in range(len(runs) - 2, -1, -1):
        hour, minute = runs[index]["utc_h"], runs[index]["utc_m"]
        previous = runs[index + 1]["dt"]
        candidate = previous.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate >= previous:
            candidate -= timedelta(days=1)
        runs[index]["dt"] = candidate
    for index in range(1, len(runs)):
        hour, minute = runs[index]["utc_h"], runs[index]["utc_m"]
        previous = runs[index - 1]["dt"]
        candidate = previous.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= previous:
            candidate += timedelta(days=1)
        runs[index]["dt"] = candidate
    delta = anchor_utc.replace(second=0, microsecond=0) - runs[-1]["dt"]
    day_shift = timedelta(days=round(delta.total_seconds() / 86400))
    if abs(day_shift.days) >= 1:
        for run in runs:
            run["dt"] += day_shift
    elif abs(delta.total_seconds()) > 3600:
        for run in runs:
            run["dt"] += delta


def parse_runs(lines: list[str], mtime: datetime) -> list[dict]:
    runs: list[dict] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if "=== OKX_Strategy_D_Bot" in line and "DRY_RUN=False" in line:
            run: dict[str, Any] = {"events": []}
            cursor = index
            while cursor < min(index + 180, len(lines)):
                current = lines[cursor]
                if current.startswith("UTC "):
                    stamp = UTC_RE.match(current)
                    if stamp:
                        run["utc_h"], run["utc_m"] = int(stamp.group(1)), int(stamp.group(2))
                elif current.startswith("USDT free"):
                    free = FREE_RE.search(current)
                    equity = EQUITY_RE.search(current)
                    if free:
                        run["free"] = float(free.group(1))
                    if equity:
                        run["equity_raw"] = float(equity.group(1))
                elif current.startswith("positions:") and "pos" not in run:
                    left, right = current.find("["), current.find("]")
                    if left >= 0 and right > left:
                        inner = current[left + 1 : right].strip()
                        if inner:
                            run["pos"] = [
                                item.strip().strip("'\"")
                                for item in inner.split(",")
                                if item.strip()
                            ]
                            run["n_pos"] = len(run["pos"])
                elif "Strategy D done" in current:
                    run["pos"] = _parse_positions(current)
                    run["n_pos"] = len(run["pos"])
                    break
                elif any(
                    token in current
                    for token in (
                        "진입 신호",
                        "롱 진입",
                        "피라미딩",
                        "청산",
                        "entry fail",
                        "pending entry",
                    )
                ):
                    run["events"].append(current.strip())
                cursor += 1
            if "utc_h" in run and "free" in run:
                run.setdefault("pos", [])
                run.setdefault("n_pos", len(run.get("pos") or []))
                runs.append(run)
            index = cursor + 1
        else:
            index += 1
    _assign_utc_datetimes(runs, mtime)
    for run in runs:
        run["utc"] = run["dt"].strftime("%Y-%m-%d %H:%M:%S+00:00")
    return runs


def attach_equity(runs: list[dict]) -> list[dict]:
    rows = []
    for run in runs:
        row = dict(run)
        if run.get("equity_raw") is not None:
            row["equity"] = round(float(run["equity_raw"]), 2)
            row["has_equity"] = True
        else:
            row["equity"] = round(float(run["free"]), 2)
            row["has_equity"] = False
        rows.append(row)
    return rows


def apply_cashflow_adjust(
    rows: list[dict],
    jump_threshold: float = 50.0,
    deploy_drop_threshold: float = 2.0,
) -> tuple[list[dict], list[dict]]:
    out: list[dict] = []
    events: list[dict] = []
    cum_in = cum_out = cum_deploy = 0.0
    previous = None
    for row in rows:
        current = dict(row)
        current["deposit"] = 0.0
        current["withdrawal"] = 0.0
        if previous is not None:
            free_delta = float(row["free"]) - float(previous["free"])
            equity_delta = float(row["equity"]) - float(previous["equity"])
            event_text = " ".join(row.get("events") or [])
            previous_events = " ".join(previous.get("events") or [])
            if free_delta >= jump_threshold and row["n_pos"] >= previous["n_pos"]:
                cum_in += free_delta
                current["deposit"] = round(free_delta, 2)
                events.append(
                    {"utc": row["utc"], "amount": round(free_delta, 2), "kind": "deposit_like"}
                )
            elif free_delta <= -jump_threshold and row["n_pos"] <= previous["n_pos"]:
                is_trade = (
                    row["n_pos"] > previous["n_pos"]
                    or "피라미딩" in event_text
                    or "롱 진입" in event_text
                    or "피라미딩" in previous_events
                    or "롱 진입" in previous_events
                )
                if not is_trade:
                    amount = -free_delta
                    cum_out += amount
                    current["withdrawal"] = round(amount, 2)
                    events.append(
                        {"utc": row["utc"], "amount": round(amount, 2), "kind": "withdraw_like"}
                    )
            has_equity = bool(row.get("has_equity") or previous.get("has_equity"))
            if has_equity:
                is_deploy = row["n_pos"] > previous["n_pos"] and equity_delta <= -deploy_drop_threshold
                is_deploy_follow = False
            else:
                is_deploy = (
                    row["n_pos"] > previous["n_pos"]
                    or "피라미딩" in event_text
                    or "롱 진입" in event_text
                )
                is_deploy_follow = (
                    "피라미딩" in previous_events or "롱 진입" in previous_events
                ) and row["n_pos"] >= previous["n_pos"]
            if (is_deploy or is_deploy_follow) and equity_delta <= -deploy_drop_threshold:
                cum_deploy += -equity_delta
                events.append(
                    {
                        "utc": row["utc"],
                        "amount": round(-equity_delta, 2),
                        "kind": "deploy_cliff",
                        "n_pos": f"{previous['n_pos']}->{row['n_pos']}",
                    }
                )
            if previous["n_pos"] > 0 and row["n_pos"] == 0 and cum_deploy:
                events.append(
                    {
                        "utc": row["utc"],
                        "amount": round(-cum_deploy, 2),
                        "kind": "deploy_release",
                        "n_pos": f"{previous['n_pos']}->0",
                    }
                )
                cum_deploy = 0.0
        current["cum_inflow"] = round(cum_in, 2)
        current["cum_outflow"] = round(cum_out, 2)
        current["cum_deploy"] = round(cum_deploy, 2)
        current["adj_equity"] = round(float(row["equity"]) - cum_in + cum_out + cum_deploy, 2)
        current["equity_est"] = round(float(row["equity"]) + cum_deploy, 2)
        out.append(current)
        previous = row
    if out:
        base = float(out[0]["equity"])
        for row in out:
            capital = base + float(row["cum_inflow"]) - float(row["cum_outflow"])
            row["capital_in"] = round(capital, 2)
            row["ret_on_capital"] = (
                (float(row["equity_est"]) / capital - 1.0) * 100.0 if capital > 1e-9 else 0.0
            )
    return out, events


def _xirr(cashflows: list[tuple[datetime, float]], guess: float = 0.1) -> float | None:
    if len(cashflows) < 2:
        return None
    start = cashflows[0][0]
    times = [(stamp - start).total_seconds() / (365.25 * 86400.0) for stamp, _ in cashflows]
    amounts = [amount for _, amount in cashflows]
    if not (any(amount < 0 for amount in amounts) and any(amount > 0 for amount in amounts)):
        return None

    def npv(rate: float) -> float:
        return sum(amount / ((1.0 + rate) ** time) for amount, time in zip(amounts, times))

    rate = guess
    for _ in range(80):
        value = npv(rate)
        derivative = sum(
            -time * amount / ((1.0 + rate) ** (time + 1))
            for amount, time in zip(amounts, times)
            if abs(1.0 + rate) > 1e-12
        )
        if abs(derivative) < 1e-14:
            break
        nxt = rate - value / derivative
        if abs(nxt - rate) < 1e-10:
            return float(nxt)
        rate = nxt
    return None


def compute_performance(rows: list[dict], cash_events: list[dict]) -> tuple[dict, list[dict], list[str]]:
    twr = 1.0
    previous_equity = float(rows[0]["equity_est"])
    previous_in = float(rows[0]["cum_inflow"])
    previous_out = float(rows[0]["cum_outflow"])
    peak = 1.0
    mdd = 0.0
    mdd_at = rows[0]["utc"]
    mdd_eq = previous_equity
    for row in rows:
        equity = float(row["equity_est"])
        deposit = float(row["cum_inflow"]) - previous_in
        withdrawal = float(row["cum_outflow"]) - previous_out
        if previous_equity > 1e-9:
            twr *= (equity - deposit + withdrawal) / previous_equity
        if twr > peak:
            peak = twr
        drawdown = twr / peak - 1.0
        row["twr_pct"] = (twr - 1.0) * 100.0
        row["dd_pct"] = drawdown * 100.0
        row["pnl_usdt"] = round(equity - float(row["capital_in"]), 2)
        if drawdown < mdd:
            mdd = drawdown
            mdd_at = row["utc"]
            mdd_eq = equity
        previous_equity = equity
        previous_in = float(row["cum_inflow"])
        previous_out = float(row["cum_outflow"])

    series: list[dict] = []
    for index, row in enumerate(rows):
        keep = (
            index % 15 == 0
            or index == len(rows) - 1
            or row["utc"].endswith(":00:00+00:00")
            or bool(row.get("events"))
            or float(row.get("deposit") or 0) > 0
            or float(row.get("withdrawal") or 0) > 0
        )
        if not keep:
            continue
        series.append(
            {
                "utc": row["utc"],
                "ret_pct": round(float(row["twr_pct"]), 3),
                "dd_pct": round(float(row["dd_pct"]), 3),
                "pnl_usdt": round(float(row["pnl_usdt"]), 2),
                "equity_est": row["equity_est"],
                "n_pos": row["n_pos"],
                "pos": list(row.get("pos") or []),
            }
        )
    series = sorted({item["utc"]: item for item in series}.values(), key=lambda item: item["utc"])

    notes: list[str] = []
    for row in rows:
        for event in row.get("events") or []:
            if any(token in event for token in ("진입 신호", "롱 진입", "피라미딩", "청산", "entry fail")):
                notes.append(f"{row['utc'][:16]} {event[:140]}")
    notes = notes[-16:]

    last = rows[-1]
    cashflows = [(rows[0]["dt"], -float(rows[0]["equity_est"]))]
    for event in cash_events:
        stamp = datetime.fromisoformat(event["utc"])
        if event["kind"] == "deposit_like":
            cashflows.append((stamp, -float(event["amount"])))
        elif event["kind"] == "withdraw_like":
            cashflows.append((stamp, float(event["amount"])))
    cashflows.append((rows[-1]["dt"], float(rows[-1]["equity_est"])))
    xirr_val = _xirr(cashflows)
    summary = {
        "range_utc": f"{rows[0]['utc']} ~ {rows[-1]['utc']}",
        "n_ticks": len(rows),
        "capital_in": round(float(last["capital_in"]), 2),
        "equity_est": round(float(last["equity_est"]), 2),
        "pnl_usdt": round(float(last["pnl_usdt"]), 2),
        "twr_pct": round(float(last["twr_pct"]), 2),
        "roi_pct": round(float(last["ret_on_capital"]), 2),
        "mdd_pct": round(mdd * 100, 2),
        "mdd_at": mdd_at,
        "mdd_eq": round(mdd_eq, 2),
        "xirr_pct": round(xirr_val * 100, 2) if xirr_val is not None else None,
        "n_pos": last["n_pos"],
        "positions": list(last.get("pos") or []),
        "free": round(float(last["free"]), 2),
        "n_buys": sum(
            1
            for row in rows
            for event in row.get("events") or []
            if "롱 진입" in event or "피라미딩" in event
        ),
        "n_sells": sum(
            1 for row in rows for event in row.get("events") or [] if "청산" in event
        ),
        "cash_events": cash_events[-20:],
    }
    return summary, series, notes


def _sync_project_log(log_path: Path) -> Path:
    dest = STRATEGYD_ROOT / "okx_bot.log"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if log_path.resolve() != dest.resolve():
        dest.write_bytes(log_path.read_bytes())
    return dest


def push_sheet(log_path: Path | None = None) -> dict:
    if IS_VERCEL:
        raise RuntimeError(
            "StrategyD Google Sheet push is not available on Vercel yet; "
            "service-account credentials are local-only."
        )
    from services.strategyd_sheet import push as write_tab

    dest = log_path or (STRATEGYD_ROOT / "okx_bot.log")
    if log_path:
        dest = _sync_project_log(log_path)
    return write_tab(dest)


def run(log_path: Path, push: bool = False) -> dict:
    text = log_path.read_text(encoding="utf-8", errors="replace")
    mtime = datetime.fromtimestamp(log_path.stat().st_mtime)
    raw = parse_runs(text.splitlines(), mtime)
    if not raw:
        raise ValueError("no Strategy D live runs in log")
    rows, cash_events = apply_cashflow_adjust(attach_equity(raw))
    summary, series, notes = compute_performance(rows, cash_events)
    dest = _sync_project_log(log_path)
    hub_out = OUTPUTS / "strategyd"
    hub_out.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": summary,
        "series": series,
        "trade_notes": notes,
        "n_buys": summary["n_buys"],
        "n_sells": summary["n_sells"],
        "saved_to": str(hub_out / "summary.json"),
        "project_log": str(dest),
        "sheet_url": SHEET_URL,
        "sheet_tab": SHEET_TAB,
    }
    (hub_out / "summary.json").write_text(
        json.dumps({"summary": summary, "n_ticks": summary["n_ticks"]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if push:
        try:
            from services.strategyd_sheet import push_parsed

            payload["sheet"] = push_parsed(rows, cash_events, summary, notes)
        except Exception as exc:  # noqa: BLE001
            payload["sheet"] = {"ok": False, "error": str(exc)}
    else:
        payload["sheet"] = {"ok": False, "skipped": True}
    return payload
