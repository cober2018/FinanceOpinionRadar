"""审计日志（RAD-052）：所有人工修改落 audit_log（PRD 13.15，ADR-0006 兑现）。"""

from sqlalchemy.orm import Session

from app.db.models import AuditLog


def write_audit(
    session: Session,
    *,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: int,
    before: dict | None,
    after: dict | None,
    reason: str | None = None,
) -> None:
    session.add(
        AuditLog(
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            before_json=before,
            after_json=after,
            reason=reason,
        )
    )
