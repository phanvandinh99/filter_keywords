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

# ── Global job state ──────────────────────────────────────────────────────
from collections import deque
_msg_queue: queue.Queue = queue.Queue()
_stop_event: threading.Event = threading.Event()
_job_running: bool = False
_log_buffer: deque = deque(maxlen=200)   # buffer 200 log gan nhat (cho polling fallback)

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
    import time as _time
    entry = {"type": "log", "level": level, "text": text,
             "ts": _time.strftime("%H:%M:%S")}
    _log_buffer.append(entry)
    push(entry)

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

@app.get("/api/seed")
async def api_get_seed():
    input_file = BASE_DIR / "input_keywords.txt"
    if input_file.exists():
        return {"content": input_file.read_text(encoding="utf-8")}
    return {"content": ""}

@app.post("/api/seed")
async def api_save_seed(body: Dict[str, str]):
    input_file = BASE_DIR / "input_keywords.txt"
    input_file.write_text(body.get("content", ""), encoding="utf-8")
    return {"ok": True}

@app.get("/api/ip-info")
def api_ip_info():
    import urllib.request
    import json
    
    sources = [
        ("https://api.ip.sb/geoip", lambda d: (d.get("ip"), d.get("country_code"), d.get("country"))),
        ("http://ip-api.com/json", lambda d: (d.get("query"), d.get("countryCode"), d.get("country"))),
        ("https://ipapi.co/json/", lambda d: (d.get("ip"), d.get("country_code"), d.get("country_name")))
    ]
    
    country_code_map = {
        "VN": "Việt Nam", "US": "Mỹ", "CN": "Trung Quốc", "JP": "Nhật Bản",
        "KR": "Hàn Quốc", "KP": "Triều Tiên", "SG": "Singapore", "TH": "Thái Lan",
        "MY": "Malaysia", "PH": "Philippines", "ID": "Indonesia", "KH": "Campuchia",
        "LA": "Lào", "MM": "Myanmar", "HK": "Hồng Kông", "TW": "Đài Loan",
        "GB": "Anh Quốc", "FR": "Pháp", "DE": "Đức", "IT": "Ý", "RU": "Nga",
        "AU": "Úc", "CA": "Canada", "IN": "Ấn Độ", "BR": "Brazil", "ES": "Tây Ban Nha",
        "PT": "Bồ Đào Nha", "NL": "Hà Lan", "BE": "Bỉ", "CH": "Thụy Sĩ",
        "SE": "Thụy Điển", "NO": "Na Uy", "DK": "Đan Mạch", "FI": "Phần Lan",
        "PL": "Ba Lan", "UA": "Ukraine", "GR": "Hy Lạp", "TR": "Thổ Nhĩ Kỳ",
        "ZA": "Nam Phi", "NZ": "New Zealand", "IE": "Ireland", "AT": "Áo",
        "MX": "Mexico", "AR": "Argentina", "CL": "Chile", "CO": "Colombia",
        "PE": "Peru", "SA": "Ả Rập Xê Út", "AE": "UAE", "IL": "Israel", "EG": "Ai Cập",
        "MO": "Macao", "PK": "Pakistan", "BD": "Bangladesh", "LK": "Sri Lanka",
        "KZ": "Kazakhstan", "UZ": "Uzbekistan", "RO": "Romania", "HU": "Hungary",
        "CZ": "Cộng hòa Séc", "SK": "Slovakia", "HR": "Croatia", "BG": "Bulgaria"
    }
    
    for url, parser in sources:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=3) as response:
                data = json.loads(response.read().decode('utf-8'))
                ip, country_code, country = parser(data)
                if ip:
                    code_upper = country_code.upper() if country_code else ""
                    country_vn = country_code_map.get(code_upper, country or "Không rõ")
                    return {"ip": ip, "country": country_vn}
        except Exception:
            continue
            
    return {"ip": "Không thể lấy IP", "country": "Không rõ"}

# ── GetNewKeywords files ───────────────────────────────────────────────────────

