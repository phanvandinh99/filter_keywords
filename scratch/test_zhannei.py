import re

ZHANNEI_STRIP_SUFFIXES = [
    r'官方版下载$', r'官方版免费版$', r'官方版官方版$', r'免费版下载$',
    r'官方版$', r'免费版$', r'最新版$', r'安卓版$',
    r'版下载$', r'版免费版$', r'版官方版$', r'下载$',
]

def _extract_keyword(title: str, block: str = "") -> str:
    """Lấy từ khóa từ title và block theo logic mới."""
    if not title:
        return ''
    
    clean_title = re.sub(r'<[^>]+>', '', title)
    clean_title = re.sub(r'\s+', ' ', clean_title).strip()
    
    # 1. Tìm các ứng viên ở c-abstract
    if block:
        abstract_m = re.search(r'class="c-abstract">(.*?)</div>', block, re.DOTALL)
        if abstract_m:
            abstract = abstract_m.group(1)
            clean_abstract = re.sub(r'<[^>]+>', '', abstract)
            clean_abstract = re.sub(r'\s+', ' ', clean_abstract).strip()
            
            candidates = []

            # A. Trích xuất các nội dung trong “”, "", 《》, 【】
            patterns = [
                r'“([^”]+)”',
                r'《([^》]+)》',
                r'【([^】]+)】',
                r'"([^"]+)"'
            ]
            for pattern in patterns:
                found = re.findall(pattern, clean_abstract)
                for f in found:
                    candidates.append(f.strip())
            
            # B. Trích xuất các khối từ \w+ (bao gồm ký tự Trung, Anh, số ngăn cách bởi biểu tượng/emoji/dấu câu/khoảng trắng)
            words = re.findall(r'\w+', clean_abstract)
            for w in words:
                candidates.append(w.strip())

            # C. Tìm ứng viên dựa trên từ khóa kết thúc bằng "官网-APP下载" (hoặc biến thể)
            for m in re.finditer(r'(.*?)(?:官网\s*-\s*(?:APP|app)\s*下载)', clean_abstract, re.IGNORECASE):
                prefix_part = m.group(1).strip()
                if prefix_part:
                    # Tìm suffix dài nhất của prefix_part mà clean_title bắt đầu bằng nó
                    for i in range(len(prefix_part)):
                        sub = prefix_part[i:].strip()
                        if len(sub) >= 2:
                            candidates.append(sub)

            # Lọc các ứng viên khớp với phần đầu của title (không phân biệt hoa thường)
            valid_candidates = []
            title_lower = clean_title.lower()
            for cand in candidates:
                if cand and len(cand) >= 2:
                    cand_lower = cand.lower()
                    if title_lower.startswith(cand_lower):
                        valid_candidates.append(cand)
            
            if valid_candidates:
                # Sắp xếp theo chiều dài giảm dần để ưu tiên từ dài nhất
                valid_candidates.sort(key=len, reverse=True)
                return valid_candidates[0]

    # 2. Fallback: logic cũ (chia tách theo '-' và xóa suffix)
    part = clean_title.split('-')[0].strip()
    for pattern in ZHANNEI_STRIP_SUFFIXES:
        part = re.sub(pattern, '', part).strip()
    return part

# Test cases
test_cases = [
    {
        "title": "ayx官方app官方版-ayx官方app2026最新版V71.827.417安卓版-2265...",
        "block": """
        <div class="c-abstract">
            ayx官方app下载方式: ①通过浏览器下载  打开“ayx官方app”手机浏览器(例如百度浏览器)。在搜索框...
        </div>
        """,
        "expected": "ayx官方app"
    },
    {
        "title": "银河app手机版下载2025-银河app手机版官方最新版v.42.72.8.7 安卓...",
        "block": """
        <div class="c-abstract">
            ✅第一步:访问 银河app手机版下载 官网✨🦄✨ 1.打开银河app手机版下载下载... 《银河app手机版下载》[海克斯试炼]...
        </div>
        """,
        "expected": "银河app手机版下载"
    },
    {
        "title": "星空电竞app官方版-星空电竞app2025最新版V.99.86.35.9 安卓版...",
        "block": """
        <div class="c-abstract">
            🌻2026-05-31 10:12:48「🆙今年要发财🆙」【 星空电竞app 】🌻️支持:32/64bit...
        </div>
        """,
        "expected": "星空电竞app"
    },
    {
        "title": "ayx大厅手机版官方版-ayx大厅手机版2025最新版V.52.62.2.5 安卓版...",
        "block": """
        <div class="c-abstract">
            3.「🆙天天会发财🆙」 abcayx大厅手机版官网-APP下载🧐限制:winall/win7...
        </div>
        """,
        "expected": "ayx大厅手机版"
    },
    {
        "title": "KYapp官方入口下载官方版-KYapp官方入口下载2026最新版V.3.4.6.2...",
        "block": """
        <div class="c-abstract">
            ⏩kyapp⏪️已认证: 地址:http://wap.whfmb.com✅️kyapp💗️😘🧒「包含最新官方网址...
        </div>
        """,
        "expected": "kyapp"
    },
    {
        "title": "银河app手机版下载2025-银河app手机版官方最新版V.2.5.34.81 安卓...",
        "block": """
        <div class="c-abstract">
            ⏩银河app手机版下载⏪️已认证: 地址:http://wap.whfmb.com✅️银河app手机版下载💗️😘🧒...
        </div>
        """,
        "expected": "银河app手机版下载"
    },
    {
        "title": "ng导航官网入口(官方)网站平台/网页版登录/在线注册入口/ios苹果/...",
        "block": """
        <div class="c-abstract">
            ⏩ng导航官网入口⏪️已认证: 地址:http://wap.whfmb.com✅️ng导航官网入口💗️😘🧒...
        </div>
        """,
        "expected": "ng导航官网入口"
    }
]

for i, tc in enumerate(test_cases):
    res = _extract_keyword(tc["title"], tc["block"])
    print(f"Case {i+1}: expected='{tc['expected']}', got='{res}' -> {'PASS' if res == tc['expected'] else 'FAIL'}")
