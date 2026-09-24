# -*- coding: utf-8 -*-
"""SuperMA strategy1/strategy2 latest-session analyzer."""
from __future__ import annotations

import json
import re
from pathlib import Path

from config import OUTPUTS, SUPERMA_ROOT

DT_RE = re.compile(
    r"datetime\.datetime\((\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)"
)
TICKER_RE = re.compile(r">>\s*(.+?)\s+(\d{6})\s*<<")
REBAL_QTY_RE = re.compile(r"리밸런싱수량:\s*(-?\d+)")
PARK_SELL_RE = re.compile(r"파킹ETF\s*매도\(현금확보\):\s*488770\s*(\d+)주")
PARK_BUY_RE = re.compile(r"파킹ETF\s*지정가매수\(유휴현금\):\s*488770\s*(\d+)주")
ORDER_RE = re.compile(r"OrderInfo\s*:\s*\{[^}]*'OrderNum'\s*:\s*'([^']+)'")


def _sessions(text: str) -> list[tuple[str, str, list[str]]]:
    """Return list of (date YYYY-MM-DD, HH:MM, lines) for each datetime anchor run."""
    lines = text.splitlines()
    anchors: list[tuple[int, str, str]] = []
    for i, line in enumerate(lines):
        m = DT_RE.search(line)
        if m:
            y, mo, d, h, mi = m.groups()
            day = f"{y}-{int(mo):02d}-{int(d):02d}"
            hm = f"{int(h):02d}:{int(mi):02d}"
            anchors.append((i, day, hm))
    if not anchors:
        return []

    # Group consecutive same-day into one session starting at first clock of that day cluster
    sessions: list[tuple[str, str, list[str]]] = []
    i = 0
    while i < len(anchors):
        start_idx, day, hm = anchors[i]
        j = i + 1
        while j < len(anchors) and anchors[j][1] == day:
            j += 1
        end_idx = anchors[j][0] if j < len(anchors) else len(lines)
        # Include from start_idx until next different day
        chunk = lines[start_idx:end_idx]
        sessions.append((day, hm, chunk))
        i = j
    return sessions


def _detect_bot(text: str, filename: str = "") -> str:
    if "슈퍼이동평균자산배분전략_KR2" in text or "strategy2" in filename.lower():
        return "Bot2 (KR2)"
    if "슈퍼이동평균자산배분전략_KR" in text or "strategy1" in filename.lower():
        return "Bot1 (KR)"
    return "Unknown"


def analyze_one(text: str, filename: str = "") -> dict:
    sessions = _sessions(text)
    if not sessions:
        raise ValueError(f"no datetime.datetime sessions in {filename or 'log'}")

    day, hm, chunk = sessions[-1]
    body = "\n".join(chunk)
    bot = _detect_bot(body + "\n" + text[:2000], filename)

    signals: list[str] = []
    for label, pat in [
        ("FIXED 매수", "FIXED 매수조건 만족"),
        ("패스트매수", "패스트매수조건 만족"),
        ("슬로우매수", "슬로우 매수조건 만족"),
        ("매도조건", "매도 조건"),
    ]:
        if pat in body:
            # try to capture nearby stock names
            for line in chunk:
                if pat in line:
                    signals.append(line.strip()[:120])
                    break
            else:
                signals.append(label)

    rebal: list[dict] = []
    current_name = current_code = None
    for line in chunk:
        tm = TICKER_RE.search(line)
        if tm:
            current_name, current_code = tm.group(1).strip(), tm.group(2)
            continue
        qm = REBAL_QTY_RE.search(line)
        if qm and current_code:
            qty = int(qm.group(1))
            if qty != 0:
                rebal.append(
                    {"name": current_name, "code": current_code, "qty": qty}
                )

    parking = "해당없음"
    if "파킹ETF 매수 스킵" in body:
        parking = "매수 스킵"
    m_sell = PARK_SELL_RE.search(body)
    if m_sell:
        parking = f"488770 {m_sell.group(1)}주 매도"
    m_buy = PARK_BUY_RE.search(body)
    if m_buy:
        parking = f"488770 {m_buy.group(1)}주 매수"

    applied = "해당없음"
    for line in chunk:
        if "AppliedEFState 갱신" in line:
            applied = "갱신: " + line.split("AppliedEFState 갱신:", 1)[-1].strip()[:80]
            break
        if "EF 버전 변경 감지" in line:
            applied = line.strip()[:100]
            break

    finish_park = "해당없음"
    # trailing parking buy section after rebalance
    if "파킹ETF 매수 스킵" in body:
        finish_park = "스킵"
    elif m_buy:
        finish_park = f"{m_buy.group(1)}주 매수"

    orders = ORDER_RE.findall(body)
    errors: list[str] = []
    for line in chunk:
        if "Traceback" in line or (line.strip().startswith("실패") and "ETF" not in line):
            errors.append(line.strip()[:160])

    idle = "실행 완료 (변경 없음)" in body
    market_closed_signal = "매매할 종목이 있어 리밸런싱 수행 해야 하지만 지금은 장이 열려있지 않아요" in body

    rebal_str = (
        ", ".join(f"{r['name']} **{r['qty']:+d}**" for r in rebal)
        if rebal
        else ("변경 없음" if idle else "없음")
    )
    if orders:
        rebal_str += f" (Order {', '.join(orders[:4])})"

    table = {
        "신호": "; ".join(signals) if signals else ("유휴" if idle else "없음"),
        "리밸": rebal_str,
        "파킹": parking,
        "AppliedEFState": applied,
        "마무리 파킹매수": finish_park,
        "에러": "; ".join(errors) if errors else "없음",
    }

    healthy = not errors and not market_closed_signal
    if idle and not rebal:
        one_liner = f"{bot} {day} {hm}: 변경 없음, 정상 유휴."
    elif rebal:
        one_liner = f"{bot} {day} {hm}: 리밸 {rebal_str}, 파킹 {parking}."
    elif market_closed_signal:
        one_liner = f"{bot} {day} {hm}: 신호 있으나 장 미개장으로 미체결."
    else:
        one_liner = f"{bot} {day} {hm}: 요약 완료."

    return {
        "bot": bot,
        "day": day,
        "time": hm,
        "table": table,
        "rebalance": rebal,
        "orders": orders,
        "idle": idle,
        "market_closed_signal": market_closed_signal,
        "one_liner": one_liner,
        "healthy": healthy,
        "history_days": sorted({s[0] for s in sessions}),
    }


def run(log_paths: list[Path]) -> dict:
    bots: list[dict] = []
    for p in log_paths:
        text = p.read_text(encoding="utf-8", errors="replace")
        bots.append(analyze_one(text, p.name))
        # Sync into project tree by name heuristic
        name = p.name.lower()
        if "2" in name or "kr2" in name:
            dest = SUPERMA_ROOT / "strategy2.log"
        else:
            dest = SUPERMA_ROOT / "strategy1.log"
        if p.resolve() != dest.resolve():
            dest.write_bytes(p.read_bytes())

    out_dir = OUTPUTS / "superma"
    out_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "bots": bots,
        "one_liner": " / ".join(b["one_liner"] for b in bots),
        "saved_to": str(out_dir / "summary.json"),
        "sheet": {"ok": False, "skipped": True, "reason": "SuperMA analyzer has no Sheet tab"},
    }
    (out_dir / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # also under project
    proj = SUPERMA_ROOT / "result" / "log_analyzer"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result