GETNEW_DIR = BASE_DIR / "getnewkeywords"
KEYWORDS_FILE = GETNEW_DIR / "keywords.txt"          # danh sách từ khóa đã có
KEYWORDS_PRIORITY_FILE = GETNEW_DIR / "priority_titles.txt"

def load_priority_list() -> List[str]:
    if not KEYWORDS_PRIORITY_FILE.exists():
        default_list = [
            "*",
            "安卓版-2265安卓网",
            "2265安卓网",
            "2265",
            "安卓版",
            "手机版",
            "网页版",
            "iOS版",
            "下载",
            "下载安装",
            "免费下载",
            "官方版下载",
            "登录",
            "入口",
            "网页入口",
            "官网入口",
            "链接",
            "试玩",
            "在线试玩",
            "观看",
            "直播",
            "免费领取",
            "怎么下载",
            "在哪买",
            "获取",
            "获取步骤",
            "官方",
            "官方版",
            "官网",
            "正版",
            "纯净版",
            "绿色版",
            "免费",
            "免费版",
            "无插件",
            "无限金币",
            "最新",
            "最新版",
            "2024最新",
            "2025最新版",
            "2026最新版",
            "V",
            "v",
            "V.",
            "v."
        ]
        GETNEW_DIR.mkdir(exist_ok=True)
        KEYWORDS_PRIORITY_FILE.write_text("\n".join(default_list), encoding="utf-8")
        return default_list
    content = KEYWORDS_PRIORITY_FILE.read_text(encoding="utf-8")
    return [line.strip() for line in content.splitlines() if line.strip()]

@app.get("/api/getnew-keywords")
async def api_get_getnew_keywords():
    result = {}
    if KEYWORDS_FILE.exists():
        result["keywords"] = KEYWORDS_FILE.read_text(encoding="utf-8")
    else:
        result["keywords"] = ""
    
    # Đọc thêm file priority_titles.txt (nếu chưa có sẽ tự động khởi tạo)
    load_priority_list()
    result["priority_titles"] = KEYWORDS_PRIORITY_FILE.read_text(encoding="utf-8")
    return result

class GetnewKeywordsRequest(BaseModel):
    keywords: Optional[str] = None
    priority_titles: Optional[str] = None

@app.post("/api/getnew-keywords")
async def api_save_getnew_keywords(req: GetnewKeywordsRequest):
    GETNEW_DIR.mkdir(exist_ok=True)
    saved = []
    if req.keywords is not None:
        KEYWORDS_FILE.write_text(req.keywords, encoding="utf-8")
        lines = [l for l in req.keywords.splitlines() if l.strip()]
        saved.append(f"keywords.txt ({len(lines)} từ khóa)")
        push_log(f"✅ Đã lưu keywords.txt — {len(lines)} từ khóa", "success")
    if req.priority_titles is not None:
        KEYWORDS_PRIORITY_FILE.write_text(req.priority_titles, encoding="utf-8")
        lines_prio = [l for l in req.priority_titles.splitlines() if l.strip()]
        saved.append(f"priority_titles.txt ({len(lines_prio)} điều kiện lọc)")
        push_log(f"✅ Đã lưu priority_titles.txt — {len(lines_prio)} điều kiện lọc", "success")
    return {"ok": True, "saved": saved}

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
    # 1. Update config.py on disk safely using lambda to avoid backslash escaping issues (e.g. \U in Windows path)
    cfg_path = BASE_DIR / "config.py"
    content = cfg_path.read_text(encoding="utf-8")
    content = re.sub(
        r'(PROFILE_PATH\s*=\s*os\.environ\.get\(\s*"PROFILE_PATH",\s*)r"[^"]*"',
        lambda m: f'{m.group(1)}r"{req.profile_path}"', content)
    content = re.sub(
        r'(CHROME_PATH\s*=\s*os\.environ\.get\(\s*"CHROME_PATH",\s*)r"[^"]*"',
        lambda m: f'{m.group(1)}r"{req.chrome_path}"', content)
    cfg_path.write_text(content, encoding="utf-8")

    # 2. Update variables in memory for the currently running server process
    import sys
    import config
    config.PROFILE_PATH = req.profile_path
    config.CHROME_PATH = req.chrome_path

    # Propagate changes to other loaded modules that imported them at startup
    modules_to_update = ["search_keywords", "google_search", "sogou_search", "domain_extractor"]
    for mod_name in modules_to_update:
        if mod_name in sys.modules:
            mod = sys.modules[mod_name]
            if hasattr(mod, "PROFILE_PATH"):
                mod.PROFILE_PATH = req.profile_path
            if hasattr(mod, "CHROME_PATH"):
                mod.CHROME_PATH = req.chrome_path

    push_log(f"⚙️ Đã cập nhật cấu hình Chrome thành công! Cửa sổ tìm kiếm sẽ sử dụng đường dẫn mới.", "success")
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

