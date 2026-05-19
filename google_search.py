"""Module tìm kiếm từ khóa trên Google"""
import json
import time
import random
import urllib.parse
from urllib.parse import urlparse
import logging
from pathlib import Path
from collections import deque
from typing import List, Tuple, Optional, Dict, Any
from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
    Error as PlaywrightError,
    Page,
    BrowserContext,
)
from config import PROFILE_PATH, CHROME_PATH
from excel_writer import write_search_results
from constants import (
    BROWSER_CONFIG,
    TIMEOUTS,
    DELAYS,
    DEBUG_ARTIFACTS_DIR,
    DEBUG_ARTIFACTS_MAX_RESPONSES,
    DEBUG_ARTIFACTS_MAX_FAILED_REQUESTS,
    DEBUG_ARTIFACTS_MAX_CONSOLE,
)

logger = logging.getLogger()

# Google-specific selectors
GOOGLE_SELECTORS = {
    "result_container": [
        "div.g",
        "div[data-ved]",
        ".g",
        ".tF2Cxc",
        ".hlcw0c",
    ],
    "title": [
        "h3",
        "a h3",
        ".LC20lb",
        ".DKV0Md",
    ],
    "domain": [
        "cite",
        ".tjvcx",
        ".iUh30",
        ".qzEoUe",
    ],
    "search_input": [
        "input[name='q']",
        "textarea[name='q']",
        "#APjFqb",
    ],
    "search_button": [
        "input[type='submit']",
        "button[type='submit']",
        ".gNO89b",
    ],
}

GOOGLE_URLS = {
    "google_search": "https://www.google.com/search?q={}",
}

# Selector kết hợp để dùng với wait_for_selector
_RESULT_SELECTOR = ", ".join(GOOGLE_SELECTORS["result_container"])


def _extract_google_title(container: Any) -> str:
    """Trích xuất title từ Google search result"""
    for selector in GOOGLE_SELECTORS["title"]:
        try:
            title_elem = container.query_selector(selector)
            if title_elem:
                title = (title_elem.text_content() or "").strip()
                if title:
                    return title
        except Exception:
            continue
    return ""


def _extract_google_domain(container: Any) -> str:
    """Trích xuất domain từ Google search result"""
    for selector in GOOGLE_SELECTORS["domain"]:
        try:
            domain_elem = container.query_selector(selector)
            if domain_elem:
                domain_text = (domain_elem.text_content() or "").strip()
                if domain_text:
                    if not domain_text.startswith(("http://", "https://")):
                        domain_text = "https://" + domain_text
                    try:
                        return urlparse(domain_text).netloc
                    except Exception:
                        return domain_text
        except Exception:
            continue

    # Fallback: lấy từ link
    try:
        link_elem = container.query_selector("a[href]")
        if link_elem:
            href = link_elem.get_attribute("href")
            if href and href.startswith("http"):
                return urlparse(href).netloc
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
            recent_responses.append({
                "url": resp.url,
                "status": resp.status,
                "ok": resp.ok,
            })
        except Exception:
            pass

    def _on_request_failed(req):
        try:
            recent_failed_requests.append({
                "url": req.url,
                "method": req.method,
                "resource_type": req.resource_type,
            })
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
    """Làm sạch tên file để an toàn"""
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
        except Exception:
            pass

        try:
            (out_dir / f"{base}.html").write_text(page.content(), encoding="utf-8")
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


def _navigate_to_google_search(page: Page, url: str, keyword: str) -> bool:
    """Điều hướng đến Google search URL với retry logic"""
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
                    logger.warning(f"[⚠️] Dùng fallback navigation cho Google: {keyword}")
                    return True
                except Exception:
                    pass
            else:
                time.sleep(0.5)

    return False


def _wait_for_google_results(page: Page, keyword: str) -> bool:
    """Chờ kết quả Google search xuất hiện"""
    try:
        page.wait_for_selector(_RESULT_SELECTOR, timeout=TIMEOUTS["selector_visible"], state="visible")
        return True
    except PlaywrightTimeoutError:
        try:
            page.wait_for_selector(_RESULT_SELECTOR, timeout=TIMEOUTS["selector_attached"], state="attached")
            logger.warning(f"[⚠️] Google selector có thể đang ẩn cho {keyword}")
            return True
        except PlaywrightTimeoutError as e:
            logger.error(f"[❌] Timeout chờ Google selector – {keyword}: {e}")
            return False


