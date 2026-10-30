import json
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.audit import log_action
from app.database import get_db
from app.dependencies import get_current_company_id, require_roles
from app.models import (
    User, UserRole, DataQualityIssue, DataQualityIssueEvent, ReconciliationRun,
    IssueSeverity, IssueStatus, RunStatus,
)
from app.schemas import (
    RunOut, RunListResponse, SummaryOut, IssueRow, IssueListResponse, IssueDetail, IssueEventOut,
    IssueStatusUpdate, IssueFilterOptions,
)
from app.services import data_quality as dq

router = APIRouter(prefix="/data-quality", tags=["data-quality"])

# Anyone who can use analytics can LOOK at data-quality results; changing
# anything (running checks, resolving issues) is limited to admins.
VIEW_ROLES = [UserRole.COMPANY_ADMIN, UserRole.SUPER_ADMIN, UserRole.ANALYST]
MANAGE_ROLES = [UserRole.COMPANY_ADMIN, UserRole.SUPER_ADMIN]


def _run_out(run: ReconciliationRun) -> RunOut:
    if run.trigger_user:
        who = run.trigger_user.name
    else:
        who = "Automatic (after import)" if run.trigger_type == "IMPORT" else None
    return RunOut(
        id=run.id, status=run.status, trigger_type=run.trigger_type, triggered_by_name=who,
        started_at=run.started_at, completed_at=run.completed_at, records_checked=run.records_checked,
        issues_detected=run.issues_detected, new_issues=run.new_issues, issues_resolved=run.issues_resolved,
        failed_checks=run.failed_checks, error_message=run.error_message,
        check_results=json.loads(run.check_results_json) if run.check_results_json else [],
    )


def _issue_row(i: DataQualityIssue) -> IssueRow:
    return IssueRow(
        id=i.id, issue_code=i.issue_code, issue_type=i.issue_type, severity=i.severity, module=i.module,
        record_type=i.record_type, record_id=i.record_id, record_label=i.record_label,
        description=i.description, detected_at=i.detected_at, last_detected_at=i.last_detected_at,
        status=i.status, resolved_by_name=i.resolver.name if i.resolver else None,
        resolved_at=i.resolved_at, resolution_note=i.resolution_note,
    )


def _get_issue_or_404(db: Session, company_id: str, issue_id: str) -> DataQualityIssue:
    # company_id is in the WHERE clause itself: another company's issue id
    # simply matches nothing.
    issue = db.query(DataQualityIssue).options(joinedload(DataQualityIssue.resolver)).filter(
        DataQualityIssue.id == issue_id, DataQualityIssue.company_id == company_id
    ).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    return issue


# ---------------------------------------------------------------- dashboard

@router.get("/summary", response_model=SummaryOut)
def summary(
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    current_user: User = Depends(require_roles(VIEW_ROLES)),
):
    s = dq.get_summary(db, company_id)
    return SummaryOut(
        records_checked=s["records_checked"], valid_records=s["valid_records"],
        records_with_warnings=s["records_with_warnings"], records_with_errors=s["records_with_errors"],
        unresolved_issues=s["unresolved_issues"], last_reconciliation_at=s["last_reconciliation_at"],
        last_run=_run_out(s["last_run"]) if s["last_run"] else None,
        running_run=_run_out(s["running_run"]) if s["running_run"] else None,
        status_counts=s["status_counts"], by_module=s["by_module"],
    )


# ---------------------------------------------------------------- runs

@router.post("/runs", response_model=RunOut, status_code=202)
def trigger_reconciliation(
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    current_user: User = Depends(require_roles(MANAGE_ROLES)),
):
    """Starts a full reconciliation and returns immediately with a RUNNING
    run. The checks execute in the background; the UI polls
    GET /data-quality/runs/{id} until it finishes, so it never looks frozen."""
    run, created = dq.start_run(db, company_id, current_user.id, "MANUAL")
    if not created:
        raise HTTPException(status_code=409, detail="A reconciliation is already running for your company.")

    log_action(db, request, "Reconciliation Started", company_id=company_id, user_id=current_user.id,
               resource_type="ReconciliationRun", resource_id=run.id, entity_name="Data Quality",
               details="Manual full reconciliation")
    db.commit()
    background_tasks.add_task(dq.execute_run_in_background, run.id)
    db.refresh(run)
    return _run_out(run)