class WSStream:
    def __init__(self, original_stream):
        self.original_stream = original_stream
    def write(self, text):
        self.original_stream.write(text)
        text_str = text.strip()
        if text_str:
            level = "info"
            if "✅" in text_str: level = "success"
            elif "❌" in text_str or "🚫" in text_str: level = "error"
            elif "⚠️" in text_str: level = "warning"
            elif "🚀" in text_str: level = "search"
            push_log(text_str, level)
    def flush(self):
        self.original_stream.flush()

# ── Zhannei scraper ────────────────────────────────────────────────────────────

import re as _re

ZHANNEI_STRIP_SUFFIXES = [
    r'官方版下载$', r'官方版免费版$', r'官方版官方版$', r'免费版下载$',
    r'官方版$', r'免费版$', r'最新版$', r'安卓版$',
    r'版下载$', r'版免费版$', r'版官方版$', r'下载$',
]

def _extract_keyword(title: str) -> str:
    """Lấy phần trước dấu '-' đầu tiên rồi strip các suffix quảng cáo."""
    if not title:
        return ''
    # Lấy phần trước '-' đầu tiên
    part = title.split('-')[0].strip()
    # Strip các suffix biết trước
    for pattern in ZHANNEI_STRIP_SUFFIXES:
        part = _re.sub(pattern, '', part).strip()
    return part

def _extract_base_domain(showurl: str) -> str:
    """Trích base domain từ c-showurl text. Ví dụ: 'mobile.szetnl.com/...' → 'szetnl.com'"""
    if not showurl:
        return ''
    # Lấy phần trước space (bỏ ngày)
    host_part = showurl.split(' ')[0].strip()
    # Lấy hostname (bỏ path)
    hostname = host_part.split('/')[0]
    # Lấy 2 phần cuối (base domain)
    parts = hostname.split('.')
    if len(parts) >= 2:
        return '.'.join(parts[-2:])
    return hostname

def _fetch_with_stop(url: str, headers: dict, timeout: int = 12):
    """Fetch URL trong sub-thread, poll _stop_event moi 0.5s.
    Tra ve html string, None neu bi dung, raise Exception neu loi mang.
    """
    import urllib.request as _ur
    result: dict = {}

    def _do(u=url, h=headers, out=result):
        try:
            req = _ur.Request(u, headers=h)
            with _ur.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                cs = resp.headers.get_content_charset() or 'utf-8'
                out['html'] = raw.decode(cs, errors='replace')
        except Exception as ex:
            out['error'] = str(ex)

    t = threading.Thread(target=_do, daemon=True)
    t.start()
    while t.is_alive():
        if _stop_event.wait(timeout=0.5):
            return None          # User nhan Dung
    t.join(timeout=0.1)
    if _stop_event.is_set():
        return None
    if 'error' in result:
        raise Exception(result['error'])
    return result.get('html', '')


def _is_golink_error(err_msg: str) -> bool:
    keywords = [
        "getaddrinfo", "Name or service", "ERR_NAME_NOT_RESOLVED",
        "timed out", "timeout", "Connection refused", "No route",
        "Network is unreachable", "urlopen error",
    ]
    return any(k.lower() in err_msg.lower() for k in keywords)


