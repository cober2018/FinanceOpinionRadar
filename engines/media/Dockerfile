# 本产品媒体引擎（dtk 5.1.0 vendored 源码构建，替代外部黑盒镜像）
# 构建：docker build -t radar-engine-media -f engines/media/Dockerfile engines/media
FROM python:3.12-slim

WORKDIR /engine
COPY vendor/ /engine/vendor/
COPY requirements.txt requirements.frozen.txt /engine/
COPY server.py /engine/engines/media/server.py
RUN pip install --no-cache-dir -r requirements.frozen.txt

# server.py 以 `engines.media.server` 可导入；dtk 从 vendor 面导入
ENV PYTHONPATH=/engine:/engine/vendor
EXPOSE 8000
# 启动前需完成 dtk migrate（编排中由 dtk-migrate 一次性任务执行）
CMD ["uvicorn", "engines.media.server:app", "--host", "0.0.0.0", "--port", "8000"]
