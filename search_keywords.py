"""Module tìm kiếm từ khóa trên Baidu Mobile"""
import json
import shutil
import tempfile
import time
import random
import urllib.parse
from urllib.parse import urlparse
import logging
import threading
import subprocess
from pathlib import Path
from collections import deque
from typing import List, Tuple, Optional, Dict, Any
import psutil
from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
    Error as PlaywrightError,
    Page,
)
from config import PROFILE_PATH, CHROME_PATH
from excel_writer import write_search_results
from constants import (
    TIME_LABELS,
    TIME_PRIORITY,
    REPLACEMENT_PATTERNS,
    MID_PATTERNS,
    BROWSER_CONFIG,
    TIMEOUTS,
    LOCATION_PROFILES,
    DELAYS,
    SELECTORS,
    URLS,
    BLOCKED_SOURCES,
    DEBUG_ARTIFACTS_DIR,
    DEBUG_ARTIFACTS_MAX_RESPONSES,
    DEBUG_ARTIFACTS_MAX_FAILED_REQUESTS,
    DEBUG_ARTIFACTS_MAX_CONSOLE,
    CAPTCHA_URL_PATTERNS,
    CAPTCHA_PAGE_TITLES,
    BLOCKED_DOMAINS,
    NO_RESULT_MAX_RETRIES,
    NO_RESULT_RETRY_DELAY_MIN,
    NO_RESULT_RETRY_DELAY_MAX,
)

logger = logging.getLogger()

# Selector kết hợp để dùng với wait_for_selector
_RESULT_SELECTOR = ", ".join(SELECTORS["result_container"])


def _cleanup_chrome_locks(profile_path: str) -> None:
    """Xóa lock files của Chrome profile để tránh lỗi ProcessSingleton.
    Chrome tạo các file này khi đang chạy; nếu bị kill đột ngột chúng không được xóa.
    """
    for lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile"):
        lock_path = Path(profile_path) / lock_name
        if lock_path.exists():
            try:
                lock_path.unlink()
                logger.info(f"[🔓] Đã xóa lock file: {lock_name}")
            except Exception as e:
                logger.warning(f"[⚠️] Không thể xóa {lock_name}: {e}")


def _is_captcha_page(page: Page) -> bool:
    """Kiểm tra trang hiện tại có phải trang captcha của Baidu không"""
    try:
        current_url = page.url or ""
        for pattern in CAPTCHA_URL_PATTERNS:
            if pattern in current_url:
                return True
        try:
            title = page.title() or ""
            for cap_title in CAPTCHA_PAGE_TITLES:
                if cap_title in title:
                    return True
        except Exception:
            pass
    except Exception:
        pass
    return False


def _extract_title(container: Any) -> str:
    """Trích xuất title từ container với nhiều phương pháp fallback"""
    for selector in SELECTORS["title"]:
        try:
            title_elem = container.query_selector(selector)
            if title_elem:
                title = (title_elem.text_content() or "").strip()
                if title:
                    return title
        except Exception:
            continue

    # Fallback: lấy dòng đầu tiên của inner_text
    try:
        art = container.query_selector("article.c-container") or container
        txt = (art.inner_text() or "").strip()
        if txt:
            title = next((line.strip() for line in txt.splitlines() if line.strip()), "")
            if title:
                return title
    except Exception:
        pass

    return ""


def _title_match_info(keyword: str, title: str) -> Tuple[bool, bool, int, int]:
    """Trả về (match, startswith, pos, len_gap) để ưu tiên title sát keyword"""
    kw_norm = "".join(keyword.lower().split())
    title_norm = "".join(title.lower().split())
    if not kw_norm or not title_norm:
        return (False, False, -1, 9999)
    len_gap = abs(len(title_norm) - len(kw_norm))
    if title_norm.startswith(kw_norm):
        return (True, True, 0, len_gap)
    pos = title_norm.find(kw_norm)
    if 0 <= pos < 120:
        return (True, False, pos, len_gap)
    return (False, False, -1, len_gap)