def _run_zhannei(domains: List[str], suffix: str, max_pages: int) -> None:
    """Cào ket qua tu zhannei.baidu.com, ho tro dung nhanh."""
    global _job_running
    total_found = 0

    # Dedup domains
    seen_d: set = set()
    unique_domains: List[str] = []
    for d in domains:
        d = d.strip()
        if d and d not in seen_d:
            seen_d.add(d)
            unique_domains.append(d)
    skipped = len(domains) - len(unique_domains)

    import urllib.parse
    import time

    _H = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        ),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }

    try:
        push_log(f"\U0001f577 Zhannei: {len(unique_domains)} domain, suffix='{suffix}'", "info")
        if skipped:
            push_log(f"\u26a0\ufe0f Da bo qua {skipped} domain trung lap", "warning")

        # ── Pre-flight: thu tim kiem that de xac nhan GOLINK hoat dong ──
        push_log("\U0001f50d Kiem tra GOLINK / ket noi zhannei...", "info")
        _PREFLIGHT_URL = (
            "https://zhannei.baidu.com/cse/site"
            "?q=baidu.com+app&click=1&s=&nsid="
        )
        try:
            _pf = _fetch_with_stop(
                _PREFLIGHT_URL,
                _H,
                timeout=8,
            )
        except Exception as _pf_err:
            _em = str(_pf_err)
            _msg = (
                "\u274c Khong ket noi duoc zhannei.baidu.com\n"
                "\u27a1 Vui long bat app GOLINK roi thu lai!"
                if _is_golink_error(_em)
                else f"\u274c Loi ket noi: {_em}"
            )
            push_log(_msg, "error")
            push({"type": "zhannei_error", "message": _msg})
            push({"type": "zhannei_done", "total_domains": 0, "total_results": 0})
            return

        if _pf is None:           # User nhan Dung trong khi pre-flight
            push_log("\u23f9 Da dung.", "warning")
            push({"type": "zhannei_done", "total_domains": 0, "total_results": 0})
            return

        # Kiem tra noi dung: can co chu Han hoac result block
        # Neu khong co → zhannei khong tra ket qua → can GOLINK
        import re as _re_pf
        _has_chinese = bool(_re_pf.search(r'[\u4e00-\u9fff]', _pf))
        _has_results = '<div class="result' in _pf or 'class="c-showurl"' in _pf
        if not _has_chinese and not _has_results:
            _msg = (
                "\u274c Zhannei khong tra ve du lieu tieng Trung.\n"
                "\u27a1 Vui long bat app GOLINK roi thu lai!"
            )
            push_log(_msg, "error")
            push({"type": "zhannei_error", "message": _msg})
            push({"type": "zhannei_done", "total_domains": 0, "total_results": 0})
            return

        push_log("\u2705 GOLINK OK \u2014 bat dau cao du lieu...", "success")

        # ── Main loop ──────────────────────────────────────────────────
        for domain in unique_domains:
            if _stop_event.is_set():
                push_log("\u23f9 Dung theo yeu cau.", "warning")
                break

            push_log(f"\U0001f310 {domain} + '{suffix}'", "info")
            push({"type": "zhannei_sep", "domain": domain, "suffix": suffix})
            domain_count = 0

            for page_idx in range(max_pages):
                if _stop_event.is_set():
                    push_log("\u23f9 Dung.", "warning")
                    break

                q = f"{domain} {suffix}"
                url = (
                    f"https://zhannei.baidu.com/cse/site"
                    f"?q={urllib.parse.quote_plus(q)}&click=1&s=&nsid="
                    if page_idx == 0
                    else
                    f"https://zhannei.baidu.com/cse/site"
                    f"?q={urllib.parse.quote(q)}&p={page_idx}&nsid=&cc="
                )
                push_log(f"  \U0001f4c4 Trang {page_idx + 1}", "info")

                try:
                    html_text = _fetch_with_stop(url, _H, timeout=12)
                except Exception as ex:
                    err_s = str(ex)
                    if _is_golink_error(err_s):
                        push_log(
                            "\u274c Mat ket noi \u2014 Vui long kiem tra GOLINK!",
                            "error",
                        )
                        push({"type": "zhannei_error",
                              "message": "\u274c Mat ket noi \u2014 Vui long bat GOLINK!"})
                        push({"type": "zhannei_done",
                              "total_domains": len(unique_domains),
                              "total_results": total_found})
                        return
                    push_log(f"  \u274c Loi: {err_s}", "error")
                    break

                if html_text is None:           # Dung giua trang
                    push_log("\u23f9 Dung giua trang, giu ket qua.", "warning")
                    break

                # Parse result blocks
                starts = [m.start() for m in _re.finditer(r'<div class="result\b', html_text)]
                if not starts:
                    push_log(f"  \u26a0\ufe0f Khong co ket qua trang {page_idx + 1}", "warning")
                    break

                footer_m = _re.search(r'id="pageFooter"', html_text)
                area_end = footer_m.start() if footer_m else len(html_text)
                page_count = 0

                for i, start in enumerate(starts):
                    if start >= area_end:
                        break
                    end = starts[i + 1] if i + 1 < len(starts) else area_end
                    block = html_text[start:end]

                    tm = _re.search(r'cpos=["\']title["\'][^>]*>(.*?)</a>', block, _re.DOTALL)
                    if not tm:
                        continue
                    title = _re.sub(r'<[^>]+>', '', tm.group(1))
                    title = _re.sub(r'\s+', ' ', title).strip()
                    keyword = _extract_keyword(title)

                    sm = _re.search(r'class="c-showurl">(.*?)</span>', block, _re.DOTALL)
                    showurl = _re.sub(r'<[^>]+>', '', sm.group(1)).strip() if sm else ''
                    base_domain = _extract_base_domain(showurl) or domain

                    if keyword:
                        push({"type": "zhannei_result",
                              "keyword": keyword, "title": title, "domain": base_domain})
                        domain_count += 1
                        total_found += 1
                        page_count += 1

                push_log(f"  \u2705 Trang {page_idx + 1}: {page_count} ket qua", "success")

                if not _re.search(r'class="pager-next-foot', html_text):
                    break
                time.sleep(0.3)

            push_log(f"\u2705 {domain}: {domain_count} ket qua", "success")

        push_log(
            f"\U0001f389 Xong! {total_found} ket qua / {len(unique_domains)} domain",
            "success",
        )
        push({"type": "zhannei_done",
              "total_domains": len(unique_domains), "total_results": total_found})

    except Exception as e:
        push_log(f"\u274c Loi Zhannei: {e}", "error")
        push({"type": "zhannei_done",
              "total_domains": len(unique_domains), "total_results": total_found})
    finally:
        _job_running = False



