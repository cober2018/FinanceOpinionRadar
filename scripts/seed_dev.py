"""开发种子数据：3 creators / 5 topics / 10 entities / 2 source accounts。幂等。"""

from app.db.models import Creator, Entity, SourceAccount, Topic
from app.repositories import CreatorRepository, SourceAccountRepository
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

TOPICS = [
    ("AI算力", "sector", ["算力", "AI芯片", "算力基建"]),
    ("黄金", "commodity", ["gold", "金价的"]),
    ("美股大盘", "macro", ["标普", "纳指"]),
    ("A股指数", "macro", ["沪深300", "上证指数"]),
    ("美债利率", "macro", ["10年期美债", "美债收益率"]),
]

ENTITIES = [
    ("stock", "英伟达", "NVDA", "US"),
    ("stock", "台积电", "TSM", "US"),
    ("stock", "微软", "MSFT", "US"),
    ("stock", "苹果", "AAPL", "US"),
    ("stock", "贵州茅台", "600519", "CN"),
    ("stock", "宁德时代", "300750", "CN"),
    ("index", "标普500", "SPX", "US"),
    ("index", "纳斯达克指数", "IXIC", "US"),
    ("index", "沪深300", "000300", "CN"),
    ("commodity", "COMEX黄金", "GC", "GLOBAL"),
]

CREATORS = [("陈观点", "财经主播"), ("王策略", "卖方分析师"), ("刘观察", "独立研究者")]

ACCOUNTS = [
    ("陈观点", "youtube", "UC_demo_chen", "@chen-viewpoint", "https://youtube.com/@chen-viewpoint"),
    ("陈观点", "bilibili", "2233_demo_chen", "陈观点", "https://space.bilibili.com/2233_demo_chen"),
]


def _get_by_name(session: Session, model, name: str):
    return session.scalar(select(model).where(model.canonical_name == name))


def run_seed(factory: sessionmaker) -> None:
    with factory() as session:
        creators = CreatorRepository(session)
        accounts = SourceAccountRepository(session)

        for display_name, bio in CREATORS:
            if creators.get_by_name(display_name) is None:
                creators.create(display_name=display_name, bio=bio)

        for canonical_name, topic_type, aliases in TOPICS:
            if _get_by_name(session, Topic, canonical_name) is None:
                session.add(
                    Topic(canonical_name=canonical_name, topic_type=topic_type, aliases=aliases)
                )

        for entity_type, name, symbol, market in ENTITIES:
            if _get_by_name(session, Entity, name) is None:
                session.add(
                    Entity(
                        entity_type=entity_type,
                        canonical_name=name,
                        symbol=symbol,
                        market=market,
                    )
                )

        for display_name, platform, external_id, handle, url in ACCOUNTS:
            creator = creators.get_by_name(display_name)
            if creator is None:
                # D29：种子前置数据缺失属程序错误，直接失败而非静默跳过
                raise RuntimeError(f"seed: creator {display_name!r} not found")
            accounts.upsert_by_external(
                creator_id=creator.id,
                platform=platform,
                external_id=external_id,
                handle=handle,
                url=url,
                discovery_mode="auto_poll",
            )

        session.commit()
        # 动态计数：数据源常量变更时 print 不会撒谎（Plan #1 审查遗留）
        print(
            "seed done: "
            f"creators={session.query(Creator).count()} "
            f"topics={session.query(Topic).count()} "
            f"entities={session.query(Entity).count()} "
            f"source_accounts={session.query(SourceAccount).count()}"
        )


if __name__ == "__main__":
    from app.db.session import get_session_factory

    run_seed(get_session_factory())
