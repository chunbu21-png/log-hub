# -*- coding: utf-8 -*-
"""Multi-bot live log hub — FastAPI backend."""
from __future__ import annotations

import traceback
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import HUB_ROOT, PROJECTS, UPLOADS, ensure_dirs, mark_sheet_push, project_state
from services import content, openai_narrate

ensure_dirs()

app = FastAPI(title="Autobot Log Hub", version="1.0.0")
STATIC = HUB_ROOT / "static"
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


class NarrateBody(BaseModel):
    project_id: str
    analysis: dict


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/projects")
def api_projects():
    projects = []
    for pid, meta in PROJECTS.items():
        projects.append({**meta, "state": project_state(pid)})
    return {"projects": projects}


@app.get("/api/config")
def api_config():
    """Expose non-secret runtime capabilities to the browser."""
    return {
        "openai_configured": openai_narrate.is_configured(),
        "openai_model": openai_narrate.model_name(),
    }


@app.get("/api/projects/{project_id}/state")
def api_project_state(project_id: str):
    if project_id not in PROJECTS:
        raise HTTPException(404, "unknown project")
    return project_state(project_id)


@app.get("/api/projects/{project_id}/content")
def api_list_content(project_id: str):
    if project_id not in PROJECTS:
        raise HTTPException(404, "unknown project")
    return {"items": content.list_content(project_id)}


@app.get("/api/projects/{project_id}/logs")
def api_list_logs(project_id: str):
    if project_id not in PROJECTS:
        raise HTTPException(404, "unknown project")
    return {"items": content.list_logs(project_id)}


@app.get("/api/projects/{project_id}/log-content/{log_id}")
def api_read_log(project_id: str, log_id: str):
    if project_id not in PROJECTS:
        raise HTTPException(404, "unknown project")
    try:
        return content.read_log(project_id, log_id)
    except KeyError:
        raise HTTPException(404, "unknown log file") from None


@app.get("/api/projects/{project_id}/content/{content_id}")
def api_read_content(project_id: str, content_id: str):
    if project_id not in PROJECTS:
        raise HTTPException(404, "unknown project")
    try:
        return content.read_content(project_id, content_id)
    except KeyError:
        raise HTTPException(404, "unknown content id") from None


def _as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


@app.post("/api/analyze/{project_id}")
async def api_analyze(
    project_id: str,
    files: list[UploadFile] = File(...),
    push_sheet: str = Form("false"),
    use_openai: str = Form("false"),
):
    if project_id not in PROJECTS:
        raise HTTPException(404, "unknown project")
    if not files:
        raise HTTPException(400, "no files uploaded")

    do_push = _as_bool(push_sheet)
    do_openai = _as_bool(use_openai)

    dest_dir = UPLOADS / project_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for uf in files:
        name = Path(uf.filename or "upload.log").name
        path = dest_dir / name
        data = await uf.read()
        path.write_bytes(data)
        saved.append(path)

    try:
        if project_id == "coin":
            from services import coin_3in1

            result = coin_3in1.run(saved[0], push=do_push)
        elif project_id == "smallcap":
            from services import smallcap_mtcb

            result = smallcap_mtcb.run(saved[0], push_sheet=do_push)
        elif project_id == "superma":
            from services import superma

            result = superma.run(saved)
        else:
            raise HTTPException(400, "unsupported project")
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "error": str(e),
            "trace": traceback.format_exc()[-2000:],
        }

    if do_push and isinstance(result, dict) and result.get("sheet", {}).get("ok"):
        result["sheet"]["pushed_at"] = mark_sheet_push(project_id)

    narrative = None
    if do_openai:
        narrative = openai_narrate.narrate(project_id, result)

    return {
        "ok": True,
        "project_id": project_id,
        "uploaded": [str(p) for p in saved],
        "result": result,
        "narrative": narrative,
        "state": project_state(project_id),
    }


@app.post("/api/narrate")
def api_narrate(body: NarrateBody):
    if body.project_id not in PROJECTS:
        raise HTTPException(404, "unknown project")
    return openai_narrate.narrate(body.project_id, body.analysis)


@app.post("/api/push/{project_id}")
def api_push(project_id: str):
    """Re-push last local artifacts to Google Sheet without re-upload."""
    if project_id not in PROJECTS:
        raise HTTPException(404, "unknown project")
    try:
        if project_id == "coin":
            from services import coin_3in1

            result = coin_3in1.push_sheet()
            ts = mark_sheet_push(project_id)
            return {**result, "pushed_at": ts}
        if project_id == "smallcap":
            import sys

            from config import SMALLCAP_ROOT

            scripts = str(SMALLCAP_ROOT / "scripts")
            if scripts not in sys.path:
                sys.path.insert(0, scripts)
            from push_live_report_to_sheet import push as sheet_push

            report_dir = SMALLCAP_ROOT / "result" / "live_report"
            sheet_push(report_dir)
            ts = mark_sheet_push(project_id)
            return {
                "ok": True,
                "sheet_url": PROJECTS[project_id]["sheet_url"],
                "pushed_at": ts,
            }
        return {"ok": False, "error": "this project has no Sheet push"}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, str(e)) from e


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8765, reload=False)
