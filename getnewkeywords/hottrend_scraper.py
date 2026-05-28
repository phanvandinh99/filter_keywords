"""
hottrend_scraper.py
Tương tự auto_browser_scraper.py nhưng thay vì lắng nghe API /rec,
scraper này mở m.baidu.com, tìm kiếm từng seed keyword, rồi
đọc DOM phần '大家还在搜' để lấy các từ khóa hottrend liên quan.
"""
from playwright.sync_api import sync_playwright
import time
import random
import urllib.parse
import json
import re
from pathlib import Path

# ============= CONFIG =============
MODULE_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = MODULE_DIR.parent.resolve()

HEADLESS = True
WAIT_TIME = 5           # Giây chờ page load xong để DOM hiện ra
DELAY_BETWEEN_KEYWORDS = 2
DELAY_RANDOM_EXTRA = 1.5
MAX_CAPTCHA_RETRIES = 3
CAPTCHA_RESTART_DELAY = 5
# ==================================

CAPTCHA_URL_PATTERNS = [
    'wappass.baidu.com/static/captcha',
    'wappass.baidu.com/wp/',
    'wappass.baidu.com/cap/',
]
CAPTCHA_TITLE_PATTERNS = [
    '百度安全验证',
    'baidu security',
]

# Global state
interrupted = False


def is_captcha_page(page) -> bool:
    try:
        current_url = page.url.lower()
        for pattern in CAPTCHA_URL_PATTERNS:
            if pattern in current_url:
                return True
        title = page.title().lower()
        for pattern in CAPTCHA_TITLE_PATTERNS:
            if pattern.lower() in title:
                return True
    except Exception:
        pass
    return False


def create_browser_and_context(playwright, headless=True):
    """Tạo browser và context mới (iPhone emulation như auto_browser_scraper)."""
    browser = playwright.chromium.launch(
        headless=headless,
        args=[
            '--disable-blink-features=AutomationControlled',
            '--disable-dev-shm-usage',
            '--no-sandbox',
            '--disable-gpu',
        ]
    )
    context = browser.new_context(
        viewport={'width': 390, 'height': 844},
        user_agent=(
            'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) '
            'AppleWebKit/605.1.15 (KHTML, like Gecko) '
            'Version/16.6 Mobile/15E148 Safari/604.1'
        ),
        locale='zh-CN',
    )
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    return browser, context


# ── JS snippet chạy trong browser để extract relation_words ──
_JS_EXTRACT_HOTTREND = r"""
() => {
    const results = [];
    const seen = new Set();

    // ── Cách 1: Tìm data-feedback JSON chứa relation_words ──
    // Tìm element có data-feedback attribute gần phần 大家还在搜
    const feedbackEls = document.querySelectorAll('[data-feedback]');
    for (const el of feedbackEls) {
        try {
            const raw = el.getAttribute('data-feedback');
            if (!raw || !raw.includes('relation_words')) continue;
            // Unescape &quot; → " nếu là HTML-encoded
            const decoded = raw
                .replace(/&quot;/g, '"')
                .replace(/&amp;/g, '&')
                .replace(/&lt;/g, '<')
                .replace(/&gt;/g, '>');
            const obj = JSON.parse(decoded);
            // Tìm relation_words đệ quy trong JSON
            const findRelWords = (o) => {
                if (!o || typeof o !== 'object') return null;
                if (o.relation_words) return o.relation_words;
                for (const v of Object.values(o)) {
                    const found = findRelWords(v);
                    if (found) return found;
                }
                return null;
            };
            const relWords = findRelWords(obj);
            if (relWords && typeof relWords === 'string') {
                const parts = relWords.split('&');
                for (const kw of parts) {
                    const trimmed = kw.trim();
                    if (trimmed && !seen.has(trimmed)) {
                        seen.add(trimmed);
                        results.push(trimmed);
                    }
                }
            }
        } catch(e) {}
    }

    // ── Cách 2: Tìm các link <a data-rgitem-type="search"> bên trong container 大家还在搜 ──
    if (results.length === 0) {
        // Tìm tất cả text nodes chứa 大家还在搜
        const walker = document.createTreeWalker(
            document.body, NodeFilter.SHOW_TEXT
        );
        let hotSection = null;
        let node;
        while ((node = walker.nextNode())) {
            if (node.textContent.includes('大家还在搜')) {
                hotSection = node.parentElement;
                break;
            }
        }
        if (hotSection) {
            // Lấy container cha (có thể cần leo lên 2-3 cấp)
            let container = hotSection;
            for (let i = 0; i < 5; i++) {
                const links = container.querySelectorAll('a[data-rgitem-type="search"]');
                if (links.length > 0) {
                    for (const a of links) {
                        const txt = a.textContent.trim();
                        if (txt && !seen.has(txt)) {
                            seen.add(txt);
                            results.push(txt);
                        }
                    }
                    break;
                }
                if (container.parentElement) container = container.parentElement;
                else break;
            }
        }
    }

    // ── Cách 3: Tìm tất cả <a data-rgitem-type="search"> trên cả trang ──
    // Chỉ dùng nếu 2 cách trên không tìm được gì
    if (results.length === 0) {
        const allLinks = document.querySelectorAll('a[data-rgitem-type="search"]');
        for (const a of allLinks) {
            const txt = a.textContent.trim();
            if (txt && !seen.has(txt)) {
                seen.add(txt);
                results.push(txt);
            }
        }
    }

    return results;
}
"""