def _extract_domain(container: Any) -> str:
    """Trích xuất domain từ container với nhiều cơ chế fallback,
    ưu tiên JavaScript evaluation để đọc trực tiếp từ DOM trình duyệt."""
    import re

    def _parse_url_to_domain(url: str) -> str:
        """Lấy netloc từ URL, trả về chuỗi rỗng nếu là baidu.com."""
        if not url:
            return ""
        try:
            parsed = urlparse(url if url.startswith("http") else "http://" + url)
            netloc = parsed.netloc or ""
            if netloc and not _is_blocked_domain(netloc):
                return netloc
        except Exception:
            pass
        return ""

    def _clean_visual_domain(text: str) -> str:
        """Làm sạch chuỗi hiển thị domain từ element trực quan."""
        if not text:
            return ""
        text = re.sub(r'^https?://', '', text, flags=re.IGNORECASE).strip()
        # Lấy phần trước khoảng trắng / dấu / / ký tự phân cách
        parts = re.split(r'[\s/\\›>|；;—]', text)
        candidate = parts[0].strip().lower()
        candidate = re.sub(r'[^\w.\-]', '', candidate)
        # Phải có ít nhất 1 dấu chấm và kết thúc bằng TLD 2-63 ký tự
        if re.match(r'^[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.(?:[a-z0-9\-]{1,62}\.)*[a-z]{2,63}$', candidate):
            return candidate
        return ""

    # ──────────────────────────────────────────────────────────────────────────
    # Phương án 1: Đọc data-log.mu bằng Python (phương án cơ bản)
    # ──────────────────────────────────────────────────────────────────────────
    try:
        data_log = container.get_attribute("data-log")
        if data_log:
            info = json.loads(data_log)
            # Thử nhiều trường có thể chứa URL đích
            for field in ("mu", "url", "pu", "di"):
                raw = info.get(field) or ""
                if raw:
                    domain = _parse_url_to_domain(raw)
                    if domain:
                        return domain
    except Exception:
        pass

    # ──────────────────────────────────────────────────────────────────────────
    # Phương án 2: JavaScript evaluation - đọc data-log từ DOM thực tế
    # (đảm bảo lấy đúng giá trị ngay cả khi Playwright cache attribute cũ)
    # ──────────────────────────────────────────────────────────────────────────
    try:
        js_result = container.evaluate("""
            el => {
                // Thử data-log.mu trực tiếp
                const raw = el.getAttribute('data-log');
                if (raw) {
                    try {
                        const obj = JSON.parse(raw);
                        for (const f of ['mu', 'url', 'pu', 'di']) {
                            if (obj[f]) return obj[f];
                        }
                    } catch(e) {}
                }
                // Thử các data attribute khác trên container
                for (const attr of ['data-mu', 'data-shareurl', 'data-url', 'data-sf-href']) {
                    const v = el.getAttribute(attr);
                    if (v && v.startsWith('http')) return v;
                }
                // Tìm element hiển thị URL trực quan
                const urlSelectors = [
                    '.c-showurl', 'span.c-showurl',
                    '.cosc-source-text', 'span.cosc-source-text',
                    '.c-source', '.c-source-text',
                    '.cosc-source', '.c-showurl-source',
                    '[class*="showurl"]', '[class*="source"]'
                ];
                for (const sel of urlSelectors) {
                    const elem = el.querySelector(sel);
                    if (elem) {
                        const t = (elem.textContent || '').trim();
                        if (t) return '##VISUAL##' + t;
                    }
                }
                // Tìm trong các thẻ a - lấy data-log.mu từ thẻ a nếu có
                for (const a of el.querySelectorAll('a')) {
                    const aLog = a.getAttribute('data-log');
                    if (aLog) {
                        try {
                            const obj = JSON.parse(aLog);
                            for (const f of ['mu', 'url', 'pu']) {
                                if (obj[f]) return obj[f];
                            }
                        } catch(e) {}
                    }
                }
                return '';
            }
        """)
        if js_result:
            if js_result.startswith("##VISUAL##"):
                domain = _clean_visual_domain(js_result[10:])
                if domain:
                    return domain
            else:
                domain = _parse_url_to_domain(js_result)
                if domain:
                    return domain
    except Exception:
        pass

    # ──────────────────────────────────────────────────────────────────────────
    # Phương án 3: Quét tất cả attribute trên container và thẻ a con
    # ──────────────────────────────────────────────────────────────────────────
    for attr in ("data-mu", "mu", "data-shareurl", "data-url", "data-sf-href"):
        try:
            val = container.get_attribute(attr)
            if val:
                domain = _parse_url_to_domain(val)
                if domain:
                    return domain
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────────────────
    # Phương án 4: Element hiển thị URL trực quan (fallback Python)
    # ──────────────────────────────────────────────────────────────────────────
    for selector in (
        ".c-showurl", "span.c-showurl",
        ".cosc-source-text", "span.cosc-source-text",
        ".c-source", ".c-source-text",
        ".cosc-source", ".c-showurl-source",
        "[class*='showurl']", "[class*='source']",
    ):
        try:
            for elem in container.query_selector_all(selector):
                txt = (elem.text_content() or "").strip()
                if txt:
                    domain = _clean_visual_domain(txt)
                    if domain:
                        return domain
        except Exception:
            pass

    return ""


def _extract_time_tag(container: Any) -> str:
    """Trích xuất time tag — match trực tiếp 3 label: 刚刚发布 / 今日发布 / 近期发布

    Baidu Mobile hiện dùng data-module attribute:
      - data-module="today_pub"  → 今日发布
      - data-module="recent_pub" → 近期发布
      - data-module="just_pub"   → 刚刚发布  (dự phòng)
    Fallback sang selector cũ và inner_text nếu không tìm thấy.
    """
    art = container.query_selector("article.c-container") or container

    # Ưu tiên 1: data-module attribute (cấu trúc mới của Baidu Mobile)
    _DATA_MODULE_MAP = {
        "just_pub":   "刚刚发布",
        "today_pub":  "今日发布",
        "recent_pub": "近期发布",
    }
    for module_name, label in _DATA_MODULE_MAP.items():
        try:
            elem = art.query_selector(f'[data-module="{module_name}"]')
            if elem:
                return label
        except Exception:
            continue

    # Ưu tiên 2: selector cũ (span/div.c-color-gray...)
    for selector in SELECTORS["time_tag"]:
        try:
            elems = art.query_selector_all(selector)
            for elem in elems:
                text = (elem.inner_text() or "").strip()
                if text:
                    for label in TIME_LABELS:
                        if label in text:
                            return label
        except Exception:
            continue

    # Fallback: tìm trong toàn bộ inner_text
    try:
        inner = (art.inner_text() or "").strip()
        for label in TIME_LABELS:
            if label in inner:
                return label
    except Exception:
        pass

    return ""


def _is_blocked_source(container: Any) -> bool:
    """Kiểm tra kết quả có nguồn bị cấm không (AI生成, GitHub...).
    Selector: span.cosc-source-text (Baidu Mobile)"""
    try:
        source_elems = container.query_selector_all("span.cosc-source-text, .cosc-source-text")
        for elem in source_elems:
            text = (elem.text_content() or "").strip()
            if text:
                for blocked in BLOCKED_SOURCES:
                    if blocked in text:
                        return True
    except Exception:
        pass
    return False


