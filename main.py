"""Module chính - Menu điều khiển chương trình"""
import logging
import sys
from pathlib import Path
from utils import read_keywords_from_excel, write_keywords_to_excel
from remove_duplicates import remove_duplicates_and_color
from search_keywords import search_keywords, search_keywords_detailed
from google_search import search_google_keywords
from sogou_search import search_sogou_keywords

# Thêm thư mục getnewkeywords vào sys.path để import module
_GETNEWKEYWORDS_DIR = Path(__file__).parent / "getnewkeywords"
if str(_GETNEWKEYWORDS_DIR) not in sys.path:
    sys.path.insert(0, str(_GETNEWKEYWORDS_DIR))

# === THIẾT LẬP LOGGING ===
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger()


def show_menu() -> None:
    """Hiển thị menu và xử lý lựa chọn người dùng"""
    while True:
        print("\n" + "=" * 50)
        print("📋 MENU CHỌN CHỨC NĂNG")
        print("=" * 50)
        print("1. Loại bỏ từ khóa trùng lặp")
        print("2. Thực hiện tìm kiếm (Baidu)")
        print("3. Thực hiện tìm kiếm Chi Tiết (Baidu)")
        print("4. Tìm kiếm trên Google")
        print("5. Tìm kiếm trên Sogou")
        print("6. Lấy từ khóa mới + Tìm kiếm Baidu (Tự động)")
        print("0. Thoát")
        print("_" * 50)

        try:
            choice = input("👉 Chọn chức năng (0-6): ").strip()
        except (KeyboardInterrupt, EOFError):
            logger.info("\n👋 Đã thoát chương trình.")
            break

        if choice == "0":
            logger.info("👋 Đã thoát chương trình.")
            break

        elif choice == "1":
            try:
                logger.info("\n🔄 Đang xử lý loại bỏ từ khóa trùng lặp...")
                remove_duplicates_and_color()
            except KeyboardInterrupt:
                logger.info("\n⏹ Đã dừng.")

        elif choice == "2":
            try:
                logger.info("\n🔍 Đang bắt đầu tìm kiếm...")
                keywords = read_keywords_from_excel()
                if keywords:
                    search_keywords(keywords)
                else:
                    logger.error("❌ Không có từ khóa để tìm kiếm!")
            except KeyboardInterrupt:
                logger.info("\n⏹ Đã dừng tìm kiếm. Kết quả đã tìm được sẽ được lưu.")

        elif choice == "3":
            try:
                logger.info("\n🔍 Đang bắt đầu tìm kiếm Chi Tiết (nhấn button 3 lần)...")
                keywords = read_keywords_from_excel()
                if keywords:
                    search_keywords_detailed(keywords)
                else:
                    logger.error("❌ Không có từ khóa để tìm kiếm!")
            except KeyboardInterrupt:
                logger.info("\n⏹ Đã dừng tìm kiếm chi tiết. Kết quả đã tìm được sẽ được lưu.")

        elif choice == "4":
            try:
                logger.info("\n🔍 Đang bắt đầu tìm kiếm trên Google...")
                keywords = read_keywords_from_excel()
                if keywords:
                    search_google_keywords(keywords)
                else:
                    logger.error("❌ Không có từ khóa để tìm kiếm!")
            except KeyboardInterrupt:
                logger.info("\n⏹ Đã dừng tìm kiếm Google.")

        elif choice == "5":
            try:
                logger.info("\n🔍 Đang bắt đầu tìm kiếm trên Sogou...")
                keywords = read_keywords_from_excel()
                if keywords:
                    search_sogou_keywords(keywords)
                else:
                    logger.error("❌ Không có từ khóa để tìm kiếm!")
            except KeyboardInterrupt:
                logger.info("\n⏹ Đã dừng tìm kiếm Sogou.")

        elif choice == "6":
            try:
                logger.info("\n🚀 Bắt đầu: Lấy từ khóa mới + Tìm kiếm Baidu (Tự động)...")
                _run_getnewkeywords_then_search()
            except KeyboardInterrupt:
                logger.info("\n⏹ Đã dừng.")

        else:
            logger.warning("⚠️ Lựa chọn không hợp lệ! Vui lòng chọn từ 0 đến 6.")


def _run_getnewkeywords_then_search() -> None:
    """
    Luồng tự động:
      1. Lấy từ khóa mới từ Baidu (GetNewKeywords)
      2. Ghi từ khóa mới vào cột A của keywords.xlsx
      3. Thực hiện tìm kiếm Baidu trên danh sách từ khóa vừa ghi
    """
    # Bước 1: Lấy từ khóa mới
    logger.info("\n📥 [Bước 1/3] Đang lấy từ khóa mới từ Baidu...")
    try:
        from auto_browser_scraper import run as getnewkeywords_run
        new_keywords = getnewkeywords_run() or set()
    except ImportError:
        logger.error(
            "❌ Không thể import module getnewkeywords!\n"
            "   Hãy chắc chắn đã cài: pip install playwright && playwright install chromium"
        )
        return
    except Exception as e:
        logger.error(f"❌ Lỗi khi chạy GetNewKeywords: {e}")
        return

    if not new_keywords:
        logger.error("❌ Không có từ khóa mới nào được lấy về. Dừng lại.")
        return

    keywords_list = sorted(new_keywords)
    logger.info(f"✅ Lấy được {len(keywords_list)} từ khóa mới.")

    # Bước 2: Ghi vào keywords.xlsx
    logger.info(f"\n📝 [Bước 2/3] Đang ghi {len(keywords_list)} từ khóa vào keywords.xlsx...")
    success = write_keywords_to_excel(keywords_list)
    if not success:
        logger.error("❌ Không thể ghi từ khóa vào Excel. Dừng lại.")
        return

    # Bước 3: Tìm kiếm Baidu
    logger.info(f"\n🔍 [Bước 3/3] Đang thực hiện tìm kiếm Baidu cho {len(keywords_list)} từ khóa...")
    search_keywords(keywords_list)


def main() -> None:
    """Hàm main để khởi chạy chương trình"""
    try:
        show_menu()
    except KeyboardInterrupt:
        logger.info("\n👋 Đã thoát chương trình.")


if __name__ == "__main__":
    main()
