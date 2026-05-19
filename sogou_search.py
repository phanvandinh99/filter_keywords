"""Module tìm kiếm từ khóa trên Sogou (sogou.com)"""
import json
import time
import random
import urllib.parse
from urllib.parse import urlparse
import logging
from pathlib import Path
from collections import deque
from typing import List, Tuple, Dict, Any
from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
    Error as PlaywrightError,
    Page,
)
from config import PROFILE_PATH, CHROME_PATH
from excel_writer import write_search_results
from constants import (
    TIMEOUTS,
    DELAYS,
    DEBUG_ARTIFACTS_DIR,
    DEBUG_ARTIFACTS_MAX_RESPONSES,
    DEBUG_ARTIFACTS_MAX_FAILED_REQUESTS,
    DEBUG_ARTIFACTS_MAX_CONSOLE,
)

logger = logging.getLogger()

# ── Browser config riêng cho Sogou ───────────────────────────────────────────
# Dùng User Agent PC thay vì mobile để tránh trang WAP React lazy-load
SOGOU_BROWSER_CONFIG = {
    "viewport": {"width": 1280, "height": 800},
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "args": [
        "--disable-blink-features=AutomationControlled",
        "--no-default-browser-check",
        "--disable-infobars",
        "--disable-dev-shm-usage",
        "--disable-gpu",
    ],
    "timeout": 30000,
}

# ── Selectors (từ HTML thực tế của Sogou WAP) ────────────────────────────────
# ── Selectors (hỗ trợ cả 2 layout: WAP và PC) ───────────────────────────────
# WAP layout: div.vrResult + a.resultLink + div.citeurl
# PC  layout: div.vrwrap  + h3.vr-title a + cite.citeLinkClass
SOGOU_SELECTORS = {
    "result_container": [
        "div.vrwrap",                   # PC – container chính (confirmed từ HTML)
        "div.sgresult",                 # PC – fallback
        "div.vrResult:not(.vr-topic)",  # WAP – bỏ qua suggestion ẩn
        "div.vrResult",                 # WAP – fallback
        "div.reactResult",              # WAP React SSR
    ],
    "title": [
        "h3.vr-title a",                # PC (confirmed từ HTML: <h3 class="vr-title"><a>)
        "h3.vrTitle a",                 # PC variant
        "a.resultLink",                 # WAP
        "div.vr-tit a",                 # WAP fallback
        "h3 a",                         # generic
        "h3",
    ],
    "domain": [
        "cite.citeLinkClass",           # PC (confirmed từ HTML)
        "cite",                         # PC/WAP fallback
        "div.citeurl",                  # WAP
        "div.flake-cite-url",           # WAP flake
    ],
}

SOGOU_URLS = {
    # Thêm params cố định để Sogou trả về đúng layout PC web
    # w=01029901: web search, p=40040100: PC layout, dp=1, s_from=result_up
    "search": "https://www.sogou.com/web?query={}&w=01029901&p=40040100&dp=1&s_from=result_up",
}

# Selector wait — bao gồm cả PC lẫn WAP, dùng state="attached"
_WAIT_SELECTOR = "div.vrwrap, div.vrResult, div.reactResult"
# Selector kết hợp để query_selector_all
_RESULT_SELECTOR = ", ".join(SOGOU_SELECTORS["result_container"])


def _extract_sogou_title(container: Any) -> str:
    """Trích xuất title từ Sogou search result"""
    for selector in SOGOU_SELECTORS["title"]:
        try:
            elem = container.query_selector(selector)
            if elem:
                title = (elem.text_content() or "").strip()
                if title:
                    return title
        except Exception:
            continue
    return ""