def _is_blocked_domain(domain: str) -> bool:
    """Kiểm tra domain có nằm trong danh sách bị chặn không.
    Dùng exact-domain match: chỉ block chính xác domain được liệt kê.
    Lưu ý: không còn dùng suffix match nữa (để không block nhầm haokan.baidu.com etc.)
    """
    if not domain:
        return False
    domain_lower = domain.lower().rstrip(".")
    for blocked in BLOCKED_DOMAINS:
        blocked_lower = blocked.lower()
        if domain_lower == blocked_lower:
            return True
    return False


def _process_title_patterns(title: str, keyword: str) -> Tuple[str, str, bool]:
    """Xử lý các pattern thay thế trong title"""
    added_text = ""
    is_processed = False

    for pattern, replacement in REPLACEMENT_PATTERNS:
        if title.endswith(pattern):
            added_text = replacement
            title = (title[:-3] if pattern.endswith("...") else title) + added_text
            is_processed = True
            break

    if not is_processed:
        for pattern, replacement in MID_PATTERNS:
            if pattern in title:
                pattern_idx = title.find(pattern)
                added_text = replacement
                title = title[: pattern_idx + len(pattern)] + added_text
                is_processed = True
                break

    return title.strip(), added_text, is_processed


def _sanitize_filename(text: str) -> str:
    """Làm sạch tên file để an toàn"""
    safe = "".join(ch if ch.isalnum() or ch in "-_ ." else "_" for ch in text)
    return safe[:80] or "kw"


