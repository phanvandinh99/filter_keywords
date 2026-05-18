"""
Cấu hình ứng dụng — đọc từ biến môi trường.

Trên Windows (local): DOCKER không được set → IS_DOCKER = False
Trên Docker (Linux) : đặt DOCKER=1 trong .env → IS_DOCKER = True
"""
import os

# ── Chế độ chạy ──────────────────────────────────────────────
# DOCKER=1  → headless, dùng Playwright Chromium, không cần Chrome profile
# DOCKER=0  → dùng Chrome profile trên Windows (mặc định)
IS_DOCKER = os.environ.get("DOCKER", "0").strip() == "1"

# ── Chrome profile (chỉ dùng khi IS_DOCKER=False) ────────────
PROFILE_PATH = os.environ.get(
    "PROFILE_PATH",
    r"C:\Users\DINH\AppData\Local\Google\Chrome\User Data\Profile 9",
)
CHROME_PATH = os.environ.get(
    "CHROME_PATH",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
)

# ── File Excel output ─────────────────────────────────────────
EXCEL_FILE = os.environ.get("EXCEL_FILE", "keywords.xlsx")
