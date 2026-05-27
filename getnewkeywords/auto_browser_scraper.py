from playwright.sync_api import sync_playwright
import time
import random
import urllib.parse
import os
import signal
import sys
from pathlib import Path


# ============= CONFIG =============
# Thư mục chứa module này
MODULE_DIR = Path(__file__).parent.resolve()
# Thư mục gốc của project (thư mục cha)
PROJECT_DIR = MODULE_DIR.parent.resolve()

# input_keywords.txt nằm ở thư mục gốc để dễ chỉnh sửa
INPUT_FILE = PROJECT_DIR / "input_keywords.txt"
# Các file còn lại nằm trong thư mục module
# keywords.txt = danh sách từ khóa đã có (dùng để đối chiếu trùng lặp)
KEYWORDS_FILE = MODULE_DIR / "keywords.txt"
TEMP_FILE = MODULE_DIR / "keywords_temp.txt"

HEADLESS = True
WAIT_TIME = 3
DELAY_BETWEEN_KEYWORDS = 2      # Tăng lên để giảm nguy cơ bị captcha
DELAY_RANDOM_EXTRA = 1.5        # Thêm delay ngẫu nhiên tối đa (giây)
MAX_CAPTCHA_RETRIES = 3         # Số lần restart browser khi gặp captcha
CAPTCHA_RESTART_DELAY = 5       # Chờ trước khi restart browser (giây)
# ==================================

# Dấu hiệu nhận biết trang captcha của Baidu
CAPTCHA_URL_PATTERNS = [
    'wappass.baidu.com/static/captcha',
    'wappass.baidu.com/wp/',
    'wappass.baidu.com/cap/',
]
CAPTCHA_TITLE_PATTERNS = [
    '百度安全验证',
    'baidu security',
]


# Global variable for graceful shutdown
all_collected_keywords = set()
interrupted = False


def signal_handler(sig, frame):
    global interrupted
    interrupted = True
    print("\n\n⚠️ Interrupted! Saving collected keywords...")


def is_captcha_page(page) -> bool:
    """Kiểm tra xem trang hiện tại có phải trang captcha của Baidu không."""
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


def create_browser_and_context(playwright):
    """Tạo browser và context mới (dùng khi khởi động hoặc restart sau captcha)."""
    browser = playwright.chromium.launch(
        headless=HEADLESS,
        args=[
            '--disable-blink-features=AutomationControlled',
            '--disable-dev-shm-usage',
            '--no-sandbox',
            '--disable-gpu'
        ]
    )
    context = browser.new_context(
        viewport={'width': 390, 'height': 844},
        user_agent='Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
        locale='zh-CN'
    )
    context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    return browser, context


def _resolve_path(filename) -> Path:
    """Resolve file path — chấp nhận cả str lẫn Path object"""
    p = Path(filename)
    if p.is_absolute():
        return p
    return MODULE_DIR / filename


def load_keywords_from_file(filename) -> list:
    path = _resolve_path(filename)
    if not path.exists():
        return []
    with open(path, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip() and not line.startswith('#')]


def load_old_keywords(filename) -> set:
    path = _resolve_path(filename)
    if not path.exists():
        return set()
    with open(path, 'r', encoding='utf-8') as f:
        return {line.strip() for line in f if line.strip()}


def save_temp_results(keywords, filename) -> None:
    """Save temporary results"""
    try:
        path = _resolve_path(filename)
        with open(path, 'w', encoding='utf-8') as f:
            for kw in sorted(keywords):
                f.write(kw + '\n')
    except Exception:
        pass


