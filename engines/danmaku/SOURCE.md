# engines/danmaku 来源与版本

- 来源：上游 jwwsjlm/douyinLive 仓库 tag `v2.2.1` 源码 tarball（github.com/jwwsjlm/douyinLive/archive/refs/tags/v2.2.1.tar.gz，2026-11-03 下载）。
- 与生产一致性核对：生产编排 pin 的镜像 `ghcr.io/jwwsjlm/douyinlive:v2.2.1`（本机已 pull 核对）内为该 tag 的 Go 编译产物（静态链接、stripped，BuildID 4DF8KD…），即本 vendor 源码的构建结果。
- 许可与作者信息见 vendor/douyinLive 内 LICENSE/README（随源码保留）。
- 雷达依赖面：`ws://<host>:1088/ws/<room_id>` 订阅 + 30s 文本 "ping" 保活 + 房间状态接口（apps/api/app/modules/collection/danmaku/collector.py）。
- 本产品改造点：以本仓库可构建引擎进程交付（go build，见 engines/danmaku/README.md），替换外部 ghcr 镜像作为运行来源；协议行为零改动。