class ZhanneiRequest(BaseModel):
    domains: List[str]
    suffix: str = "app"
    max_pages: int = 2


@app.post("/api/zhannei")
async def api_zhannei(req: ZhanneiRequest):
    global _job_running
    if _job_running:
        raise HTTPException(409, "Đang có tác vụ chạy. Nhấn Dừng trước.")
    if not req.domains:
        raise HTTPException(400, "Vui lòng nhập ít nhất một domain")
    _job_running = True
    _stop_event.clear()
    push_log(f"\U0001f577 Bat dau Zhannei \u2014 {len(req.domains)} domain, suffix='{req.suffix}'", "info")
    threading.Thread(
        target=_run_zhannei,
        args=(req.domains, req.suffix, min(req.max_pages, 10)),
        daemon=True
    ).start()
    return {"ok": True}

@app.get("/api/zhannei/status")
async def api_zhannei_status(since: int = 0):
    """Polling fallback: tra ve trang thai job va log buffer tu vi tri 'since'."""
    logs = list(_log_buffer)
    return {
        "running": _job_running,
        "log_count": len(logs),
        "logs": logs[since:],   # chi tra logs moi (tu index 'since')
    }

def _run_auto_baidu_keywords_only(headless: bool = False) -> None:
    """Chỉ chạy Bước 1+2: lấy gợi ý keywords từ Baidu và ghi vào bảng. KHÔNG tìm title."""
    global _job_running
    done_pushed = False
    try:

        _stop_event.clear()

        push_log("🚀 [Bước 1/2] Đang lấy gợi ý từ khóa mới từ Baidu (Scraper)...", "info")
        import sys
        _GETNEWKEYWORDS_DIR = BASE_DIR / "getnewkeywords"
        if str(_GETNEWKEYWORDS_DIR) not in sys.path:
            sys.path.insert(0, str(_GETNEWKEYWORDS_DIR))

        from auto_browser_scraper import run as getnewkeywords_run

        original_stdout = sys.stdout
        sys.stdout = WSStream(original_stdout)
        try:
            import auto_browser_scraper
            auto_browser_scraper.interrupted = False
            new_kws = getnewkeywords_run() or set()
        finally:
            sys.stdout = original_stdout

        if _stop_event.is_set() or auto_browser_scraper.interrupted:
            push_log("⏹ Đã dừng theo yêu cầu.", "warning")
            return

        if not new_kws:
            push_log("⚠️ Không lấy được từ khóa gợi ý nào mới (hoặc đã trùng hết).", "warning")
            push_done(0, 0, 0, 0)
            done_pushed = True
            return

        keywords_list = sorted(list(new_kws))
        push_log(f"✅ Đã lấy gợi ý thành công! Nhận được {len(keywords_list)} từ khóa mới.", "success")

        # Bước 2: Ghi keywords vào bảng (không tìm title)
        push_log(f"📝 [Bước 2/2] Đang cập nhật {len(keywords_list)} từ khóa vào bảng...", "info")
        new_rows = []
        for i, kw in enumerate(keywords_list, 1):
            new_rows.append({
                "stt": i,
                "keyword": kw,
                "title": "",
                "domain": "",
                "time_tag": "",
                "main_title": ""
            })
        write_data({"rows": new_rows})

        try:
            from utils import write_keywords_to_excel
            write_keywords_to_excel(keywords_list)
        except Exception as ex:
            push_log(f"⚠️ Không thể lưu đồng bộ vào Excel: {ex}", "warning")

        # Refresh UI grid
        push({"type": "refresh_data", "rows": new_rows})
        push_log(f"✅ Hoàn thành! Đã thêm {len(keywords_list)} từ khóa mới vào bảng. Bạn có thể nhấn Baidu để tìm title tiếp.", "success")
        push_done(len(keywords_list), 0, 0, len(keywords_list))
        done_pushed = True

    except Exception as e:
        push_log(f"❌ Lỗi trong luồng tự động: {e}", "error")
    finally:
        _job_running = False
        if not done_pushed:
            push_done(0, 0, 0, 0)


