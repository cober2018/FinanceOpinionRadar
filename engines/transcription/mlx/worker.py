"""mlx-whisper ASR backend for voice-pro.

mlx-whisper requires Python 3.12+ while voice-pro's installer env runs 3.10.
This module is a subprocess bridge: it spawns the standalone mlx_worker.py
that lives in venv_arm64 (Python 3.12 + mlx 0.32 + mlx-metal) and forwards
audio + params through CLI, then parses the JSON output back into the same
segment-dict shape that FasterWhisperInference returns.

Mirrors the FasterWhisperInference public API so gradio_asr.switch_case can
drop it in alongside the existing engines without any other code changes.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import BinaryIO, List, Optional, Tuple, Union

import gradio as gr
import numpy as np

import whisper  # only used for tokenizer.LANGUAGES (ISO code <-> display name)

import structlog
logger = structlog.get_logger()


# Path to venv_arm64 — independent from voice-pro's installer env so we can
# run Python 3.12 + mlx-metal even when voice-pro itself is on 3.10.
MLX_VENV = "/Users/mshengran/Project/voice-pro/venv_arm64"
MLX_PYTHON = os.path.join(MLX_VENV, "bin", "python3.12")
MLX_WORKER = os.path.join(
    MLX_VENV, "lib", "python3.12", "site-packages", "mlx_worker.py"
)
MLX_MODEL_DEFAULT = "mlx-community/whisper-small-mlx"

# Map display names -> mlx-whisper friendly ISO codes. mlx-whisper takes the
# ISO 639-1 code as a string; we hand it None for "Auto Detect" to skip.
_LANG_NAME_TO_CODE: dict[str, str] = {
    v.title(): k for k, v in whisper.tokenizer.LANGUAGES.items()
}


class MlxWhisperInference:
    def __init__(self):
        if not Path(MLX_PYTHON).exists():
            raise RuntimeError(
                f"venv_arm64 not found at {MLX_VENV}. "
                "Run: /opt/homebrew/bin/python3.12 -m venv venv_arm64 && "
                f"{MLX_PYTHON} -m pip install mlx==0.32.0 mlx-whisper==0.4.3"
            )
        if not Path(MLX_WORKER).exists():
            raise RuntimeError(f"mlx_worker.py missing at {MLX_WORKER}")

        self.current_model_size: Optional[str] = None
        self.translatable_models = [
            "mlx-community/whisper-large-v3-mlx",
            "mlx-community/whisper-large-v2-mlx",
            "mlx-community/whisper-small-mlx",
            "mlx-community/whisper-medium-mlx",
        ]

    # -------- introspection (mirror FasterWhisperInference) --------

    @staticmethod
    def available_models() -> List[str]:
        return [
            "mlx-community/whisper-tiny-mlx",
            "mlx-community/whisper-base-mlx",
            "mlx-community/whisper-small-mlx",
            "mlx-community/whisper-medium-mlx",
            "mlx-community/whisper-large-v2-mlx",
            "mlx-community/whisper-large-v3-mlx",
        ]

    @staticmethod
    def available_langs() -> List[str]:
        return sorted(list(whisper.tokenizer.LANGUAGES.values()))

    @staticmethod
    def available_compute_types() -> List[str]:
        # mlx-metal ignores compute_type; report a single "metal" sentinel so
        # the UI's compute_type dropdown doesn't error when switched to us.
        return ["metal"]

    # -------- core: spawn worker, parse JSON --------

    def update_model(self, model_size: str, compute_type: str, progress: Optional[gr.Progress] = None) -> None:
        if progress is not None:
            progress(0, desc="Loading mlx model..")
        self.current_model_size = model_size

    def _resolve_lang(self, lang: Optional[str]) -> Optional[str]:
        if lang in (None, "Automatic Detection", "auto", "Auto"):
            return None
        return _LANG_NAME_TO_CODE.get(lang, lang)

    def _resolve_model(self, model_size: Optional[str]) -> str:
        if model_size and model_size in self.available_models():
            return model_size
        return MLX_MODEL_DEFAULT

    def transcribe(
        self,
        audio: Union[str, BinaryIO, np.ndarray],
        params,
        progress: Optional[gr.Progress] = None,
    ) -> Tuple[List[dict], float]:
        start = time.time()

        if isinstance(audio, (str, os.PathLike)):
            audio_path = str(audio)
        else:
            # numpy or file-like — dump to a temp wav so mlx_whisper can load it
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            try:
                self._write_audio_to_path(audio, tmp.name)
                audio_path = tmp.name
            finally:
                tmp.close()

        out_json = tempfile.NamedTemporaryFile(suffix=".json", delete=False).name
        try:
            cmd = [
                MLX_PYTHON, MLX_WORKER,
                "--audio", audio_path,
                "--model", self._resolve_model(getattr(params, "model_size", None)),
                "--lang", self._resolve_lang(getattr(params, "lang", None)) or "auto",
                "--task", "translate" if getattr(params, "is_translate", False) else "transcribe",
                "--output-json", out_json,
            ]
            env = os.environ.copy()
            env.setdefault("HF_HOME", os.path.expanduser("~/.cache/huggingface"))

            if progress is not None:
                progress(0, desc="mlx-whisper: loading + transcribing..")

            proc = subprocess.run(
                cmd, env=env, capture_output=True, text=True, check=False
            )
            if proc.returncode != 0:
                logger.error(
                    f"[abus_asr_mlx_whisper] worker failed rc={proc.returncode}\n"
                    f"stderr: {proc.stderr[-800:]}"
                )
                raise RuntimeError(f"mlx_worker failed (rc={proc.returncode}): {proc.stderr[-200:]}")

            payload = json.loads(Path(out_json).read_text())
            segments = payload.get("segments", [])
            for seg in segments:
                if "words" not in seg:
                    seg["words"] = None

            if progress is not None:
                progress(1.0, desc="mlx-whisper: done")
            return segments, time.time() - start
        finally:
            try:
                os.unlink(out_json)
            except OSError:
                pass

    @staticmethod
    def _write_audio_to_path(audio: Union[BinaryIO, np.ndarray], path: str) -> None:
        """Handle numpy ndarray or file-like — convert to 16k mono wav for mlx."""
        import soundfile as sf  # already in voice-pro deps via faster-whisper
        if isinstance(audio, np.ndarray):
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            sf.write(path, audio.astype(np.float32), 16000)
            return
        # BinaryIO — assume it's already a wav/flac on disk path; just copy
        if hasattr(audio, "read"):
            data = audio.read()
            with open(path, "wb") as f:
                f.write(data)
            return
        raise TypeError(f"Unsupported audio type for mlx backend: {type(audio)}")

    # -------- higher-level helpers (mirror FasterWhisperInference) --------

    def transcribe_live(
        self,
        input_sample: np.ndarray,
        params,
        progress: Optional[gr.Progress] = None,
    ) -> Tuple[List[dict], float]:
        try:
            segments, elapsed = self.transcribe(input_sample, params.copy(), progress)
            return segments, elapsed
        except Exception as e:
            logger.error(f"[abus_asr_mlx_whisper] transcribe_live failed: {e}")
            return None, None

    def transcribe_file(
        self,
        input_path: str,
        params,
        highlight_words: bool,
        progress: Optional[gr.Progress] = None,
    ) -> List[str]:
        try:
            from app.abus_subtitle import get_srt, get_srt_wordlevel, get_txt, write_file
            from app.abus_path import path_change_ext

            segments, _ = self.transcribe(input_path, params.copy(), progress)

            subtitles: List[str] = []
            if not segments:
                return subtitles

            import pysubs2
            subs = pysubs2.load_from_whisper(segments)

            vtt_path = path_change_ext(input_path, ".vtt")
            subs.save(vtt_path)
            subtitles.append(vtt_path)

            ass_path = path_change_ext(input_path, ".ass")
            subs.save(ass_path)
            subtitles.append(ass_path)

            txt_path = path_change_ext(input_path, ".txt")
            write_file(get_txt(segments), txt_path)
            subtitles.append(txt_path)

            srt_path = path_change_ext(input_path, ".srt")
            if highlight_words:
                write_file(get_srt_wordlevel(segments), srt_path)
            else:
                write_file(get_srt(segments), srt_path)
            subtitles.append(srt_path)

            return subtitles
        except Exception as e:
            logger.error(f"[abus_asr_mlx_whisper] transcribe_file failed: {e}")
            return []