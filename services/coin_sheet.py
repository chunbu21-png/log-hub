# -*- coding: utf-8 -*-
"""Push OKX 3in1 live log metrics to Google Sheet「3in1」."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import find_sa
from services.coin_parser import SHEET_KEY, apply_cashflow_adjust, parse_live_rows

SHEET_TAB = "3in1"
SHEET_GID = 1193052549
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_KEY}/edit#gid={SHEET_GID}"


def sheet_safe(v):
    if isinstance(v, str) and v[:1] in "=+-@":
        return "'" + v
    return v


def safe_row(row: list) -> list:
    return [sheet_safe(x) for x in row]


def parse_utc(s: str) -> datetime:
    return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def xirr(cashflows: list[tuple[datetime, float]], guess: float = 0.1) -> Optional[float]:
    if len(cashflows) < 2:
        return None
    cashflows = sorted(cashflows, key=lambda x: x[0])
    t0 = cashflows[0][0]
    vs = [a for _, a in cashflows]
    days = [(t - t0).total_seconds() / 86400.0 for t, _ in cashflows]
    if not any(v > 0 for v in vs) or not any(v < 0 for v in vs):
        return None

    def npv(r: float) -> float:
        return sum(v / ((1.0 + r) ** (d / 365.0)) for v, d in zip(vs, days))

    def dnpv(r: float) -> float:
        return sum(
            -(d / 365.0) * v / ((1.0 + r) ** (d / 365.0 + 1.0)) for v, d in zip(vs, days)
        )

    for guess_r in (guess, 0.0, 0.2, 0.5, -0.2, 1.0, -0.5):
        r = guess_r
        for _ in range(80):
            f = npv(r)
            df = dnpv(r)
            if abs(df) < 1e-14:
                break
            r2 = r - f / df
            if abs(r2 - r) < 1e-10:
                return r2
            r = r2
    return None


def build_twr_series(rows: list[dict], cash_events: list[dict]) -> tuple[list[dict], float, float]:
    deposit_utc = {
        e["utc"]: float(e["amount"])
        for e in cash_events
        if e["kind"] == "deposit_like"
    }
    hold = [r for r in rows if r["n_pos"] > 0]
    if not hold:
        return [], 0.0, 0.0

    series: list[dict] = []
    anchor = float(hold[0]["adj_equity"])
    locked = 1.0
    peak = anchor
    mdd = 0.0
    prev = hold[0]

    for r in hold:
        v = float(r["adj_equity"])
        utc = r["utc"]
        if utc in deposit_utc and utc != hold[0]["utc"]:
            v_prev = float(prev["adj_equity"])
            if anchor > 0:
                locked *= v_prev / anchor
            anchor = v
        cum = locked * (v / anchor) - 1.0 if anchor > 0 else 0.0
        if v > peak:
            peak = v
        dd = v / peak - 1.0 if peak > 0 else 0.0
        if dd < mdd:
            mdd = dd
        v_prev = float(prev["adj_equity"])
        step = (v / v_prev - 1.0) if v_prev > 0 else 0.0
        series.append(
            {
                "utc": utc,
                "equity_raw": round(float(r["equity"]), 2),
                "adj_equity": round(v, 2),
                "free": round(float(r["free"] or 0), 2),
                "n_pos": r["n_pos"],
                "pos": ", ".join(r["pos"]) if r["pos"] else "",
                "deposit": deposit_utc.get(utc, 0.0),
                "step_ret_pct": round(step * 100, 4),
                "cum_twr_pct": round(cum * 100, 4),
                "dd_pct": round(dd * 100, 4),
            }
        )
        prev = r

    v_last = float(hold[-1]["adj_equity"])
    twr = locked * (v_last / anchor) - 1.0 if anchor > 0 else 0.0
    return series, twr, mdd


def downsample(series: list[dict]) -> list[dict]:
    out = []
    for idx, s in enumerate(series):
        keep = (
            idx % 4 == 0
            or idx == len(series) - 1
            or s["utc"].endswith((":00:03", ":00:04", ":00:05"))
            or float(s.get("deposit") or 0) > 0
        )
        if keep:
            out.append(s)
    return sorted({s["utc"]: s for s in out}.values(), key=lambda x: x["utc"])


def _trade_notes(lines: list[str], limit: int = 12) -> list[str]:
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


def _fmt_events(cash_events: list[dict], kind: str) -> str:
    ev = [e for e in cash_events if e["kind"] == kind]
    if not ev:
        return "없음"
    return "; ".join(
        f"{e['utc']} {e['amount']}" + (f" ({e['n_pos']})" if e.get("n_pos") else "")
        for e in ev
    )


def push(log_path: Path) -> dict:
    sa = find_sa()
    if sa is None:
        raise RuntimeError("Google service-account JSON not found")
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    rows, cash_events = apply_cashflow_adjust(parse_live_rows(lines))
    if not rows:
        raise RuntimeError("no live rows in OKX 3in1 log")
    full_series, twr, mdd = build_twr_series(rows, cash_events)
    if not full_series:
        raise RuntimeError("no holding ticks")
    series = downsample(full_series)
    hold_rows = [r for r in rows if r["n_pos"] > 0]

    t0 = parse_utc(hold_rows[0]["utc"])
    v0_raw = float(hold_rows[0]["equity"])
    cfs: list[tuple[datetime, float]] = [(t0, -v0_raw)]
    for e in cash_events:
        if e["kind"] != "deposit_like":
            continue
        cfs.append((parse_utc(e["utc"]), -float(e["amount"])))
    t1 = parse_utc(hold_rows[-1]["utc"])
    v1_raw = float(hold_rows[-1]["equity"])
    cfs.append((t1, v1_raw))
    xirr_v = xirr(cfs)

    elapsed_days = max((t1 - t0).total_seconds() / 86400.0, 1.0)
    twr_ann = (1.0 + twr) ** (365.25 / elapsed_days) - 1.0 if twr > -0.999999 else -1.0
    daily = {r["utc"][:10]: r for r in rows}
    trade_notes = _trade_notes(lines)

    import gspread
    from google.oauth2.service_account import Credentials

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(str(sa), scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_KEY)
    ws = sh.worksheet(SHEET_TAB)
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
                            "endRowIndex": 250,
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

    xirr_txt = f"{xirr_v * 100:.2f}%" if xirr_v is not None else "계산불가"
    v0 = float(hold_rows[0]["adj_equity"])
    v1 = float(hold_rows[-1]["adj_equity"])
    header: list[list] = [
        ["3in1 실매매 성과 — TWR / XIRR (run.log)"],
        ["생성시각(KST)", datetime.now().strftime("%Y-%m-%d %H:%M")],
        ["데이터 구간(UTC)", f"{rows[0]['utc']} ~ {rows[-1]['utc']}"],
        ["측정 시작(보유)", f"{hold_rows[0]['utc']}  adj={v0:.2f}  raw={v0_raw:.2f}"],
        ["프로필", "low_mdd (LowMDD H heat)"],
        [
            "방법",
            "TWR=adj 경로(입금 제거·측정착시 보정)로 전략성과. "
            "XIRR=raw equity + 실제 입금 현금흐름으로 내 돈의 연환산.",
        ],
        [],
        ["[요약]"],
        ["항목", "값", "설명"],
        ["누적 TWR", f"{twr * 100:.2f}%", "전략 시간가중 수익률 (입출금 왜곡 제거)"],
        ["연환산 TWR", f"{twr_ann * 100:.2f}%", f"측정 {elapsed_days:.0f}일 기준"],
        ["XIRR", xirr_txt, "기초 raw NAV + 입금 대비 최종 raw NAV 연환산"],
        ["TWR 경로 MDD", f"{mdd * 100:.2f}%", "adj equity peak→trough"],
        ["최근 adj equity", f"{v1:.2f}", "전략 성과 경로"],
        ["최근 equity~ raw", f"{v1_raw:.2f}", "XIRR 최종평가"],
        ["포지션 수", str(hold_rows[-1]["n_pos"]), ""],
        ["포지션", ", ".join(hold_rows[-1]["pos"]), ""],
        [],
        ["[입출금·보정 이벤트]"],
        ["입금(deposit)", _fmt_events(cash_events, "deposit_like"), "XIRR/TWR 구간절단에 사용"],
        ["진입절벽 보정", _fmt_events(cash_events, "deploy_cliff"), "equity~ 측정착시"],
        ["유령청산 보정", _fmt_events(cash_events, "ghost_flat_release"), "equity~ 측정착시"],
        ["유령state정리", _fmt_events(cash_events, "ghost_state_clear"), "equity~ 측정착시"],
        [],
        ["[XIRR 현금흐름]"],
        ["날짜(UTC)", "현금흐름", "부호"],
    ]
    for t, a in sorted(cfs, key=lambda x: x[0]):
        header.append(
            [
                t.strftime("%Y-%m-%d %H:%M:%S"),
                round(a, 2),
                "투입(-)" if a < 0 else "회수/평가(+)",
            ]
        )
    header += [[], ["[최근 시가패스 메모]"]]
    if trade_notes:
        for n in trade_notes:
            header.append(["이벤트", n, ""])
    else:
        header.append(["이벤트", "없음", ""])
    header += [[], ["[일별 스냅샷]"], ["UTC day", "equity~ raw", "adj", "free", "positions"]]
    for d, r in sorted(daily.items()):
        header.append(
            [
                d,
                f"{r['equity']:.2f}",
                f"{r['adj_equity']:.2f}",
                f"{r['free']:.2f}",
                ", ".join(r["pos"]) if r["pos"] else str(r["n_pos"]),
            ]
        )

    ws.update(range_name="A1", values=[safe_row(r) for r in header], value_input_option="USER_ENTERED")

    ts_start = max(len(header) + 3, 45)
    ts_header = [
        [
            "UTC",
            "Cum TWR %",
            "Drawdown %",
            "step ret %",
            "deposit",
            "adj equity",
            "equity~ raw",
            "free",
            "n_pos",
            "positions",
        ]
    ]
    ts_data = [
        [
            s["utc"],
            s["cum_twr_pct"],
            s["dd_pct"],
            s["step_ret_pct"],
            s["deposit"],
            s["adj_equity"],
            s["equity_raw"],
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

    n = len(series)
    r0 = ts_start - 1
    r1 = ts_start + n

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

    sh.batch_update(
        {
            "requests": [
                {
                    "addChart": {
                        "chart": {
                            "spec": {
                                "title": "Cumulative TWR % (deposit-linked, adj equity)",
                                "basicChart": {
                                    "chartType": "LINE",
                                    "legendPosition": "BOTTOM_LEGEND",
                                    "headerCount": 0,
                                    "axis": [
                                        {"position": "BOTTOM_AXIS", "title": "UTC"},
                                        {"position": "LEFT_AXIS", "title": "Cum TWR %"},
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
                                        "columnIndex": 10,
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
                                "title": "Drawdown % on TWR path (adj)",
                                "basicChart": {
                                    "chartType": "LINE",
                                    "legendPosition": "BOTTOM_LEGEND",
                                    "headerCount": 0,
                                    "axis": [
                                        {"position": "BOTTOM_AXIS", "title": "UTC"},
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
                                        "columnIndex": 10,
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
                            "endColumnIndex": 3,
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
        "twr_pct": round(twr * 100, 2),
        "mdd_pct": round(mdd * 100, 2),
        "xirr_pct": round(xirr_v * 100, 2) if xirr_v is not None else None,
        "n_points": n,
    }
