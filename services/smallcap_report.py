"""Bundled Canonical V19 log report implementation for hosted runtimes."""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

BUY_RE = re.compile(r"^BUY\s+(\d{6})\s+(\d+)\s+([\d.]+)\s*$")
SELL_RE = re.compile(r"^SELL\s+(\d{6})\s+(\d+)\s+(\S+)\s*$")
PLAN_BUY_RE = re.compile(
    r"BUY plan\s+(.+?)\((\d{6})\)\s+qty=(\d+)\s+w=([\d.]+)\s+px=([\d.]+)"
)
DT_RE = re.compile(r"datetime\.datetime\((\d+),\s*(\d+),\s*(\d+)")
ORDER_OK_RE = re.compile(r"OrderInfo\s*:\s*\{[^}]*'OrderNum'\s*:\s*'([^']+)'")


@dataclass
class Fill:
    side: str
    code: str
    qty: int
    date: str
    px: float
    name: str = ""
    weight: float = 0.0
    reason: str = ""
    order_num: str = ""


@dataclass
class Position:
    code: str
    name: str
    qty: float
    entry_px: float
    entry_date: str
    weight: float = 0.0


def parse_log(text: str) -> list[Fill]:
    runs: list[tuple[str, list[str]]] = []
    current_date = None
    buffer: list[str] = []
    for line in text.splitlines():
        match = DT_RE.search(line)
        if match:
            if current_date is not None:
                runs.append((current_date, buffer))
            current_date = f"{match.group(1)}{int(match.group(2)):02d}{int(match.group(3)):02d}"
            buffer = []
        elif current_date is not None:
            buffer.append(line)
    if current_date is not None:
        runs.append((current_date, buffer))

    fills: list[Fill] = []
    names: dict[str, str] = {}
    for run_date, run_lines in runs:
        plans: dict[str, tuple[str, float, float]] = {}
        for line in run_lines:
            plan = PLAN_BUY_RE.search(line)
            if plan:
                name, code = plan.group(1).strip(), plan.group(2)
                plans[code] = (name, float(plan.group(5)), float(plan.group(4)))
                names[code] = name

        pending_buy: tuple[str, int, float] | None = None
        pending_sell: tuple[str, int, str] | None = None
        for line in run_lines:
            buy = BUY_RE.match(line.strip())
            if buy:
                pending_buy = (buy.group(1), int(buy.group(2)), float(buy.group(3)))
                pending_sell = None
                continue
            sell = SELL_RE.match(line.strip())
            if sell:
                pending_sell = (sell.group(1), int(sell.group(2)), sell.group(3))
                pending_buy = None
                continue
            if "Error Code" in line:
                pending_buy = pending_sell = None
                continue
            order = ORDER_OK_RE.search(line)
            if not order:
                continue
            order_number = order.group(1)
            if pending_buy:
                code, qty, weight = pending_buy
                name, price, plan_weight = plans.get(
                    code, (names.get(code, code), 0.0, weight)
                )
                if price > 0:
                    fills.append(
                        Fill(
                            "BUY",
                            code,
                            qty,
                            run_date,
                            price,
                            name=name,
                            weight=plan_weight,
                            order_num=order_number,
                        )
                    )
                    names[code] = name
                pending_buy = None
            elif pending_sell:
                code, qty, reason = pending_sell
                fills.append(
                    Fill(
                        "SELL",
                        code,
                        qty,
                        run_date,
                        0.0,
                        name=names.get(code, code),
                        reason=reason,
                        order_num=order_number,
                    )
                )
                pending_sell = None
    return fills


def get_ohlcv(code: str, start: str, end: str) -> pd.DataFrame:
    start_iso = f"{start[:4]}-{start[4:6]}-{start[6:]}"
    end_iso = f"{end[:4]}-{end[4:6]}-{end[6:]}"
    try:
        import FinanceDataReader as fdr

        data = fdr.DataReader(code, start_iso, end_iso)
        if data is not None and len(data):
            result = data.rename(
                columns={"Open": "open", "High": "high", "Low": "low", "Close": "close"}
            )
            result.index = pd.to_datetime(result.index)
            return result[["open", "close"]].astype(float)
    except Exception:
        pass
    return pd.DataFrame()


def trading_days(start: str, end: str) -> list[str]:
    proxy = get_ohlcv("005930", start, end)
    if not proxy.empty:
        return [index.strftime("%Y%m%d") for index in proxy.index]
    current = datetime.strptime(start, "%Y%m%d")
    finish = datetime.strptime(end, "%Y%m%d")
    days = []
    while current <= finish:
        if current.weekday() < 5:
            days.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return days