def _dump_artifacts(
    page_obj: Page,
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
            page_obj.screenshot(path=str(out_dir / f"{base}.png"), full_page=True)
            logger.info(f"[🖼] Đã lưu screenshot: {out_dir / f'{base}.png'}")
        except Exception:
            pass

        try:
            html_path = out_dir / f"{base}.html"
            html_path.write_text(page_obj.content(), encoding="utf-8")
            logger.info(f"[📄] Đã lưu HTML: {html_path}")
        except Exception:
            pass

        try:
            meta = {
                "keyword": keyword,
                "phase": phase,
                "url": getattr(page_obj, "url", None),
                "readyState": page_obj.evaluate("document.readyState"),
                "recentResponses": list(recent_responses)[-10:],
                "recentFailedRequests": list(recent_failed_requests)[-10:],
                "recentConsole": list(recent_console)[-10:],
            }
            meta_path = out_dir / f"{base}.json"
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"[🧾] Đã lưu meta: {meta_path}\n")
        except Exception:
            pass
    except Exception:
        pass


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
                "timing": resp.timing if hasattr(resp, "timing") else None,
            })
        except Exception:
            pass

    def _on_request_failed(req):
        try:
            recent_failed_requests.append({
                "url": req.url,
                "method": req.method,
                "failure": getattr(req, "failure", lambda: None)(),
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


def _wait_for_user_solve_captcha(page: Page, keyword: str) -> bool:
    """Chờ người dùng giải captcha thủ công trên Chrome.
    
    Hiện thông báo trên terminal, poll liên tục cho đến khi:
    - Trang không còn là captcha → trả về True (đã giải xong)
    - Người dùng nhấn Enter để bỏ qua → trả về False
    
    Dùng thread riêng để đọc input mà không block polling.
    """
    print("\n" + "=" * 60)
    print("🔒 CAPTCHA DETECTED!")
    print(f"   Từ khóa: {keyword}")
    print("   → Vui lòng giải captcha trên cửa sổ Chrome đang mở.")
    print("   → Chương trình sẽ tự động tiếp tục sau khi bạn giải xong.")
    print("   → Nhấn Enter tiếp tục tìm kiếm.")
    print("=" * 60)

    skip_event = threading.Event()

    def _wait_input():
        try:
            input()  # chờ người dùng nhấn Enter
        except Exception:
            pass
        skip_event.set()

    input_thread = threading.Thread(target=_wait_input, daemon=True)
    input_thread.start()

    # Poll mỗi 2s để kiểm tra captcha đã qua chưa
    while not skip_event.is_set():
        try:
            if not _is_captcha_page(page):
                print("\n✅ Captcha đã được giải! Tiếp tục tìm kiếm...")
                return True
        except Exception:
            pass
        time.sleep(2.0)

    print(f"\n⏭ Bỏ qua từ khóa: {keyword}")
    return False


def _navigate_to_search_url(page: Page, url: str, keyword: str) -> bool:
    """Điều hướng đến URL tìm kiếm với retry logic"""
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
                    logger.warning(f"[⚠️] Dùng fallback navigation cho {keyword}")
                    return True
                except Exception:
                    pass
            else:
                time.sleep(0.5)

    return False


def _wait_for_search_results(page: Page, keyword: str) -> bool:
    """Chờ kết quả tìm kiếm xuất hiện. Trả về False ngay nếu phát hiện captcha."""
    # Kiểm tra captcha trước
    if _is_captcha_page(page):
        logger.warning(f"[🔒] Captcha ngay sau navigation – {keyword}")
        return False

    try:
        page.wait_for_selector(_RESULT_SELECTOR, timeout=TIMEOUTS["selector_visible"], state="visible")
        if _is_captcha_page(page):
            return False
        return True
    except PlaywrightTimeoutError:
        if _is_captcha_page(page):
            logger.warning(f"[🔒] Redirect sang captcha – {keyword}")
            return False
        try:
            page.wait_for_selector(_RESULT_SELECTOR, timeout=TIMEOUTS["selector_attached"], state="attached")
            if _is_captcha_page(page):
                return False
            logger.warning(f"[⚠️] Selector có thể đang ẩn cho {keyword}")
            return True
        except PlaywrightTimeoutError as e:
            if _is_captcha_page(page):
                logger.warning(f"[🔒] Redirect sang captcha – {keyword}")
            else:
                logger.error(f"[❌] Timeout chờ selector – {keyword}: {e}")
            return False


def _extract_search_results(page: Page, keyword: str) -> List[Dict[str, Any]]:
    """Trích xuất kết quả tìm kiếm từ trang"""
    matched_results = []

    # Tìm containers — nếu chưa có, chờ thêm bằng wait_for_selector
    containers = []
    for selector in SELECTORS["result_container"]:
        containers = page.query_selector_all(selector)
        if containers:
            break

    if not containers:
        try:
            page.wait_for_selector(_RESULT_SELECTOR, timeout=1500, state="attached")
        except PlaywrightTimeoutError:
            pass
        for selector in SELECTORS["result_container"]:
            containers = page.query_selector_all(selector)
            if containers:
                break

    for container in containers:
        title = _extract_title(container)
        if not title:
            continue

        # Bỏ qua kết quả từ nguồn bị cấm (AI生成, GitHub...)
        if _is_blocked_source(container):
            logger.debug(f"[⛔] Bỏ qua kết quả từ nguồn bị cấm: {title[:40]}")
            continue

        domain = _extract_domain(container)
        time_tag = _extract_time_tag(container)

        # Nếu domain bị block (tịp trang search/home Baidu): giữ title nhưng để domain rỗng
        if _is_blocked_domain(domain):
            logger.debug(f"[⛔] Domain bị chặn → để trống: {domain} | {title[:40]}")
            domain = ""

        matched, startswith_kw, kw_pos, len_gap = _title_match_info(keyword, title)
        if matched:
            processed_title, added_text, is_processed = _process_title_patterns(title, keyword)
            matched_results.append({
                "title": processed_title,
                "original_title": title,
                "domain": domain,
                "time_tag": time_tag,
                "is_processed": is_processed,
                "added_text": added_text,
                "startswith_kw": startswith_kw,
                "kw_pos": kw_pos,
                "len_gap": len_gap,
            })

    return matched_results


def _wait_for_load(page: Page) -> None:
    """Chờ trang load xong (networkidle → domcontentloaded fallback)"""
    try:
        page.wait_for_load_state("networkidle", timeout=TIMEOUTS["network_idle"])
    except PlaywrightTimeoutError:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=TIMEOUTS["dom_content_loaded"])
        except PlaywrightTimeoutError:
            pass


def _wait_for_results_after_submit(page: Page) -> None:
    """Chờ kết quả tìm kiếm sau khi submit"""
    _wait_for_load(page)
    try:
        page.wait_for_selector(_RESULT_SELECTOR, timeout=TIMEOUTS["selector_visible"], state="visible")
        time.sleep(DELAYS["result_stable"])
    except PlaywrightTimeoutError:
        try:
            page.wait_for_selector(_RESULT_SELECTOR, timeout=TIMEOUTS["selector_attached"], state="attached")
            time.sleep(DELAYS["result_stable"])
        except PlaywrightTimeoutError:
            pass


def _find_element(page: Page, selectors: List[str]):
    """Tìm element đầu tiên khớp trong danh sách selectors"""
    for selector in selectors:
        try:
            elem = page.query_selector(selector)
            if elem:
                return elem
        except Exception:
            continue
    return None


def _perform_detailed_search(
    page: Page,
    keyword: str,
    recent_responses: deque,
    recent_failed_requests: deque,
    recent_console: deque,
) -> Optional[List[Dict[str, Any]]]:
    """Thực hiện tìm kiếm chi tiết (nhấn button 3 lần)"""
    try:
        # Điều hướng đến trang chủ Baidu Mobile
        try:
            page.goto(URLS["baidu_mobile"], wait_until="domcontentloaded", timeout=TIMEOUTS["homepage"])
            time.sleep(DELAYS["homepage_load"])
        except PlaywrightTimeoutError:
            try:
                page.goto(URLS["baidu_mobile"], wait_until="commit", timeout=TIMEOUTS["homepage_fallback"])
                time.sleep(DELAYS["homepage_load_fallback"])
            except Exception as e:
                logger.error(f"   ❌ Không thể load trang chủ: {e}")
                return None

        # Tìm ô input
        search_input = _find_element(page, SELECTORS["search_input"])
        if not search_input:
            search_input = _find_element(page, SELECTORS["search_input_placeholder"])
        if not search_input:
            logger.error("   ❌ Không tìm thấy ô input")
            return None

        # Click label để focus input (nếu có)
        label = _find_element(page, SELECTORS["label_for_input"])
        if label:
            try:
                label.click(timeout=2000)
                time.sleep(DELAYS["label_click"])
            except Exception:
                pass

        # Nhập từ khóa lần đầu
        try:
            search_input.fill("")
            time.sleep(DELAYS["input_fill"])
            search_input.fill(keyword)
            time.sleep(DELAYS["input_after_fill"])
        except Exception as e:
            logger.error(f"   ❌ Lỗi khi nhập từ khóa: {e}")
            return None

        # Tìm button tìm kiếm
        search_button = _find_element(page, SELECTORS["search_button"])
        use_enter = search_button is None

        # Nhấn button/Enter 3 lần (KHÔNG nhập lại từ khóa, chỉ click button)
        for i in range(3):
            try:
                if i > 0:
                    # Chờ trang load sau lần submit trước
                    _wait_for_load(page)

                    # Tìm lại button trên trang kết quả (không nhập lại từ khóa)
                    current_button = _find_element(page, SELECTORS["search_button"])
                    if current_button:
                        search_button = current_button
                        use_enter = False
                    else:
                        use_enter = True

                # Submit
                if use_enter:
                    if search_input:
                        search_input.press("Enter")
                    else:
                        page.keyboard.press("Enter")
                else:
                    btn = _find_element(page, SELECTORS["search_button"])
                    if btn:
                        btn.click()
                    elif search_input:
                        search_input.press("Enter")
                    else:
                        page.keyboard.press("Enter")
                    use_enter = btn is None

                _wait_for_results_after_submit(page)

            except Exception as e:
                if i == 0:
                    raise
                # Lần 2, 3 có lỗi vẫn tiếp tục

        time.sleep(DELAYS["result_stable"])
        return _extract_search_results(page, keyword)

    except Exception as e:
        logger.error(f"   ❌ Lỗi trong quá trình tìm kiếm chi tiết: {e}")
        _dump_artifacts(page, keyword, "detailed_search_error", recent_responses, recent_failed_requests, recent_console)
        return None


def _process_single_keyword(
    page: Page,
    keyword: str,
    idx: int,
    total: int,
    processed_keywords: set,
    recent_responses: deque,
    recent_failed_requests: deque,
    recent_console: deque,
    is_detailed: bool = False,
) -> Tuple[str, str, str, bool, str, str]:
    """Xử lý một từ khóa đơn lẻ"""
    kw = str(keyword).strip()
    if not kw:
        return ("", "", "", False, "", "")

    logger.info(f"[🔍] ({idx}/{total}) Đang tìm kiếm: {kw}")

    if kw in processed_keywords:
        logger.warning(f"[⚠️] Trùng lặp từ khóa: {kw}")
        return ("Trùng lặp từ khóa", "", "", False, "", "")

    processed_keywords.add(kw)

    try:
        try:
            _ = page.url
        except Exception:
            return ("Lỗi: Page không còn hoạt động", "", "", False, "", "")

        if is_detailed:
            matched_results = _perform_detailed_search(
                page, kw, recent_responses, recent_failed_requests, recent_console
            )
            if matched_results is None:
                return ("Lỗi: Không thể thực hiện tìm kiếm chi tiết", "", "", False, "", "")
        else:
            encoded_kw = urllib.parse.quote(kw, encoding="utf-8")
            url = URLS["baidu_search"].format(encoded_kw)

            if not _navigate_to_search_url(page, url, kw):
                raise PlaywrightTimeoutError(f"Navigation failed for {kw}")

            # Xử lý captcha — chờ người dùng giải thủ công
            if _is_captcha_page(page):
                _dump_artifacts(page, kw, "captcha_detected", recent_responses, recent_failed_requests, recent_console)
                solved = _wait_for_user_solve_captcha(page, kw)
                if not solved:
                    return ("__CAPTCHA_SKIPPED__", "", "", False, "", "")
                # Giải xong → navigate lại URL tìm kiếm
                if not _navigate_to_search_url(page, url, kw):
                    raise PlaywrightTimeoutError(f"Navigation failed after captcha for {kw}")

            if not _wait_for_search_results(page, kw):
                if _is_captcha_page(page):
                    _dump_artifacts(page, kw, "captcha_redirect", recent_responses, recent_failed_requests, recent_console)
                    solved = _wait_for_user_solve_captcha(page, kw)
                    if not solved:
                        return ("__CAPTCHA_SKIPPED__", "", "", False, "", "")
                    # Giải xong → navigate lại
                    if not _navigate_to_search_url(page, url, kw):
                        raise PlaywrightTimeoutError(f"Navigation failed after captcha for {kw}")
                    if not _wait_for_search_results(page, kw):
                        raise PlaywrightTimeoutError(f"Timeout after captcha solved for {kw}")
                else:
                    _dump_artifacts(page, kw, "selector_timeout", recent_responses, recent_failed_requests, recent_console)
                    raise PlaywrightTimeoutError(f"Timeout waiting for results for {kw}")

            matched_results = _extract_search_results(page, kw)

        if matched_results:
            best = max(
                matched_results,
                key=lambda x: (
                    TIME_PRIORITY.get(x["time_tag"], 0),
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
                best.get("original_title", best["title"]),
            )
        else:
            return ("", "", "", False, "", "")

    except PlaywrightTimeoutError:
        error_msg = "Lỗi: Timeout khi tải trang"
        logger.error(f"[❌] {error_msg} – {kw}")
        # Không đóng page — navigate về blank để reset state, giữ page sống cho từ khóa tiếp theo
        try:
            page.goto("about:blank", wait_until="commit", timeout=5000)
        except Exception:
            pass
        return (error_msg, "", "", False, "", "")
    except PlaywrightError as e:
        msg = str(e)
        logger.error(f"[❌] PlaywrightError – {kw}: {msg}")
        # Nếu page/context đã chết thực sự thì để worker loop tạo page mới
        # Không gọi page.close() để tránh làm hỏng page còn sống
        try:
            page.goto("about:blank", wait_until="commit", timeout=5000)
        except Exception:
            pass
        return (f"Lỗi: {msg[:80]}", "", "", False, "", "")
    except Exception as e:
        error_msg = f"Lỗi: {str(e)[:50]}"
        logger.exception(f"[❌] {error_msg} – {kw}")
        try:
            page.goto("about:blank", wait_until="commit", timeout=5000)
        except Exception:
            pass
        return (error_msg, "", "", False, "", "")


def _kill_browser_pid(pid: int) -> None:
    """Kill toàn bộ Chrome process tree và chờ chúng thoát hẳn"""
    if not pid:
        return
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=8)
        logger.info(f"[🔒] taskkill /F /T /PID {pid}")
    except Exception:
        pass
    # Fallback psutil
    try:
        parent = psutil.Process(pid)
        for child in parent.children(recursive=True):
            try: child.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied): pass
        parent.kill()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    except Exception:
        pass
    # Chờ process thoát hẳn (tối đa 10s)
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            still_alive = any(
                "chrome.exe" in (p.name() or "").lower()
                and str(pid) in " ".join(p.cmdline() or [])
                for p in psutil.process_iter(["name", "cmdline"])
            )
            if not still_alive:
                break
        except Exception:
            break
        time.sleep(0.5)
    time.sleep(2.0)  # buffer cho OS giải phóng file handle & profile lock


