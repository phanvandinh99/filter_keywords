"""Constants và cấu hình cho baidu-keyword-checker"""

# ── Time labels ──────────────────────────────────────────────────────────────
# Baidu Mobile chỉ hiển thị đúng 3 chuỗi này, match trực tiếp không cần mapping.
TIME_LABELS = (
    "刚刚发布",  # Vừa mới được đăng
    "今日发布",  # Được đăng tải hôm nay
    "近期发布",  # Được đăng tải gần đây
)

# Priority dùng để chọn kết quả tốt nhất (số càng cao càng ưu tiên)
TIME_PRIORITY = {
    "刚刚发布": 3,
    "今日发布": 2,
    "近期发布": 1,
}

# Replacement patterns (sắp xếp từ dài đến ngắn để match pattern dài hơn trước)
REPLACEMENT_PATTERNS = [
    ("2025最新版v...", "{getone name=\"diy:banben\" cacheid=\"1\"/} 安卓版-2265安卓网"),
    ("2025最新版V...", "{getone name=\"diy:banben\" cacheid=\"1\"/} 安卓版-2265安卓网"),
    ("2026最新版v...", "{getone name=\"diy:banben\" cacheid=\"1\"/} 安卓版-2265安卓网"),
    ("2026最新版V...", "{getone name=\"diy:banben\" cacheid=\"1\"/} 安卓版-2265安卓网"),
    ("最新版v...", "{getone name=\"diy:banben\" cacheid=\"1\"/} 安卓版-2265安卓网"),
    ("最新版V...", "{getone name=\"diy:banben\" cacheid=\"1\"/} 安卓版-2265安卓网"),
    ("最新版...", "v{getone name=\"diy:banben\" cacheid=\"1\"/} 安卓版-2265安卓网"),
    ("最新...", "版v{getone name=\"diy:banben\" cacheid=\"1\"/} 安卓版-2265安卓网"),
    ("2025...", "最新版v{getone name=\"diy:banben\" cacheid=\"1\"/} 安卓版-2265安卓网"),
    ("2026...", "最新版v{getone name=\"diy:banben\" cacheid=\"1\"/} 安卓版-2265安卓网"),
    ("安卓版...", "-2265安卓网"),
    ("2265...", "安卓网"),
    ("安卓...", "版-2265安卓网"),
    ("v...", "{getone name=\"diy:banben\" cacheid=\"1\"/} 安卓版-2265安卓网"),
    ("V...", "{getone name=\"diy:banben\" cacheid=\"1\"/} 安卓版-2265安卓网"),
    ("安卓版", "-2265安卓网"),
]

MID_PATTERNS = [
    ("最新版下载v", "{getone name=\"diy:banben\" cacheid=\"1\"/} 安卓版-2265安卓网"),
    ("最新版下载V", "{getone name=\"diy:banben\" cacheid=\"1\"/} 安卓版-2265安卓网"),
    ("最新版v", "{getone name=\"diy:banben\" cacheid=\"1\"/} 安卓版-2265安卓网"),
    ("最新版V", "{getone name=\"diy:banben\" cacheid=\"1\"/} 安卓版-2265安卓网"),
    ("下载v", "{getone name=\"diy:banben\" cacheid=\"1\"/} 安卓版-2265安卓网"),
    ("下载V", "{getone name=\"diy:banben\" cacheid=\"1\"/} 安卓版-2265安卓网"),
]

# Browser configuration
BROWSER_CONFIG = {
    "viewport": {"width": 390, "height": 844},
    "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
    "args": [
        "--start-maximized",
        "--disable-blink-features=AutomationControlled",
        "--no-default-browser-check",
        "--disable-infobars",
        "--disable-dev-shm-usage",
        "--disable-gpu",
    ],
    "timeout": 30000,
}

# Timeout settings
TIMEOUTS = {
    "ip_check": 10000,
    "navigation": 18000,
    "navigation_fallback": 20000,
    "navigation_commit": 8000,
    "selector_visible": 12000,
    "selector_attached": 6000,
    "network_idle": 15000,
    "dom_content_loaded": 8000,
    "homepage": 15000,
    "homepage_fallback": 8000,
}

# Delay settings (seconds)
DELAYS = {
    "normal_min": 1.5,
    "normal_max": 2.5,
    "error_min": 1.2,
    "error_max": 2.0,
    "detailed_min": 2.0,
    "detailed_max": 3.0,
    "input_fill": 0.2,
    "input_after_fill": 0.5,
    "label_click": 0.2,
    "result_stable": 0.5,
    "homepage_load": 1.0,
    "homepage_load_fallback": 2.0,
}

# Selectors
SELECTORS = {
    "result_container": [
        "div.c-result.result",
        "div.c-result",
        "div.result-op",
        "article.c-container",
        "div.result",
        "div.news-result",
    ],
    "title": ["h3 a", "article h3 a", ".c-result h3 a", "h3"],
    "time_tag": [
        "span.c-color-gray",
        "div.c-color-gray",
        "p.c-color-gray",
        "span.c-line-clamp1",
        ".c-color-gray2",
        ".c-color-gray-a",
        ".news-source",
    ],
    "search_input": [
        "input#index-kw",
        "input[type='search']",
        "input[type='text']",
        "#kw",
        ".search-input",
    ],
    "search_input_placeholder": [
        "input[placeholder*='搜索']",
        "input[placeholder*='百度']",
    ],
    "search_button": [
        "button[type='submit']",
        "input[type='submit']",
        ".search-btn",
        "#search-btn",
        "button.search-btn",
    ],
    "label_for_input": [
        "label[for='index-kw']",
        "label.fake-placeholder",
    ],
}