def scrape_keywords_for_keyword(page, keyword):
    """
    Trả về (collected_keywords: set, captcha_detected: bool)
    """
    collected_keywords = set()
    api_received = False
    captcha_detected = False

    def handle_response(response):
        nonlocal api_received
        try:
            if '/rec' in response.url and 'platform=wise' in response.url and response.status == 200:
                data = response.json()
                if data.get('psstatus') == 0:
                    api_received = True
                    rcmd_list = data.get('rs', {}).get('rcmd', {}).get('list', [])
                    for item in rcmd_list:
                        collected_keywords.update(item.get('up', []))
                        collected_keywords.update(item.get('down', []))
        except:
            pass

    page.on('response', handle_response)

    try:
        search_url = f'https://m.baidu.com/s?word={urllib.parse.quote(keyword)}'
        page.goto(search_url, wait_until='domcontentloaded', timeout=15000)

        # Kiểm tra captcha ngay sau khi load
        if is_captcha_page(page):
            captcha_detected = True
            print(f"    🚫 Captcha detected!")
            return collected_keywords, captcha_detected

        # Smart wait with interrupt check
        start_time = time.time()
        while not api_received and (time.time() - start_time) < WAIT_TIME and not interrupted:
            # Kiểm tra captcha trong lúc chờ
            if is_captcha_page(page):
                captcha_detected = True
                print(f"    🚫 Captcha detected during wait!")
                return collected_keywords, captcha_detected
            page.evaluate('window.scrollTo(0, document.body.scrollHeight);')
            time.sleep(0.3)

        if not api_received and not interrupted:
            page.evaluate('window.scrollTo(0, document.body.scrollHeight);')
            time.sleep(0.5)

    except Exception as e:
        if not interrupted:
            print(f"    ⚠️ {str(e)[:50]}")
    finally:
        page.remove_listener('response', handle_response)

    return collected_keywords, captcha_detected


def save_results(all_keywords: set, existing_keywords: set) -> set:
    """Tính toán từ khóa MỚI (chưa có trong existing_keywords). Không ghi file.
    Việc cập nhật keywords.txt do người dùng thực hiện thủ công."""
    print("\n" + "=" * 70)
    print("📊 RESULTS")
    print("=" * 70)

    if all_keywords:
        print(f"📦 Total collected: {len(all_keywords)} keywords")

        new_keywords = all_keywords - existing_keywords
        duplicate_count = len(all_keywords) - len(new_keywords)

        print(f"🔄 Duplicates (already in keywords.txt): {duplicate_count}")
        print(f"✨ NEW: {len(new_keywords)}")

        if new_keywords:
            sample_size = min(20, len(new_keywords))
            print(f"\n📋 NEW keywords (showing {sample_size}/{len(new_keywords)}):")
            print("-" * 70)
            for kw in sorted(new_keywords)[:sample_size]:
                print(f"  {kw}")
            if len(new_keywords) > sample_size:
                print(f"  ... and {len(new_keywords) - sample_size} more")
        else:
            print(f"\n⚠️ No new keywords (all duplicates)")

        # Clean up temp file
        if TEMP_FILE.exists():
            TEMP_FILE.unlink()

        return new_keywords
    else:
        print("⚠️ No keywords collected")
        return set()


