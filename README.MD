## CÔNG CỤ KIỂM TRA TỪ KHÓA TRÊN BAIDU & GOOGLE

### Mục tiêu
- **Tự động tra cứu** danh sách từ khóa trên Baidu Mobile (`https://m.baidu.com`) và Google (`https://www.google.com`).
- **Trích xuất tên miền** từ kết quả tìm kiếm của cả hai công cụ.
- **Ghi kết quả** trở lại cùng file Excel đầu vào (`keywords.xlsx`).

### Tính năng chính:

#### 1. Tìm kiếm Baidu (Chức năng gốc)
Kết quả ghi vào 4 cột trong `keywords.xlsx`:
- **Cột A**: Từ khóa gốc
- **Cột B**: Tiêu đề bài viết khớp nhất, hoặc "Trùng lặp từ khóa", hoặc "Lỗi: ..."
- **Cột C**: Tên miền (domain) nguồn bài viết (ví dụ: `www.xlfgs.cn`)
- **Cột D**: Nhãn thời gian (nếu có): `刚刚发布`, `今日发布`, `近期发布` — trống nếu không có

#### 2. Tìm kiếm Google (Mới)
- Tìm kiếm từ khóa trên Google với cùng format kết quả như Baidu
- Trích xuất tiêu đề và domain từ kết quả Google search
- Ghi kết quả vào cùng file Excel

#### 3. Trích xuất tên miền (Mới)
- Tự động trích xuất tất cả tên miền từ kết quả tìm kiếm của cả Baidu và Google
- Tạo file `domains_extracted.xlsx` với thông tin chi tiết:
  - Domains từ Baidu
  - Domains từ Google  
  - Tất cả domains (gộp chung)
  - Số lượng thống kê

#### 4. Top domains phổ biến (Mới)
- Phân tích và tìm ra các tên miền xuất hiện nhiều nhất
- Tạo file `top_domains.xlsx` với ranking domains theo tần suất
- Hiển thị từ khóa nào tìm thấy domain đó

---

## Cài đặt nhanh (Windows)

1) Cài Python 3.10+ và Google Chrome.

2) Tạo môi trường và cài thư viện:

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
```

3) (Khuyến nghị) Cài thêm runtime Playwright nếu cần:

```bash
python -m playwright install chromium
```

4) Bật VPN/Proxy để có IP Trung Quốc (ví dụ: GoLinkCN). Script có bước tự kiểm tra IP qua `https://api.ip.sb/geoip`.

---

## Cấu hình bắt buộc trước khi chạy

Mở `baidu-keyword-checker/run.py` và cập nhật 2 biến sau cho phù hợp với máy của bạn (đường dẫn Chrome và Profile):

```python
PROFILE_PATH = r"C:\\Users\\<TEN_USER>\\AppData\\Local\\Google\\Chrome\\User Data\\Profile <N>"
CHROME_PATH = r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
```

- **PROFILE_PATH**: trỏ tới profile Chrome đang dùng (nên là profile có thiết lập VPN hoạt động).
- **CHROME_PATH**: trỏ tới `chrome.exe` đúng vị trí cài đặt trên máy.
- **EXCEL_FILE** (mặc định là `keywords.xlsx`): đảm bảo file này nằm cùng thư mục với `run.py` và cột A có dữ liệu từ khóa.

---

## Cách chạy

```bash
cd baidu-keyword-checker
python main.py
```

### Menu chức năng:

```
📋 MENU CHỌN CHỨC NĂNG
==================================================
1. Loại bỏ từ khóa trùng lặp
2. Loại bỏ từ cấm
3. Thực hiện tìm kiếm (Baidu)
4. Thực hiện tìm kiếm Chi Tiết (Baidu - nhấn button 3 lần)
5. Tìm kiếm trên Google
6. Trích xuất tên miền từ Baidu & Google
7. Top domains phổ biến nhất
8. Từ khóa đài
9. Từ khóa đài (+ title)
0. Thoát
```

### Chi tiết từng chức năng:

**Chức năng 3 & 4 (Baidu):**
- Tìm kiếm từ khóa trên Baidu Mobile
- Chức năng 4 sẽ nhấn button tìm kiếm 3 lần để có kết quả chính xác hơn

**Chức năng 5 (Google):**
- Tìm kiếm từ khóa trên Google
- Trích xuất tiêu đề và domain từ kết quả

