from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.models import IssueSeverity, IssueStatus, RunStatus


class RunOut(BaseModel):
    id: str
    status: RunStatus
    trigger_type: str
    triggered_by_name: Optional[str] = None
    started_at: datetime
    completed_at: Optional[datetime] = None
    records_checked: int
    issues_detected: int
    new_issues: int
    issues_resolved: int
    failed_checks: int
    error_message: Optional[str] = None
    check_results: list[dict] = []


class RunListResponse(BaseModel):
    items: list[RunOut]
    total: int
    page: int
    page_size: int


class ModuleBreakdown(BaseModel):
    module: str
    errors: int
    warnings: int


class SummaryOut(BaseModel):
    records_checked: int
    valid_records: int
    records_with_warnings: int
    records_with_errors: int
    unresolved_issues: int
    last_reconciliation_at: Optional[datetime] = None
    last_run: Optional[RunOut] = None
    running_run: Optional[RunOut] = None
    status_counts: dict[str, int]
    by_module: list[ModuleBreakdown]


class IssueRow(BaseModel):
    id: str
    issue_code: str
    issue_type: str
    severity: IssueSeverity
    module: str
    record_type: str
    record_id: str
    record_label: Optional[str] = None
    description: str
    detected_at: datetime
    last_detected_at: datetime
    status: IssueStatus
    resolved_by_name: Optional[str] = None
    resolved_at: Optional[datetime] = None
    resolution_note: Optional[str] = None


class IssueListResponse(BaseModel):
    items: list[IssueRow]
    total: int
    page: int
    page_size: int


class IssueEventOut(BaseModel):
    id: str
    user_name: Optional[str] = None  # None = the system
    previous_status: Optional[IssueStatus] = None
    new_status: IssueStatus
    note: Optional[str] = None
    created_at: datetime


class IssueDetail(IssueRow):
    context: dict[str, Any]
    events: list[IssueEventOut]


class IssueStatusUpdate(BaseModel):
    status: IssueStatus
    resolution_note: Optional[str] = Field(None, max_length=2000)


class IssueFilterOptions(BaseModel):
    issue_types: list[str]
    modules: list[str]
