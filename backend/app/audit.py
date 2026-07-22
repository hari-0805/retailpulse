from typing import Optional

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import AuditLog


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def log_action(
    db: Session,
    request: Request,
    action: str,
    company_id: Optional[str] = None,
    user_id: Optional[str] = None,
    entity_name: Optional[str] = None,
):
    entry = AuditLog(
        company_id=company_id,
        user_id=user_id,
        action=action,
        entity_name=entity_name,
        ip_address=get_client_ip(request),
        browser=request.headers.get("user-agent", "unknown"),
    )
    db.add(entry)
    db.commit()