def scrape_hottrend_for_keyword(page, keyword, log_func=None) -> tuple:
    """
    Mở m.baidu.com tìm kiếm `keyword`, đọc DOM phần 大家还在搜.
    Trả về (keywords: set, captcha_detected: bool)
    """
    collected = set()
    captcha_detected = False

    def _log(msg, level='info'):
        if log_func:
            log_func(msg, level)

    try:
        search_url = f'https://m.baidu.com/s?word={urllib.parse.quote(keyword)}'
        page.goto(search_url, wait_until='domcontentloaded', timeout=15000)

        if is_captcha_page(page):
            captcha_detected = True
            _log(f'    🚫 Captcha detected for "{keyword}"!', 'warning')
            return collected, captcha_detected

        # Scroll nhẹ để kích thích lazy-load
        page.evaluate('window.scrollTo(0, document.body.scrollHeight * 0.5);')
        time.sleep(1.0)
        page.evaluate('window.scrollTo(0, document.body.scrollHeight);')
        time.sleep(1.5)

        if is_captcha_page(page):
            captcha_detected = True
            _log(f'    🚫 Captcha detected during scroll!', 'warning')
            return collected, captcha_detected

        # Thêm thời gian chờ để section 大家还在搜 render
        time.sleep(1.0)

        # Extract bằng JS
        try:
            kws = page.evaluate(_JS_EXTRACT_HOTTREND)
            if kws:
                for kw in kws:
                    kw = kw.strip()
                    if kw:
                        collected.add(kw)
        except Exception as e:
            _log(f'    ⚠️ JS extract lỗi: {e}', 'warning')

        # Nếu JS không tìm được → thử parse HTML thô
        if not collected:
            try:
                html_text = page.content()
                fallback_kws = _parse_hottrend_from_html(html_text)
                collected.update(fallback_kws)
            except Exception as e:
                _log(f'    ⚠️ HTML fallback lỗi: {e}', 'warning')

    except Exception as e:
        if not interrupted:
            _log(f'    ⚠️ Lỗi: {str(e)[:80]}', 'warning')

    return collected, captcha_detected


def _parse_hottrend_from_html(html_text: str) -> list:
    """
    Fallback: parse HTML thô tìm relation_words trong phần 大家还在搜.
    Dùng khi JS evaluate không hoạt động.
    """
    import html as _html_mod
    results = []
    seen = set()

    idx = html_text.find('大家还在搜')
    if idx == -1:
        return results

    # Lấy vùng 15000 ký tự quanh vị trí đó
    section = html_text[max(0, idx - 500): idx + 15000]

    # Dạng HTML-encoded: relation_words&quot;:&quot;kw1&amp;kw2...
    m = re.search(r'relation_words&quot;:&quot;([^&]+(?:&amp;[^&]+)*)', section)
    if m:
        raw = _html_mod.unescape(m.group(1))
        for kw in raw.split('&'):
            kw = kw.strip()
            if kw and kw not in seen:
                seen.add(kw)
                results.append(kw)
        return results

    # Dạng JSON thuần: "relation_words":"kw1&kw2..."
    m2 = re.search(r'"relation_words"\s*:\s*"([^"]+)"', section)
    if m2:
        raw = _html_mod.unescape(m2.group(1))
        for kw in raw.split('&'):
            kw = kw.strip()
            if kw and kw not in seen:
                seen.add(kw)
                results.append(kw)

    return results


