import enum
from datetime import datetime

from sqlalchemy import (
    Column, String, DateTime, ForeignKey, Enum as SAEnum, Text, Integer, Index, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.base import gen_uuid


class IssueSeverity(str, enum.Enum):
    WARNING = "WARNING"
    ERROR = "ERROR"


class IssueStatus(str, enum.Enum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    RESOLVED = "RESOLVED"
    IGNORED = "IGNORED"


class RunStatus(str, enum.Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_ISSUES = "COMPLETED_WITH_ISSUES"
    FAILED = "FAILED"


class ReconciliationRun(Base):
    """One row per full (manual or post-import) reconciliation. Small
    automatic checks after a single sale/adjustment do NOT create a row —
    they would flood this history — they only create/update issues."""
    __tablename__ = "reconciliation_runs"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    company_id = Column(UUID(as_uuid=False), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    triggered_by = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    trigger_type = Column(String(20), nullable=False, default="MANUAL")  # MANUAL | IMPORT

    status = Column(SAEnum(RunStatus), nullable=False, default=RunStatus.RUNNING, index=True)
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    records_checked = Column(Integer, nullable=False, default=0)
    issues_detected = Column(Integer, nullable=False, default=0)   # findings present at this run
    new_issues = Column(Integer, nullable=False, default=0)        # of those, newly created or reopened
    issues_resolved = Column(Integer, nullable=False, default=0)   # auto-resolved because the problem is gone
    failed_checks = Column(Integer, nullable=False, default=0)

    check_codes_json = Column(Text, nullable=True)      # which checks this run covered
    check_results_json = Column(Text, nullable=True)    # per-check outcome: found / failed + error
    error_message = Column(String(1000), nullable=True)

    trigger_user = relationship("User")

    __table_args__ = (Index("ix_recon_runs_company_started", "company_id", "started_at"),)


class DataQualityIssue(Base):
    """
    One row per distinct problem, for the life of that problem. The
    fingerprint (check + record + optional key) is unique per company, so
    re-detecting the same problem updates this row (or reopens it) rather
    than creating a duplicate.
    """
    __tablename__ = "data_quality_issues"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    company_id = Column(UUID(as_uuid=False), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)

    issue_code = Column(String(60), nullable=False, index=True)
    issue_type = Column(String(120), nullable=False)
    severity = Column(SAEnum(IssueSeverity), nullable=False, index=True)
    module = Column(String(40), nullable=False, index=True)

    record_type = Column(String(40), nullable=False)
    record_id = Column(String(64), nullable=False)
    record_label = Column(String(255), nullable=True)
    description = Column(String(1000), nullable=False)
    details_json = Column(Text, nullable=False, default="{}")
    fingerprint = Column(String(300), nullable=False)

    status = Column(SAEnum(IssueStatus), nullable=False, default=IssueStatus.OPEN, index=True)
    detected_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    last_detected_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    run_id = Column(UUID(as_uuid=False), ForeignKey("reconciliation_runs.id", ondelete="SET NULL"), nullable=True)

    # Latest resolution info (full history lives in DataQualityIssueEvent).
    resolved_by = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolution_note = Column(Text, nullable=True)

    resolver = relationship("User")
    events = relationship("DataQualityIssueEvent", back_populates="issue", cascade="all, delete-orphan",
                          order_by="DataQualityIssueEvent.created_at")

    __table_args__ = (
        UniqueConstraint("company_id", "fingerprint", name="uq_dq_issue_company_fingerprint"),
        Index("ix_dq_issues_company_status", "company_id", "status"),
        Index("ix_dq_issues_company_detected", "company_id", "detected_at"),
    )


class DataQualityIssueEvent(Base):
    """Every status change: who, when, previous -> new, and the note."""
    __tablename__ = "data_quality_issue_events"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    issue_id = Column(UUID(as_uuid=False), ForeignKey("data_quality_issues.id", ondelete="CASCADE"), nullable=False, index=True)
    company_id = Column(UUID(as_uuid=False), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)  # NULL = system
    previous_status = Column(SAEnum(IssueStatus), nullable=True)
    new_status = Column(SAEnum(IssueStatus), nullable=False)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    issue = relationship("DataQualityIssue", back_populates="events")
    user = relationship("User")
