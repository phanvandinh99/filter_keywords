"""
Cấu hình ứng dụng — đọc từ biến môi trường.
"""
import os

# ── Chrome profile ────────────────────────────────────────────
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