def run(log_func=None) -> set:
    """
    Hàm chính — có thể gọi từ bên ngoài (ví dụ: từ main.py của project cha).
    Trả về tập từ khóa MỚI thu thập được (set), hoặc set rỗng nếu không có gì.
    log_func: optional callback(text, level='info') — gọi khi có tiến trình keyword
    """
    global all_collected_keywords, interrupted
    all_collected_keywords = set()
    interrupted = False

    # Setup signal handler for Ctrl+C
    try:
        signal.signal(signal.SIGINT, signal_handler)
    except ValueError:
        pass

    print("🚀 Baidu Keyword Scraper (Optimized)")
    print("=" * 70)

    # Kiểm tra file input
    if not INPUT_FILE.exists():
        with open(INPUT_FILE, 'w', encoding='utf-8') as f:
            f.write("# Danh sách từ khóa cần lấy gợi ý từ Baidu (mỗi từ khóa một dòng)\n")
            f.write("# Dòng bắt đầu bằng # sẽ bị bỏ qua\n\n")
        print(f"✅ Created: {INPUT_FILE}")
        print(f"💡 Hãy thêm từ khóa vào file: {INPUT_FILE}")
        return set()

    input_keywords = load_keywords_from_file(INPUT_FILE)
    if not input_keywords:
        print(f"❌ Không có từ khóa trong {INPUT_FILE}")
        return set()

    print(f"📥 {len(input_keywords)} keywords to process")

    old_keywords = load_old_keywords(KEYWORDS_FILE)
    if old_keywords:
        print(f"📚 {len(old_keywords)} existing keywords loaded from keywords.txt")

    print(f"💡 Press Ctrl+C to stop and save progress")
    print("=" * 70)

    start_time = time.time()
    i = 0

    try:
        with sync_playwright() as p:
            print("\n🌐 Starting browser...")

            browser, context = create_browser_and_context(p)
            page = context.new_page()

            # Vào trang chủ một lần
            page.goto('https://m.baidu.com/', wait_until='domcontentloaded', timeout=10000)
            time.sleep(1)

            captcha_retry_count = 0

            # Xử lý từng từ khóa
            keyword_index = 0
            while keyword_index < len(input_keywords):
                if interrupted:
                    break

                keyword = input_keywords[keyword_index]
                i = keyword_index + 1
                if log_func:
                    # Gọi callback trực tiếp — không dùng print để tránh log 2 lần
                    log_func(f"[{i}/{len(input_keywords)}] {keyword}", "info")
                else:
                    print(f"\n[{i}/{len(input_keywords)}] {keyword}")

                keywords, captcha_detected = scrape_keywords_for_keyword(page, keyword)

                if captcha_detected:
                    captcha_retry_count += 1
                    if captcha_retry_count > MAX_CAPTCHA_RETRIES:
                        print(f"    ❌ Captcha retry limit ({MAX_CAPTCHA_RETRIES}) reached. Stopping.")
                        interrupted = True
                        break

                    print(f"    🔄 Restarting browser to bypass captcha (attempt {captcha_retry_count}/{MAX_CAPTCHA_RETRIES})...")
                    try:
                        browser.close()
                    except Exception:
                        pass

                    time.sleep(CAPTCHA_RESTART_DELAY)

                    browser, context = create_browser_and_context(p)
                    page = context.new_page()
                    page.goto('https://m.baidu.com/', wait_until='domcontentloaded', timeout=10000)
                    time.sleep(1)
                    print(f"    ✅ Browser restarted. Retrying keyword...")
                    # Không tăng index — thử lại keyword hiện tại
                    continue

                # Keyword xử lý thành công (dù có kết quả hay không)
                captcha_retry_count = 0  # Reset counter khi thành công
                keyword_index += 1

                if keywords:
                    print(f"    ✅ {len(keywords)} keywords")
                    all_collected_keywords.update(keywords)

                    # Lưu tạm mỗi 5 từ khóa
                    if i % 5 == 0:
                        save_temp_results(all_collected_keywords, TEMP_FILE)
                else:
                    print(f"    ⚠️ No keywords")

                if keyword_index < len(input_keywords) and not interrupted:
                    delay = DELAY_BETWEEN_KEYWORDS + random.uniform(0, DELAY_RANDOM_EXTRA)
                    time.sleep(delay)

            browser.close()

    except KeyboardInterrupt:
        interrupted = True
        print("\n\n⚠️ Interrupted by user!")

    except Exception as e:
        print(f"\n❌ Error: {e}")

    finally:
        elapsed = time.time() - start_time

        if all_collected_keywords:
            if interrupted:
                print(f"\n⚠️ Stopped at keyword {i}/{len(input_keywords)}")

            print(f"⏱️ Time: {elapsed:.1f}s")

            new_keywords = save_results(all_collected_keywords, old_keywords)

            print("\n" + "=" * 70)
            if interrupted:
                print("⚠️ INTERRUPTED - Results saved")
            else:
                print("✅ DONE!")
            print("=" * 70)

            return new_keywords
        else:
            print(f"\n⏱️ Time: {elapsed:.1f}s")
            print("⚠️ No keywords collected")
            return set()


def main():
    run()


if __name__ == "__main__":
    main()
