"""
Phân tích HTML dump từ Sogou để debug khi không lấy được kết quả.
Chạy: python analyze_sogou_dump.py [keyword_fragment]
"""
import re
import glob
import os
import sys

html_files = glob.glob("log/*no_result*.html") or glob.glob("log/*.html")
if not html_files:
    print("Không tìm thấy file HTML trong log/")
    sys.exit()

if len(sys.argv) > 1:
    target = sys.argv[1]
    html_files = [f for f in html_files if target in f]
    if not html_files:
        print(f"Không tìm thấy file chứa '{target}'")
        sys.exit()

latest = max(html_files, key=os.path.getmtime)
print(f"{'='*60}")
print(f"File: {latest}")
content = open(latest, encoding="utf-8").read()
print(f"Kích thước: {len(content)} ký tự\n")

# ── Kiểm tra loại trang ──────────────────────────────────────────────────────
checks = {
    "captcha/robot": ["captcha", "验证码", "robot", "人机验证", "安全验证"],
    "redirect/block": ["403", "forbidden", "blocked"],
    "image search":  ["pic.sogou", "图片搜索"],
    "no results":    ["很抱歉", "没有找到", "未找到相关"],
}
for label, keywords in checks.items():
    hits = [k for k in keywords if k.lower() in content.lower()]
    if hits:
        print(f"⚠️  [{label}]: {hits}")

# ── Đếm containers ───────────────────────────────────────────────────────────
print()
patterns = {
    "div.vrwrap (HTML tag)":    r'<div[^>]*class="[^"]*\bvrwrap\b[^"]*"',
    "div.vrResult":             r'<div[^>]*class="[^"]*\bvrResult\b[^"]*"',
    "div.reactResult":          r'<div[^>]*class="[^"]*\breactResult\b[^"]*"',
    "div.sgresult":             r'<div[^>]*class="[^"]*\bsgresult\b[^"]*"',
    "div.js-page-results":      r'<div[^>]*class="[^"]*\bjs-page-results\b[^"]*"',
    "li (result list)":         r'<li[^>]*class="[^"]*\bresult\b[^"]*"',
}
for name, pat in patterns.items():
    count = len(re.findall(pat, content))
    print(f"  {name}: {count}")

# ── Tìm tất cả title ─────────────────────────────────────────────────────────
print("\n=== Titles tìm thấy ===")
found_any = False

for label, pat in [
    ("resultLink", r'<a[^>]*class="[^"]*resultLink[^"]*"[^>]*>(.*?)</a>'),
    ("vr-title",   r'<h3[^>]*class="[^"]*vr-title[^"]*"[^>]*>(.*?)</h3>'),
    ("vrTitle",    r'<h3[^>]*class="[^"]*vrTitle[^"]*"[^>]*>(.*?)</h3>'),
    ("h3 a",       r'<h3[^>]*>.*?<a[^>]*>(.*?)</a>.*?</h3>'),
]:
    items = re.findall(pat, content, re.DOTALL)
    for item in items[:8]:
        clean = re.sub(r'<[^>]+>', '', item).strip()
        if clean:
            print(f"  [{label}] {clean[:90]}")
            found_any = True

if not found_any:
    print("  ⚠️  Không tìm thấy title nào!")

# ── In HTML thực của vrwrap đầu tiên ─────────────────────────────────────────
print("\n=== HTML thực của div.vrwrap đầu tiên ===")
m = re.search(r'<div[^>]*class="[^"]*\bvrwrap\b[^"]*"[^>]*>(.*?)</div>\s*</div>', content, re.DOTALL)
if m:
    snippet = re.sub(r'\s+', ' ', m.group(0))
    print(snippet[:800])
else:
    # Tìm theo id sogou_vr
    m2 = re.search(r'<div[^>]*id="sogou_vr[^"]*"[^>]*>(.*?)</div>\s*</div>', content, re.DOTALL)
    if m2:
        snippet = re.sub(r'\s+', ' ', m2.group(0))
        print(f"[sogou_vr id] {snippet[:800]}")
    else:
        print("  Không tìm thấy div.vrwrap trong HTML")

# ── Tìm div.js-page-results ──────────────────────────────────────────────────
print("\n=== div.js-page-results ===")
m3 = re.search(r'<div[^>]*class="[^"]*js-page-results[^"]*"[^>]*>(.*?)</div>', content, re.DOTALL)
if m3:
    snippet = re.sub(r'\s+', ' ', m3.group(0))
    print(snippet[:600])
else:
    print("  Không tìm thấy")