def _kill_all_chrome_on_profile(profile_path: str) -> None:
    """Kill toàn bộ Chrome process đang sử dụng profile_path (phòng trường hợp PID cũ không được track)."""
    profile_norm = str(profile_path).replace("\\", "/").lower()
    killed_pids: list = []
    try:
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = (proc.info["name"] or "").lower()
                if "chrome" not in name:
                    continue
                cmd = " ".join(proc.info["cmdline"] or "").replace("\\", "/").lower()
                if profile_norm in cmd:
                    try:
                        proc.kill()
                        killed_pids.append(proc.info["pid"])
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except Exception:
                continue
    except Exception:
        pass
    if killed_pids:
        logger.info(f"[🔒] Đã kill {len(killed_pids)} Chrome process dùng profile cũ: {killed_pids}")
        # Chờ các process thoát hẳn
        deadline = time.time() + 10
        while time.time() < deadline:
            still_any = False
            try:
                for proc in psutil.process_iter(["pid", "name"]):
                    if proc.info["pid"] in killed_pids:
                        still_any = True
                        break
            except Exception:
                break
            if not still_any:
                break
            time.sleep(0.5)
        time.sleep(2.0)  # buffer cho OS giải phóng profile lock


def _launch_browser(p, use_temp_profile: bool = False, headless: bool = False, location: str = "default"):
    """Launch browser, trả về (browser, browser_context, browser_pid, temp_dir)."""
    _browser = None
    _browser_context = None
    _browser_pid = None
    _temp_dir = None

    loc_prof = LOCATION_PROFILES.get(location, LOCATION_PROFILES["default"])
    locale = loc_prof.get("locale")
    timezone_id = loc_prof.get("timezone_id")
    geolocation = loc_prof.get("geolocation")
    permissions = ["geolocation"] if geolocation else None

    if use_temp_profile:
        _temp_dir = tempfile.mkdtemp(prefix="chrome_tmp_")
        logger.info(f"💻 Dùng profile tạm: {_temp_dir}")
    else:
        logger.info("💻 Chạy ở chế độ Local (Chrome profile)")
        # Kill trước mọi Chrome còn sót lại đang giữ profile, rồi mới xóa lock
        _kill_all_chrome_on_profile(PROFILE_PATH)
        _cleanup_chrome_locks(PROFILE_PATH)

    profile_dir = _temp_dir or PROFILE_PATH
    try:
        _browser_context = p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=headless,
            executable_path=CHROME_PATH,
            viewport=BROWSER_CONFIG.get("viewport", {"width": 390, "height": 844}),
            user_agent=BROWSER_CONFIG.get("user_agent"),
            locale=locale,
            timezone_id=timezone_id,
            geolocation=geolocation,
            permissions=permissions,
        )
        logger.info(f"   ✅ Persistent context: {'profile tạm' if _temp_dir else 'profile gốc'}")
    except Exception as exc:
        err = str(exc)
        # exitCode=21 = ERROR_NOT_READY → profile đang bị lock bởi Chrome cũ
        is_lock = any(k in err for k in (
            "ProcessSingleton", "profile is already in use",
            "Lock file", "TargetClosedError", "exitCode=21",
        ))
        if is_lock and not use_temp_profile:
            logger.warning("[⚠️] Profile bị lock (exitCode=21?) — thử kill Chrome & xóa lock rồi launch lại")
            _kill_all_chrome_on_profile(PROFILE_PATH)
            _cleanup_chrome_locks(PROFILE_PATH)
            try:
                # Retry lần 2 với profile gốc sau khi đã dọn dẹp
                _browser_context = p.chromium.launch_persistent_context(
                    user_data_dir=PROFILE_PATH,
                    headless=headless,
                    executable_path=CHROME_PATH,
                    viewport=BROWSER_CONFIG.get("viewport", {"width": 390, "height": 844}),
                    user_agent=BROWSER_CONFIG.get("user_agent"),
                    locale=locale,
                    timezone_id=timezone_id,
                    geolocation=geolocation,
                    permissions=permissions,
                )
                logger.info("   ✅ Retry thành công với profile gốc")
            except Exception as exc2:
                logger.warning(f"[⚠️] Vẫn lỗi sau retry: {exc2} — fallback sang profile tạm")
                _temp_dir = tempfile.mkdtemp(prefix="chrome_tmp_")
                _browser_context = p.chromium.launch_persistent_context(
                    user_data_dir=_temp_dir,
                    headless=headless,
                    executable_path=CHROME_PATH,
                    viewport=BROWSER_CONFIG.get("viewport", {"width": 390, "height": 844}),
                    user_agent=BROWSER_CONFIG.get("user_agent"),
                    locale=locale,
                    timezone_id=timezone_id,
                    geolocation=geolocation,
                    permissions=permissions,
                )
                logger.info(f"   ✅ Dùng profile tạm: {_temp_dir}")
        else:
            raise

    # Lấy PID Chrome
    profile_used = _temp_dir or PROFILE_PATH
    try:
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if "chrome.exe" in (proc.info["name"] or "").lower():
                    cmd = " ".join(proc.info["cmdline"] or [])
                    if "--remote-debugging-pipe" in cmd and profile_used in cmd:
                        _browser_pid = proc.info["pid"]
                        logger.info(f"   🔢 Chrome PID: {_browser_pid}")
                        break
            except Exception:
                continue
    except Exception as exc:
        logger.warning(f"   ⚠️ Không lấy được PID: {exc}")

    return _browser, _browser_context, _browser_pid, _temp_dir