**Chức năng 6 (Trích xuất domains):**
- Tìm kiếm trên cả Baidu và Google
- Trích xuất tất cả tên miền từ kết quả
- Tạo file `domains_extracted.xlsx` với thông tin chi tiết

**Chức năng 7 (Top domains):**
- Phân tích tần suất xuất hiện của domains
- Tạo file `top_domains.xlsx` với ranking
- Có thể chọn số lượng top domains (mặc định 10)

Trong lúc chạy, chương trình sẽ:
- Kiểm tra IP có phải Trung Quốc (cho Baidu).
- Duy trì một tab trình duyệt và lần lượt tra cứu các từ khóa.
- Tự động đợi ngẫu nhiên 1.5–2.5 giây giữa các lần tra cứu.
- Sau khi hoàn tất, cập nhật kết quả vào file Excel tương ứng.

---

## Nguyên tắc xử lý kết quả

- Chỉ chọn các kết quả mà **tiêu đề chứa đúng nguyên dạng** từ khóa (so khớp substring, không fuzzy, không đổi hoa/thường, không thay ký tự).
- Nếu có nhiều kết quả khớp: ưu tiên theo nhãn thời gian: `刚刚发布` → `今日发布` → `近期发布` → không nhãn.
- Nếu không có kết quả khớp: để trống cột B/C/D cho từ khóa đó.
- Nếu gặp từ khóa trùng lặp trong cùng phiên: ghi "Trùng lặp từ khóa" ở cột B.

---

## Lưu ý vận hành

- Trình duyệt chạy ở chế độ có GUI (headful) để tương thích với các công cụ VPN trên Windows.
- **Cho Baidu**: Cần VPN/Proxy để có IP Trung Quốc (ví dụ: GoLinkCN). Script có bước tự kiểm tra IP qua `https://api.ip.sb/geoip`.
- **Cho Google**: Không cần VPN, có thể chạy với IP bình thường.
- Có thể chặn thêm tài nguyên không cần thiết để tăng tốc (đã có code mẫu, đang tắt mặc định).
- Các lỗi `Timeout`/mạng sẽ được ghi ngắn gọn vào cột B theo dạng: `Lỗi: ...`.
- Thư mục `log/` chứa ảnh chụp màn hình, HTML snapshot, và meta JSON giúp debug khi lỗi.

## Files đầu ra

- `keywords.xlsx`: File gốc với kết quả tìm kiếm (cột A-F)
- `domains_extracted.xlsx`: Danh sách domains trích xuất từ cả Baidu và Google
- `top_domains.xlsx`: Ranking domains theo tần suất xuất hiện
- `log/`: Thư mục chứa debug artifacts khi có lỗi

---

## Cấu trúc thư mục

```
baidu/
├─ baidu-keyword-checker/
│  ├─ run.py                # Mã nguồn chính
│  ├─ keywords.xlsx          # File Excel đầu vào/đầu ra (cột A: từ khóa)
│  └─ debug_artifacts/       # Artifacts debug (tự tạo khi chạy)
├─ requirements.txt          # Danh sách thư viện Python
└─ README.md                 # Tài liệu này
```

---

## Sự cố thường gặp

- **Không phát hiện IP Trung Quốc (Baidu)**: bật lại VPN/Proxy, kiểm tra `PROFILE_PATH` có đúng profile đang dùng VPN không, chạy lại.
- **Không mở được Chrome**: kiểm tra lại `CHROME_PATH` đúng vị trí cài đặt.
- **Không ghi đè được file Excel**: đóng file Excel đang mở. Script sẽ tự lưu sang file `*_output.xlsx` nếu bị khóa.
- **Không thấy kết quả**: đảm bảo từ khóa ở cột A, mỗi dòng một từ khóa, không có hàng trống thừa ở đầu.
- **Google bị block**: Nếu Google phát hiện bot, hãy giảm tốc độ tìm kiếm hoặc sử dụng proxy khác nhau.
- **Kết quả không chính xác**: Kiểm tra selectors trong code có còn đúng với layout mới của Baidu/Google không.

---

## Bản quyền và ghi chú

- Chỉ dùng cho mục đích nghiên cứu/nội bộ. Người dùng tự chịu trách nhiệm khi vận hành.