"""直播房间在线人数提取（用户 2026-10-08 需求：每切片采样 + 收尾平均）。

数据源：弹幕 WS 流中的 WebcastRoomStatsMessage（douyinLive ~60s 转发一次抖音
房间统计，`total` 即房间页展示的在线观众数；protojson int64 为字符串形态），
采集器已全量落盘 jsonl 档案——采样零新增对抖音的请求、不引风控暴露。
档案随场次增长（长场数 MB），只读尾部窗口找最近一条。
"""

from pathlib import Path

from app.services.danmaku.parse import loads_line

_TAIL_BYTES = 512 * 1024
_METHOD = "WebcastRoomStatsMessage"


def latest_viewer_count(sink_file: Path) -> int | None:
    """jsonl 档案 → 最近一条房间统计的在线观众数。

    文件缺失 / 无统计消息 / 字段脏 → None（采样缺失不得阻断转写主链）。
    从尾部反向取第一条可解析的统计消息：尾部半行（写入中被读）自然跳过。
    """
    try:
        size = sink_file.stat().st_size
        with sink_file.open("rb") as fh:
            if size > _TAIL_BYTES:
                fh.seek(-_TAIL_BYTES, 2)
            blob = fh.read()
    except OSError:
        return None
    marker = _METHOD.encode()
    for line in reversed(blob.splitlines()):
        if marker not in line:
            continue
        msg = (loads_line(line.decode("utf-8", errors="replace")) or {}).get("msg") or {}
        if not isinstance(msg, dict) or msg.get("method") != _METHOD:
            continue
        total = msg.get("total")
        if isinstance(total, bool):
            continue
        if isinstance(total, int):
            return total
        if isinstance(total, str) and total.isdigit():
            return int(total)
    return None