def _run_auto_baidu(headless: bool = False) -> None:
    global _job_running
    done_pushed = False
    try:
        _stop_event.clear()

        push_log("🚀 [Bước 1/3] Đang lấy gợi ý từ khóa mới từ Baidu (Scraper)...", "info")
        import sys
        _GETNEWKEYWORDS_DIR = BASE_DIR / "getnewkeywords"
        if str(_GETNEWKEYWORDS_DIR) not in sys.path:
            sys.path.insert(0, str(_GETNEWKEYWORDS_DIR))
            
        from auto_browser_scraper import run as getnewkeywords_run
        
        original_stdout = sys.stdout
        sys.stdout = WSStream(original_stdout)
        try:
            # Reset global interrupted state in scraper just in case
            import auto_browser_scraper
            auto_browser_scraper.interrupted = False
            new_kws = getnewkeywords_run() or set()
        finally:
            sys.stdout = original_stdout

        if _stop_event.is_set() or auto_browser_scraper.interrupted:
            push_log("⏹ Đã dừng theo yêu cầu.", "warning")
            return

        if not new_kws:
            push_log("⚠️ Không lấy được từ khóa gợi ý nào mới (hoặc đã trùng hết).", "warning")
            push_done(0, 0, 0, 0)
            done_pushed = True
            return

        keywords_list = sorted(list(new_kws))
        push_log(f"✅ Đã lấy gợi ý thành công! Nhận được {len(keywords_list)} từ khóa mới.", "success")
        
        # Step 2: Write to json and Excel
        push_log("📝 [Bước 2/3] Đang cập nhật từ khóa gợi ý vào bảng...", "info")
        new_rows = []
        for i, kw in enumerate(keywords_list, 1):
            new_rows.append({
                "stt": i,
                "keyword": kw,
                "title": "",
                "domain": "",
                "time_tag": "",
                "main_title": ""
            })
        write_data({"rows": new_rows})
        
        try:
            from utils import write_keywords_to_excel
            write_keywords_to_excel(keywords_list)
        except Exception as ex:
            push_log(f"⚠️ Không thể lưu đồng bộ vào Excel: {ex}", "warning")

        # Refresh the UI grid rows
        push({"type": "refresh_data", "rows": new_rows})
        
        # Step 3: Baidu search
        push_log(f"🔍 [Bước 3/3] Đang tự động tìm kiếm Baidu cho {len(keywords_list)} từ khóa mới...", "info")
        
        kw_to_idx = {kw.strip(): i for i, kw in enumerate(keywords_list)}
        save_counter = [0]
        
        def on_progress(idx: int, total: int, kw: str) -> None:
            push_progress(idx, total, kw)

        def on_result(idx: int, kw: str, result: tuple) -> None:
            title, domain, time_tag, *_ = result
            row_idx = kw_to_idx.get(kw.strip(), -1)
            push_result(row_idx, kw, result)
            if row_idx >= 0 and row_idx < len(new_rows):
                new_rows[row_idx]["title"] = title or ""
                new_rows[row_idx]["domain"] = domain or ""
                new_rows[row_idx]["time_tag"] = time_tag or ""
            save_counter[0] += 1
            if save_counter[0] % 10 == 0:
                write_data({"rows": new_rows})

        from search_keywords import search_keywords as _fn
        _fn(keywords_list, on_progress=on_progress, on_result=on_result, stop_event=_stop_event,
            headless=headless, location="default")
        write_data({"rows": new_rows})

        # Lọc danh sách kết quả theo độ ưu tiên của title
        push_log("🎯 Đang lọc từ khóa dựa trên danh sách tiêu đề ưu tiên...", "info")
        priority_list = load_priority_list()
        has_wildcard = "*" in priority_list

        filtered_rows = []
        if has_wildcard:
            filtered_rows = new_rows
            push_log("ℹ️ Phát hiện ký tự '*' trong danh sách ưu tiên, giữ lại toàn bộ từ khóa.", "info")
        else:
            for row in new_rows:
                title = row.get("title", "")
                kw = row.get("keyword", "")

                # Giữ lại nếu là Lỗi hoặc Trùng lặp để người dùng theo dõi trạng thái lỗi/trùng
                if title == "Trùng lặp từ khóa" or title.startswith("Lỗi"):
                    filtered_rows.append(row)
                    continue

                # Kiểm tra title có chứa bất kỳ từ khóa ưu tiên nào không
                match = False
                for p_word in priority_list:
                    if p_word and p_word in title:
                        match = True
                        break

                if match:
                    filtered_rows.append(row)
                else:
                    push_log(f"🗑️ Loại bỏ '{kw}' do tiêu đề '{title}' không chứa từ khóa ưu tiên.", "warning")

        # Cập nhật lại số thứ tự
        for idx, r in enumerate(filtered_rows, 1):
            r["stt"] = idx

        write_data({"rows": filtered_rows})

        try:
            from utils import write_keywords_to_excel
            write_keywords_to_excel([r["keyword"] for r in filtered_rows if r.get("title") and not r.get("title", "").startswith("Lỗi") and r.get("title") != "Trùng lặp từ khóa"])
        except Exception as ex:
            push_log(f"⚠️ Không thể lưu đồng bộ vào Excel: {ex}", "warning")

        # Refresh UI
        push({"type": "refresh_data", "rows": filtered_rows})


        success = sum(1 for r in filtered_rows if r.get("title") and not str(r.get("title", "")).startswith("Lỗi") and r.get("title") != "Trùng lặp từ khóa")
        errors = sum(1 for r in filtered_rows if str(r.get("title", "")).startswith("Lỗi"))
        dupes = sum(1 for r in filtered_rows if r.get("title") == "Trùng lặp từ khóa")
        push_done(success, errors, dupes, len(filtered_rows))
        done_pushed = True

    except Exception as e:
        push_log(f"❌ Lỗi trong luồng tự động: {e}", "error")
    finally:
        _job_running = False
        if not done_pushed:
            push_done(0, 0, 0, 0)

