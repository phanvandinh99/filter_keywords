"""
Script debug: Mở Chrome, tìm kiếm một từ khóa mẫu trên Baidu Mobile,
in ra HTML, data-log, và kết quả của _extract_domain cho mỗi container.
Chạy: python debug_domain.py
"""
import json
import sys
import logging
from pathlib import Path
from urllib.parse import urlparse

logging.basicConfig(level=logging.INFO, format="%(message)s")

# Thêm thư mục gốc vào path
BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from config import PROFILE_PATH, CHROME_PATH
from constants import BROWSER_CONFIG, SELECTORS
from playwright.sync_api import sync_playwright

# ── Từ khóa để debug ─────────────────────────────────────────────────────────
TEST_KEYWORD = "迷你世界"   # <-- Thay bằng từ khóa bạn đang test
# ─────────────────────────────────────────────────────────────────────────────

import urllib.parse

def main():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_PATH,
            headless=False,
            executable_path=CHROME_PATH,
            viewport={"width": 390, "height": 844},
            user_agent=BROWSER_CONFIG.get("user_agent"),
        )
        page = ctx.new_page()
        encoded = urllib.parse.quote(TEST_KEYWORD, encoding="utf-8")
        url = f"https://m.baidu.com/s?word={encoded}"
        print(f"\n🔍 Đang mở: {url}\n")
        page.goto(url, wait_until="domcontentloaded", timeout=20000)

        # Chờ kết quả xuất hiện
        try:
            page.wait_for_selector("div.c-result.result, div.c-result, article.c-container",
                                   timeout=12000, state="visible")
        except Exception:
            pass

        page.wait_for_timeout(2000)

        containers = []
        for selector in SELECTORS["result_container"]:
            containers = page.query_selector_all(selector)
            if containers:
                print(f"✅ Dùng selector: {selector!r} → {len(containers)} containers\n")
                break

        if not containers:
            print("❌ Không tìm thấy container nào!")
            ctx.close()
            return

        for i, container in enumerate(containers[:10], 1):
            print("=" * 70)
            print(f"--- Container #{i} ---")

            # Title
            title = ""
            for sel in SELECTORS["title"]:
                el = container.query_selector(sel)
                if el:
                    title = (el.text_content() or "").strip()
                    if title:
                        break
            print(f"TITLE: {title[:80]!r}")

            # data-log
            data_log_raw = container.get_attribute("data-log") or ""
            print(f"DATA-LOG (raw, 300 chars): {data_log_raw[:300]!r}")
            if data_log_raw:
                try:
                    dl = json.loads(data_log_raw)
                    for field in ("mu", "url", "pu", "di", "src"):
                        if dl.get(field):
                            print(f"  └─ data-log[{field!r}]: {dl[field]!r}")
                except Exception as e:
                    print(f"  └─ JSON parse error: {e}")

            # Các attribute khác
            for attr in ("data-mu", "mu", "data-shareurl", "data-url", "data-sf-href"):
                val = container.get_attribute(attr)
                if val:
                    print(f"ATTR [{attr}]: {val[:200]!r}")

            # Visual elements
            for sel in (".c-showurl", "span.c-showurl", ".cosc-source-text",
                        "span.cosc-source-text", ".c-source", ".c-source-text",
                        ".cosc-source", ".c-showurl-source",
                        "[class*='showurl']"):
                for el in container.query_selector_all(sel):
                    txt = (el.text_content() or "").strip()
                    if txt:
                        print(f"VISUAL [{sel}]: {txt[:100]!r}")

            # JS evaluation
            js_result = ""
            try:
                js_result = container.evaluate("""
                    el => {
                        const raw = el.getAttribute('data-log');
                        if (raw) {
                            try {
                                const obj = JSON.parse(raw);
                                for (const f of ['mu', 'url', 'pu', 'di']) {
                                    if (obj[f]) return '(data-log.' + f + ') ' + obj[f];
                                }
                            } catch(e) {}
                        }
                        for (const attr of ['data-mu', 'data-shareurl', 'data-url']) {
                            const v = el.getAttribute(attr);
                            if (v && v.startsWith('http')) return '(attr:' + attr + ') ' + v;
                        }
                        const urlSelectors = [
                            '.c-showurl', 'span.c-showurl', '.cosc-source-text',
                            'span.cosc-source-text', '.c-source', '.c-source-text',
                            '.cosc-source', '[class*="showurl"]'
                        ];
                        for (const s of urlSelectors) {
                            const e = el.querySelector(s);
                            if (e && e.textContent.trim())
                                return '(visual:' + s + ') ' + e.textContent.trim();
                        }
                        // a tags data-log
                        for (const a of el.querySelectorAll('a')) {
                            const aLog = a.getAttribute('data-log');
                            if (aLog) {
                                try {
                                    const obj = JSON.parse(aLog);
                                    for (const f of ['mu', 'url']) {
                                        if (obj[f]) return '(a.data-log.' + f + ') ' + obj[f];
                                    }
                                } catch(e) {}
                            }
                        }
                        return '(NOTHING FOUND)';
                    }
                """)
            except Exception as e:
                js_result = f"(JS ERROR: {e})"
            print(f"JS EVAL: {js_result!r}")

            # outer HTML (500 ký tự đầu)
            try:
                html = container.evaluate("el => el.outerHTML") or ""
                print(f"OUTER HTML (500 chars):\n{html[:500]}")
            except Exception:
                pass

            print()

        input("\nNhấn Enter để đóng trình duyệt...")
        ctx.close()

if __name__ == "__main__":
    main()
