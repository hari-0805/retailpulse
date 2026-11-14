import csv
import io
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, UploadFile, File, Form, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import (
    User, UserRole, ImportJob, ImportError as ImportErrorRow,
    ImportType, ImportStatus, ImportRowStatus, NotificationType, NotificationPriority, AuditStatus,
)
from app.services.notifications import notify_roles
from app.services import data_quality as dq
from app.schemas import (
    ImportUploadResponse, ImportPreviewRow, ImportRowIssue, ImportValidationSummary,
    ImportJobOut, ImportJobListResponse, ImportJobDetailOut, ImportResultOut, ImportErrorOut,
    ImportProcessRequest, ImportProgressOut,
)
from app.dependencies import require_roles, get_current_company_id
from app.audit import log_action
from app.services import imports as import_service
from app.services.customers import recalculate_purchase_summary

router = APIRouter(prefix="/import", tags=["data-import"])

# Task 14: import functionality is Admin-only, front and back.
ADMIN_ONLY = [UserRole.COMPANY_ADMIN, UserRole.SUPER_ADMIN]

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
PREVIEW_ROW_LIMIT = 10
MAX_ROW_ISSUES_RETURNED = 200  # keep the upload response small; full list via /errors


def _uploader_name(job: ImportJob) -> Optional[str]:
    return job.uploader.name if job.uploader else None


def _job_to_out(job: ImportJob) -> ImportJobOut:
    return ImportJobOut(
        id=job.id,
        import_type=job.import_type,
        filename=job.filename,
        uploaded_by_name=_uploader_name(job),
        total_records=job.total_records,
        successful_records=job.successful_records,
        failed_records=job.failed_records,
        duplicate_records=job.duplicate_records,
        skipped_records=getattr(job, "skipped_records", 0) or 0,
        processed_records=getattr(job, "processed_records", 0) or 0,
        duplicate_strategy=getattr(job, "duplicate_strategy", "SKIP") or "SKIP",
        status=job.status,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        duration_seconds=job.duration_seconds,
    )


def _get_job_or_404(db: Session, company_id: str, import_id: str) -> ImportJob:
    job = db.query(ImportJob).options(joinedload(ImportJob.uploader)).filter(
        ImportJob.id == import_id, ImportJob.company_id == company_id
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Import job not found")
    return job


def _stage_validation(db: Session, job: ImportJob, results: list[import_service.RowResult]) -> ImportValidationSummary:
    """Clears any previously-stored row issues and re-stores the current
    validation pass, updating the job's counters."""
    db.query(ImportErrorRow).filter(ImportErrorRow.import_job_id == job.id).delete()

    valid = invalid = duplicate = 0
    for r in results:
        if r.status is None:
            valid += 1
            continue
        if r.status == ImportRowStatus.INVALID:
            invalid += 1
        elif r.status == ImportRowStatus.DUPLICATE:
            duplicate += 1
        db.add(ImportErrorRow(
            import_job_id=job.id,
            row_number=r.row_number,
            row_status=r.status,
            row_data=json.dumps(r.data),
            error_message=r.message,
        ))

    job.total_records = len(results)
    job.valid_records = valid
    job.invalid_records = invalid
    job.duplicate_records = duplicate
    job.status = ImportStatus.VALIDATED
    db.commit()
    db.refresh(job)
    return ImportValidationSummary(
        total_records=job.total_records, valid_records=valid,
        invalid_records=invalid, duplicate_records=duplicate,
    )


@router.get("/template/{import_type}")
def download_template(
    import_type: ImportType,
    current_user: User = Depends(require_roles(ADMIN_ONLY)),
):
    """
    Provides a downloadable CSV template with standard columns and sample data rows
    for the selected import type (Products, Inventory, Customers, Sales).
    """
    filename, content = import_service.get_template_csv(import_type)
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/upload", response_model=ImportUploadResponse)
async def upload_import(
    request: Request,
    import_type: ImportType = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    current_user: User = Depends(require_roles(ADMIN_ONLY)),
):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv files are supported")

    raw_bytes = await file.read()
    if len(raw_bytes) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="File is too large (max 10 MB)")
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="The uploaded file is empty")

    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded CSV")

    parsed = import_service.parse_csv_text(text)
    if not parsed.fieldnames:
        raise HTTPException(status_code=400, detail="Could not read a header row from this CSV")

    missing = import_service.missing_required_columns(import_type, parsed.fieldnames)
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Missing required column(s) for {import_type.value.title()}: {', '.join(missing)}",
        )
    if not parsed.rows:
        raise HTTPException(status_code=400, detail="The CSV has a header row but no data rows")

    job = ImportJob(
        company_id=company_id,
        import_type=import_type,
        filename=file.filename,
        uploaded_by=current_user.id,
        raw_content=text,
        total_records=len(parsed.rows),
        status=ImportStatus.PENDING,
    )
    db.add(job)
    db.flush()

    results = import_service.validate_rows(db, company_id, import_type, parsed)
    summary = _stage_validation(db, job, results)

    log_action(db, request, "Import Uploaded", company_id=company_id, user_id=current_user.id,
               entity_name=file.filename, resource_type="ImportJob", resource_id=job.id,
               details=f"{import_type.value}, {summary.total_records} rows")
    db.commit()

    preview_rows = [ImportPreviewRow(row_number=r.row_number, data=r.data) for r in results[:PREVIEW_ROW_LIMIT]]
    row_issues = [
        ImportRowIssue(row_number=r.row_number, status=r.status, message=r.message, data=r.data)
        for r in results if r.status is not None
    ][:MAX_ROW_ISSUES_RETURNED]

    return ImportUploadResponse(
        import_id=job.id,
        import_type=import_type,
        filename=job.filename,
        status=job.status,
        detected_columns=parsed.fieldnames,
        missing_columns=[],
        preview_rows=preview_rows,
        summary=summary,
        row_issues=row_issues,
    )


