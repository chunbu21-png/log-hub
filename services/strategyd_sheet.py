# -*- coding: utf-8 -*-
"""Push Strategy D live metrics to Google Sheet「StrategyD」."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from string import ascii_uppercase

from config import find_sa
from services.strategyd import (
    SHEET_KEY,
    SHEET_TAB,
    SHEET_URL,
    STRATEGYD_ROOT,
    apply_cashflow_adjust,
    attach_equity,
    compute_performance,
    parse_runs,
)

TS_START_ROW = 40


def sheet_safe(v):
    if isinstance(v, str) and v[:1] in "=+-@":
        return "'" + v
    return v


def safe_row(row: list) -> list:
    return [sheet_safe(x) for x in row]


def col_letter(idx0: int) -> str:
    n = idx0 + 1
    out = ""
    while n:
        n, rem = divmod(n - 1, 26)
        out = ascii_uppercase[rem] + out
    return out


def _fmt_events(cash_events: list[dict], kind: str) -> str:
    ev = [e for e in cash_events if e["kind"] == kind]
    if not ev:
        return "없음"
    parts = []
    for e in ev:
        extra = f" ({e['n_pos']})" if e.get("n_pos") else ""
        parts.append(f"{e['utc']} {e['amount']}{extra}")
    return "; ".join(parts)


def build_daily_ledger(rows: list[dict]) -> list[dict]:
    by_day: dict[str, dict] = {}
    dep_day: dict[str, float] = {}
    wdr_day: dict[str, float] = {}
    for row in rows:
        day = row["utc"][:10]
        by_day[day] = row
        dep_day[day] = dep_day.get(day, 0.0) + float(row.get("deposit") or 0.0)
        wdr_day[day] = wdr_day.get(day, 0.0) + float(row.get("withdrawal") or 0.0)
    ledger: list[dict] = []
    prev_eq = None
    cum_twr = 1.0
    peak = 1.0
    for day in sorted(by_day):
        row = by_day[day]
        equity = float(row["equity_est"])
        dep = float(dep_day.get(day, 0.0))
        wdr = float(wdr_day.get(day, 0.0))
        if prev_eq is None or prev_eq <= 1e-9:
            day_twr = 0.0
        else:
            day_twr = (equity - dep + wdr) / prev_eq - 1.0
        cum_twr *= 1.0 + day_twr
        if cum_twr > peak:
            peak = cum_twr
        dd = cum_twr / peak - 1.0
        ledger.append(
            {
                "date": day,
                "equity": round(equity, 2),
                "deposit": round(dep, 2),
                "withdrawal": round(wdr, 2),
                "day_twr_pct": round(day_twr * 100.0, 4),
                "cum_twr_pct": round((cum_twr - 1.0) * 100.0, 4),
                "dd_pct": round(dd * 100.0, 4),
                "pnl_usdt": row.get("pnl_usdt"),
                "capital_in": row.get("capital_in"),
                "free": round(float(row["free"]), 2),
                "n_pos": row["n_pos"],
                "pos": ", ".join(row["pos"]) if row.get("pos") else "",
            }
        )
        prev_eq = equity
    return ledger


def _chart_series(rows: list[dict]) -> list[dict]:
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
                "pnl_usdt": round(float(row["pnl_usdt"]), 2),
                "twr_pct": round(float(row["twr_pct"]), 3),
                "dd_pct": round(float(row["dd_pct"]), 3),
                "equity_est": row["equity_est"],
                "capital_in": row["capital_in"],
                "free": round(float(row["free"]), 2),
                "n_pos": row["n_pos"],
                "pos": ", ".join(row["pos"]) if row.get("pos") else "",
            }
        )
    return sorted({item["utc"]: item for item in series}.values(), key=lambda item: item["utc"])


def push_parsed(
    rows: list[dict],
    cash_events: list[dict],
    summary: dict,
    notes: list[str],
) -> dict:
    sa = find_sa()
    if sa is None:
        raise RuntimeError("Google service-account JSON not found")
    if not rows:
        raise RuntimeError("no Strategy D rows to push")

    daily_ledger = build_daily_ledger(rows)
    series = _chart_series(rows)
    last = rows[-1]
    xirr_txt = (
        f"{summary['xirr_pct']:.2f}%" if summary.get("xirr_pct") is not None else "N/A (기간 짧거나 CF 부족)"
    )
    daily_twr_pct = float(daily_ledger[-1]["cum_twr_pct"]) if daily_ledger else float(summary.get("twr_pct") or 0)

    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_file(
        str(sa),
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_KEY)
    try:
        ws = sh.worksheet(SHEET_TAB)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=SHEET_TAB, rows=3000, cols=20)
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
                            "endRowIndex": 200,
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
    for sheet in meta.get("sheets", []):
        if sheet["properties"]["sheetId"] == sid:
            charts = sheet.get("charts", [])
    if charts:
        sh.batch_update(
            {
                "requests": [
                    {"deleteEmbeddedObject": {"objectId": c["chartId"]}} for c in charts
                ]
            }
        )

    header: list[list] = [
        ["Strategy D — TWR + XIRR (입출금 반영)"],
        ["생성시각(KST)", datetime.now().strftime("%Y-%m-%d %H:%M")],
        ["데이터 구간(UTC)", summary.get("range_utc") or f"{rows[0]['utc']} ~ {rows[-1]['utc']}"],
        ["프로필", "OKX Strategy D (R² long-only, Top10)"],
        [
            "비고",
            "TWR=전략성과(입출금 시점 끊어서 연결). XIRR=실제 내 돈 연환산 수익률. "
            "일별: 날짜/평가액/입금/출금/일간TWR/누적TWR. 차트=누적TWR%·DD%·PnL.",
        ],
        [],
        ["[요약]"],
        ["항목", "값"],
        ["누적 투자원금", round(float(last["capital_in"]), 2)],
        ["추정 자산", round(float(last["equity_est"]), 2)],
        ["누적 매매손익(USDT)", round(float(last["pnl_usdt"]), 2)],
        ["TWR 누적 (분단위)", f"{float(summary.get('twr_pct') or last['twr_pct']):.2f}%"],
        ["TWR 누적 (일별연결)", f"{daily_twr_pct:.2f}%"],
        ["XIRR (연환산)", xirr_txt],
        [
            "XIRR 해석",
            "연율화 수치. 관측기간이 짧으면(수주) 값이 크게 부풀 수 있음. 전략비교는 TWR, 자금타이밍은 XIRR.",
        ],
        ["원금대비 ROI (참고)", f"{float(last['ret_on_capital']):.2f}%"],
        ["MDD (TWR 고점대비)", f"{float(summary.get('mdd_pct') or 0):.2f}%"],
        ["MDD 시점", summary.get("mdd_at") or ""],
        ["MDD 시점 추정자산", round(float(summary.get("mdd_eq") or 0), 2)],
        ["입금", _fmt_events(cash_events, "deposit_like")],
        ["출금", _fmt_events(cash_events, "withdraw_like")],
        ["진입절벽 보정", _fmt_events(cash_events, "deploy_cliff")],
        ["절벽 해제(청산)", _fmt_events(cash_events, "deploy_release")],
        ["포지션 수", str(last["n_pos"])],
        ["포지션", ", ".join(last["pos"]) if last.get("pos") else ""],
        ["최근 USDT free", round(float(last["free"]), 2)],
        [],
        ["[최근 매매 메모]"],
    ]
    if notes:
        for note in notes:
            header.append(["이벤트", note])
    else:
        header.append(["이벤트", "없음"])
    header += [
        [],
        ["[일별 원장 — 날짜 / 평가액 / 입금 / 출금 / 일간 TWR / 누적 TWR / DD]"],
        [
            "날짜",
            "평가액",
            "입금",
            "출금",
            "일간 TWR %",
            "누적 TWR %",
            "DD %",
            "PnL USDT",
            "원금",
            "free",
            "n_pos",
            "positions",
        ],
    ]
    for drow in daily_ledger:
        header.append(
            [
                drow["date"],
                drow["equity"],
                drow["deposit"],
                drow["withdrawal"],
                drow["day_twr_pct"],
                drow["cum_twr_pct"],
                drow["dd_pct"],
                drow["pnl_usdt"],
                drow["capital_in"],
                drow["free"],
                drow["n_pos"],
                drow["pos"],
            ]
        )
    header += [
        [],
        ["[XIRR 현금흐름 — 입금=음수, 출금=양수, 종료평가=양수]"],
        ["날짜", "현금흐름", "설명"],
    ]
    xirr_header_row = len(header)
    xirr_sheet_rows = [
        [rows[0]["dt"].strftime("%Y-%m-%d"), -float(rows[0]["equity_est"]), "시작 원금(투입)"]
    ]
    for event in cash_events:
        if event["kind"] == "deposit_like":
            xirr_sheet_rows.append([event["utc"][:10], -float(event["amount"]), "입금"])
        elif event["kind"] == "withdraw_like":
            xirr_sheet_rows.append([event["utc"][:10], float(event["amount"]), "출금"])
    xirr_sheet_rows.append(
        [rows[-1]["dt"].strftime("%Y-%m-%d"), float(rows[-1]["equity_est"]), "종료 평가액"]
    )
    header.extend(xirr_sheet_rows)
    xirr_data_first = xirr_header_row + 1
    xirr_data_last = xirr_header_row + len(xirr_sheet_rows)
    xirr_formula = (
        f"=IFERROR(XIRR(B{xirr_data_first}:B{xirr_data_last},"
        f'A{xirr_data_first}:A{xirr_data_last}),"N/A")'
    )
    header.append(["XIRR (시트수식)", "", f"Python XIRR={xirr_txt}"])
    xirr_formula_row = len(header)

    ws.update(range_name="A1", values=[safe_row(r) for r in header], value_input_option="USER_ENTERED")
    ws.update(range_name=f"B{xirr_formula_row}", values=[[xirr_formula]], value_input_option="USER_ENTERED")

    ts_start = max(TS_START_ROW, len(header) + 3)
    ts_header = [
        [
            "UTC",
            "PnL USDT",
            "TWR %",
            "Drawdown %",
            "equity_est",
            "capital_in",
            "free",
            "n_pos",
            "positions",
        ]
    ]
    ts_data = [
        [
            s["utc"],
            s["pnl_usdt"],
            s["twr_pct"],
            s["dd_pct"],
            s["equity_est"],
            s["capital_in"],
            s["free"],
            s["n_pos"],
            s["pos"],
        ]
        for s in series
    ]
    ws.update(
        range_name=f"A{ts_start}",
        values=[safe_row(r) for r in (ts_header + ts_data)],
        value_input_option="USER_ENTERED",
    )

    daily_chart_col = 11
    daily_chart_start = ts_start
    daily_block = [["날짜", "누적 TWR %", "일간 TWR %", "DD %", "PnL USDT", "평가액"]] + [
        [d["date"], d["cum_twr_pct"], d["day_twr_pct"], d["dd_pct"], d["pnl_usdt"], d["equity"]]
        for d in daily_ledger
    ]
    ws.update(
        range_name=f"{col_letter(daily_chart_col)}{daily_chart_start}",
        values=[safe_row(r) for r in daily_block],
        value_input_option="USER_ENTERED",
    )

    n = len(series)
    r0 = ts_start - 1
    r1 = ts_start + n
    nd = len(daily_ledger)
    d0 = daily_chart_start - 1
    d1 = daily_chart_start + nd

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

    def d_domain():
        return {
            "sheetId": sid,
            "startRowIndex": d0 + 1,
            "endRowIndex": d1,
            "startColumnIndex": daily_chart_col,
            "endColumnIndex": daily_chart_col + 1,
        }

    def d_col(off: int, width: int = 1):
        a = daily_chart_col + off
        return {
            "sheetId": sid,
            "startRowIndex": d0 + 1,
            "endRowIndex": d1,
            "startColumnIndex": a,
            "endColumnIndex": a + width,
        }

    sh.batch_update(
        {
            "requests": [
                {
                    "addChart": {
                        "chart": {
                            "spec": {
                                "title": "누적 TWR % + Drawdown % (입출금 제거)",
                                "basicChart": {
                                    "chartType": "LINE",
                                    "legendPosition": "BOTTOM_LEGEND",
                                    "headerCount": 0,
                                    "axis": [
                                        {"position": "BOTTOM_AXIS", "title": "UTC"},
                                        {"position": "LEFT_AXIS", "title": "%"},
                                    ],
                                    "domains": [{"domain": {"sourceRange": {"sources": [domain()]}}}],
                                    "series": [
                                        {
                                            "series": {"sourceRange": {"sources": [col(2, 3)]}},
                                            "targetAxis": "LEFT_AXIS",
                                        },
                                        {
                                            "series": {"sourceRange": {"sources": [col(3, 4)]}},
                                            "targetAxis": "LEFT_AXIS",
                                        },
                                    ],
                                },
                            },
                            "position": {
                                "overlayPosition": {
                                    "anchorCell": {
                                        "sheetId": sid,
                                        "rowIndex": 0,
                                        "columnIndex": 13,
                                    },
                                    "offsetXPixels": 10,
                                    "offsetYPixels": 10,
                                    "widthPixels": 720,
                                    "heightPixels": 340,
                                }
                            },
                        }
                    }
                },
                {
                    "addChart": {
                        "chart": {
                            "spec": {
                                "title": "누적 매매손익 PnL (USDT)",
                                "basicChart": {
                                    "chartType": "LINE",
                                    "legendPosition": "BOTTOM_LEGEND",
                                    "headerCount": 0,
                                    "axis": [
                                        {"position": "BOTTOM_AXIS", "title": "UTC"},
                                        {"position": "LEFT_AXIS", "title": "PnL USDT"},
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
                                        "rowIndex": 18,
                                        "columnIndex": 13,
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
                {
                    "addChart": {
                        "chart": {
                            "spec": {
                                "title": "일별 누적 TWR % (기사식 일간연결)",
                                "basicChart": {
                                    "chartType": "LINE",
                                    "legendPosition": "BOTTOM_LEGEND",
                                    "headerCount": 0,
                                    "axis": [
                                        {"position": "BOTTOM_AXIS", "title": "날짜"},
                                        {"position": "LEFT_AXIS", "title": "TWR %"},
                                    ],
                                    "domains": [{"domain": {"sourceRange": {"sources": [d_domain()]}}}],
                                    "series": [
                                        {
                                            "series": {"sourceRange": {"sources": [d_col(1)]}},
                                            "targetAxis": "LEFT_AXIS",
                                        },
                                        {
                                            "series": {"sourceRange": {"sources": [d_col(3)]}},
                                            "targetAxis": "LEFT_AXIS",
                                        },
                                    ],
                                },
                            },
                            "position": {
                                "overlayPosition": {
                                    "anchorCell": {
                                        "sheetId": sid,
                                        "rowIndex": 34,
                                        "columnIndex": 13,
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
        }
    )
    return {
        "ok": True,
        "sheet_url": SHEET_URL,
        "sheet_tab": SHEET_TAB,
        "twr_pct": summary.get("twr_pct"),
        "mdd_pct": summary.get("mdd_pct"),
        "xirr_pct": summary.get("xirr_pct"),
        "n_points": n,
        "n_days": nd,
    }


def push(log_path: Path | None = None) -> dict:
    dest = log_path or (STRATEGYD_ROOT / "okx_bot.log")
    if not dest.is_file():
        raise RuntimeError("missing okx_bot.log — analyze a StrategyD log first")
    text = dest.read_text(encoding="utf-8", errors="replace")
    mtime = datetime.fromtimestamp(dest.stat().st_mtime)
    raw = parse_runs(text.splitlines(), mtime)
    if not raw:
        raise RuntimeError("no Strategy D live runs in log")
    rows, cash_events = apply_cashflow_adjust(attach_equity(raw))
    summary, _series, notes = compute_performance(rows, cash_events)
    return push_parsed(rows, cash_events, summary, notes)