def build_report(fills: list[Fill], asof: str | None = None) -> dict:
    if not fills:
        raise ValueError("no successful fills in log")
    asof = asof or datetime.now().strftime("%Y%m%d")
    start = min(fill.date for fill in fills)
    market = {code: get_ohlcv(code, start, asof) for code in {f.code for f in fills}}

    for fill in fills:
        if fill.side != "SELL":
            continue
        data = market.get(fill.code)
        if data is None or data.empty:
            continue
        day = pd.Timestamp(f"{fill.date[:4]}-{fill.date[4:6]}-{fill.date[6:]}")
        if day in data.index:
            fill.px = float(data.loc[day, "open"])
        else:
            following = data[data.index >= day]
            if len(following):
                fill.px = float(following.iloc[0]["open"])

    fills_by_day: dict[str, list[Fill]] = {}
    for fill in fills:
        fills_by_day.setdefault(fill.date, []).append(fill)

    positions: dict[str, Position] = {}
    cash = realized = 0.0
    equity_rows: list[dict] = []
    trade_rows: list[dict] = []
    days = trading_days(start, asof)

    for day in days:
        for fill in fills_by_day.get(day, []):
            if fill.side == "BUY":
                cash -= fill.qty * fill.px
                if fill.code in positions:
                    position = positions[fill.code]
                    new_qty = position.qty + fill.qty
                    position.entry_px = (
                        position.entry_px * position.qty + fill.px * fill.qty
                    ) / new_qty
                    position.qty = new_qty
                    position.weight = fill.weight or position.weight
                else:
                    positions[fill.code] = Position(
                        fill.code,
                        fill.name,
                        float(fill.qty),
                        fill.px,
                        fill.date,
                        fill.weight,
                    )
                pnl: float | str = ""
            else:
                position = positions.get(fill.code)
                entry = position.entry_px if position else fill.px
                if fill.px <= 0 and position:
                    fill.px = entry
                pnl = (fill.px - entry) * fill.qty
                realized += pnl
                cash += fill.qty * fill.px
                if position:
                    position.qty -= fill.qty
                    if position.qty <= 0:
                        positions.pop(fill.code, None)

            trade_rows.append(
                {
                    "date": day,
                    "side": fill.side,
                    "code": fill.code,
                    "name": fill.name,
                    "qty": fill.qty,
                    "px": fill.px,
                    "amount": fill.qty * fill.px,
                    "reason": fill.reason,
                    "order_num": fill.order_num,
                    "pnl": round(pnl, 0) if pnl != "" else "",
                }
            )

        marked_value = cost_open = 0.0
        for code, position in positions.items():
            price = position.entry_px
            data = market.get(code)
            if data is not None and len(data):
                day_timestamp = pd.Timestamp(f"{day[:4]}-{day[4:6]}-{day[6:]}")
                available = data[data.index <= day_timestamp]
                if len(available):
                    price = float(available.iloc[-1]["close"])
            marked_value += position.qty * price
            cost_open += position.qty * position.entry_px
        equity_rows.append(
            {
                "date": day,
                "equity": round(marked_value + cash, 0),
                "mtm_open": round(marked_value, 0),
                "cost_open": round(cost_open, 0),
                "cash": round(cash, 0),
                "realized_cum": round(realized, 0),
                "unrealized": round(marked_value - cost_open, 0),
                "n_pos": len(positions),
            }
        )

    peak_cost = 0.0
    peak_nav = None
    max_drawdown = 0.0
    nav_rows = []
    for row in equity_rows:
        peak_cost = max(peak_cost, row["cost_open"])
        base = max(peak_cost, 1.0)
        nav = 1.0 + row["equity"] / base
        peak_nav = nav if peak_nav is None else max(peak_nav, nav)
        drawdown = nav / peak_nav - 1.0 if peak_nav else 0.0
        max_drawdown = min(max_drawdown, drawdown)
        nav_rows.append(
            {
                **row,
                "nav": round(nav, 6),
                "dd": round(drawdown, 6),
                "peak_cost": round(base, 0),
            }
        )

    position_rows = []
    last_day = days[-1] if days else asof
    for code, position in sorted(
        positions.items(), key=lambda item: -item[1].qty * item[1].entry_px
    ):
        price = position.entry_px
        data = market.get(code)
        if data is not None and len(data):
            price = float(data.iloc[-1]["close"])
            last_day = data.index[-1].strftime("%Y%m%d")
        position_rows.append(
            {
                "code": code,
                "name": position.name,
                "qty": int(position.qty),
                "entry_px": position.entry_px,
                "entry_date": position.entry_date,
                "last_px": price,
                "asof": last_day,
                "cost": round(position.qty * position.entry_px, 0),
                "mkt": round(position.qty * price, 0),
                "pnl": round(position.qty * (price - position.entry_px), 0),
                "ret_pct": round((price / position.entry_px - 1.0) * 100, 2),
                "weight": position.weight,
            }
        )

    open_cost = sum(position["cost"] for position in position_rows)
    open_market = sum(position["mkt"] for position in position_rows)
    closed_pnl = sum(
        float(trade["pnl"])
        for trade in trade_rows
        if trade["side"] == "SELL" and trade["pnl"] != ""
    )
    peak_invested = max(
        (row["peak_cost"] for row in nav_rows), default=open_cost
    ) or 1
    summary = {
        "asof": asof,
        "start": start,
        "n_buys": sum(fill.side == "BUY" for fill in fills),
        "n_sells": sum(fill.side == "SELL" for fill in fills),
        "open_positions": len(position_rows),
        "open_cost": open_cost,
        "open_mkt": open_market,
        "open_unrealized": open_market - open_cost,
        "closed_realized": closed_pnl,
        "total_pnl": open_market - open_cost + closed_pnl,
        "mdd_pct": round(max_drawdown * 100, 2),
        "last_equity": nav_rows[-1]["equity"] if nav_rows else None,
        "last_nav": nav_rows[-1]["nav"] if nav_rows else None,
        "peak_invested": peak_invested,
    }
    summary["total_ret_pct"] = round(summary["total_pnl"] / peak_invested * 100, 2)
    return {
        "summary": summary,
        "equity": nav_rows,
        "positions": position_rows,
        "trades": trade_rows,
        "fills": fills,
    }


def write_outputs(report: dict, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(
        json.dumps(report["summary"], ensure_ascii=False, indent=2), encoding="utf-8"
    )

    def write_csv(name: str, rows: list[dict]) -> None:
        if not rows:
            return
        with (output_dir / name).open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    write_csv("equity_daily.csv", report["equity"])
    write_csv("positions.csv", report["positions"])
    write_csv("trades.csv", report["trades"])
