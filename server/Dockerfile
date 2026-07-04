FROM python:3.11-slim

# ─── System dependencies ────────────────────────────────────────────────
RUN apt-get update && apt-get install -y \
    ffmpeg \
    sox \
    libsndfile1 \
    libportaudio2 \
    cmake \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# ─── Build llama.cpp from source for best GPU performance ───────────────
WORKDIR /tmp/llama.cpp
RUN git clone https://github.com/ggerganov/llama.cpp.git . \
    && cmake -B build \
    && cmake --build build --config Release -j$(nproc) \
    && cmake --install build --config Release \
    && ldconfig \
    && rm -rf /tmp/llama.cpp

# ─── Application ────────────────────────────────────────────────────────
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create model directory
RUN mkdir -p /app/models

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]