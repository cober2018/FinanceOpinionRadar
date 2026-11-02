#!/usr/bin/env bash
# 安装本产品自有的 mlx 转写环境（Apple Silicon 专用），不再借用 voice-pro venv。
# 用法：bash engines/transcription/mlx/setup.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
if [ ! -x "$HERE/.venv/bin/python" ]; then
  python3.12 -m venv "$HERE/.venv"
  "$HERE/.venv/bin/pip" install -U pip
fi
"$HERE/.venv/bin/pip" install -r "$HERE/requirements.txt"
echo "安装完成。雷达配置指向："
echo "  ASR_MLX_PYTHON=$HERE/.venv/bin/python"
echo "  ASR_MLX_WORKER=$HERE/worker.py"
