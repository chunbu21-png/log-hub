# -*- coding: utf-8 -*-
"""Canonical V19 smallcap live report + Sheet「리포트」push."""
from __future__ import annotations

import json
from pathlib import Path

from config import IS_VERCEL, OUTPUTS, SMALLCAP_ROOT
from services.smallcap_report import build_report, parse_log, write_outputs
from services.smallcap_sheet import SHEET_TAB, SHEET_URL
from services.smallcap_sheet import push as write_report_tab


def push_sheet(report_dir: Path | None = None) -> dict:
    if IS_VERCEL:
        raise RuntimeError(
            "Canonical Google Sheet push is not available on Vercel yet; "
            "its service-account credentials are local-only."
        )
    out_dir = report_dir or (SMALLCAP_ROOT / "result" / "live_report")
    if not (out_dir / "summary.json").is_file():
        raise RuntimeError(f"missing live report in {out_dir} — analyze a Canonical log first")
    return write_report_tab(out_dir)


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
        try:
            sheet_info = write_report_tab(out_dir)
        except Exception as exc:  # noqa: BLE001
            sheet_info = {"ok": False, "error": str(exc)}

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
        "sheet_url": SHEET_URL,
        "sheet_tab": SHEET_TAB,
        "sheet": sheet_info,
    }