def _close_browser(browser, browser_context, browser_pid, temp_dir=None) -> None:
    """Đóng browser hoàn toàn và xóa profile tạm nếu có.

    Gọi browser_context.close() trực tiếp trên thread hiện tại (bắt buộc bởi
    Playwright greenlet). Dùng watchdog thread để force kill nếu close() bị treo.
    """
    logger.info(f"[DEBUG] _close_browser: context={browser_context is not None}, "
                f"pid={browser_pid}, temp={temp_dir}")

    graceful_ok = False

    # Watchdog: nếu close() treo quá 15s → force kill
    watchdog_fired = threading.Event()
    def _watchdog():
        if not watchdog_fired.wait(timeout=15):
            logger.warning("[⚠️] close() treo quá 15s → force kill Chrome")
            if browser_pid:
                _kill_browser_pid(browser_pid)

    if browser_context is not None:
        # Khởi động watchdog trước khi gọi close
        wd = threading.Thread(target=_watchdog, daemon=True)
        wd.start()

        try:
            browser_context.close()
            graceful_ok = True
            logger.info("[✅] Browser context đã đóng sạch")
        except Exception as e:
            logger.warning(f"[⚠️] browser_context.close() lỗi: {e}")
        finally:
            watchdog_fired.set()  # Hủy watchdog

    if browser is not None:
        try:
            browser.close()
        except Exception:
            pass

    # Force kill chỉ khi graceful close thất bại
    if not graceful_ok and browser_pid:
        logger.info(f"[🔒] Force kill Chrome PID {browser_pid}")
        _kill_browser_pid(browser_pid)

    if temp_dir:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.info(f"   🗑 Đã xóa profile tạm: {temp_dir}")
        except Exception:
            pass


