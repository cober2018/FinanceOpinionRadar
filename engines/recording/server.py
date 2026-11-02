"""本产品录制引擎进程入口（engines/recording，headless）。

复用 vendored StreamCap v1.0.3 的原版 main 协程与全部录制核心，仅以进程内
虚拟 Flet 页面会话替代"第一个浏览器会话连上才启动探测任务"的上游行为
（scripts/streamcap_keepalive.sh 注释记录了该行为缺陷：会话死亡会永久杀死
监控循环）。运维不再需要浏览器/管理页面会话激活监控。

运行：python engines/recording/server.py
环境变量：
  STREAMCAP_CONFIG_DIR  产品配置目录（recordings.json / user_settings.json 所在 bind）
  STREAMCAP_PORT        可选：同时对外提供原 Web UI（默认不开）
"""

import asyncio
import contextlib
import os
import signal
import sys
from pathlib import Path
from unittest.mock import MagicMock

VENDOR_DIR = Path(__file__).resolve().parent / "vendor" / "streamcap"


class HeadlessPage(MagicMock):
    """进程内虚拟页面：吸收全部 UI 交互，仅 run_task 真实派发任务。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.web = True
        self.route = "/home"
        self.width = 1280
        self.height = 800
        self.controls = []
        self.overlay = []
        self.window = MagicMock()

    def run_task(self, coro, *args):  # 与 flet Page.run_task 同语义
        # 纯 UI 任务（页面切换/渲染）在无头模式下无意义，吸收掉以免噪声与假失败；
        # 生命周期任务（start_periodic_tasks 等）必须真实派发。
        if getattr(coro, "__name__", "") in {"switch_page"}:
            return None
        return asyncio.get_running_loop().create_task(coro(*args))

    def add(self, *_controls):
        return None

    def update(self, *_a, **_k):
        return None

    async def push_route(self, route):
        self.route = route


async def _run() -> None:
    os.chdir(VENDOR_DIR)
    if VENDOR_DIR not in sys.path:
        sys.path.insert(0, str(VENDOR_DIR))
    try:
        from dotenv import load_dotenv

        load_dotenv(VENDOR_DIR / ".env")
    except ImportError:
        pass

    config_dir = os.environ.get("STREAMCAP_CONFIG_DIR")
    if config_dir:
        link = VENDOR_DIR / "config"
        if link.is_symlink():
            link.unlink()
        elif link.is_dir():
            # 镜像自带默认 config 目录：产品配置目录优先（shutil 覆盖）
            import shutil

            shutil.rmtree(link)
        link.symlink_to(Path(config_dir).resolve())
        # 自愈：镜像默认 config 文件缺失会导致会话初始化必崩（language.json →
        # SettingsPage.load_language 空 dict；version.json → KeyError('原画')）。
        # 只补缺不覆盖（与 recorder_bridge._ensure_user_settings 同语义 E1）。
        for default in sorted((Path(__file__).resolve().parent / "defaults").iterdir()):
            target = Path(config_dir) / default.name
            if not target.exists():
                target.write_text(default.read_text())

    from main import main as streamcap_main  # noqa: E402  上游入口原样复用

    page = HeadlessPage()
    await streamcap_main(page)  # 返回后 periodic_check 已在后台任务中运行
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    while not stop.is_set():
        # 存活心跳：便于运维确认监控循环未随任何会话死亡（原缺陷的可观测面）
        print("[radar-recording-engine] alive", flush=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=60)
        except asyncio.TimeoutError:
            pass


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
