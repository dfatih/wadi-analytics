# Dockerfile
FROM python:3.10-slim

# 2. Arbeitsverzeichnis
WORKDIR /app
ENV PYTHONPATH=/app

# 1. system deps
RUN apt-get update && apt-get install -y \
    build-essential \
    gcc \
    g++ \
    gdal-bin \
    libgdal-dev \
    proj-bin \
    libproj-dev \
    libgeos-dev \
    libspatialindex-dev \
    libatlas-base-dev \
    libffi-dev \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

# 2. Copy requirements first  – erzeugt Cache-Layer
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip setuptools wheel
RUN pip install --no-cache-dir -r requirements.txt

# 3. Dann erst den restlichen App-Code
COPY app/           app/
COPY modules/       modules/
COPY config/        config/
COPY templates/     templates/
COPY .env           . 

# 6. GDAL- und PROJ-Umgebungsvariablen
ENV GDAL_DATA=/usr/share/gdal
ENV PROJ_LIB=/usr/share/proj

# 4. Entrypoint (HF-Model download zur Laufzeit o. via volume)
CMD ["streamlit", "run", "app/main.py", "--server.port=8501", "--server.address=0.0.0.0"]