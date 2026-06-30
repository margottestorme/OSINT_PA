from fastapi import Request
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def create_audit_log(
    db: AsyncSession,
    action: str,
    target: str | None = None,
    user_id: int | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        user_id=user_id,
        action=action,
        target=target,
        ip_address=ip_address,
    )
    db.add(entry)
    await db.flush()
    logger.info(f"[audit] {action} | user_id={user_id} | target={target} | ip={ip_address}")
    return entry
