FROM python:3.11-slim

# ── System dependencies cho Playwright Chromium ──────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Chromium runtime libs
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    libatspi2.0-0 \
    libwayland-client0 \
    # Fonts tiếng Trung (quan trọng để render đúng)
    fonts-noto-cjk \
    # Tiện ích
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Cài Python dependencies ───────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Cài Playwright Chromium ───────────────────────────────────
RUN playwright install chromium

# ── Copy source code ──────────────────────────────────────────
COPY . .

# ── Tạo thư mục output (mount volume vào đây) ─────────────────
RUN mkdir -p /app/log /app/data

# ── Biến môi trường mặc định ─────────────────────────────────
ENV DOCKER=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    EXCEL_FILE=/app/data/keywords.xlsx

ENTRYPOINT ["python", "main.py"]
