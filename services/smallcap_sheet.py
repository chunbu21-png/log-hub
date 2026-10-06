# -*- coding: utf-8 -*-
"""Push Canonical V19 live report artifacts to Google Sheet「리포트」."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import find_sa

SHEET_KEY = "1O4q7Vt2-W62Kp8xJ4SvRFucFhgDvg44muJ-3mgrOLIE"
SHEET_TAB = "리포트"
SHEET_GID = 369475009
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_KEY}/edit#gid={SHEET_GID}"
KST = timezone(timedelta(hours=9))


def sheet_safe(v):
    if isinstance(v, str) and v[:1] in "=+-@":
        return "'" + v
    return v


def safe_row(row: list) -> list:
    return [sheet_safe(x) for x in row]


def read_csv(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def fmt_date(yyyymmdd: str) -> str:
    s = str(yyyymmdd or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def fmt_axis_day(yyyymmdd: str) -> str:
    s = str(yyyymmdd or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"'{s[4:6]}-{s[6:8]}"
    return s


def build_rows(report_dir: Path) -> tuple[list[list], int, int]:
    s = json.loads((report_dir / "summary.json").read_text(encoding="utf-8"))
    equity = read_csv(report_dir / "equity_daily.csv")
    positions = read_csv(report_dir / "positions.csv")
    trades = read_csv(report_dir / "trades.csv")
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")

    rows: list[list] = [
        ["Canonical V19 실매매 리포트 (로그 기반)"],
        ["생성시각(KST)", now],
        ["기준일", fmt_date(s.get("asof", ""))],
        ["시작일", fmt_date(s.get("start", ""))],
        ["출처", "Canonical_V19.log OrderInfo 체결"],
        [],
        ["[요약]"],
        ["항목", "값"],
        ["매수체결", s.get("n_buys")],
        ["매도체결", s.get("n_sells")],
        ["보유종목수", s.get("open_positions")],
        ["보유원가", s.get("open_cost")],
        ["보유평가", s.get("open_mkt")],
        ["미실현손익", s.get("open_unrealized")],
        ["실현손익", s.get("closed_realized")],
        ["총손익", s.get("total_pnl")],
        ["수익률(%)", s.get("total_ret_pct")],
        ["MDD(%)", s.get("mdd_pct")],
        ["피크투입금", s.get("peak_invested")],
        ["최종 NAV", s.get("last_nav")],
        [],
        ["[일별 equity]"],
        [
            "date",
            "Return %",
            "Drawdown %",
            "equity",
            "mtm_open",
            "cost_open",
            "realized_cum",
            "n_pos",
            "nav",
        ],
    ]
    eq_header_row = len(rows)

    peak = float(s.get("peak_invested") or 1) or 1.0
    for r in equity:
        eq = float(r.get("equity") or 0)
        dd = float(r.get("dd") or 0) * 100.0
        ret = eq / peak * 100.0
        rows.append(
            [
                fmt_axis_day(r.get("date", "")),
                round(ret, 4),
                round(dd, 4),
                eq,
                float(r.get("mtm_open") or 0),
                float(r.get("cost_open") or 0),
                float(r.get("realized_cum") or 0),
                int(float(r.get("n_pos") or 0)),
                float(r.get("nav") or 0),
            ]
        )
    n_equity = len(equity)

    rows += [[], ["[보유 포지션]"]]
    if positions:
        rows.append(
            [
                "code",
                "name",
                "qty",
                "entry_px",
                "entry_date",
                "last_px",
                "cost",
                "mkt",
                "pnl",
                "ret_pct",
                "weight",
            ]
        )
        for p in positions:
            rows.append(
                [
                    p.get("code", ""),
                    p.get("name", ""),
                    int(float(p.get("qty") or 0)),
                    float(p.get("entry_px") or 0),
                    fmt_date(p.get("entry_date", "")),
                    float(p.get("last_px") or 0),
                    float(p.get("cost") or 0),
                    float(p.get("mkt") or 0),
                    float(p.get("pnl") or 0),
                    float(p.get("ret_pct") or 0),
                    float(p.get("weight") or 0),
                ]
            )
    else:
        rows.append(["(없음)"])

    rows += [[], ["[체결]"]]
    if trades:
        rows.append(
            ["date", "side", "code", "name", "qty", "px", "amount", "reason", "order_num", "pnl"]
        )
        for t in trades:
            pnl = t.get("pnl", "")
            rows.append(
                [
                    fmt_date(t.get("date", "")),
                    t.get("side", ""),
                    t.get("code", ""),
                    t.get("name", ""),
                    int(float(t.get("qty") or 0)) if t.get("qty") not in ("", None) else "",
                    float(t.get("px") or 0) if t.get("px") not in ("", None) else "",
                    float(t.get("amount") or 0) if t.get("amount") not in ("", None) else "",
                    t.get("reason", ""),
                    t.get("order_num", ""),
                    float(pnl) if pnl not in ("", None) else "",
                ]
            )
    else:
        rows.append(["(없음)"])

    return rows, eq_header_row, n_equity


def push(report_dir: Path, sheet_key: str = SHEET_KEY, tab: str = SHEET_TAB) -> dict:
    sa = find_sa()
    if sa is None:
        raise RuntimeError("Google service-account JSON not found")
    rows, eq_header_row, n_equity = build_rows(report_dir)

    import gspread
    from google.oauth2.service_account import Credentials

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(str(sa), scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheet_key)
    try:
        ws = sh.worksheet(tab)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=tab, rows=400, cols=16)
    sid = ws.id

    ws.clear()
    sh.batch_update(
        {
            "requests": [
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sid,
                            "startRowIndex": 0,
                            "endRowIndex": max(len(rows) + 5, 200),
                            "startColumnIndex": 0,
                            "endColumnIndex": 12,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "numberFormat": {"type": "NUMBER", "pattern": "0.##"}
                            }
                        },
                        "fields": "userEnteredFormat.numberFormat",
                    }
                }
            ]
        }
    )

    meta = sh.fetch_sheet_metadata()
    charts = []
    for s in meta.get("sheets", []):
        if s["properties"]["sheetId"] == sid:
            charts = s.get("charts", [])
    if charts:
        sh.batch_update(
            {
                "requests": [
                    {"deleteEmbeddedObject": {"objectId": c["chartId"]}} for c in charts
                ]
            }
        )

    ws.update(
        range_name="A1",
        values=[safe_row(r) for r in rows],
        value_input_option="USER_ENTERED",
    )

    requests = [
        {
            "repeatCell": {
                "range": {
                    "sheetId": sid,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 2,
                },
                "cell": {
                    "userEnteredFormat": {"textFormat": {"bold": True, "fontSize": 14}}
                },
                "fields": "userEnteredFormat.textFormat",
            }
        },
    ]

    if n_equity > 0:
        r0 = eq_header_row - 1
        r1 = eq_header_row + n_equity

        def domain():
            return {
                "sheetId": sid,
                "startRowIndex": r0 + 1,
                "endRowIndex": r1,
                "startColumnIndex": 0,
                "endColumnIndex": 1,
            }

        def col(a: int, b: int):
            return {
                "sheetId": sid,
                "startRowIndex": r0 + 1,
                "endRowIndex": r1,
                "startColumnIndex": a,
                "endColumnIndex": b,
            }

        requests += [
            {
                "addChart": {
                    "chart": {
                        "spec": {
                            "title": "수익률 % (피크투입 대비)",
                            "basicChart": {
                                "chartType": "LINE",
                                "legendPosition": "BOTTOM_LEGEND",
                                "headerCount": 0,
                                "axis": [
                                    {"position": "BOTTOM_AXIS", "title": "거래일"},
                                    {"position": "LEFT_AXIS", "title": "Return %"},
                                ],
                                "domains": [{"domain": {"sourceRange": {"sources": [domain()]}}}],
                                "series": [
                                    {
                                        "series": {"sourceRange": {"sources": [col(1, 2)]}},
                                        "targetAxis": "LEFT_AXIS",
                                    }
                                ],
                            },
                        },
                        "position": {
                            "overlayPosition": {
                                "anchorCell": {
                                    "sheetId": sid,
                                    "rowIndex": 0,
                                    "columnIndex": 12,
                                },
                                "offsetXPixels": 10,
                                "offsetYPixels": 10,
                                "widthPixels": 720,
                                "heightPixels": 360,
                            }
                        },
                    }
                }
            },
            {
                "addChart": {
                    "chart": {
                        "spec": {
                            "title": "MDD / Drawdown %",
                            "basicChart": {
                                "chartType": "LINE",
                                "legendPosition": "BOTTOM_LEGEND",
                                "headerCount": 0,
                                "axis": [
                                    {"position": "BOTTOM_AXIS", "title": "거래일"},
                                    {"position": "LEFT_AXIS", "title": "Drawdown %"},
                                ],
                                "domains": [{"domain": {"sourceRange": {"sources": [domain()]}}}],
                                "series": [
                                    {
                                        "series": {"sourceRange": {"sources": [col(2, 3)]}},
                                        "targetAxis": "LEFT_AXIS",
                                    }
                                ],
                            },
                        },
                        "position": {
                            "overlayPosition": {
                                "anchorCell": {
                                    "sheetId": sid,
                                    "rowIndex": 20,
                                    "columnIndex": 12,
                                },
                                "offsetXPixels": 10,
                                "offsetYPixels": 10,
                                "widthPixels": 720,
                                "heightPixels": 280,
                            }
                        },
                    }
                }
            },
        ]

    sh.batch_update({"requests": requests})
    return {
        "ok": True,
        "sheet_url": SHEET_URL,
        "sheet_tab": tab,
        "n_equity": n_equity,
        "n_rows": len(rows),
    }
