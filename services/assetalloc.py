# -*- coding: utf-8 -*-
"""KIS AssetAllocation (3-sleeve) live log analyzer + 매일현황 Sheet push."""
from __future__ import annotations

import ast
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from config import ASSETALLOC_ROOT, IS_VERCEL, OUTPUTS, find_sa

SHEET_KEY = "13VoO1XvjpZ9bP6vQRl7o1FjBY19CYUIfnFueirL4u8k"
SHEET_GID = 2003988218
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_KEY}/edit#gid={SHEET_GID}"
SHEET_TAB = "매일현황"

SESSION_RE = re.compile(r"^===== (\S+) AssetAll_Bot =====")
BALANCE_RE = re.compile(
    r"--------------내 보유 잔고---------------------\s*(\{.*?\})",
    re.DOTALL,
)
ALLOC_RE = re.compile(r"포트폴리오 할당금액:\s*([\d,]+)")
COMBINED_RE = re.compile(r"combined\s+(\{.*?\})\s+cash\s+([0-9.]+)")
SLEEVE_RE = re.compile(r"sleeves:\s*jab/mo/d2\s*=\s*([0-9.]+)/([0-9.]+)/([0-9.]+)")
TICKER_RE = re.compile(r">>\s*(.+?)\((\d{6})\)\s*<<")
WEIGHT_RE = re.compile(r"비중:\s*([\d.]+)/([\d.]+)%")
REBAL_RE = re.compile(r"리밸수량:\s*(-?\d+)")
EVAL_RE = re.compile(r"평가:\s*([\d,.-]+)")
ORDER_RE = re.compile(r"'OrderNum2':\s*'([^']+)'")
SKIP_RE = re.compile(r"\[skip\].*")
OK_DAY_RE = re.compile(r"\[ok\] last weekday of month:\s*(\S+)")


def _parse_balance(chunk: str) -> dict:
    match = BALANCE_RE.search(chunk)
    if not match:
        return {}
    try:
        raw = ast.literal_eval(match.group(1))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        "total_money": float(raw.get("TotalMoney") or 0),
        "remain_money": float(raw.get("RemainMoney") or 0),
        "stock_money": float(raw.get("StockMoney") or 0),
        "stock_revenue": float(raw.get("StockRevenue") or 0),
    }


def _extract_holdings(chunk: str) -> list[dict]:
    marker = "--------------내 보유 주식---------------------"
    start = chunk.find(marker)
    if start < 0:
        return []
    bracket = chunk.find("[", start)
    if bracket < 0:
        return []
    depth = 0
    for index, char in enumerate(chunk[bracket:], bracket):
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                try:
                    raw = ast.literal_eval(chunk[bracket : index + 1])
                except Exception:
                    return []
                holdings = []
                for item in raw if isinstance(raw, list) else []:
                    if not isinstance(item, dict):
                        continue
                    holdings.append(
                        {
                            "code": str(item.get("StockCode") or ""),
                            "name": str(item.get("StockName") or ""),
                            "qty": int(float(item.get("StockAmt") or 0)),
                            "avg_px": float(item.get("StockAvgPrice") or 0),
                            "last_px": float(item.get("StockNowPrice") or 0),
                            "mkt": float(item.get("StockNowMoney") or 0),
                            "pnl": float(item.get("StockRevenueMoney") or 0),
                            "ret_pct": float(item.get("StockRevenueRate") or 0),
                        }
                    )
                return holdings
    return []


def _parse_rebalance(chunk: str) -> list[dict]:
    rows: list[dict] = []
    current: dict | None = None
    for line in chunk.splitlines():
        ticker = TICKER_RE.search(line)
        if ticker:
            if current:
                rows.append(current)
            current = {"name": ticker.group(1).strip(), "code": ticker.group(2), "qty": 0}
            continue
        if current is None:
            continue
        weight = WEIGHT_RE.search(line)
        if weight:
            current["now_pct"] = float(weight.group(1))
            current["target_pct"] = float(weight.group(2))
        eval_m = EVAL_RE.search(line)
        if eval_m:
            current["eval"] = float(eval_m.group(1).replace(",", ""))
        qty = REBAL_RE.search(line)
        if qty:
            current["qty"] = int(qty.group(1))
    if current:
        rows.append(current)
    return [row for row in rows if row.get("qty")]


