"""直播房间在线人数提取（用户 2026-10-08 需求：每切片采样 + 收尾平均）。

数据源：弹幕 WS 流中的 WebcastRoomStatsMessage（douyinLive ~60s 转发一次抖音
房间统计，`total` 即房间页展示的在线观众数；protojson int64 为字符串形态），
采集器已全量落盘 jsonl 档案——采样零新增对抖音的请求、不引风控暴露。
档案随场次增长（长场数 MB），只读尾部窗口。

采样语义：取「分片时刻」的人数（at_time 之前最近一条统计），而非处理时刻的
最新值——补转写/重启恢复的场次也拿得到切片当时的真实人数。信封 received_at
超出 max_age_sec（对 at_time 而言过旧/过新都算错位）→ None，宁缺毋错。
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.services.danmaku.parse import loads_line

_TAIL_BYTES = 512 * 1024
_METHOD = "WebcastRoomStatsMessage"
# 采集器建立连接前的分片（开播即录、弹幕稍后）：放宽到 10 分钟内的稍晚统计
_AFTER_GRACE = timedelta(minutes=10)
# 只取切片时刻 ± 窗口内的统计：更旧的是别的时段的值（如收尾后的停摆值），宁缺毋错
_MAX_AGE = timedelta(minutes=15)


def _parse_received_at(envelope: dict) -> datetime | None:
    raw = envelope.get("received_at")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def latest_viewer_count(
    sink_file: Path, *, at_time: datetime | None = None, max_age: timedelta = _MAX_AGE
) -> int | None:
    """jsonl 档案 → 切片时刻（at_time，缺省=最新）的在线观众数。

    文件缺失 / 无统计消息 / 字段脏 / 时间错位超窗 → None（采样缺失不得阻断转写主链）。
    从尾部反向取第一条可解析且 at_time-max_age ≤ received_at ≤ at_time+grace 的
    统计消息：尾部半行（写入中被读）自然跳过。
    """
    try:
        size = sink_file.stat().st_size
        with sink_file.open("rb") as fh:
            if size > _TAIL_BYTES:
                fh.seek(-_TAIL_BYTES, 2)
            blob = fh.read()
    except OSError:
        return None
    deadline = at_time + _AFTER_GRACE if at_time is not None else None
    floor = at_time - max_age if at_time is not None else None
    marker = _METHOD.encode()
    for line in reversed(blob.splitlines()):
        if marker not in line:
            continue
        envelope = loads_line(line.decode("utf-8", errors="replace")) or {}
        msg = envelope.get("msg") or {}
        if not isinstance(msg, dict) or msg.get("method") != _METHOD:
            continue
        if deadline is not None:
            received_at = _parse_received_at(envelope)
            if received_at is None or not (floor <= received_at <= deadline):
                continue  # 错位过远（太新/太旧）的统计，不是切片当时的值
        total = msg.get("total")
        if isinstance(total, bool):
            continue
        if isinstance(total, int):
            return total
        if isinstance(total, str) and total.isdigit():
            return int(total)
    return None
