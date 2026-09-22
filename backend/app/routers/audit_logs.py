import csv
import io
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_roles, get_current_company_id
from app.audit import log_action
from app.models import User, UserRole, AuditStatus
from app.schemas import AuditLogRow, AuditLogDetail, AuditLogListResponse, AuditLogFilterOptions
from app.services import audit_logs as audit_service

router = APIRouter(prefix="/audit-logs", tags=["audit-logs"])

# Task 14-style enforcement, mirrored here per Task 13's "Admin-Only Access"
# requirement: only Admins may view, export, or clear audit logs. Frontend
# hides the nav item too, but that's cosmetic — this is what actually blocks it.
ADMIN_ONLY = [UserRole.COMPANY_ADMIN, UserRole.SUPER_ADMIN]


@router.get("", response_model=AuditLogListResponse)
def list_audit_logs(
    user_id: Optional[str] = None,
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    status_filter: Optional[AuditStatus] = Query(None, alias="status"),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    search: Optional[str] = None,
    sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    current_user: User = Depends(require_roles(ADMIN_ONLY)),
):
    rows, total = audit_service.list_audit_logs(
        db, company_id, user_id=user_id, action=action, resource_type=resource_type,
        status=status_filter, date_from=date_from, date_to=date_to, search=search,
        sort_dir=sort_dir, page=page, page_size=page_size,
    )
    return AuditLogListResponse(
        items=[AuditLogRow(**audit_service.serialize_row(r)) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.get("/filter-options", response_model=AuditLogFilterOptions)
def get_filter_options(
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    current_user: User = Depends(require_roles(ADMIN_ONLY)),
):
    return AuditLogFilterOptions(**audit_service.get_filter_options(db, company_id))


@router.get("/export")
def export_audit_logs(
    format: str = Query(..., pattern="^(csv|pdf)$"),
    user_id: Optional[str] = None,
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    status_filter: Optional[AuditStatus] = Query(None, alias="status"),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    search: Optional[str] = None,
    sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
    request: Request = None,
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    current_user: User = Depends(require_roles(ADMIN_ONLY)),
):
    # No page/page_size here on purpose — export respects every filter
    # above but always covers the FULL matching set, not just one page.
    rows, _total = audit_service.list_audit_logs(
        db, company_id, user_id=user_id, action=action, resource_type=resource_type,
        status=status_filter, date_from=date_from, date_to=date_to, search=search,
        sort_dir=sort_dir, page=1, page_size=100_000,
    )

    log_action(db, request, "Audit Logs Exported", company_id=company_id, user_id=current_user.id,
               resource_type="AuditLog", status=AuditStatus.SUCCESS, details=f"format={format}, count={len(rows)}")
    db.commit()

    headers = ["Timestamp", "User", "Action", "Resource Type", "Resource ID", "Description", "IP Address", "Status"]
    data_rows = [[
        r.timestamp.isoformat(), r.user.name if r.user else "System", r.action,
        r.resource_type or "", r.resource_id or "", r.details or "", r.ip_address or "", r.status.value,
    ] for r in rows]

    if format == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(headers)
        writer.writerows(data_rows)
        buffer.seek(0)
        return StreamingResponse(
            iter([buffer.getvalue()]), media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=audit_logs.csv"},
        )

    try:
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Spacer, Paragraph
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import cm
    except ImportError:
        raise HTTPException(status_code=500, detail="PDF export requires the 'reportlab' package.")

    pdf_buffer = io.BytesIO()
    doc = SimpleDocTemplate(pdf_buffer, pagesize=landscape(A4))
    styles = getSampleStyleSheet()
    elements = [Paragraph("RetailPulse Audit Log Report", styles["Title"]), Spacer(1, 0.5 * cm)]
    table = Table([headers] + data_rows, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2563eb")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
    ]))
    elements.append(table)
    doc.build(elements)
    pdf_buffer.seek(0)
    return StreamingResponse(
        pdf_buffer, media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=audit_logs.pdf"},
    )


@router.delete("", status_code=status.HTTP_200_OK)
def clear_audit_logs(
    request: Request,
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    current_user: User = Depends(require_roles(ADMIN_ONLY)),
):
    """
    Admin-only, irreversible. The frontend requires a typed/explicit
    confirmation before ever calling this (see DataImport-style confirm
    dialogs elsewhere in the app for the pattern). Deletes only this
    company's logs, then records the clearing itself as the sole
    remaining entry — so there's always a trace of who wiped the log.
    """
    deleted_count = audit_service.clear_audit_logs(db, company_id)
    log_action(db, request, "Audit Logs Cleared", company_id=company_id, user_id=current_user.id,
               resource_type="AuditLog", status=AuditStatus.SUCCESS, details=f"{deleted_count} records cleared")
    db.commit()
    return {"deleted_count": deleted_count}


@router.get("/{log_id}", response_model=AuditLogDetail)
def get_audit_log(
    log_id: str,
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    current_user: User = Depends(require_roles(ADMIN_ONLY)),
):
    entry = audit_service.get_audit_log(db, company_id, log_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Audit log not found")
    return AuditLogDetail(**audit_service.serialize_detail(entry))
