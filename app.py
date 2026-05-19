"""FastAPI Web Server — Keyword Tool UI"""
import asyncio
import io
import json
import logging
import queue
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_FILE = DATA_DIR / "keywords.json"
WEB_DIR = BASE_DIR / "web"
BANNED_FILE = BASE_DIR / "Bannedkeywords.txt"

# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(title="Keyword Tool", docs_url=None, redoc_url=None)

# Serve static files (css, js)
WEB_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

# ── Global job state ───────────────────────────────────────────────────────────
_msg_queue: queue.Queue = queue.Queue()
_stop_event: threading.Event = threading.Event()
_job_running: bool = False

# ── JSON helpers ───────────────────────────────────────────────────────────────

def read_data() -> Dict[str, Any]:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"rows": []}


def write_data(data: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DATA_FILE)

# ── WebSocket push helpers ─────────────────────────────────────────────────────

def push(msg: dict) -> None:
    _msg_queue.put(msg)

def push_log(text: str, level: str = "info") -> None:
    push({"type": "log", "level": level, "text": text})

def push_progress(current: int, total: int, keyword: str) -> None:
    pct = int(current / total * 100) if total > 0 else 0
    push({"type": "progress", "current": current, "total": total,
          "keyword": keyword, "pct": pct})

def push_result(row_idx: int, keyword: str, result: tuple) -> None:
    title, domain, time_tag, is_processed, added_text, original_title = result
    push({"type": "result", "row_idx": row_idx,
          "keyword": keyword, "title": title or "",
          "domain": domain or "", "time_tag": time_tag or ""})

def push_done(success: int, error: int, duplicate: int, total: int) -> None:
    push({"type": "done", "success": success, "error": error,
          "duplicate": duplicate, "total": total})

# ── Custom log handler → WebSocket ─────────────────────────────────────────────

class _WSLogHandler(logging.Handler):
    _LEVEL_MAP = {"DEBUG": "info", "INFO": "info",
                  "WARNING": "warning", "ERROR": "error", "CRITICAL": "error"}

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            level = self._LEVEL_MAP.get(record.levelname, "info")
            if msg.startswith("✅"): level = "success"
            elif msg.startswith("❌"): level = "error"
            elif msg.startswith("⚠️"): level = "warning"
            elif msg.startswith("🔍"): level = "search"
            push_log(msg, level)
        except Exception:
            pass

_ws_handler = _WSLogHandler()
_ws_handler.setFormatter(logging.Formatter("%(message)s"))
logging.getLogger().addHandler(_ws_handler)

# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse(str(WEB_DIR / "index.html"))

@app.get("/api/keywords")
async def api_get_keywords():
    return read_data()

class SaveRequest(BaseModel):
    rows: List[Dict[str, Any]]

@app.post("/api/keywords")
async def api_save_keywords(req: SaveRequest):
    rows = req.rows
    for i, row in enumerate(rows, 1):
        row["stt"] = i
    write_data({"rows": rows})
    return {"ok": True, "count": len(rows)}

# ── Dedup & filter ─────────────────────────────────────────────────────────────

@app.post("/api/keywords/deduplicate")
async def api_deduplicate():
    data = read_data()
    rows = data.get("rows", [])
    seen: set = set()
    unique: List[dict] = []
    removed = 0
    for row in rows:
        kw = row.get("keyword", "").strip()
        if kw and kw not in seen:
            seen.add(kw)
            unique.append(row)
        elif kw:
            removed += 1
    for i, row in enumerate(unique, 1):
        row["stt"] = i
    write_data({"rows": unique})
    push_log(f"✅ Loại bỏ {removed} từ khóa trùng lặp. Còn lại: {len(unique)}", "success")
    return {"ok": True, "removed": removed, "rows": unique}