def _run_search(action: str, keywords: List[str], headless: bool = False, location: str = "default") -> None:
    global _job_running
    done_pushed = False
    try:
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

        kwargs = dict(on_progress=on_progress, on_result=on_result, stop_event=_stop_event,
                      headless=headless, location=location)

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
        done_pushed = True

    except Exception as e:
        push_log(f"❌ Lỗi nghiêm trọng: {e}", "error")
    finally:
        _job_running = False
        if not done_pushed:
            push_done(0, 0, 0, 0)

class SearchRequest(BaseModel):
    headless: bool = False
    location: str = "default"

@app.post("/api/search/{action}")
async def api_search(action: str, req: SearchRequest = None):
    global _job_running
    if _job_running:
        raise HTTPException(409, "Đang có tác vụ chạy. Nhấn Dừng trước.")
    
    _job_running = True
    headless = req.headless if req else False
    mode = "ẩn Chrome" if headless else "hiện Chrome"

    if action == "auto_baidu":
        input_file = BASE_DIR / "input_keywords.txt"
        if not input_file.exists():
            _job_running = False
            with open(input_file, 'w', encoding='utf-8') as f:
                f.write("# Danh sách từ khóa cần lấy gợi ý từ Baidu (mỗi từ khóa một dòng)\n")
            raise HTTPException(400, "Không tìm thấy từ khóa mới! Đã tạo file mẫu, vui lòng thêm từ khóa mới.")

        # Check if there are valid non-comment keywords
        lines = [l.strip() for l in input_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        words = [l for l in lines if not l.startswith("#")]
        if not words:
            _job_running = False
            raise HTTPException(400, "Danh sách từ khóa mới đang trống! Vui lòng nhập từ khóa mới.")

        push_log(f"🚀 Bắt đầu Tự Động: Lấy gợi ý + Tìm kiếm Baidu ({mode})", "info")
        threading.Thread(target=_run_auto_baidu, args=(headless,), daemon=True).start()
        return {"ok": True, "total": 0}

    if action == "auto_baidu_keywords_only":
        input_file = BASE_DIR / "input_keywords.txt"
        if not input_file.exists():
            _job_running = False
            with open(input_file, 'w', encoding='utf-8') as f:
                f.write("# Danh sách từ khóa cần lấy gợi ý từ Baidu (mỗi từ khóa một dòng)\n")
            raise HTTPException(400, "Không tìm thấy từ khóa mới! Đã tạo file mẫu, vui lòng thêm từ khóa mới.")

        lines = [l.strip() for l in input_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        words = [l for l in lines if not l.startswith("#")]
        if not words:
            _job_running = False
            raise HTTPException(400, "Danh sách từ khóa mới đang trống! Vui lòng nhập từ khóa mới.")

        push_log(f"🔑 Bắt đầu: Chỉ lấy Keywords mới từ Baidu (không tìm title) ({mode})", "info")
        threading.Thread(target=_run_auto_baidu_keywords_only, args=(headless,), daemon=True).start()
        return {"ok": True, "total": 0}

    data = read_data()
    keywords = [r["keyword"] for r in data.get("rows", []) if r.get("keyword", "").strip()]
    if not keywords:
        _job_running = False
        raise HTTPException(400, "Không có từ khóa để tìm kiếm")
    location = req.location if req else "default"
    push_log(f"🚀 Bắt đầu tìm kiếm [{action}] — {len(keywords)} từ khóa ({mode})", "info")
    threading.Thread(target=_run_search, args=(action, keywords, headless, location), daemon=True).start()
    return {"ok": True, "total": len(keywords)}

@app.post("/api/stop")
async def api_stop():
    _stop_event.set()
    try:
        import auto_browser_scraper
        auto_browser_scraper.interrupted = True
    except Exception:
        pass
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