@router.get("/runs", response_model=RunListResponse)
def list_runs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    current_user: User = Depends(require_roles(VIEW_ROLES)),
):
    query = db.query(ReconciliationRun).options(joinedload(ReconciliationRun.trigger_user)).filter(
        ReconciliationRun.company_id == company_id)
    total = query.count()
    rows = query.order_by(ReconciliationRun.started_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return RunListResponse(items=[_run_out(r) for r in rows], total=total, page=page, page_size=page_size)


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(
    run_id: str,
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    current_user: User = Depends(require_roles(VIEW_ROLES)),
):
    run = db.query(ReconciliationRun).options(joinedload(ReconciliationRun.trigger_user)).filter(
        ReconciliationRun.id == run_id, ReconciliationRun.company_id == company_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return _run_out(run)


# ---------------------------------------------------------------- issues

@router.get("/issues/filter-options", response_model=IssueFilterOptions)
def issue_filter_options(
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    current_user: User = Depends(require_roles(VIEW_ROLES)),
):
    types = [t for (t,) in db.query(DataQualityIssue.issue_type).filter(
        DataQualityIssue.company_id == company_id).distinct().order_by(DataQualityIssue.issue_type).all()]
    modules = [m for (m,) in db.query(DataQualityIssue.module).filter(
        DataQualityIssue.company_id == company_id).distinct().order_by(DataQualityIssue.module).all()]
    return IssueFilterOptions(issue_types=types, modules=modules)


@router.get("/issues", response_model=IssueListResponse)
def list_issues(
    search: Optional[str] = None,
    issue_type: Optional[str] = None,
    severity: Optional[IssueSeverity] = None,
    module: Optional[str] = None,
    status: Optional[IssueStatus] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    current_user: User = Depends(require_roles(VIEW_ROLES)),
):
    query = db.query(DataQualityIssue).options(joinedload(DataQualityIssue.resolver)).filter(
        DataQualityIssue.company_id == company_id)

    if search:
        like = f"%{search}%"
        query = query.filter(or_(
            DataQualityIssue.description.ilike(like), DataQualityIssue.record_label.ilike(like),
            DataQualityIssue.issue_type.ilike(like), DataQualityIssue.record_id.ilike(like),
        ))
    if issue_type:
        query = query.filter(DataQualityIssue.issue_type == issue_type)
    if severity:
        query = query.filter(DataQualityIssue.severity == severity)
    if module:
        query = query.filter(DataQualityIssue.module == module)
    if status:
        query = query.filter(DataQualityIssue.status == status)
    if date_from:
        query = query.filter(DataQualityIssue.detected_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        query = query.filter(DataQualityIssue.detected_at <= datetime.combine(date_to, datetime.max.time()))

    total = query.count()
    order = DataQualityIssue.detected_at.asc() if sort_dir == "asc" else DataQualityIssue.detected_at.desc()
    rows = query.order_by(order).offset((page - 1) * page_size).limit(page_size).all()
    return IssueListResponse(items=[_issue_row(i) for i in rows], total=total, page=page, page_size=page_size)


@router.get("/issues/{issue_id}", response_model=IssueDetail)
def get_issue(
    issue_id: str,
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    current_user: User = Depends(require_roles(VIEW_ROLES)),
):
    issue = _get_issue_or_404(db, company_id, issue_id)
    events = db.query(DataQualityIssueEvent).options(joinedload(DataQualityIssueEvent.user)).filter(
        DataQualityIssueEvent.issue_id == issue.id, DataQualityIssueEvent.company_id == company_id
    ).order_by(DataQualityIssueEvent.created_at.asc()).all()

    base = _issue_row(issue).model_dump()
    return IssueDetail(
        **base,
        context=dq.build_issue_context(db, company_id, issue),
        events=[IssueEventOut(
            id=e.id, user_name=e.user.name if e.user else None, previous_status=e.previous_status,
            new_status=e.new_status, note=e.note, created_at=e.created_at,
        ) for e in events],
    )


@router.patch("/issues/{issue_id}/status", response_model=IssueRow)
def update_issue_status(
    issue_id: str,
    payload: IssueStatusUpdate,
    request: Request,
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    current_user: User = Depends(require_roles(MANAGE_ROLES)),
):
    issue = _get_issue_or_404(db, company_id, issue_id)
    previous = issue.status
    if payload.status == previous:
        raise HTTPException(status_code=400, detail=f"This issue is already {previous.value.lower()}.")

    note = (payload.resolution_note or "").strip()
    closing = payload.status in (IssueStatus.RESOLVED, IssueStatus.IGNORED)
    if closing and len(note) < 3:
        raise HTTPException(status_code=400, detail="A resolution note is required to resolve or ignore an issue.")

    issue.status = payload.status
    if closing:
        issue.resolved_by, issue.resolved_at, issue.resolution_note = current_user.id, datetime.utcnow(), note
    else:
        # Reopened / moved to investigating: the old resolution no longer applies
        # (it stays visible in the issue's history).
        issue.resolved_by, issue.resolved_at, issue.resolution_note = None, None, None

    db.add(DataQualityIssueEvent(
        issue_id=issue.id, company_id=company_id, user_id=current_user.id,
        previous_status=previous, new_status=payload.status, note=note or None,
    ))
    record = issue.record_label or issue.record_id
    audit_details = f"{record}: {note}" if note else record
    log_action(db, request, "Data Quality Issue Updated", company_id=company_id, user_id=current_user.id,
               entity_name=issue.issue_type, resource_type="DataQualityIssue", resource_id=issue.id,
               details=audit_details[:480],
               before={"status": previous.value}, after={"status": payload.status.value})
    db.commit()
    db.refresh(issue)
    return _issue_row(issue)
