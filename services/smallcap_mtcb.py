# -*- coding: utf-8 -*-
"""Canonical V19 smallcap live report + optional Sheet「리포트」push."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from config import IS_VERCEL, OUTPUTS, SMALLCAP_ROOT
from services.smallcap_report import build_report, parse_log, write_outputs

SHEET_KEY = "1O4q7Vt2-W62Kp8xJ4SvRFucFhgDvg44muJ-3mgrOLIE"


def run(log_path: Path, push_sheet: bool = False) -> dict:
    text = log_path.read_text(encoding="utf-8", errors="replace")
    fills = parse_log(text)
    report = build_report(fills)
    out_dir = SMALLCAP_ROOT / "result" / "live_report"
    write_outputs(report, out_dir)

    canvas_data = {
        "summary": report["summary"],
        "equity": report["equity"],
        "positions": report["positions"],
        "trades": report["trades"],
    }
    (out_dir / "canvas_data.json").write_text(
        json.dumps(canvas_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Mirror into hub outputs + keep project canonical log
    hub_out = OUTPUTS / "smallcap"
    hub_out.mkdir(parents=True, exist_ok=True)
    (hub_out / "summary.json").write_text(
        json.dumps(
            {
                "summary": report["summary"],
                "n_fills": len(fills),
                "report_dir": str(out_dir),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    dest_log = SMALLCAP_ROOT / "Canonical_V19.log"
    if log_path.resolve() != dest_log.resolve():
        dest_log.write_bytes(log_path.read_bytes())

    sheet_info: dict = {"ok": False, "skipped": True}
    if push_sheet:
        if IS_VERCEL:
            raise RuntimeError(
                "Canonical Google Sheet push is not available on Vercel yet; "
                "its service-account credentials are local-only."
            )
        scripts = SMALLCAP_ROOT / "scripts"
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        from push_live_report_to_sheet import push

        push(out_dir)
        sheet_info = {
            "ok": True,
            "sheet_url": f"https://docs.google.com/spreadsheets/d/{SHEET_KEY}/edit#gid=369475009",
        }

    return {
        "summary": report["summary"],
        "positions": report["positions"][:50],
        "trades": report["trades"][-40:],
        "equity_tail": report["equity"][-30:],
        "n_fills": len(fills),
        "n_buys": sum(1 for f in fills if f.side == "BUY"),
        "n_sells": sum(1 for f in fills if f.side == "SELL"),
        "report_dir": str(out_dir),
        "saved_to": str(hub_out / "summary.json"),
        "project_log": str(dest_log),
        "sheet_url": f"https://docs.google.com/spreadsheets/d/{SHEET_KEY}/edit#gid=369475009",
        "sheet": sheet_info,
    }