def _extract_google_results(page: Page, keyword: str) -> List[Dict[str, Any]]:
    """Trích xuất kết quả tìm kiếm từ Google"""
    matched_results = []

    # Tìm containers
    containers = []
    for selector in GOOGLE_SELECTORS["result_container"]:
        containers = page.query_selector_all(selector)
        if containers:
            break

    if not containers:
        try:
            page.wait_for_selector(_RESULT_SELECTOR, timeout=1500, state="attached")
        except PlaywrightTimeoutError:
            pass
        for selector in GOOGLE_SELECTORS["result_container"]:
            containers = page.query_selector_all(selector)
            if containers:
                break

    keyword_lower = keyword.lower()
    for container in containers:
        title = _extract_google_title(container)
        if not title:
            continue

        domain = _extract_google_domain(container)
        title_lower = title.lower()

        if keyword_lower in title_lower:
            kw_pos = title_lower.find(keyword_lower)
            len_gap = abs(len(title) - len(keyword))
            startswith_kw = title_lower.startswith(keyword_lower)

            matched_results.append({
                "title": title,
                "domain": domain,
                "time_tag": "",
                "is_processed": False,
                "added_text": "",
                "startswith_kw": startswith_kw,
                "kw_pos": kw_pos,
                "len_gap": len_gap,
            })

    return matched_results


def _process_google_keyword(
    page: Page,
    keyword: str,
    idx: int,
    total: int,
    processed_keywords: set,
    recent_responses: deque,
    recent_failed_requests: deque,
    recent_console: deque,
) -> Tuple[str, str, str, bool, str, str]:
    """Xử lý một từ khóa trên Google"""
    kw = str(keyword).strip()
    if not kw:
        return ("", "", "", False, "", "")

    logger.info(f"[🔍] Google ({idx}/{total}) Đang tìm kiếm: {kw}")

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
        url = GOOGLE_URLS["google_search"].format(encoded_kw)

        if not _navigate_to_google_search(page, url, kw):
            raise PlaywrightTimeoutError(f"Google navigation failed for {kw}")

        if not _wait_for_google_results(page, kw):
            _dump_artifacts(page, kw, "selector_timeout", recent_responses, recent_failed_requests, recent_console)
            raise PlaywrightTimeoutError(f"Timeout waiting for Google results for {kw}")

        matched_results = _extract_google_results(page, kw)

        if matched_results:
            best = max(
                matched_results,
                key=lambda x: (
                    1 if x.get("startswith_kw") else 0,
                    -x.get("kw_pos", 9999),
                    -x.get("len_gap", 9999),
                ),
            )
            return (
                best["title"],
                best["domain"],
                best["time_tag"],
                best.get("is_processed", False),
                best.get("added_text", ""),
                best["title"],
            )
        else:
            return ("", "", "", False, "", "")

    except PlaywrightTimeoutError:
        error_msg = "Lỗi: Timeout khi tải trang Google"
        logger.error(f"[❌] {error_msg} – {kw}")
        try:
            page.close()
        except Exception:
            pass
        return (error_msg, "", "", False, "", "")
    except PlaywrightError as e:
        msg = str(e)
        logger.error(f"[❌] Google PlaywrightError – {kw}: {msg}")
        try:
            page.close()
        except Exception:
            pass
        return (f"Lỗi: {msg[:80]}", "", "", False, "", "")
    except Exception as e:
        error_msg = f"Lỗi: {str(e)[:50]}"
        logger.exception(f"[❌] Google {error_msg} – {kw}")
        return (error_msg, "", "", False, "", "")


def search_google_keywords(keywords: List[str], on_progress=None, on_result=None, stop_event=None) -> None:
    """Tìm kiếm từ khóa trên Google"""
    if not keywords:
        logger.error("❌ Không có từ khóa để tìm kiếm trên Google!")
        return

    results = []
    processed_keywords: set = set()

    with sync_playwright() as p:
        browser_context = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_PATH,
            headless=False,
            executable_path=CHROME_PATH,
            **BROWSER_CONFIG,
        )

        page = browser_context.new_page()
        recent_responses, recent_failed_requests, recent_console = _setup_debug_handlers(page)

        total = len(keywords)
        duplicate_count = 0
        success_count = 0
        error_count = 0

        for idx, kw in enumerate(keywords, start=1):
            if stop_event and stop_event.is_set():
                logger.info("⏹ Đã dừng tìm kiếm Google.")
                break
            if on_progress:
                on_progress(idx, total, kw)
            # Khôi phục page nếu bị đóng
            try:
                _ = page.url
            except Exception:
                page = browser_context.new_page()
                recent_responses, recent_failed_requests, recent_console = _setup_debug_handlers(page)

            result = _process_google_keyword(
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

            # Delay để tránh bị block
            if error_count > 0 and idx < total:
                delay = random.uniform(DELAYS["error_min"], DELAYS["error_max"])
            else:
                delay = random.uniform(DELAYS["normal_min"], DELAYS["normal_max"])
            time.sleep(delay)

        browser_context.close()

    write_search_results(keywords, results, duplicate_count, success_count, error_count, total)
