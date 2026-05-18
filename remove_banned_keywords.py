"""Module loại bỏ từ khóa chứa từ cấm"""
import pandas as pd
import logging
from pathlib import Path
from typing import List, Tuple, Dict, Any
from openpyxl import load_workbook
from config import EXCEL_FILE

logger = logging.getLogger()

BANNED_KEYWORDS_FILE = "Bannedkeywords.txt"


def _read_banned_keywords(file_path: Path) -> List[str]:
    """Đọc danh sách từ cấm từ file"""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except Exception as e:
        logger.error(f"❌ Lỗi khi đọc file {file_path}: {e}", exc_info=True)
        return []


def _clean_text(text: str) -> str:
    """Làm sạch text: loại bỏ khoảng trống ở đầu/cuối"""
    return text.strip() if text else ""


def _check_contains_banned(text: str, banned_keywords: List[str]) -> List[str]:
    """Kiểm tra text có chứa từ cấm nào và trả về danh sách từ cấm tìm thấy"""
    return [banned for banned in banned_keywords if banned in text]


def _get_row_data(df: pd.DataFrame, idx: int) -> Tuple[str, str]:
    """Lấy từ khóa và title từ DataFrame"""
    kw_raw = str(df.iloc[idx, 0]) if pd.notna(df.iloc[idx, 0]) else ""
    kw = _clean_text(kw_raw)

    title = ""
    if len(df.columns) > 1:
        title_raw = str(df.iloc[idx, 1]) if pd.notna(df.iloc[idx, 1]) else ""
        title = _clean_text(title_raw)

    return kw, title


def remove_banned_keywords() -> None:
    """Loại bỏ các dòng có từ khóa hoặc title chứa từ cấm trong Bannedkeywords.txt"""
    # Kiểm tra file Excel
    excel_path = Path(EXCEL_FILE)
    if not excel_path.exists():
        logger.error(f"❌ File {EXCEL_FILE} không tồn tại!")
        return

    # Đọc file từ cấm
    banned_file_path = Path(BANNED_KEYWORDS_FILE)
    if not banned_file_path.exists():
        logger.error(f"❌ File {BANNED_KEYWORDS_FILE} không tồn tại!")
        return

    banned_keywords = _read_banned_keywords(banned_file_path)
    if not banned_keywords:
        logger.warning(f"⚠️ File {BANNED_KEYWORDS_FILE} trống!")
        return

    logger.info(
        f"📋 Đã đọc {len(banned_keywords)} từ cấm từ file {BANNED_KEYWORDS_FILE}"
    )

    # Đọc file Excel
    try:
        df = pd.read_excel(EXCEL_FILE, header=None, dtype=str)
    except Exception as e:
        logger.error(f"❌ Lỗi khi đọc file Excel: {e}", exc_info=True)
        return

    if df.empty or len(df.columns) < 1:
        logger.error("❌ File Excel trống hoặc không có cột A!")
        return

    # Tìm các dòng chứa từ cấm
    rows_to_remove = []
    matched_keywords: List[Dict[str, Any]] = []

    for idx in range(1, len(df)):  # Bắt đầu từ row 2 (index 1)
        row_num = idx + 1
        kw, title = _get_row_data(df, idx)

        # Kiểm tra từ khóa và title có chứa từ cấm
        matched_in_kw = _check_contains_banned(kw, banned_keywords)
        matched_in_title = _check_contains_banned(title, banned_keywords)
        matched_banned = list(
            set(matched_in_kw + matched_in_title)
        )  # Loại bỏ trùng lặp

        if matched_banned:
            rows_to_remove.append(row_num)
            matched_keywords.append(
                {
                    "row": row_num,
                    "keyword": kw,
                    "title": title,
                    "banned_words": matched_banned,
                }
            )

    if not rows_to_remove:
        logger.info("✅ Không tìm thấy từ khóa hoặc title nào chứa từ cấm!")
        return

    # Hiển thị thông tin các dòng sẽ bị xóa
    logger.info(f"📊 Tìm thấy {len(rows_to_remove)} dòng chứa từ cấm:")
    for match in matched_keywords:
        banned_str = ", ".join(match["banned_words"])
        title_info = f" (title: '{match['title']}')" if match["title"] else ""
        logger.info(
            f"  - Hàng {match['row']}: '{match['keyword']}'{title_info} - Từ cấm: {banned_str}"
        )

    # Xóa các dòng chứa từ cấm
    try:
        wb = load_workbook(EXCEL_FILE)
        ws = wb.active

        # Xóa các hàng từ dưới lên để không ảnh hưởng index
        for row_num in sorted(rows_to_remove, reverse=True):
            ws.delete_rows(row_num)

        wb.save(EXCEL_FILE)
        logger.info(f"✅ Đã xóa {len(rows_to_remove)} dòng chứa từ cấm!")
        logger.info(f"📝 Đã lưu vào file: {EXCEL_FILE}")

    except Exception as e:
        logger.error(f"❌ Lỗi khi xử lý file Excel: {e}", exc_info=True)
