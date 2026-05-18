# Baidu Keyword Scraper

Tự động lấy từ khóa liên quan từ Baidu mobile search.

## Cài đặt

```bash
pip install playwright
playwright install chromium
```

## Sử dụng

### 1. Cấu hình

Chỉnh sửa file `input_keywords.txt` - thêm từ khóa cần tìm:
```
九游棋牌官网
hth手机版登录入口
từ khóa khác...
```

Chỉnh sửa file `old_keywords.txt` - thêm từ khóa cũ (để loại bỏ trùng lặp):
```
từ khóa cũ 1
từ khóa cũ 2
...
```

### 2. Chạy

```bash
python auto_browser_scraper.py
```

### 3. Kết quả

- `keywords_output.txt` - Từ khóa MỚI (không trùng)
- `keywords_all.txt` - TẤT CẢ từ khóa

## Tính năng

✅ Tự động mở browser và lấy keywords
✅ Đọc input từ file
✅ Loại bỏ trùng lặp với từ khóa cũ
✅ Lưu kết quả vào file TXT

## Files

- `auto_browser_scraper.py` - Script chính
- `extract_from_json.py` - Extract từ JSON (backup)
- `input_keywords.txt` - Input keywords
- `old_keywords.txt` - Old keywords (để filter)
- `keywords_output.txt` - Output (new keywords)
- `keywords_all.txt` - Output (all keywords)

## Cấu hình nâng cao

Mở `auto_browser_scraper.py` và sửa:

```python
HEADLESS = False  # True = ẩn browser
WAIT_TIME = 5     # Thời gian chờ API (giây)
```
