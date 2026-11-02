# engines/transcription 来源与版本

- 来源：`/Users/mshengran/Project/voice-pro/abus-aikorea-voice-pro/app/abus_asr_mlx_whisper.py`（2026-11-03 原样复制为 `mlx/worker.py`；该脚本仅用 argv+JSON 文件通信，无 voice-pro 内部 import）。
- 依赖冻结：voice-pro `venv_arm64` 实测版本 mlx 0.32.0 / mlx-metal 0.32.0 / mlx-whisper 0.4.3（见 mlx/requirements.txt）。
- 迁移后运行前提：`bash engines/transcription/mlx/setup.sh` 创建本产品自有 `.venv`；雷达配置 `ASR_MLX_PYTHON`/`ASR_MLX_WORKER` 指向产品内路径。faster-whisper 路径继续使用雷达主 venv（无 voice-pro 依赖）。
- 契约不变：worker argv（--audio/--model/--lang/--output-json）与输出 JSON schema 与 `mlx_whisper.py` 桥接完全一致。
