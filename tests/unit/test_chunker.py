"""转录分段器单测（EPIC-04 RAD-040）：目标时长/边界/不切段/overlap 标记。"""

from app.domain.transcript.chunker import chunk_transcript, Chunk


def _seg(i: int, start_ms: int, end_ms: int, text: str = "x") -> dict:
    return {"id": i, "start_ms": start_ms, "end_ms": end_ms, "text": text}


def test_single_short_transcript_is_one_chunk():
    segs = [_seg(1, 0, 60_000), _seg(2, 60_000, 120_000)]
    chunks = chunk_transcript(segs, target_ms=300_000, max_ms=600_000, overlap=1)
    assert len(chunks) == 1
    assert [c.segment_ids for c in chunks] == [[1, 2]]


def test_splits_at_segment_boundary_near_target():
    # 5 段各 120s：target 300s → 前 3 段（360s）+ 后 2 段
    segs = [_seg(i, i * 120_000, (i + 1) * 120_000) for i in range(1, 6)]
    chunks = chunk_transcript(segs, target_ms=300_000, max_ms=600_000, overlap=1)
    assert len(chunks) == 2
    assert chunks[0].segment_ids == [1, 2, 3]
    assert chunks[1].segment_ids == [3, 4, 5]  # overlap=1：带 1 段上文


def test_overlap_marked_and_excluded_from_first():
    segs = [_seg(i, i * 120_000, (i + 1) * 120_000) for i in range(1, 6)]
    chunks = chunk_transcript(segs, target_ms=300_000, max_ms=600_000, overlap=1)
    assert chunks[0].overlap_from is None
    assert chunks[1].overlap_from == [3]  # 属上一 chunk 的段，dedupe 须知


def test_oversized_single_segment_gets_own_chunk():
    # 单段超 max：不切段（RAD-040 规则），独立成 chunk
    segs = [
        _seg(1, 0, 700_000),  # 11.7min > max
        _seg(2, 700_000, 720_000),
    ]
    chunks = chunk_transcript(segs, target_ms=300_000, max_ms=600_000, overlap=1)
    assert [c.segment_ids for c in chunks] == [[1], [1, 2]] or [
        c.segment_ids for c in chunks
    ] == [[1], [2]]
    # 无论 overlap 与否，段 1 都不被切开：任一 chunk 的 segment_ids 里 1 只出现一次
    all_ids = [i for c in chunks for i in c.segment_ids]
    assert all_ids.count(1) == 1


def test_chunk_covers_time_range_and_text():
    segs = [
        _seg(1, 0, 60_000, "开头"),
        _seg(2, 60_000, 130_000, "中间"),
    ]
    chunks = chunk_transcript(segs, target_ms=300_000, max_ms=600_000, overlap=0)
    c = chunks[0]
    assert isinstance(c, Chunk)
    assert c.start_ms == 0 and c.end_ms == 130_000
    assert "开头" in c.text and "中间" in c.text


def test_empty_transcript_returns_empty():
    assert chunk_transcript([], target_ms=300_000, max_ms=600_000, overlap=1) == []