def _extract_sogou_domain(container: Any) -> str:
    """Trích xuất domain từ Sogou search result.
    Sogou PC dùng redirect URL (/link?url=...) nên lấy domain từ cite text,
    hoặc fallback lấy từ href trực tiếp nếu là URL thật.
    """
    # Phương pháp 1: cite / citeurl text (thường chứa domain dạng text)
    for selector in SOGOU_SELECTORS["domain"]:
        try:
            elem = container.query_selector(selector)
            if elem:
                text = (elem.text_content() or "").strip()
                if text and "sogou" not in text.lower():
                    if not text.startswith(("http://", "https://")):
                        text = "https://" + text
                    try:
                        netloc = urlparse(text).netloc
                        if netloc:
                            return netloc
                    except Exception:
                        return text
        except Exception:
            continue

    # Phương pháp 2: lấy từ href của link title
    # Sogou PC dùng /link?url=... (redirect) — bỏ qua, lấy href trực tiếp nếu có
    try:
        link = container.query_selector("h3 a, a.resultLink, a[href]")
        if link:
            href = link.get_attribute("href") or ""
            if href.startswith("http") and "sogou" not in href:
                return urlparse(href).netloc
            # Sogou redirect: /link?url=... — không thể resolve mà không follow
            # Thử lấy từ data-url hoặc attribute khác
            for attr in ["data-url", "data-href", "data-link"]:
                val = link.get_attribute(attr) or ""
                if val.startswith("http") and "sogou" not in val:
                    return urlparse(val).netloc
    except Exception:
        pass

    return ""


def _setup_debug_handlers(page: Page) -> Tuple[deque, deque, deque]:
    """Thiết lập các handler để thu thập debug info"""
    recent_responses: deque = deque(maxlen=DEBUG_ARTIFACTS_MAX_RESPONSES)
    recent_failed_requests: deque = deque(maxlen=DEBUG_ARTIFACTS_MAX_FAILED_REQUESTS)
    recent_console: deque = deque(maxlen=DEBUG_ARTIFACTS_MAX_CONSOLE)

    def _on_response(resp):
        try:
            recent_responses.append({"url": resp.url, "status": resp.status, "ok": resp.ok})
        except Exception:
            pass

    def _on_request_failed(req):
        try:
            recent_failed_requests.append({"url": req.url, "method": req.method, "resource_type": req.resource_type})
        except Exception:
            pass

    def _on_console(msg):
        try:
            recent_console.append({"type": msg.type, "text": msg.text})
        except Exception:
            pass

    page.on("response", _on_response)
    page.on("requestfailed", _on_request_failed)
    page.on("console", _on_console)

    return recent_responses, recent_failed_requests, recent_console