@router.post("/{import_id}/validate", response_model=ImportUploadResponse)
def revalidate_import(
    import_id: str,
    request: Request,
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    current_user: User = Depends(require_roles(ADMIN_ONLY)),
):
    """
    Re-runs column + row validation against the currently staged CSV.
    Useful if the Admin fixed conflicting data (e.g. added a missing
    product) between uploading and processing.
    """
    job = _get_job_or_404(db, company_id, import_id)
    if job.status in (ImportStatus.COMPLETED, ImportStatus.COMPLETED_WITH_ERRORS, ImportStatus.PROCESSING):
        raise HTTPException(status_code=400, detail=f"Import is already {job.status.value.lower()} and cannot be re-validated")

    parsed = import_service.parse_csv_text(job.raw_content)
    results = import_service.validate_rows(db, company_id, job.import_type, parsed)
    summary = _stage_validation(db, job, results)

    preview_rows = [ImportPreviewRow(row_number=r.row_number, data=r.data) for r in results[:PREVIEW_ROW_LIMIT]]
    row_issues = [
        ImportRowIssue(row_number=r.row_number, status=r.status, message=r.message, data=r.data)
        for r in results if r.status is not None
    ][:MAX_ROW_ISSUES_RETURNED]

    return ImportUploadResponse(
        import_id=job.id,
        import_type=job.import_type,
        filename=job.filename,
        status=job.status,
        detected_columns=parsed.fieldnames,
        missing_columns=[],
        preview_rows=preview_rows,
        summary=summary,
        row_issues=row_issues,
    )


@router.post("/{import_id}/process", response_model=ImportResultOut)
def process_import(
    import_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    duplicate_strategy: Optional[str] = Query("SKIP", description="Duplicate strategy: SKIP, UPDATE, or REJECT"),
    body: Optional[ImportProcessRequest] = None,
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    current_user: User = Depends(require_roles(ADMIN_ONLY)),
):
    strategy = (body.duplicate_strategy if body and body.duplicate_strategy else duplicate_strategy) or "SKIP"
    strategy = strategy.upper()
    if strategy not in ("SKIP", "UPDATE", "REJECT"):
        strategy = "SKIP"

    job = _get_job_or_404(db, company_id, import_id)
    if job.status in (ImportStatus.COMPLETED, ImportStatus.COMPLETED_WITH_ERRORS, ImportStatus.PROCESSING):
        raise HTTPException(status_code=400, detail=f"Import is already {job.status.value.lower()}")

    job.status = ImportStatus.PROCESSING
    job.started_at = datetime.utcnow()
    job.duplicate_strategy = strategy
    job.processed_records = 0
    job.successful_records = 0
    job.failed_records = 0
    job.duplicate_records = 0
    job.skipped_records = 0
    db.commit()
    db.refresh(job)

    log_action(db, request, "Import Started", company_id=company_id, user_id=current_user.id,
               entity_name=job.filename, resource_type="ImportJob", resource_id=job.id,
               details=f"Strategy: {strategy}, {job.total_records} records")
    db.commit()

    background_tasks.add_task(
        import_service.execute_import_job_background,
        job.id,
        company_id,
        current_user.id,
        strategy,
    )

    return ImportResultOut(
        id=job.id,
        status=job.status,
        total_records=job.total_records,
        successful_records=job.successful_records,
        failed_records=job.failed_records,
        duplicate_records=job.duplicate_records,
        skipped_records=job.skipped_records,
        processed_records=job.processed_records,
        duration_seconds=job.duration_seconds,
        completed_at=job.completed_at,
    )


