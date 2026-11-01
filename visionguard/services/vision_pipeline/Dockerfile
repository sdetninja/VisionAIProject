FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN sed -i 's/^opencv-python .*/opencv-python-headless/' requirements.txt && pip install --no-cache-dir -r requirements.txt
COPY vision_pipeline ./vision_pipeline
COPY configs ./configs
ENV CONFIG_PATH=/app/configs/pipeline.webcam.yaml
EXPOSE 8001
CMD ["uvicorn", "vision_pipeline.app:app", "--host", "0.0.0.0", "--port", "8001"]