def parse_sessions(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    starts = [(i, SESSION_RE.match(line).group(1)) for i, line in enumerate(lines) if SESSION_RE.match(line)]
    if not starts:
        return []
    sessions = []
    for index, (start, stamp) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(lines)
        chunk = "\n".join(lines[start:end])
        balance = _parse_balance(chunk)
        alloc = ALLOC_RE.search(chunk)
        combined = {}
        cash_w = None
        comb = COMBINED_RE.search(chunk)
        if comb:
            try:
                combined = ast.literal_eval(comb.group(1))
            except Exception:
                combined = {}
            cash_w = float(comb.group(2))
        sleeves = SLEEVE_RE.search(chunk)
        orders = ORDER_RE.findall(chunk)
        sim_only = "ENABLE_ORDER_EXECUTION=False" in chunk
        live = "리밸런싱 시작" in chunk and not sim_only
        sessions.append(
            {
                "stamp": stamp,
                "total_money": balance.get("total_money"),
                "remain_money": balance.get("remain_money"),
                "stock_money": balance.get("stock_money"),
                "stock_revenue": balance.get("stock_revenue"),
                "allocation": int(alloc.group(1).replace(",", "")) if alloc else None,
                "combined": combined,
                "cash_weight": cash_w,
                "sleeves": {
                    "jab": float(sleeves.group(1)),
                    "mo": float(sleeves.group(2)),
                    "d2": float(sleeves.group(3)),
                }
                if sleeves
                else {"jab": 0.2, "mo": 0.4, "d2": 0.4},
                "holdings": _extract_holdings(chunk),
                "rebalance": _parse_rebalance(chunk),
                "orders": orders,
                "live_orders": live,
                "sim_only": sim_only,
                "rebalance_done": "리밸런싱 끝" in chunk,
                "errors": [ln.strip()[:160] for ln in chunk.splitlines() if "Traceback" in ln or ln.startswith("실패")],
            }
        )
    return sessions


def _series(sessions: list[dict]) -> list[dict]:
    points = [s for s in sessions if s.get("total_money")]
    if not points:
        return []
    start = float(points[0]["total_money"])
    peak = start
    out = []
    for session in points:
        equity = float(session["total_money"])
        if equity > peak:
            peak = equity
        out.append(
            {
                "utc": session["stamp"],
                "ret_pct": round((equity / start - 1.0) * 100, 3) if start else 0.0,
                "dd_pct": round((equity / peak - 1.0) * 100, 3) if peak else 0.0,
                "equity": round(equity, 0),
            }
        )
    return out


def analyze_text(text: str) -> dict:
    sessions = parse_sessions(text)
    if not sessions:
        skips = SKIP_RE.findall(text)
        oks = OK_DAY_RE.findall(text)
        if skips or oks:
            return {
                "summary": {
                    "mode": "calendar",
                    "last_ok_day": oks[-1] if oks else None,
                    "skip_count": len(skips),
                    "one_liner": "월말 리밸런싱 대상일이 아니어서 봇 세션이 없습니다."
                    if skips and not sessions
                    else "달력 로그만 있습니다.",
                },
                "sessions": [],
                "series": [],
                "holdings": [],
                "rebalance": [],
            }
        raise ValueError("AssetAll 봇 세션을 찾지 못했습니다. KIS_AssetAll_Bot.log 를 올려 주세요.")

    last = sessions[-1]
    series = _series(sessions)
    mdd = min((p["dd_pct"] for p in series), default=0.0)
    ret = series[-1]["ret_pct"] if series else 0.0
    total = last.get("total_money")
    total_txt = f"{total:,.0f}" if total is not None else "n/a"
    extra = " (시뮬레이션)" if last["sim_only"] else (" · 리밸런싱 끝" if last["rebalance_done"] else "")
    one_liner = (
        f"{last['stamp']}: 총자산 {total_txt}, "
        f"리밸 {len(last['rebalance'])}종, 주문 {len(last['orders'])}건{extra}."
    )
    summary = {
        "mode": "session",
        "stamp": last["stamp"],
        "total_money": last["total_money"],
        "remain_money": last["remain_money"],
        "stock_money": last["stock_money"],
        "stock_revenue": last["stock_revenue"],
        "allocation": last["allocation"],
        "n_holdings": len(last["holdings"]),
        "n_rebalance": len(last["rebalance"]),
        "n_orders": len(last["orders"]),
        "sim_only": last["sim_only"],
        "live_orders": last["live_orders"],
        "rebalance_done": last["rebalance_done"],
        "sleeves": last["sleeves"],
        "combined": last["combined"],
        "cash_weight": last.get("cash_weight"),
        "total_return_pct": ret,
        "mdd_pct": mdd,
        "n_sessions": len(sessions),
        "one_liner": one_liner,
        "healthy": not last["errors"],
    }
    return {
        "summary": summary,
        "sessions": [
            {
                "stamp": s["stamp"],
                "total_money": s["total_money"],
                "orders": len(s["orders"]),
                "sim_only": s["sim_only"],
            }
            for s in sessions
        ],
        "series": series,
        "holdings": last["holdings"],
        "rebalance": last["rebalance"],
        "orders": last["orders"],
        "trade_notes": [
            f"{row['name']}({row['code']}) {row['qty']:+d}  {row.get('now_pct', 0)}→{row.get('target_pct', 0)}%"
            for row in last["rebalance"]
        ],
    }


def _sync_project_log(log_path: Path) -> Path:
    name = log_path.name
    dest_name = "KIS_AssetAll.log" if name.lower() == "kis_assetall.log" else "KIS_AssetAll_Bot.log"
    dest = ASSETALLOC_ROOT / dest_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if log_path.resolve() != dest.resolve():
        dest.write_bytes(log_path.read_bytes())
    return dest


def _load_last_analysis() -> dict:
    last = OUTPUTS / "assetalloc" / "last_analysis.json"
    if last.is_file():
        return json.loads(last.read_text(encoding="utf-8"))
    log = ASSETALLOC_ROOT / "KIS_AssetAll_Bot.log"
    if log.is_file():
        return analyze_text(log.read_text(encoding="utf-8", errors="replace"))
    raise RuntimeError("No AssetAllocation analysis to push. Analyze a log first.")


def push_sheet(analysis: dict | None = None) -> dict:
    if IS_VERCEL:
        raise RuntimeError(
            "AssetAllocation Google Sheet push is not available on Vercel yet; "
            "service-account credentials are local-only."
        )
    cred = find_sa()
    if cred is None:
        raise RuntimeError("Google service-account JSON not found")
    import gspread
    from google.oauth2.service_account import Credentials

    payload = analysis or _load_last_analysis()
    summary = payload.get("summary") or {}
    holdings = payload.get("holdings") or []
    creds = Credentials.from_service_account_file(
        str(cred),
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    client = gspread.authorize(creds)
    book = client.open_by_key(SHEET_KEY)
    try:
        worksheet = book.worksheet(SHEET_TAB)
    except Exception:
        worksheet = book.get_worksheet_by_id(SHEET_GID)
    worksheet.clear()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = int(float(summary.get("total_money") or 0))
    cash = int(float(summary.get("remain_money") or 0))
    stock = int(float(summary.get("stock_money") or 0))
    pnl = int(float(summary.get("stock_revenue") or 0))
    worksheet.update(range_name="A1", values=[[f"업데이트 시간: {now} (로그 {summary.get('stamp') or ''})"]])
    worksheet.update(
        range_name="A2",
        values=[
            ["계좌 총자산", total, "D+2 예수금", cash],
            ["주식 평가합계", stock, "평가손익", pnl],
            ["할당금액", int(float(summary.get("allocation") or 0)), "보유종목", len(holdings)],
        ],
    )
    if holdings:
        header = ["구분", "종목명", "종목코드", "보유수량", "평균단가", "현재가", "평가금액", "평가손익", "수익률"]
        rows = [
            [
                "주식/ETF",
                h["name"],
                h["code"],
                h["qty"],
                int(h["avg_px"]),
                h["last_px"],
                int(h["mkt"]),
                int(h["pnl"]),
                h["ret_pct"],
            ]
            for h in holdings
        ]
        worksheet.update(range_name="A6", values=[header] + rows)
    else:
        worksheet.update(range_name="A6", values=[["보유 중인 자산/종목이 없습니다."]])
    return {"ok": True, "sheet_url": SHEET_URL, "sheet_tab": SHEET_TAB}


def run(log_path: Path, push: bool = False) -> dict:
    text = log_path.read_text(encoding="utf-8", errors="replace")
    result = analyze_text(text)
    dest = _sync_project_log(log_path)
    hub_out = OUTPUTS / "assetalloc"
    hub_out.mkdir(parents=True, exist_ok=True)
    result["saved_to"] = str(hub_out / "summary.json")
    result["project_log"] = str(dest)
    result["sheet_url"] = SHEET_URL
    result["sheet_tab"] = SHEET_TAB
    (hub_out / "summary.json").write_text(
        json.dumps(result["summary"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (hub_out / "last_analysis.json").write_text(
        json.dumps(
            {
                "summary": result["summary"],
                "holdings": result.get("holdings") or [],
                "rebalance": result.get("rebalance") or [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if push:
        result["sheet"] = push_sheet(result)
    else:
        result["sheet"] = {"ok": False, "skipped": True}
    return result