def _search_keywords_common(keywords: List[str], is_detailed: bool = False,
                            on_progress=None, on_result=None, stop_event=None,
                            headless: bool = False, location: str = "default") -> None:
    """Hàm chung để tìm kiếm từ khóa (thông thường hoặc chi tiết)"""
    if not keywords:
        logger.error("❌ Không có từ khóa để tìm kiếm!")
        return

    results: List = []
    processed_keywords: set = set()
    counters = {"duplicate": 0, "success": 0, "error": 0}

    done_event = threading.Event()
    exception_holder = {"exc": None}

    def _playwright_worker():
        """Worker thread that manages browser lifecycle with proper cleanup"""
        p = None
        browser = None
        browser_context = None
        _browser_pid = None
        _temp_dir = None

        try:
            p = sync_playwright().start()
            mode_label = 'headless' if headless else 'visible'
            logger.info(f"[🌐] Khởi động Chrome ({mode_label}, vị trí: {location})")
            browser, browser_context, _browser_pid, _temp_dir = _launch_browser(p, headless=headless, location=location)

            page = browser_context.new_page()
            recent_responses, recent_failed_requests, recent_console = _setup_debug_handlers(page)

            total = len(keywords)

            for idx, kw in enumerate(keywords, start=1):
                # Kiểm tra stop signal
                if stop_event and stop_event.is_set():
                    logger.info("⏹ Đã dừng tìm kiếm Baidu.")
                    break
                # Gọi on_progress
                if on_progress:
                    on_progress(idx, total, kw)
                # Khôi phục page nếu bị đóng hoặc không còn dùng được
                _page_ok = False
                try:
                    _ = page.url
                    page.evaluate("1")  # ping thực sự để phát hiện page chết
                    _page_ok = True
                except Exception:
                    _page_ok = False

                if not _page_ok:
                    logger.warning(f"[⚠️] Page không còn hoạt động – tạo page mới cho: {kw}")
                    try:
                        page = browser_context.new_page()
                        recent_responses, recent_failed_requests, recent_console = _setup_debug_handlers(page)
                        logger.info("[✅] Đã tạo page mới thành công")
                    except Exception as e:
                        logger.error(f"[❌] Không thể tạo page mới: {e}")
                        remaining = keywords[idx - 1:]
                        for _rem_kw in remaining:
                            results.append(("Lỗi: Không thể tạo page mới", "", "", False, "", ""))
                            counters["error"] += 1
                        break

                # ── Retry loop: thử lại từ khóa bị timeout/lỗi ──
                result = None
                max_retries = NO_RESULT_MAX_RETRIES
                for attempt in range(1, max_retries + 1):
                    # Xóa keyword khỏi processed_keywords nếu đang retry
                    # (lần đầu thì chưa có, các lần sau thì cần xóa để không bị "Trùng lặp")
                    if attempt > 1:
                        processed_keywords.discard(kw)

                    # Lần cuối: chuyển sang tìm kiếm chi tiết (homepage → gõ → search)
                    use_detailed = is_detailed or (attempt == max_retries)
                    if attempt == max_retries and not is_detailed:
                        logger.info(
                            f"[🔄] Retry {attempt}/{max_retries} cho '{kw}' "
                            f"— chuyển sang tìm kiếm chi tiết"
                        )

                    result = _process_single_keyword(
                        page, kw, idx, total, processed_keywords,
                        recent_responses, recent_failed_requests, recent_console,
                        use_detailed,
                    )

                    # Xử lý captcha: người dùng đã bỏ qua (không giải)
                    if result[0] == "__CAPTCHA_SKIPPED__":
                        logger.warning(f"[⏭] Người dùng bỏ qua captcha: {kw}")
                        result = ("Lỗi: Bị captcha Baidu", "", "", False, "", "")
                        break  # Captcha → không retry

                    # Kiểm tra kết quả có phải lỗi timeout/tải trang không
                    is_retryable_error = (
                        result[0].startswith("Lỗi") and
                        result[0] != "Trùng lặp từ khóa"
                    )

                    if is_retryable_error and attempt < max_retries:
                        retry_delay = random.uniform(
                            NO_RESULT_RETRY_DELAY_MIN,
                            NO_RESULT_RETRY_DELAY_MAX,
                        )
                        logger.warning(
                            f"[🔄] Retry {attempt}/{max_retries} cho '{kw}' "
                            f"(lỗi: {result[0]}) — chờ {retry_delay:.1f}s"
                        )
                        time.sleep(retry_delay)

                        # Khôi phục page trước khi retry
                        _page_ok = False
                        try:
                            _ = page.url
                            page.evaluate("1")
                            _page_ok = True
                        except Exception:
                            _page_ok = False

                        if not _page_ok:
                            logger.warning(f"[⚠️] Page chết trước retry – tạo page mới")
                            try:
                                page = browser_context.new_page()
                                recent_responses, recent_failed_requests, recent_console = _setup_debug_handlers(page)
                                logger.info("[✅] Đã tạo page mới cho retry")
                            except Exception as e:
                                logger.error(f"[❌] Không thể tạo page mới cho retry: {e}")
                                break  # Không thể retry, giữ kết quả lỗi hiện tại
                        continue  # Retry lại từ khóa
                    else:
                        # Thành công hoặc hết retry
                        if is_retryable_error and attempt == max_retries:
                            logger.error(
                                f"[❌] Đã hết {max_retries} lần retry cho '{kw}' "
                                f"– lỗi cuối: {result[0]}"
                            )
                        break

                # Gọi on_result ngay sau khi có kết quả cuối cùng
                if on_result:
                    on_result(idx, kw, result)

                if result[0] == "Trùng lặp từ khóa":
                    counters["duplicate"] += 1
                elif result[0] and not result[0].startswith("Lỗi"):
                    counters["success"] += 1
                elif result[0].startswith("Lỗi"):
                    counters["error"] += 1

                results.append(result)

                # Delay giữa các request
                if counters["error"] > 0 and idx < total:
                    delay = random.uniform(DELAYS["error_min"], DELAYS["error_max"])
                elif is_detailed:
                    delay = random.uniform(DELAYS["detailed_min"], DELAYS["detailed_max"])
                else:
                    delay = random.uniform(DELAYS["normal_min"], DELAYS["normal_max"])
                time.sleep(delay)

        except Exception as e:
            logger.error(f"[❌] Lỗi trong playwright_worker: {e}", exc_info=True)
            exception_holder["exc"] = e
        finally:
            logger.info("[🔒] Đang đóng browser...")
            _close_browser(browser, browser_context, _browser_pid, _temp_dir)

            # Dừng playwright (background thread, không block)
            if p is not None:
                def _stop_p():
                    try: p.stop()
                    except Exception: pass
                threading.Thread(target=_stop_p, daemon=True).start()

            done_event.set()

    # Chạy worker trong thread daemon — dùng done_event để biết khi nào xong
    worker = threading.Thread(target=_playwright_worker, daemon=True)
    worker.start()

    # Timeout tổng: mỗi keyword tối đa 60s + 30s buffer
    total_timeout = len(keywords) * 60 + 30
    try:
        done_event.wait(timeout=total_timeout)
    except KeyboardInterrupt:
        logger.info("\n⏹ Nhận Ctrl+C — đang dừng và lưu kết quả...")
        done_event.wait(timeout=10)  # chờ worker dọn dẹp tối đa 10s

    if not done_event.is_set():
        logger.warning("[⚠️] Worker không kết thúc — force-killing Chrome...")
        try:
            subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], capture_output=True, timeout=4)
        except Exception:
            pass
        done_event.wait(timeout=5)

    if results:
        logger.info("[💾] Đang ghi kết quả vào Excel...")
        try:
            write_search_results(
                keywords, results,
                counters["duplicate"], counters["success"], counters["error"],
                len(keywords),
            )
        except KeyboardInterrupt:
            logger.warning("[⚠️] Ctrl+C trong lúc ghi Excel — file có thể chưa hoàn chỉnh.")
        except Exception as e:
            logger.error(f"[❌] Lỗi ghi Excel: {e}")
    else:
        logger.info("[ℹ️] Không có kết quả để ghi.")


def search_keywords(keywords: List[str], on_progress=None, on_result=None, stop_event=None,
                    headless: bool = False, location: str = "default") -> None:
    """Tìm kiếm từ khóa trên Baidu (thông thường)"""
    _search_keywords_common(keywords, is_detailed=False,
                            on_progress=on_progress, on_result=on_result, stop_event=stop_event,
                            headless=headless, location=location)


def search_keywords_detailed(keywords: List[str], on_progress=None, on_result=None, stop_event=None,
                             headless: bool = False, location: str = "default") -> None:
    """Tìm kiếm từ khóa trên Baidu với chi tiết (nhấn button 3 lần)"""
    _search_keywords_common(keywords, is_detailed=True,
                            on_progress=on_progress, on_result=on_result, stop_event=stop_event,
                            headless=headless, location=location)