def run(seed_keywords: list, log_func=None, stop_event=None, headless: bool = True) -> set:
    """
    Hàm chính — nhận danh sách seed_keywords, scrape hottrend từ Baidu.
    Trả về set các từ khóa thu thập được (chưa dedup với keywords.txt).

    Args:
        seed_keywords: Danh sách từ khóa seed để tìm kiếm
        log_func: callback(text, level) để push log ra UI
        stop_event: threading.Event — set() để dừng
        headless: True = ẩn browser
    """
    global interrupted
    interrupted = False

    all_collected = set()

    def _log(msg, level='info'):
        if log_func:
            log_func(msg, level)
        else:
            print(msg)

    def _stopped():
        return interrupted or (stop_event is not None and stop_event.is_set())

    if not seed_keywords:
        _log('❌ Không có từ khóa seed nào để tìm.', 'error')
        return all_collected

    _log(f'🔥 Hottrend Scraper — {len(seed_keywords)} seed keywords', 'info')
    _log('=' * 60, 'info')

    try:
        with sync_playwright() as p:
            _log('🌐 Đang khởi động browser...', 'info')
            browser, context = create_browser_and_context(p, headless=headless)
            page = context.new_page()

            # Vào trang chủ Baidu mobile trước
            page.goto('https://m.baidu.com/', wait_until='domcontentloaded', timeout=10000)
            time.sleep(1)

            captcha_retry_count = 0
            keyword_index = 0

            while keyword_index < len(seed_keywords):
                if _stopped():
                    _log('⏹ Đã dừng theo yêu cầu.', 'warning')
                    break

                keyword = seed_keywords[keyword_index]
                i = keyword_index + 1
                _log(f'[{i}/{len(seed_keywords)}] 🔍 Đang tìm: {keyword}', 'info')

                kws, captcha = scrape_hottrend_for_keyword(page, keyword, log_func=_log)

                if captcha:
                    captcha_retry_count += 1
                    if captcha_retry_count > MAX_CAPTCHA_RETRIES:
                        _log(f'❌ Quá số lần retry captcha ({MAX_CAPTCHA_RETRIES}). Dừng.', 'error')
                        interrupted = True
                        break

                    _log(f'🔄 Restart browser để vượt captcha (lần {captcha_retry_count}/{MAX_CAPTCHA_RETRIES})...', 'warning')
                    try:
                        browser.close()
                    except Exception:
                        pass

                    time.sleep(CAPTCHA_RESTART_DELAY)
                    browser, context = create_browser_and_context(p, headless=headless)
                    page = context.new_page()
                    page.goto('https://m.baidu.com/', wait_until='domcontentloaded', timeout=10000)
                    time.sleep(1)
                    _log('✅ Browser restart xong. Thử lại từ khóa...', 'info')
                    continue  # Thử lại keyword hiện tại

                # Xử lý thành công
                captcha_retry_count = 0
                keyword_index += 1

                if kws:
                    _log(f'    ✅ Tìm thấy {len(kws)} từ khóa hottrend', 'success')
                    for kw in sorted(kws):
                        _log(f'       • {kw}', 'info')
                    all_collected.update(kws)
                else:
                    _log(f'    ⚠️ Không tìm thấy phần 大家还在搜 cho từ khóa này', 'warning')

                # Delay giữa các từ khóa
                if keyword_index < len(seed_keywords) and not _stopped():
                    delay = DELAY_BETWEEN_KEYWORDS + random.uniform(0, DELAY_RANDOM_EXTRA)
                    time.sleep(delay)

            try:
                browser.close()
            except Exception:
                pass

    except Exception as e:
        _log(f'❌ Lỗi nghiêm trọng trong scraper: {e}', 'error')

    _log(f'📊 Tổng cộng thu thập được: {len(all_collected)} từ khóa hottrend', 'success' if all_collected else 'warning')
    return all_collected
