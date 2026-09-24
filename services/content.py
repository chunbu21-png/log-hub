# -*- coding: utf-8 -*-
"""Read current local content files for the View Current panel."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from config import PROJECTS, UPLOADS

MAX_CHARS = 200_000


def list_logs(project_id: str) -> list[dict]:
    """List project-root and hub-upload log files without recursive scanning."""
    proj = PROJECTS.get(project_id)
    if not proj:
        raise KeyError(project_id)

    locations = [
        ("Project", Path(proj["root_path"])),
        ("Uploads", UPLOADS / project_id),
    ]
    seen: set[str] = set()
    rows: list[dict] = []
    for source, folder in locations:
        if not folder.is_dir():
            continue
        candidates = list(folder.glob("*.log"))
        # Uploaded log-like text files are valid inputs, but unrelated project
        # files such as requirements.txt should not appear in this list.
        if source == "Uploads":
            candidates += list(folder.glob("*.txt"))
        for path in candidates:
            if not path.is_file():
                continue
            key = str(path.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            stat = path.stat()
            rows.append(
                {
                    "id": f"{source.lower()}:{path.name}",
                    "name": path.name,
                    "path": str(path),
                    "source": source,
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(
                        sep=" ", timespec="seconds"
                    ),
                }
            )
    return sorted(rows, key=lambda row: row["modified"], reverse=True)


def read_log(project_id: str, log_id: str, max_chars: int = MAX_CHARS) -> dict:
    """Read a log selected from list_logs; matching the list prevents path traversal."""
    item = next((row for row in list_logs(project_id) if row["id"] == log_id), None)
    if not item:
        raise KeyError(log_id)
    path = Path(item["path"])
    text = path.read_text(encoding="utf-8", errors="replace")
    truncated = len(text) > max_chars
    if truncated:
        text = text[-max_chars:]
        text = f"...[truncated to last {max_chars} chars]...\n" + text
    return {
        **item,
        "exists": True,
        "text": text,
        "truncated": truncated,
    }


def list_content(project_id: str) -> list[dict]:
    proj = PROJECTS.get(project_id)
    if not proj:
        raise KeyError(project_id)
    rows = []
    for item in proj["content_paths"]:
        p = Path(item["path"])
        rows.append(
            {
                "id": item["id"],
                "label": item["label"],
                "path": str(p),
                "exists": p.is_file(),
                "size": p.stat().st_size if p.is_file() else 0,
            }
        )
    return rows


def read_content(project_id: str, content_id: str, max_chars: int = MAX_CHARS) -> dict:
    proj = PROJECTS.get(project_id)
    if not proj:
        raise KeyError(project_id)
    item = next((c for c in proj["content_paths"] if c["id"] == content_id), None)
    if not item:
        raise KeyError(content_id)
    p = Path(item["path"])
    if not p.is_file():
        return {
            "id": content_id,
            "label": item["label"],
            "path": str(p),
            "exists": False,
            "text": "",
            "truncated": False,
        }
    text = p.read_text(encoding="utf-8", errors="replace")
    truncated = len(text) > max_chars
    if truncated:
        text = text[-max_chars:]
        text = f"...[truncated to last {max_chars} chars]...\n" + text
    return {
        "id": content_id,
        "label": item["label"],
        "path": str(p),
        "exists": True,
        "size": p.stat().st_size,
        "text": text,
        "truncated": truncated,
    }