@app.post("/api/keywords/filter-banned")
async def api_filter_banned():
    if not BANNED_FILE.exists():
        raise HTTPException(404, "Bannedkeywords.txt không tồn tại")
    banned = [l.strip() for l in BANNED_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
    data = read_data()
    rows = data.get("rows", [])
    filtered: List[dict] = []
    removed = 0
    for row in rows:
        kw = row.get("keyword", "").strip()
        title = row.get("title", "").strip()
        if any(b in kw or b in title for b in banned):
            removed += 1
        else:
            filtered.append(row)
    for i, row in enumerate(filtered, 1):
        row["stt"] = i
    write_data({"rows": filtered})
    push_log(f"✅ Xóa {removed} từ khóa chứa từ cấm. Còn lại: {len(filtered)}", "success")
    return {"ok": True, "removed": removed, "rows": filtered}

# ── Banned keywords management ─────────────────────────────────────────────────

@app.get("/api/banned")
async def api_get_banned():
    if BANNED_FILE.exists():
        return {"content": BANNED_FILE.read_text(encoding="utf-8")}
    return {"content": ""}

@app.post("/api/banned")
async def api_save_banned(body: Dict[str, str]):
    BANNED_FILE.write_text(body.get("content", ""), encoding="utf-8")
    return {"ok": True}

# ── Settings ───────────────────────────────────────────────────────────────────

@app.get("/api/settings")
async def api_get_settings():
    from config import PROFILE_PATH, CHROME_PATH, EXCEL_FILE
    return {"profile_path": PROFILE_PATH, "chrome_path": CHROME_PATH, "excel_file": EXCEL_FILE}

class SettingsRequest(BaseModel):
    profile_path: str
    chrome_path: str

@app.post("/api/settings")
async def api_save_settings(req: SettingsRequest):
    cfg_path = BASE_DIR / "config.py"
    content = cfg_path.read_text(encoding="utf-8")
    content = re.sub(
        r'(PROFILE_PATH\s*=\s*os\.environ\.get\(\s*"PROFILE_PATH",\s*)r"[^"]*"',
        f'\\1r"{req.profile_path}"', content)
    content = re.sub(
        r'(CHROME_PATH\s*=\s*os\.environ\.get\(\s*"CHROME_PATH",\s*)r"[^"]*"',
        f'\\1r"{req.chrome_path}"', content)
    cfg_path.write_text(content, encoding="utf-8")
    return {"ok": True}

# ── Import / Export ────────────────────────────────────────────────────────────

@app.post("/api/import")
async def api_import(file: UploadFile = File(...)):
    content = await file.read()
    try:
        df = pd.read_excel(io.BytesIO(content), header=None, dtype=str, skiprows=1)
    except Exception as e:
        raise HTTPException(400, f"Lỗi đọc file Excel: {e}")
    rows: List[dict] = []
    for i, (_, row) in enumerate(df.iterrows(), 1):
        def _v(col: int) -> str:
            try:
                v = row.iloc[col]
                return str(v).strip() if pd.notna(v) else ""
            except Exception:
                return ""
        kw = _v(0)
        if not kw or kw.lower() == "nan":
            continue
        rows.append({"stt": i, "keyword": kw, "title": _v(1),
                     "domain": _v(2), "time_tag": _v(3), "main_title": _v(5)})
    write_data({"rows": rows})
    push_log(f"✅ Import {len(rows)} từ khóa từ {file.filename}", "success")
    return {"ok": True, "rows": rows}

@app.get("/api/export")
async def api_export():
    from excel_writer import write_rows_to_excel
    data = read_data()
    rows = data.get("rows", [])
    if not rows:
        raise HTTPException(400, "Không có dữ liệu để export")
    out_path = BASE_DIR / "keywords_export.xlsx"
    write_rows_to_excel(rows, str(out_path))
    return FileResponse(str(out_path), filename="keywords_export.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ── Search actions ─────────────────────────────────────────────────────────────

def _run_search(action: str, keywords: List[str]) -> None:
    global _job_running
    try:
        _job_running = True
        _stop_event.clear()

        data = read_data()
        rows = data.get("rows", [])
        kw_to_idx = {r.get("keyword", "").strip(): i for i, r in enumerate(rows)}
        save_counter = [0]

        def on_progress(idx: int, total: int, kw: str) -> None:
            push_progress(idx, total, kw)

        def on_result(idx: int, kw: str, result: tuple) -> None:
            title, domain, time_tag, *_ = result
            row_idx = kw_to_idx.get(kw.strip(), -1)
            push_result(row_idx, kw, result)
            if row_idx >= 0 and row_idx < len(rows):
                rows[row_idx]["title"] = title or ""
                rows[row_idx]["domain"] = domain or ""
                rows[row_idx]["time_tag"] = time_tag or ""
            save_counter[0] += 1
            if save_counter[0] % 10 == 0:
                write_data({"rows": rows})

        kwargs = dict(on_progress=on_progress, on_result=on_result, stop_event=_stop_event)

        if action == "baidu":
            from search_keywords import search_keywords as _fn
        elif action == "baidu_detailed":
            from search_keywords import search_keywords_detailed as _fn
        elif action == "google":
            from google_search import search_google_keywords as _fn
        elif action == "sogou":
            from sogou_search import search_sogou_keywords as _fn
        else:
            push_log(f"❌ Hành động không hợp lệ: {action}", "error")
            return

        _fn(keywords, **kwargs)
        write_data({"rows": rows})

        success = sum(1 for r in rows if r.get("title") and not str(r.get("title", "")).startswith("Lỗi") and r.get("title") != "Trùng lặp từ khóa")
        errors = sum(1 for r in rows if str(r.get("title", "")).startswith("Lỗi"))
        dupes = sum(1 for r in rows if r.get("title") == "Trùng lặp từ khóa")
        push_done(success, errors, dupes, len(keywords))

    except Exception as e:
        push_log(f"❌ Lỗi nghiêm trọng: {e}", "error")
    finally:
        _job_running = False

@app.post("/api/search/{action}")
async def api_search(action: str):
    global _job_running
    if _job_running:
        raise HTTPException(409, "Đang có tác vụ chạy. Nhấn Dừng trước.")
    data = read_data()
    keywords = [r["keyword"] for r in data.get("rows", []) if r.get("keyword", "").strip()]
    if not keywords:
        raise HTTPException(400, "Không có từ khóa để tìm kiếm")
    push_log(f"🚀 Bắt đầu tìm kiếm [{action}] — {len(keywords)} từ khóa", "info")
    threading.Thread(target=_run_search, args=(action, keywords), daemon=True).start()
    return {"ok": True, "total": len(keywords)}

@app.post("/api/stop")
async def api_stop():
    _stop_event.set()
    push_log("⏹ Đang dừng tác vụ...", "warning")
    return {"ok": True}

@app.get("/api/status")
async def api_status():
    return {"running": _job_running}

# ── WebSocket ──────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            msgs: list = []
            try:
                while True:
                    msgs.append(_msg_queue.get_nowait())
            except queue.Empty:
                pass
            for msg in msgs:
                try:
                    await ws.send_json(msg)
                except Exception:
                    return
            await asyncio.sleep(0.05)
    except WebSocketDisconnect:
        pass

# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import webbrowser
    print("=" * 50)
    print("  Keyword Tool -- http://localhost:8000")
    print("  Nhan Ctrl+C de dung server")
    print("=" * 50)
    threading.Timer(1.5, lambda: webbrowser.open("http://localhost:8000")).start()
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
