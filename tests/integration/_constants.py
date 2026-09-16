"""集成测试共享常量（D55）：表清单单一来源，conftest 与 test_migrations 共用。"""

ALL_TABLES = (
    "creator, source_account, source_item, media_asset, transcript_segment, topic, entity, "
    "viewpoint, viewpoint_evidence, creator_topic_snapshot, topic_consensus_daily, "
    "prompt_version, job_run, audit_log"
)

EXPECTED_TABLES = {
    "creator",
    "source_account",
    "source_item",
    "media_asset",
    "transcript_segment",
    "topic",
    "entity",
    "viewpoint",
    "viewpoint_evidence",
    "creator_topic_snapshot",
    "topic_consensus_daily",
    "prompt_version",
    "job_run",
    "audit_log",
}


def require_test_db_name(database_url: str) -> None:
    """D15/D35 守卫：只允许对 *_test 库执行 DROP/CREATE，防止误伤开发库。"""
    db_name = database_url.rsplit("/", 1)[1]
    if not db_name.endswith("_test"):
        raise ValueError(
            f"拒绝操作非 _test 数据库: {db_name!r}（RADAR_TEST_DATABASE_URL 必须指向 *_test 库）"
        )
