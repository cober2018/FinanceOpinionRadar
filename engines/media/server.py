"""本产品媒体引擎进程入口（engines/media）。

以产品内 vendored dtk 5.1.0 源码构建运行，替代外部黑盒镜像。
启动：uvicorn engines.media.server:app（配置经环境变量，见 engines/media/README.md）。
对外契约与原 dtk API 完全一致（信封/鉴权/两个 GET 端点），radar 适配器无需改动。
"""

from dtk.api.app import create_app

app = create_app()
