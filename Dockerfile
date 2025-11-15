# Dockerfile
FROM python:3.11-slim

# ติดตั้งระบบแพ็กเกจที่จำเป็น: ca-certificates, openssl libraries, build deps
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      libssl-dev \
      build-essential \
      gcc \
      libffi-dev \
      wget \
    && rm -rf /var/lib/apt/lists/*

# ตั้ง working dir
WORKDIR /app

# คัดลอก requirements และติดตั้ง Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# คัดลอกไฟล์โปรเจกต์ทั้งหมด
COPY . .

# เปิดพอร์ต
EXPOSE 5000

# รันด้วย gunicorn (production)
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:app"]