# URLs
URLS = {
    "ip_check": "https://api.ip.sb/geoip",
    "baidu_mobile": "https://m.baidu.com",
    "baidu_search": "https://m.baidu.com/s?word={}",
}

# Expected country code for IP check
EXPECTED_COUNTRY_CODE = "TW"

# ── Nguồn bị loại bỏ ─────────────────────────────────────────────────────────
# Kết quả có source text khớp bất kỳ chuỗi nào dưới đây sẽ bị bỏ qua.
# Selector tương ứng trên Baidu Mobile: span.cosc-source-text
BLOCKED_SOURCES = [
    "文心智能体",   # AI生成 (Wenxin AI)
    "AI生成",
    "GitHub文档官网",
    "GitHub",
]

# ── Domain bị loại bỏ ──────────────────────────────────────────────────────────────────
# Kết quả có domain thuộc danh sách này sẽ bị bỏ qua hoàn toàn (không giữ title, không lấy domain).
# Hỗ trợ 2 loại:
#   - Exact match: "www.baidu.com" chỉ block đúng domain đó
#   - Suffix match (thêm dấu . ở đầu): ".baidu.com" block tất cả subdomain của baidu.com
#     VÍ DỤ: ".baidu.com" sẽ block baijiahao.baidu.com, tieba.baidu.com, v.v.
BLOCKED_DOMAINS = [
    # Tất cả subdomain của Baidu (baijiahao.baidu.com, tieba.baidu.com, v.v.)
    "baidu.com",
    ".baidu.com",
    # Game site — block tất cả subdomain của 3dmgame.com (app.3dmgame.com, v.v.)
    "3dmgame.com",
    ".3dmgame.com",
    # Game site — block tất cả subdomain của weibo.com (app.weibo.com, v.v.)
    "weibo.com",
    ".weibo.com",
    # Game site — block tất cả subdomain của vk.com (app.vk.com, v.v.)
    "vk.com",
    ".vk.com",
]

# Debug settings
DEBUG_ARTIFACTS_DIR = "log"
DEBUG_ARTIFACTS_MAX_RESPONSES = 25
DEBUG_ARTIFACTS_MAX_FAILED_REQUESTS = 25
DEBUG_ARTIFACTS_MAX_CONSOLE = 50

# ── Captcha detection ─────────────────────────────────────────────────────────
CAPTCHA_URL_PATTERNS = [
    "wappass.baidu.com/static/captcha",
    "wappass.baidu.com/wp/",
    "passport.baidu.com/v2/?login",
    "wappass.baidu.com/static/touch/",
]

CAPTCHA_PAGE_TITLES = [
    "百度安全验证",
    "安全验证",
    "百度验证",
]

CAPTCHA_MAX_RETRIES = 3
CAPTCHA_RETRY_DELAY_MIN = 8.0
CAPTCHA_RETRY_DELAY_MAX = 15.0

# Retry khi không có kết quả (selector timeout hoặc empty results)
NO_RESULT_MAX_RETRIES = 3          # Tối đa 3 lần retry
NO_RESULT_RETRY_DELAY_MIN = 3.0    # Chờ tối thiểu 3s trước khi retry
NO_RESULT_RETRY_DELAY_MAX = 6.0    # Chờ tối đa 6s trước khi retry

MOBILE_USER_AGENTS = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.144 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-A546B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Mobile Safari/537.36",
]

LOCATION_PROFILES = {
    "default": {
        "name": "Mặc định",
        "hl": None,
        "gl": None,
        "locale": None,
        "timezone_id": None,
        "geolocation": None,
    },
    "vn": {
        "name": "Việt Nam",
        "hl": "vi",
        "gl": "vn",
        "locale": "vi-VN",
        "timezone_id": "Asia/Ho_Chi_Minh",
        "geolocation": {"latitude": 21.0285, "longitude": 105.8048, "accuracy": 1000},
    },
    "cn": {
        "name": "Trung Quốc",
        "hl": "zh-CN",
        "gl": "cn",
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
        "geolocation": {"latitude": 39.9042, "longitude": 116.4074, "accuracy": 1000},
    },
    "us": {
        "name": "Mỹ",
        "hl": "en",
        "gl": "us",
        "locale": "en-US",
        "timezone_id": "America/New_York",
        "geolocation": {"latitude": 40.7128, "longitude": -74.0060, "accuracy": 1000},
    },
    "jp": {
        "name": "Nhật Bản",
        "hl": "ja",
        "gl": "jp",
        "locale": "ja-JP",
        "timezone_id": "Asia/Tokyo",
        "geolocation": {"latitude": 35.6762, "longitude": 139.6503, "accuracy": 1000},
    }
}

