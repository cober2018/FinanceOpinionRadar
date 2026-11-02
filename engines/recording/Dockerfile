# 本产品录制引擎（StreamCap v1.0.3 vendored 源码 + 无头启动，替代外部镜像+浏览器会话保活）
# 构建：docker build -t radar-engine-recording -f engines/recording/Dockerfile engines/recording
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /engine
COPY vendor/ /engine/vendor/
COPY defaults/ /engine/defaults/
COPY server.py /engine/server.py
RUN pip install --no-cache-dir -r vendor/streamcap/requirements.txt

ENV STREAMCAP_CONFIG_DIR=/data/config
VOLUME ["/data/config", "/data/downloads"]
CMD ["python", "server.py"]
