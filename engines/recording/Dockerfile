# 本产品录制引擎（StreamCap v1.0.3 vendored 源码 + 无头启动，替代外部镜像+浏览器会话保活）
# 构建：docker build -t radar-engine-recording -f engines/recording/Dockerfile engines/recording
FROM python:3.12-slim

# node 对齐原镜像 v20.x（douyin 取流 JS 签名运行时；缺 node 会触发 UI 安装对话框并撞上游
# TextButton bug，且录制期取流失败——复审 P1 实测补齐）
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg nodejs && rm -rf /var/lib/apt/lists/*

WORKDIR /engine
COPY requirements.frozen.txt /engine/requirements.frozen.txt
# 冻结清单来自原镜像实测 pip freeze（复审 P1 修复：requirements.txt 为 >= 浮动约束，
# 逐包解析会漂移，如 streamget/flet 版本漂移直接影响取流与 UI 层兼容性）。
# pip 放在 server.py 之前：改入口不必重装依赖。
RUN pip install --no-cache-dir -r requirements.frozen.txt
COPY vendor/ /engine/vendor/
COPY defaults/ /engine/defaults/
COPY server.py /engine/server.py

ENV STREAMCAP_CONFIG_DIR=/data/config
VOLUME ["/data/config", "/app/downloads"]  # /app/downloads 与 user_settings.live_save_path 对齐（P0-1）
CMD ["python", "server.py"]
