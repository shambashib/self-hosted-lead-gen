FROM python:3.12-slim

WORKDIR /app

# System deps for Playwright + lxml
RUN apt-get update && apt-get install -y \
    gcc libxml2-dev libxslt-dev \
    wget gnupg curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers
RUN playwright install --with-deps chromium

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