@router.get("/{import_id}/status", response_model=ImportProgressOut)
def get_import_status(
    import_id: str,
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    current_user: User = Depends(require_roles(ADMIN_ONLY)),
):
    """
    Returns real-time progress metrics for active or completed import jobs.
    """
    job = _get_job_or_404(db, company_id, import_id)
    pct = (job.processed_records / job.total_records * 100) if job.total_records > 0 else 0.0
    if job.status in (ImportStatus.COMPLETED, ImportStatus.COMPLETED_WITH_ERRORS, ImportStatus.FAILED, ImportStatus.CANCELLED):
        pct = 100.0
    return ImportProgressOut(
        id=job.id,
        status=job.status,
        total_records=job.total_records,
        processed_records=job.processed_records,
        progress_percentage=round(pct, 1),
        successful_records=job.successful_records,
        failed_records=job.failed_records,
        duplicate_records=job.duplicate_records,
        skipped_records=job.skipped_records,
        duration_seconds=job.duration_seconds,
    )


@router.post("/{import_id}/cancel", response_model=ImportJobOut)
def cancel_import(
    import_id: str,
    request: Request,
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    current_user: User = Depends(require_roles(ADMIN_ONLY)),
):
    """
    Cancels an active or pending import job.
    """
    job = _get_job_or_404(db, company_id, import_id)
    if job.status in (ImportStatus.COMPLETED, ImportStatus.COMPLETED_WITH_ERRORS, ImportStatus.FAILED):
        raise HTTPException(status_code=400, detail=f"Import is already {job.status.value.lower()} and cannot be cancelled")

    job.status = ImportStatus.CANCELLED
    job.completed_at = datetime.utcnow()
    if job.started_at:
        job.duration_seconds = max(0.1, (job.completed_at - job.started_at).total_seconds())

    log_action(db, request, "Import Cancelled", company_id=company_id, user_id=current_user.id,
               entity_name=job.filename, resource_type="ImportJob", resource_id=job.id,
               details=f"Cancelled by user. Processed {job.processed_records}/{job.total_records}")
    db.commit()
    db.refresh(job)
    return _job_to_out(job)


@router.get("/history", response_model=ImportJobListResponse)
def import_history(
    import_type: Optional[ImportType] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    current_user: User = Depends(require_roles(ADMIN_ONLY)),
):
    query = db.query(ImportJob).options(joinedload(ImportJob.uploader)).filter(
        ImportJob.company_id == company_id
    )
    if import_type:
        query = query.filter(ImportJob.import_type == import_type)

    total = query.count()
    jobs = query.order_by(ImportJob.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()

    return ImportJobListResponse(items=[_job_to_out(j) for j in jobs], total=total)


@router.get("/{import_id}", response_model=ImportJobDetailOut)
def get_import(
    import_id: str,
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    current_user: User = Depends(require_roles(ADMIN_ONLY)),
):
    job = _get_job_or_404(db, company_id, import_id)
    return ImportJobDetailOut(
        **_job_to_out(job).model_dump(),
        valid_records=job.valid_records,
        invalid_records=job.invalid_records,
    )


@router.get("/{import_id}/errors")
def get_import_errors(
    import_id: str,
    format: str = Query("json", pattern="^(json|csv)$"),
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    current_user: User = Depends(require_roles(ADMIN_ONLY)),
):
    job = _get_job_or_404(db, company_id, import_id)
    rows = db.query(ImportErrorRow).filter(ImportErrorRow.import_job_id == job.id).order_by(
        ImportErrorRow.row_number.asc()
    ).all()

    if format == "json":
        return [
            ImportErrorOut(
                row_number=r.row_number, status=r.row_status,
                message=r.error_message, data=json.loads(r.row_data),
            )
            for r in rows
        ]

    # CSV download: original columns + Row Number / Issue Type / Error Reason appended.
    buffer = io.StringIO()
    fieldnames: list[str] = []
    parsed_rows = []
    for r in rows:
        data = json.loads(r.row_data)
        parsed_rows.append((r, data))
        for k in data.keys():
            if k not in fieldnames:
                fieldnames.append(k)
    fieldnames += ["Row Number", "Issue Type", "Error Reason"]

    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for r, data in parsed_rows:
        out_row = dict(data)
        out_row["Row Number"] = r.row_number
        out_row["Issue Type"] = r.row_status.value
        out_row["Error Reason"] = r.error_message
        writer.writerow(out_row)

    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{job.filename}_errors.csv"'},
    )
