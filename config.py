# -*- coding: utf-8 -*-
"""Shared paths and project registry for the multi-bot log hub."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

HUB_ROOT = Path(__file__).resolve().parent
IS_VERCEL = bool(os.environ.get("VERCEL"))
RUNTIME_ROOT = Path("/tmp/log-hub") if IS_VERCEL else HUB_ROOT
UPLOADS = RUNTIME_ROOT / "uploads"
OUTPUTS = RUNTIME_ROOT / "outputs"
CACHE = RUNTIME_ROOT / "cache"

AUTOBOT = Path(os.environ.get("AUTOBOT_ROOT", r"C:\Users\var_autobot"))
if IS_VERCEL:
    PROJECT_RUNTIME = RUNTIME_ROOT / "projects"
    COIN_ROOT = Path(os.environ.get("COIN_ROOT", PROJECT_RUNTIME / "coin"))
    SMALLCAP_ROOT = Path(os.environ.get("SMALLCAP_ROOT", PROJECT_RUNTIME / "smallcap"))
    SUPERMA_ROOT = Path(os.environ.get("SUPERMA_ROOT", PROJECT_RUNTIME / "superma"))
else:
    COIN_ROOT = Path(os.environ.get("COIN_ROOT", AUTOBOT / "Coin-1계좌3전략"))
    SMALLCAP_ROOT = Path(os.environ.get("SMALLCAP_ROOT", AUTOBOT / "소형주-MTCB"))
    SUPERMA_ROOT = Path(os.environ.get("SUPERMA_ROOT", AUTOBOT / "슈퍼이동평균V2-1"))

SA_CANDIDATES = [
    AUTOBOT / "autobot-496513-3ce948f2711e.json",
    Path(r"C:\Users\Admin\Downloads\autobot-496513-3ce948f2711e.json"),
    SMALLCAP_ROOT / "autobot-496513-3ce948f2711e.json",
]

OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.6-sol")

PROJECTS = {
    "coin": {
        "id": "coin",
        "name": "Coin 1계좌 3전략",
        "short": "OKX 3in1",
        "description": "run.log → 보유 수익률/MDD + Sheet 3in1",
        "root_path": str(COIN_ROOT),
        "accept": ".log,.txt",
        "multi_file": False,
        "default_log": str(COIN_ROOT / "run.log"),
        "sheet_url": "https://docs.google.com/spreadsheets/d/1hdwoh-Tc5LDVOsOcaJnNx00vKkqNOiU3E_ikW45yKiQ/edit#gid=1193052549",
        "sheet_tab": "3in1",
        "sheet_push_available": not IS_VERCEL,
        "content_paths": [
            {"id": "run.log", "label": "run.log", "path": str(COIN_ROOT / "run.log")},
            {
                "id": "last_summary",
                "label": "마지막 분석 JSON",
                "path": str(OUTPUTS / "coin" / "summary.json"),
            },
        ],
    },
    "smallcap": {
        "id": "smallcap",
        "name": "소형주 MTCB",
        "short": "Canonical V19",
        "description": "Canonical_V19.log → 리포트 + Sheet「리포트」",
        "root_path": str(SMALLCAP_ROOT),
        "accept": ".log,.txt",
        "multi_file": False,
        "default_log": str(SMALLCAP_ROOT / "Canonical_V19.log"),
        "sheet_url": "https://docs.google.com/spreadsheets/d/1O4q7Vt2-W62Kp8xJ4SvRFucFhgDvg44muJ-3mgrOLIE/edit#gid=369475009",
        "sheet_tab": "리포트",
        "sheet_push_available": not IS_VERCEL,
        "content_paths": [
            {
                "id": "Canonical_V19.log",
                "label": "Canonical_V19.log",
                "path": str(SMALLCAP_ROOT / "Canonical_V19.log"),
            },
            {
                "id": "summary.json",
                "label": "summary.json",
                "path": str(SMALLCAP_ROOT / "result" / "live_report" / "summary.json"),
            },
            {
                "id": "positions.csv",
                "label": "positions.csv",
                "path": str(SMALLCAP_ROOT / "result" / "live_report" / "positions.csv"),
            },
            {
                "id": "trades.csv",
                "label": "trades.csv",
                "path": str(SMALLCAP_ROOT / "result" / "live_report" / "trades.csv"),
            },
            {
                "id": "equity_daily.csv",
                "label": "equity_daily.csv",
                "path": str(SMALLCAP_ROOT / "result" / "live_report" / "equity_daily.csv"),
            },
        ],
    },
    "superma": {
        "id": "superma",
        "name": "슈퍼이동평균 V2-1",
        "short": "SuperMA KR",
        "description": "strategy1/2.log → Bot1·Bot2 일일 요약",
        "root_path": str(SUPERMA_ROOT),
        "accept": ".log,.txt",
        "multi_file": True,
        "default_log": str(SUPERMA_ROOT / "strategy1.log"),
        "sheet_url": None,
        "sheet_tab": None,
        "sheet_push_available": False,
        "content_paths": [
            {
                "id": "strategy1.log",
                "label": "strategy1.log",
                "path": str(SUPERMA_ROOT / "strategy1.log"),
            },
            {
                "id": "strategy2.log",
                "label": "strategy2.log",
                "path": str(SUPERMA_ROOT / "strategy2.log"),
            },
            {
                "id": "last_summary",
                "label": "마지막 분석 JSON",
                "path": str(OUTPUTS / "superma" / "summary.json"),
            },
        ],
    },
}


def ensure_dirs() -> None:
    for p in (UPLOADS, OUTPUTS, CACHE):
        p.mkdir(parents=True, exist_ok=True)
    if IS_VERCEL:
        for p in (COIN_ROOT, SMALLCAP_ROOT, SUPERMA_ROOT):
            p.mkdir(parents=True, exist_ok=True)
    for pid in PROJECTS:
        (OUTPUTS / pid).mkdir(parents=True, exist_ok=True)
        (UPLOADS / pid).mkdir(parents=True, exist_ok=True)


def find_sa() -> Path | None:
    for p in SA_CANDIDATES:
        if p.is_file():
            return p
    return None


def _fmt_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).isoformat(sep=" ", timespec="seconds")


def project_state(project_id: str) -> dict:
    """Return timestamps for the project's cached analysis + last Sheet push."""
    proj = PROJECTS.get(project_id)
    if not proj:
        return {}
    out_dir = OUTPUTS / project_id
    summary = out_dir / "summary.json"
    last_push = out_dir / "last_sheet_push.txt"
    state = {
        "last_analyzed_at": _fmt_iso(summary.stat().st_mtime) if summary.is_file() else None,
        "last_sheet_push_at": last_push.read_text(encoding="utf-8").strip()
        if last_push.is_file()
        else None,
    }
    return state


def mark_sheet_push(project_id: str) -> str:
    out_dir = OUTPUTS / project_id
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = _fmt_iso(datetime.now().timestamp())
    (out_dir / "last_sheet_push.txt").write_text(ts, encoding="utf-8")
    return ts
