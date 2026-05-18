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
    """Trích xuất domain từ data-log attribute"""
    try:
        data_log = container.get_attribute("data-log")
        if data_log:
            info = json.loads(data_log)
            mu = info.get("mu") or ""
            if mu:
                return urlparse(mu).netloc
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
    Ví dụ: 'baidu.com' sẽ match m.baidu.com, tieba.baidu.com, www.baidu.com, ...
    """
    if not domain:
        return False
    domain_lower = domain.lower().rstrip(".")
    for blocked in BLOCKED_DOMAINS:
        blocked_lower = blocked.lower()
        if domain_lower == blocked_lower or domain_lower.endswith("." + blocked_lower):
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

        # Bỏ qua kết quả từ domain bị chặn (*.baidu.com, ...)
        if _is_blocked_domain(domain):
            logger.debug(f"[⛔] Bỏ qua domain bị chặn: {domain} | {title[:40]}")
            continue

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
    # Chờ process thoát hẳn (tối đa 8s)
    deadline = time.time() + 8
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
    time.sleep(1.0)  # buffer cho OS giải phóng file handle


def _launch_browser(p, use_temp_profile: bool = False):
    """Launch browser, trả về (browser, browser_context, browser_pid, temp_dir)."""
    _browser = None
    _browser_context = None
    _browser_pid = None
    _temp_dir = None

    if use_temp_profile:
        _temp_dir = tempfile.mkdtemp(prefix="chrome_tmp_")
        logger.info(f"💻 Dùng profile tạm: {_temp_dir}")
    else:
        logger.info("💻 Chạy ở chế độ Local (Chrome profile)")
        _cleanup_chrome_locks(PROFILE_PATH)

    profile_dir = _temp_dir or PROFILE_PATH
    try:
        _browser_context = p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=False,
            executable_path=CHROME_PATH,
            viewport=BROWSER_CONFIG.get("viewport", {"width": 390, "height": 844}),
            user_agent=BROWSER_CONFIG.get("user_agent"),
        )
        logger.info(f"   ✅ Persistent context: {'profile tạm' if _temp_dir else 'profile gốc'}")
    except Exception as exc:
        err = str(exc)
        is_lock = any(k in err for k in (
            "ProcessSingleton", "profile is already in use",
            "Lock file", "TargetClosedError",
        ))
        if is_lock and not use_temp_profile:
            logger.warning("[⚠️] Profile bị lock — fallback sang profile tạm")
            _temp_dir = tempfile.mkdtemp(prefix="chrome_tmp_")
            _browser_context = p.chromium.launch_persistent_context(
                user_data_dir=_temp_dir,
                headless=False,
                executable_path=CHROME_PATH,
                viewport=BROWSER_CONFIG.get("viewport", {"width": 390, "height": 844}),
                user_agent=BROWSER_CONFIG.get("user_agent"),
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
    """Đóng browser hoàn toàn và xóa profile tạm nếu có."""
    if browser_pid:
        _kill_browser_pid(browser_pid)
    else:
        if browser_context is not None:
            done = threading.Event()
            def _do_close():
                try: browser_context.close()
                except Exception: pass
                finally: done.set()
            t = threading.Thread(target=_do_close, daemon=True)
            t.start()
            t.join(timeout=10)
        if browser is not None:
            try: browser.close()
            except Exception: pass
    if temp_dir:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.info(f"   🗑 Đã xóa profile tạm: {temp_dir}")
        except Exception:
            pass


def _search_keywords_common(keywords: List[str], is_detailed: bool = False) -> None:
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
            browser, browser_context, _browser_pid, _temp_dir = _launch_browser(p)

            page = browser_context.new_page()
            recent_responses, recent_failed_requests, recent_console = _setup_debug_handlers(page)

            total = len(keywords)

            for idx, kw in enumerate(keywords, start=1):
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
                        # Ghi lỗi cho từ khóa hiện tại và tất cả từ khóa còn lại
                        remaining = keywords[idx - 1:]  # idx bắt đầu từ 1
                        for _rem_kw in remaining:
                            results.append(("Lỗi: Không thể tạo page mới", "", "", False, "", ""))
                            counters["error"] += 1
                        break

                result = _process_single_keyword(
                    page, kw, idx, total, processed_keywords,
                    recent_responses, recent_failed_requests, recent_console,
                    is_detailed,
                )

                # Xử lý captcha: người dùng đã bỏ qua (không giải)
                if result[0] == "__CAPTCHA_SKIPPED__":
                    logger.warning(f"[⏭] Người dùng bỏ qua captcha: {kw}")
                    result = ("Lỗi: Bị captcha Baidu", "", "", False, "", "")

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


def search_keywords(keywords: List[str]) -> None:
    """Tìm kiếm từ khóa trên Baidu (thông thường)"""
    _search_keywords_common(keywords, is_detailed=False)


def search_keywords_detailed(keywords: List[str]) -> None:
    """Tìm kiếm từ khóa trên Baidu với chi tiết (nhấn button 3 lần)"""
    _search_keywords_common(keywords, is_detailed=True)