def _sanitize_filename(text: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_ ." else "_" for ch in text)
    return safe[:80] or "kw"


def _dump_artifacts(
    page: Page,
    keyword: str,
    phase: str,
    recent_responses: deque,
    recent_failed_requests: deque,
    recent_console: deque,
) -> None:
    """Lưu artifacts debug (screenshot, HTML, meta)"""
    try:
        ts = time.strftime("%Y%m%d-%H%M%S")
        base = f"{_sanitize_filename(keyword)}_{phase}_{ts}"
        out_dir = Path(DEBUG_ARTIFACTS_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)

        try:
            page.screenshot(path=str(out_dir / f"{base}.png"), full_page=True)
            logger.info(f"[🖼] Screenshot: {out_dir / f'{base}.png'}")
        except Exception:
            pass

        try:
            (out_dir / f"{base}.html").write_text(page.content(), encoding="utf-8")
            logger.info(f"[📄] HTML: {out_dir / f'{base}.html'}")
        except Exception:
            pass

        try:
            meta = {
                "keyword": keyword,
                "phase": phase,
                "url": getattr(page, "url", None),
                "recentResponses": list(recent_responses)[-10:],
                "recentFailedRequests": list(recent_failed_requests)[-10:],
                "recentConsole": list(recent_console)[-10:],
            }
            (out_dir / f"{base}.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass
    except Exception:
        pass


def _navigate_to_sogou(page: Page, url: str, keyword: str) -> bool:
    """Điều hướng đến Sogou search URL với retry logic"""
    try:
        _ = page.url
    except Exception:
        return False

    for retry in range(2):
        try:
            wait_until = "domcontentloaded" if retry == 0 else "load"
            timeout = TIMEOUTS["navigation"] if retry == 0 else TIMEOUTS["navigation_fallback"]
            page.goto(url, wait_until=wait_until, timeout=timeout)
            return True
        except PlaywrightTimeoutError:
            if retry == 1:
                try:
                    page.goto(url, wait_until="commit", timeout=TIMEOUTS["navigation_commit"])
                    page.wait_for_timeout(2000)
                    logger.warning(f"[⚠️] Dùng fallback navigation cho Sogou: {keyword}")
                    return True
                except Exception:
                    pass
            else:
                time.sleep(0.5)

    return False


def _wait_for_sogou_results(page: Page, keyword: str) -> bool:
    """Chờ kết quả Sogou xuất hiện — hỗ trợ cả PC (div.vrwrap) và WAP (div.vrResult).
    Dùng state='attached' vì một số div có display:none."""
    try:
        page.wait_for_selector(_WAIT_SELECTOR, timeout=TIMEOUTS["selector_visible"], state="attached")
        return True
    except PlaywrightTimeoutError as e:
        logger.error(f"[❌] Timeout chờ Sogou selector – {keyword}: {e}")
        return False


def _partial_match_score(keyword: str, title: str) -> int:
    """Tính điểm khớp một phần giữa keyword và title.

    Kết hợp 2 tiêu chí:
    1. longest_common_substring × 10  — ưu tiên chuỗi liên tiếp dài nhất
    2. common_chars_count             — tổng ký tự của keyword xuất hiện trong title

    Ví dụ: keyword='ayx入口官网', title='ayx官方入口(中国)官方网站'
      - longest common substring: 'ayx' = 3  → 3×10 = 30
      - common chars: a,y,x,入,口,官,网 đều có trong title → 7
      - score = 30 + 7 = 37

    title='ayx入口官...' (gần hơn):
      - longest: 'ayx入口官' = 6 → 6×10 = 60
      - common chars: 7 → 7
      - score = 67  ← cao hơn → được chọn
    """
    kw = keyword.lower()
    t = title.lower()
    if not kw or not t:
        return 0

    kw_len = len(kw)

    # 1. Longest common substring
    longest = 0
    for length in range(kw_len, 0, -1):
        found = False
        for start in range(kw_len - length + 1):
            if kw[start:start + length] in t:
                found = True
                break
        if found:
            longest = length
            break

    # 2. Tổng ký tự của keyword xuất hiện trong title (không tính trùng)
    t_chars = list(t)
    common = 0
    for ch in kw:
        if ch in t_chars:
            t_chars.remove(ch)  # mỗi ký tự trong title chỉ dùng 1 lần
            common += 1

    return longest * 10 + common


def _similarity_pct(keyword: str, title: str) -> float:
    """Tính % tương đồng giữa keyword và title.

    Dùng LCS (longest common subsequence) để đo % ký tự keyword
    xuất hiện theo đúng thứ tự trong title — không cần liên tiếp.

    Ví dụ: keyword='ayx入口官网', title='ayx官方入口(中国)官方网站'
    → LCS = 'ayx入口官' (6/7 = 86%) ← được chọn

    Bắt buộc có ít nhất 1 chuỗi liên tiếp >= 2 ký tự để tránh match ngẫu nhiên.
    """
    kw = keyword.strip().lower()
    t = title.strip().lower()
    if not kw or not t:
        return 0.0

    kw_len = len(kw)

    # Điều kiện bắt buộc: phải có chuỗi liên tiếp >= 2 ký tự
    has_substr = any(
        kw[s:s + 2] in t
        for s in range(kw_len - 1)
    )
    if not has_substr:
        return 0.0

    # LCS length (dynamic programming)
    t_len = len(t)
    # Dùng 2 hàng để tiết kiệm bộ nhớ
    prev = [0] * (t_len + 1)
    for ch in kw:
        curr = [0] * (t_len + 1)
        for j, tc in enumerate(t, 1):
            if ch == tc:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(curr[j - 1], prev[j])
        prev = curr

    lcs_len = prev[t_len]
    return lcs_len / kw_len


def _extract_domain_from_href(href: str) -> str:
    """Lấy domain từ href trực tiếp (bỏ qua Sogou redirect /link?url=...)"""
    if not href:
        return ""
    if href.startswith("http") and "sogou.com" not in href:
        try:
            return urlparse(href).netloc
        except Exception:
            pass
    return ""


def _extract_sogou_results(
    page: Page, keyword: str
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Trích xuất kết quả tìm kiếm từ Sogou.

    Trả về (exact_matches, partial_matches):
    - exact_matches : title chứa đúng toàn bộ keyword (similarity = 100%)
    - partial_matches: title khớp >= 60% keyword, sắp xếp theo score → position
    """
    SIMILARITY_THRESHOLD = 0.65  # 65% LCS

    exact_matches: List[Dict[str, Any]] = []
    partial_matches: List[Dict[str, Any]] = []

    containers = []
    for selector in SOGOU_SELECTORS["result_container"]:
        containers = page.query_selector_all(selector)
        if containers:
            break

    if not containers:
        try:
            page.wait_for_selector(_RESULT_SELECTOR, timeout=1500, state="attached")
        except PlaywrightTimeoutError:
            pass
        for selector in SOGOU_SELECTORS["result_container"]:
            containers = page.query_selector_all(selector)
            if containers:
                break

    keyword_lower = keyword.lower()
    position = 0
    for container in containers:
        # Bỏ qua container bị ẩn (display:none)
        try:
            style = container.get_attribute("style") or ""
            if "display:none" in style.replace(" ", ""):
                continue
        except Exception:
            pass

        title = _extract_sogou_title(container)
        if not title:
            continue

        # Lấy domain từ href của link title (trực tiếp, không qua redirect)
        domain = ""
        try:
            link = container.query_selector("h3.vr-title a, a.resultLink, h3 a")
            if link:
                href = link.get_attribute("href") or ""
                domain = _extract_domain_from_href(href)
        except Exception:
            pass
        # Fallback: dùng _extract_sogou_domain nếu chưa có
        if not domain:
            domain = _extract_sogou_domain(container)

        title_lower = title.lower()
        sim = _similarity_pct(keyword, title)

        if keyword_lower in title_lower:
            # Khớp hoàn toàn — luôn lấy
            kw_pos = title_lower.find(keyword_lower)
            exact_matches.append({
                "title": title,
                "domain": domain,
                "time_tag": "",
                "is_processed": False,
                "added_text": "",
                "startswith_kw": title_lower.startswith(keyword_lower),
                "kw_pos": kw_pos,
                "len_gap": abs(len(title) - len(keyword)),
                "partial_score": len(keyword) * 11,
                "position": position,
            })
        elif sim >= SIMILARITY_THRESHOLD:
            # Khớp một phần >= 60%
            score = _partial_match_score(keyword, title)
            partial_matches.append({
                "title": title,
                "domain": domain,
                "time_tag": "",
                "is_processed": False,
                "added_text": "",
                "startswith_kw": False,
                "kw_pos": 9999,
                "len_gap": abs(len(title) - len(keyword)),
                "partial_score": score,
                "position": position,
                "similarity_pct": int(sim * 100),
            })
        else:
            logger.debug(f"   ⛔ Bỏ qua ({int(sim*100)}%): {title[:50]}")

        position += 1

    # Sắp xếp partial: score cao → position nhỏ (xuất hiện trước trên trang)
    partial_matches.sort(key=lambda x: (-x["partial_score"], x["position"]))

    return exact_matches, partial_matches


def _process_sogou_keyword(
    page: Page,
    keyword: str,
    idx: int,
    total: int,
    processed_keywords: set,
    recent_responses: deque,
    recent_failed_requests: deque,
    recent_console: deque,
) -> Tuple[str, str, str, bool, str, str]:
    """Xử lý một từ khóa trên Sogou"""
    kw = str(keyword).strip()
    if not kw:
        return ("", "", "", False, "", "")

    logger.info(f"[🔍] Sogou ({idx}/{total}) Đang tìm kiếm: {kw}")

    if kw in processed_keywords:
        logger.warning(f"[⚠️] Trùng lặp từ khóa: {kw}")
        return ("Trùng lặp từ khóa", "", "", False, "", "")

    processed_keywords.add(kw)

    try:
        try:
            _ = page.url
        except Exception:
            return ("Lỗi: Page không còn hoạt động", "", "", False, "", "")

        encoded_kw = urllib.parse.quote(kw, encoding="utf-8")
        url = SOGOU_URLS["search"].format(encoded_kw)

        if not _navigate_to_sogou(page, url, kw):
            raise PlaywrightTimeoutError(f"Sogou navigation failed for {kw}")

        if not _wait_for_sogou_results(page, kw):
            _dump_artifacts(page, kw, "selector_timeout", recent_responses, recent_failed_requests, recent_console)
            # Không đóng page — trả về lỗi nhẹ để vòng lặp tiếp tục
            return ("Lỗi: Không tìm thấy kết quả Sogou", "", "", False, "", "")

        exact_matches, partial_matches = _extract_sogou_results(page, kw)

        if exact_matches:
            # Ưu tiên: bắt đầu bằng keyword → vị trí gần đầu → độ dài gần keyword
            best = max(
                exact_matches,
                key=lambda x: (
                    1 if x.get("startswith_kw") else 0,
                    -x.get("kw_pos", 9999),
                    -x.get("len_gap", 9999),
                ),
            )
            logger.info(f"   ✅ Khớp hoàn toàn: {best['title'][:60]}")
            return (
                best["title"],
                best["domain"],
                best["time_tag"],
                best.get("is_processed", False),
                best.get("added_text", ""),
                best["title"],
            )
        elif partial_matches:
            # Fallback: lấy kết quả khớp nhiều ký tự nhất (>= 60%)
            best = partial_matches[0]
            sim_pct = best.get("similarity_pct", int(best["partial_score"] / (len(kw) * 11) * 100))
            logger.info(f"   🔶 Khớp một phần ({sim_pct}%): {best['title'][:60]}")
            return (
                best["title"],
                best["domain"],
                best["time_tag"],
                best.get("is_processed", False),
                best.get("added_text", ""),
                best["title"],
            )
        else:
            # Dump HTML để debug khi không lấy được kết quả nào
            logger.warning(f"   ⚠️ Không tìm thấy kết quả phù hợp cho: {kw}")
            _dump_artifacts(page, kw, "no_result", recent_responses, recent_failed_requests, recent_console)
            return ("", "", "", False, "", "")

    except PlaywrightTimeoutError:
        error_msg = "Lỗi: Timeout khi tải trang Sogou"
        logger.error(f"[❌] {error_msg} – {kw}")
        # KHÔNG đóng page — để vòng lặp chính tái sử dụng
        return (error_msg, "", "", False, "", "")
    except PlaywrightError as e:
        msg = str(e)
        logger.error(f"[❌] Sogou PlaywrightError – {kw}: {msg}")
        # KHÔNG đóng page
        return (f"Lỗi: {msg[:80]}", "", "", False, "", "")
    except Exception as e:
        error_msg = f"Lỗi: {str(e)[:50]}"
        logger.exception(f"[❌] Sogou {error_msg} – {kw}")
        return (error_msg, "", "", False, "", "")


def search_sogou_keywords(keywords: List[str], on_progress=None, on_result=None, stop_event=None) -> None:
    """Tìm kiếm từ khóa trên Sogou (sogou.com)"""
    if not keywords:
        logger.error("❌ Không có từ khóa để tìm kiếm trên Sogou!")
        return

    results = []
    processed_keywords: set = set()

    with sync_playwright() as p:
        browser_context = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_PATH,
            headless=False,
            executable_path=CHROME_PATH,
            **SOGOU_BROWSER_CONFIG,
        )

        page = browser_context.new_page()
        recent_responses, recent_failed_requests, recent_console = _setup_debug_handlers(page)

        total = len(keywords)
        duplicate_count = 0
        success_count = 0
        error_count = 0

        for idx, kw in enumerate(keywords, start=1):
            if stop_event and stop_event.is_set():
                logger.info("⏹ Đã dừng tìm kiếm Sogou.")
                break
            if on_progress:
                on_progress(idx, total, kw)
            # Kiểm tra page còn sống không — nếu chết thì tạo lại
            page_alive = False
            try:
                _ = page.url
                page_alive = True
            except Exception:
                pass

            if not page_alive:
                try:
                    page = browser_context.new_page()
                    recent_responses, recent_failed_requests, recent_console = _setup_debug_handlers(page)
                    logger.info("[🔄] Đã tạo lại page mới")
                except Exception as e:
                    logger.error(f"[❌] Không thể tạo page mới: {e}")
                    results.append(("Lỗi: Không thể tạo page", "", "", False, "", ""))
                    error_count += 1
                    continue

            result = _process_sogou_keyword(
                page, kw, idx, total, processed_keywords,
                recent_responses, recent_failed_requests, recent_console,
            )
            if on_result:
                on_result(idx, kw, result)

            if result[0] == "Trùng lặp từ khóa":
                duplicate_count += 1
            elif result[0] and not result[0].startswith("Lỗi"):
                success_count += 1
            elif result[0].startswith("Lỗi"):
                error_count += 1

            results.append(result)

            if error_count > 0 and idx < total:
                delay = random.uniform(DELAYS["error_min"], DELAYS["error_max"])
            else:
                delay = random.uniform(DELAYS["normal_min"], DELAYS["normal_max"])
            time.sleep(delay)

        browser_context.close()

    write_search_results(keywords, results, duplicate_count, success_count, error_count, total)
