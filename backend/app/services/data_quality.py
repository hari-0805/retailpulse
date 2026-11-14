"""
Task 16: Data Quality & Reconciliation.

Every check below is a read-only query over the EXISTING tables (products,
sales, sale_items, inventory, inventory_movements, customers) — nothing is
copied into a parallel dataset. The only things this module stores are
the *problems it found* (DataQualityIssue), the history of each
reconciliation (ReconciliationRun) and the status changes on issues
(DataQualityIssueEvent).

How the pieces fit:
  * A CHECK is a function (db, company_id, scope) -> list[Finding].
  * A FINDING is one detected inconsistency. Its `fingerprint`
    (check + record + optional key) identifies "the same problem" across runs.
  * `run_reconciliation()` runs a set of checks, then `_persist()` turns the
    findings into issues WITHOUT creating duplicates:
        new problem                 -> new issue (OPEN)
        problem seen again          -> existing issue is refreshed
        was RESOLVED, seen again    -> the same issue is reopened
        IGNORED, seen again         -> stays ignored (a person decided that)
        OPEN/INVESTIGATING, no longer found -> auto-resolved
  * SCOPE limits a run to some records. A full run (scope=None) checks
    everything; the automatic checks wired into sales/stock/product/customer
    workflows pass a small scope so they stay cheap. Auto-resolution only
    ever touches issues inside the scope that was actually re-checked.
"""
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Callable, Optional

from sqlalchemy import func, and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import (
    Product, ProductStatus, Customer, Sale, SaleItem, Inventory, InventoryMovement, MovementType,
    DataQualityIssue, DataQualityIssueEvent, ReconciliationRun,
    IssueSeverity, IssueStatus, RunStatus,
    AuditLog, AuditStatus, NotificationType, NotificationPriority,
)

logger = logging.getLogger(__name__)

UNRESOLVED = (IssueStatus.OPEN, IssueStatus.INVESTIGATING)
STALE_RUN_MINUTES = 15
AMOUNT_TOLERANCE = Decimal("0.01")
_INVOICE_RE = re.compile(r"Invoice:\s*([^)]+)\)")
_SKU_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]*$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Module names shown in the UI / used for filtering.
M_PRODUCTS, M_SALES, M_INVENTORY = "Products", "Sales", "Inventory"
M_MOVEMENTS, M_CUSTOMERS, M_REPORTS = "Stock Movements", "Customers", "Reports"


@dataclass
class Scope:
    """None for a field means 'no restriction on that entity'."""
    product_ids: Optional[set] = None
    sale_ids: Optional[set] = None
    customer_ids: Optional[set] = None


@dataclass
class Finding:
    code: str
    issue_type: str
    severity: IssueSeverity
    module: str
    record_type: str
    record_id: str
    record_label: str
    description: str
    details: dict = field(default_factory=dict)
    key: str = ""  # distinguishes several problems on the same record

    @property
    def fingerprint(self) -> str:
        return f"{self.code}:{self.record_type}:{self.record_id}:{self.key}"


def _money(value) -> str:
    return str(Decimal(value or 0).quantize(Decimal("0.01")))


# =====================================================================
# CHECKS
# =====================================================================

def _movement_base(db: Session, company_id: str, scope: Optional[Scope]):
    q = db.query(
        InventoryMovement, Inventory.product_id.label("product_id"),
        Product.name.label("product_name"), Product.sku.label("sku"),
    ).join(Inventory, Inventory.id == InventoryMovement.inventory_id).join(
        Product, Product.id == Inventory.product_id
    ).filter(Inventory.company_id == company_id)
    if scope and scope.product_ids is not None:
        q = q.filter(Inventory.product_id.in_(scope.product_ids))
    return q


def check_sale_exceeds_stock(db, company_id, scope):
    """A sale deducted more units than were in stock at that moment.
    The movement ledger records the stock level *before* each sale, so
    'stock at the time of the transaction' is read straight from it."""
    rows = _movement_base(db, company_id, scope).filter(
        InventoryMovement.movement_type == MovementType.SALE,
        InventoryMovement.quantity_changed < 0,
        InventoryMovement.previous_quantity < -InventoryMovement.quantity_changed,
    ).all()

    invoices = set()
    parsed = {}
    for r in rows:
        m = _INVOICE_RE.search(r.InventoryMovement.reason or "")
        parsed[r.InventoryMovement.id] = m.group(1).strip() if m else None
        if parsed[r.InventoryMovement.id]:
            invoices.add(parsed[r.InventoryMovement.id])
    sales_by_invoice = {}
    if invoices:
        for s in db.query(Sale.id, Sale.invoice_number).filter(
            Sale.company_id == company_id, Sale.invoice_number.in_(invoices)
        ).all():
            sales_by_invoice[s.invoice_number] = s.id

    findings = []
    for r in rows:
        mv = r.InventoryMovement
        invoice = parsed[mv.id]
        sold = -mv.quantity_changed
        sale_id = sales_by_invoice.get(invoice)
        findings.append(Finding(
            code="SALE_EXCEEDS_STOCK", issue_type="Sale quantity exceeded available stock",
            severity=IssueSeverity.ERROR, module=M_SALES,
            record_type="Sale" if sale_id else "StockMovement", record_id=sale_id or mv.id,
            record_label=f"Invoice {invoice}" if invoice else f"{r.product_name} movement",
            description=(f"{sold} unit(s) of '{r.product_name}' were sold but only {mv.previous_quantity} "
                         f"were in stock at the time (shortfall of {sold - mv.previous_quantity})."),
            details={
                "product_id": r.product_id, "product_name": r.product_name, "sku": r.sku,
                "invoice_number": invoice, "quantity_sold": sold,
                "stock_before_sale": mv.previous_quantity, "shortfall": sold - mv.previous_quantity,
                "movement_id": mv.id,
            },
            key=mv.id,
        ))
    return findings


def check_movement_arithmetic(db, company_id, scope):
    """previous + change must equal updated. A movement that was clamped to
    zero (because stock would have gone negative) is already reported by
    SALE_EXCEEDS_STOCK, so it is excluded here to avoid double reporting."""
    rows = _movement_base(db, company_id, scope).filter(
        InventoryMovement.previous_quantity + InventoryMovement.quantity_changed != InventoryMovement.updated_quantity,
        ~and_(
            InventoryMovement.updated_quantity == 0,
            InventoryMovement.previous_quantity + InventoryMovement.quantity_changed < 0,
        ),
    ).all()

    findings = []
    for r in rows:
        mv = r.InventoryMovement
        expected = mv.previous_quantity + mv.quantity_changed
        findings.append(Finding(
            code="MOVEMENT_ARITHMETIC", issue_type="Stock movement quantities don't add up",
            severity=IssueSeverity.ERROR, module=M_MOVEMENTS,
            record_type="StockMovement", record_id=mv.id,
            record_label=f"{r.product_name} ({r.sku}) on {mv.created_at:%Y-%m-%d %H:%M}",
            description=(f"Movement says {mv.previous_quantity} {mv.quantity_changed:+d} should give {expected}, "
                         f"but {mv.updated_quantity} was recorded."),
            details={
                "product_id": r.product_id, "product_name": r.product_name, "sku": r.sku,
                "movement_type": mv.movement_type.value, "previous_quantity": mv.previous_quantity,
                "quantity_changed": mv.quantity_changed, "recorded_quantity": mv.updated_quantity,
                "expected_quantity": expected, "difference": mv.updated_quantity - expected,
                "reason": mv.reason,
            },
        ))
    return findings


def check_movement_chain(db, company_id, scope):
    """Each movement should start from where the previous one ended."""
    prev_updated = func.lag(InventoryMovement.updated_quantity).over(
        partition_by=InventoryMovement.inventory_id,
        order_by=[InventoryMovement.created_at, InventoryMovement.id],
    ).label("prev_updated")
    base = db.query(
        InventoryMovement.id.label("movement_id"),
        InventoryMovement.previous_quantity.label("previous_quantity"),
        InventoryMovement.created_at.label("created_at"),
        Inventory.product_id.label("product_id"),
        prev_updated,
    ).join(Inventory, Inventory.id == InventoryMovement.inventory_id).filter(Inventory.company_id == company_id)
    if scope and scope.product_ids is not None:
        base = base.filter(Inventory.product_id.in_(scope.product_ids))
    sub = base.subquery()

    rows = db.query(
        sub.c.movement_id, sub.c.previous_quantity, sub.c.prev_updated, sub.c.created_at, sub.c.product_id,
        Product.name.label("product_name"), Product.sku.label("sku"),
    ).join(Product, Product.id == sub.c.product_id).filter(
        sub.c.prev_updated.isnot(None), sub.c.prev_updated != sub.c.previous_quantity,
    ).all()

    return [Finding(
        code="MOVEMENT_CHAIN_BREAK", issue_type="Stock movement doesn't continue from the previous one",
        severity=IssueSeverity.WARNING, module=M_MOVEMENTS,
        record_type="StockMovement", record_id=r.movement_id,
        record_label=f"{r.product_name} ({r.sku}) on {r.created_at:%Y-%m-%d %H:%M}",
        description=(f"The previous movement ended at {r.prev_updated} units, but this one starts from "
                     f"{r.previous_quantity} — stock changed outside the movement ledger."),
        details={
            "product_id": r.product_id, "product_name": r.product_name, "sku": r.sku,
            "expected_quantity": r.prev_updated, "recorded_quantity": r.previous_quantity,
            "difference": r.previous_quantity - r.prev_updated,
        },
    ) for r in rows]


def check_inventory_vs_movements(db, company_id, scope):
    """Inventory.current_stock should equal where the latest movement ended."""
    latest = db.query(
        InventoryMovement.inventory_id.label("inventory_id"),
        func.max(InventoryMovement.created_at).label("max_created"),
    ).join(Inventory, Inventory.id == InventoryMovement.inventory_id).filter(
        Inventory.company_id == company_id
    ).group_by(InventoryMovement.inventory_id).subquery()

    q = db.query(Inventory, Product, InventoryMovement).join(
        Product, Product.id == Inventory.product_id
    ).join(latest, latest.c.inventory_id == Inventory.id).join(
        InventoryMovement, and_(
            InventoryMovement.inventory_id == Inventory.id,
            InventoryMovement.created_at == latest.c.max_created,
        )
    ).filter(Inventory.company_id == company_id, Inventory.current_stock != InventoryMovement.updated_quantity)
    if scope and scope.product_ids is not None:
        q = q.filter(Inventory.product_id.in_(scope.product_ids))

    findings, seen = [], set()
    for inv, product, mv in q.all():
        if inv.id in seen:  # two movements with an identical timestamp
            continue
        seen.add(inv.id)
        diff = inv.current_stock - mv.updated_quantity
        findings.append(Finding(
            code="INVENTORY_MOVEMENT_MISMATCH", issue_type="Inventory quantity doesn't match stock movements",
            severity=IssueSeverity.ERROR, module=M_INVENTORY,
            record_type="Inventory", record_id=inv.id, record_label=f"{product.name} ({product.sku})",
            description=(f"Inventory shows {inv.current_stock} units but the latest stock movement ended at "
                         f"{mv.updated_quantity} (difference {diff:+d})."),
            details={
                "product_id": product.id, "product_name": product.name, "sku": product.sku,
                "current_quantity": inv.current_stock, "expected_quantity": mv.updated_quantity,
                "difference": diff, "last_movement_id": mv.id,
            },
        ))
    return findings


def check_product_stock_drift(db, company_id, scope):
    """The product record and the inventory record each hold a stock figure.
    They are supposed to move together."""
    q = db.query(Product, Inventory).join(Inventory, Inventory.product_id == Product.id).filter(
        Product.company_id == company_id, Inventory.company_id == company_id,
        Product.stock_quantity != Inventory.current_stock,
    )
    if scope and scope.product_ids is not None:
        q = q.filter(Product.id.in_(scope.product_ids))
    findings = []
    for product, inv in q.all():
        diff = inv.current_stock - product.stock_quantity
        findings.append(Finding(
            code="PRODUCT_STOCK_DRIFT", issue_type="Product stock differs from inventory stock",
            severity=IssueSeverity.WARNING, module=M_INVENTORY,
            record_type="Product", record_id=product.id, record_label=f"{product.name} ({product.sku})",
            description=(f"The product record says {product.stock_quantity} units but inventory says "
                         f"{inv.current_stock} (difference {diff:+d})."),
            details={
                "product_id": product.id, "product_name": product.name, "sku": product.sku,
                "current_quantity": inv.current_stock, "expected_quantity": product.stock_quantity,
                "difference": diff,
            },
        ))
    return findings


def _sale_item_base(db: Session, company_id: str, scope: Optional[Scope]):
    q = db.query(
        SaleItem.id.label("item_id"), SaleItem.product_id.label("product_id"), SaleItem.quantity.label("quantity"),
        Sale.id.label("sale_id"), Sale.invoice_number.label("invoice_number"), Sale.created_at.label("sale_created"),
        Product.id.label("pid"), Product.name.label("product_name"), Product.sku.label("sku"),
        Product.status.label("product_status"), Product.company_id.label("product_company"),
        Product.updated_at.label("product_updated"),
    ).join(Sale, Sale.id == SaleItem.sale_id).outerjoin(
        Product, Product.id == SaleItem.product_id
    ).filter(Sale.company_id == company_id)
    if scope and scope.sale_ids is not None:
        q = q.filter(Sale.id.in_(scope.sale_ids))
    return q


def check_sale_invalid_product(db, company_id, scope):
    findings = []
    for r in _sale_item_base(db, company_id, scope).all():
        if r.pid is None:
            reason, extra = "no longer exists", {}
        elif r.product_company != company_id:
            reason, extra = "belongs to a different company", {"product_name": r.product_name}
        else:
            continue
        findings.append(Finding(
            code="SALE_INVALID_PRODUCT", issue_type="Sale references an invalid product",
            severity=IssueSeverity.ERROR, module=M_SALES, record_type="Sale", record_id=r.sale_id,
            record_label=f"Invoice {r.invoice_number}",
            description=f"Invoice {r.invoice_number} includes a product that {reason}.",
            details={"product_id": r.product_id, "invoice_number": r.invoice_number, **extra}, key=r.product_id,
        ))
    return findings


def check_sale_inactive_product(db, company_id, scope):
    """Flags a sale only when the product was ALREADY inactive when the sale
    was made. A product deactivated after a sale is normal and not flagged.
    (Approximation: the product's last-updated time is used as the moment it
    reached its current status, since status history isn't stored.)"""
    findings = []
    for r in _sale_item_base(db, company_id, scope).all():
        if r.pid is None or r.product_company != company_id:
            continue
        if r.product_status != ProductStatus.ACTIVE and r.product_updated <= r.sale_created:
            findings.append(Finding(
                code="SALE_INACTIVE_PRODUCT", issue_type="Sale of an inactive product",
                severity=IssueSeverity.WARNING, module=M_SALES, record_type="Sale", record_id=r.sale_id,
                record_label=f"Invoice {r.invoice_number}",
                description=f"Invoice {r.invoice_number} sold '{r.product_name}', which was inactive at the time.",
                details={"product_id": r.product_id, "product_name": r.product_name, "sku": r.sku,
                         "invoice_number": r.invoice_number},
                key=r.product_id,
            ))
    return findings


def check_sale_invalid_customer(db, company_id, scope):
    q = db.query(
        Sale.id.label("sale_id"), Sale.invoice_number.label("invoice_number"),
        Sale.customer_id.label("customer_id"), Sale.customer_name.label("customer_name"),
        Customer.company_id.label("cust_company"), Customer.is_deleted.label("cust_deleted"),
    ).outerjoin(Customer, Customer.id == Sale.customer_id).filter(
        Sale.company_id == company_id, Sale.customer_id.isnot(None)
    )
    if scope and scope.sale_ids is not None:
        q = q.filter(Sale.id.in_(scope.sale_ids))
    elif scope and scope.customer_ids is not None:
        q = q.filter(Sale.customer_id.in_(scope.customer_ids))

    findings = []
    for r in q.all():
        if r.cust_company is None:
            severity, why = IssueSeverity.ERROR, "no longer exists"
        elif r.cust_company != company_id:
            severity, why = IssueSeverity.ERROR, "belongs to a different company"
        elif r.cust_deleted:
            severity, why = IssueSeverity.WARNING, "has been deleted"
        else:
            continue
        findings.append(Finding(
            code="SALE_INVALID_CUSTOMER", issue_type="Sale references an invalid customer",
            severity=severity, module=M_SALES, record_type="Sale", record_id=r.sale_id,
            record_label=f"Invoice {r.invoice_number}",
            description=f"Invoice {r.invoice_number} is linked to customer '{r.customer_name}', who {why}.",
            details={"customer_id": r.customer_id, "customer_name": r.customer_name,
                     "invoice_number": r.invoice_number},
        ))
    return findings


def check_sale_totals(db, company_id, scope):
    """The sale's stored total must equal the sum of its line items —
    this is the figure the Sales report and Analytics add up."""
    items_total = func.coalesce(func.sum(SaleItem.total), 0)
    q = db.query(
        Sale.id, Sale.invoice_number, Sale.total_amount, items_total.label("items_total"),
        func.count(SaleItem.id).label("item_count"),
    ).outerjoin(SaleItem, SaleItem.sale_id == Sale.id).filter(Sale.company_id == company_id)
    if scope and scope.sale_ids is not None:
        q = q.filter(Sale.id.in_(scope.sale_ids))
    q = q.group_by(Sale.id).having(func.abs(Sale.total_amount - items_total) > AMOUNT_TOLERANCE)

    return [Finding(
        code="SALE_TOTAL_MISMATCH", issue_type="Sale total doesn't match its line items",
        severity=IssueSeverity.ERROR, module=M_SALES, record_type="Sale", record_id=r.id,
        record_label=f"Invoice {r.invoice_number}",
        description=(f"Invoice {r.invoice_number} total is {_money(r.total_amount)} but its "
                     f"{r.item_count} line item(s) add up to {_money(r.items_total)}."),
        details={"invoice_number": r.invoice_number, "sale_total": _money(r.total_amount),
                 "line_items_total": _money(r.items_total),
                 "difference": _money(Decimal(r.total_amount) - Decimal(r.items_total))},
    ) for r in q.all()]


def check_report_totals(db, company_id, scope):
    """Company-wide: the revenue the Sales report/Analytics show (sum of sale
    totals) must equal what the Product Performance report shows (sum of line
    items). Full runs only — it needs every sale."""
    if scope is not None:
        return []
    sales_total = db.query(func.coalesce(func.sum(Sale.total_amount), 0)).filter(Sale.company_id == company_id).scalar()
    items_total = db.query(func.coalesce(func.sum(SaleItem.total), 0)).join(
        Sale, Sale.id == SaleItem.sale_id
    ).filter(Sale.company_id == company_id).scalar()
    diff = Decimal(sales_total) - Decimal(items_total)
    if abs(diff) <= AMOUNT_TOLERANCE:
        return []
    return [Finding(
        code="REPORT_TOTAL_MISMATCH", issue_type="Report totals don't match transaction data",
        severity=IssueSeverity.ERROR, module=M_REPORTS, record_type="Report",
        record_id="sales-vs-product-revenue", record_label="Sales report vs Product Performance report",
        description=(f"Total revenue is {_money(sales_total)} in the Sales report/Analytics but "
                     f"{_money(items_total)} in the Product Performance report (difference {_money(diff)})."),
        details={"sales_report_total": _money(sales_total), "product_performance_total": _money(items_total),
                 "difference": _money(diff)},
    )]


def _product_rows(db, company_id, scope):
    q = db.query(
        Product.id, Product.name, Product.sku, Product.unit_price, Product.cost_price,
        Product.unit_of_measure, Product.created_at,
    ).filter(Product.company_id == company_id)
    return q.all()


def check_sku_duplicates(db, company_id, scope):
    groups = defaultdict(list)
    for r in _product_rows(db, company_id, scope):
        norm = (r.sku or "").strip().lower()
        if norm:
            groups[norm].append(r)

    findings = []
    for norm, members in groups.items():
        if len(members) < 2:
            continue
        if scope and scope.product_ids is not None and not any(m.id in scope.product_ids for m in members):
            continue
        members.sort(key=lambda m: m.created_at)
        first = members[0]
        names = ", ".join(f"'{m.name}'" for m in members[:5])
        findings.append(Finding(
            code="PRODUCT_SKU_DUPLICATE", issue_type="Duplicate product SKU",
            severity=IssueSeverity.ERROR, module=M_PRODUCTS, record_type="Product", record_id=first.id,
            record_label=f"{first.name} ({first.sku})",
            description=f"SKU '{first.sku}' is used by {len(members)} products: {names}.",
            details={"product_id": first.id, "product_ids": [m.id for m in members], "sku": first.sku,
                     "products": [{"id": m.id, "name": m.name, "sku": m.sku} for m in members]},
            key=norm,
        ))
    return findings


def check_sku_format(db, company_id, scope):
    findings = []
    for r in _product_rows(db, company_id, scope):
        if scope and scope.product_ids is not None and r.id not in scope.product_ids:
            continue
        raw = r.sku or ""
        if not raw.strip():
            continue  # a blank SKU is reported by PRODUCT_MISSING_INFO
        if raw != raw.strip() or not _SKU_RE.match(raw.strip()):
            findings.append(Finding(
                code="PRODUCT_SKU_INVALID", issue_type="Invalid product SKU format",
                severity=IssueSeverity.WARNING, module=M_PRODUCTS, record_type="Product", record_id=r.id,
                record_label=f"{r.name} ({raw.strip()})",
                description=(f"SKU '{raw}' has leading/trailing spaces or characters other than letters, "
                             f"numbers, '.', '_', '-' and '/'."),
                details={"product_id": r.id, "sku": raw},
            ))
    return findings


def check_product_missing_info(db, company_id, scope):
    findings = []
    for r in _product_rows(db, company_id, scope):
        if scope and scope.product_ids is not None and r.id not in scope.product_ids:
            continue
        missing = []
        if not (r.name or "").strip():
            missing.append("name")
        if not (r.sku or "").strip():
            missing.append("SKU")
        if r.unit_price is None or r.unit_price <= 0:
            missing.append("selling price")
        if r.cost_price is None or r.cost_price < 0:
            missing.append("cost price")
        if not (r.unit_of_measure or "").strip():
            missing.append("unit of measure")
        if missing:
            findings.append(Finding(
                code="PRODUCT_MISSING_INFO", issue_type="Product is missing mandatory information",
                severity=IssueSeverity.ERROR, module=M_PRODUCTS, record_type="Product", record_id=r.id,
                record_label=f"{r.name or '(no name)'} ({r.sku or 'no SKU'})",
                description=f"Product is missing or has an invalid: {', '.join(missing)}.",
                details={"product_id": r.id, "missing_fields": missing},
            ))
    return findings


def check_customer_info(db, company_id, scope):
    q = db.query(Customer).filter(Customer.company_id == company_id, Customer.is_deleted.is_(False))
    if scope and scope.customer_ids is not None:
        q = q.filter(Customer.id.in_(scope.customer_ids))

    findings = []
    for c in q.all():
        label = f"{c.full_name or '(no name)'} ({c.customer_code})"
        missing = [name for name, value in (("name", c.full_name), ("email", c.email), ("phone", c.phone))
                   if not (value or "").strip()]
        if missing:
            findings.append(Finding(
                code="CUSTOMER_MISSING_INFO", issue_type="Customer is missing mandatory information",
                severity=IssueSeverity.ERROR, module=M_CUSTOMERS, record_type="Customer", record_id=c.id,
                record_label=label, description=f"Customer is missing: {', '.join(missing)}.",
                details={"customer_id": c.id, "missing_fields": missing}, key="missing",
            ))
        if (c.email or "").strip() and not _EMAIL_RE.match(c.email.strip()):
            findings.append(Finding(
                code="CUSTOMER_MISSING_INFO", issue_type="Customer has an invalid email address",
                severity=IssueSeverity.WARNING, module=M_CUSTOMERS, record_type="Customer", record_id=c.id,
                record_label=label, description=f"Email '{c.email}' doesn't look like a valid address.",
                details={"customer_id": c.id, "email": c.email}, key="email_format",
            ))
    return findings


@dataclass(frozen=True)
class CheckDef:
    code: str
    label: str
    module: str
    fn: Callable
    scope_by: str  # product | sale | sale_or_customer | customer | none


CHECKS = [
    CheckDef("SALE_EXCEEDS_STOCK", "Sale quantity vs stock at time of sale", M_SALES, check_sale_exceeds_stock, "product"),
    CheckDef("INVENTORY_MOVEMENT_MISMATCH", "Inventory vs stock movements", M_INVENTORY, check_inventory_vs_movements, "product"),
    CheckDef("PRODUCT_STOCK_DRIFT", "Product stock vs inventory stock", M_INVENTORY, check_product_stock_drift, "product"),
    CheckDef("MOVEMENT_ARITHMETIC", "Stock movement arithmetic", M_MOVEMENTS, check_movement_arithmetic, "product"),
    CheckDef("MOVEMENT_CHAIN_BREAK", "Stock movement continuity", M_MOVEMENTS, check_movement_chain, "product"),
    CheckDef("SALE_INVALID_PRODUCT", "Sales referencing missing/foreign products", M_SALES, check_sale_invalid_product, "sale"),
    CheckDef("SALE_INACTIVE_PRODUCT", "Sales of inactive products", M_SALES, check_sale_inactive_product, "sale"),
    CheckDef("SALE_INVALID_CUSTOMER", "Sales referencing invalid customers", M_SALES, check_sale_invalid_customer, "sale_or_customer"),
    CheckDef("SALE_TOTAL_MISMATCH", "Sale totals vs line items", M_SALES, check_sale_totals, "sale"),
    CheckDef("REPORT_TOTAL_MISMATCH", "Report totals vs transactions", M_REPORTS, check_report_totals, "none"),
    CheckDef("PRODUCT_SKU_DUPLICATE", "Duplicate SKUs", M_PRODUCTS, check_sku_duplicates, "product"),
    CheckDef("PRODUCT_SKU_INVALID", "SKU format", M_PRODUCTS, check_sku_format, "product"),
    CheckDef("PRODUCT_MISSING_INFO", "Product mandatory fields", M_PRODUCTS, check_product_missing_info, "product"),
    CheckDef("CUSTOMER_MISSING_INFO", "Customer mandatory fields", M_CUSTOMERS, check_customer_info, "customer"),
]
CHECKS_BY_CODE = {c.code: c for c in CHECKS}
ALL_CHECK_CODES = [c.code for c in CHECKS]

# Check sets used by the automatic hooks and the post-import run.
SALE_WORKFLOW_CHECKS = [
    "SALE_EXCEEDS_STOCK", "SALE_TOTAL_MISMATCH", "SALE_INVALID_PRODUCT", "SALE_INACTIVE_PRODUCT",
    "SALE_INVALID_CUSTOMER", "INVENTORY_MOVEMENT_MISMATCH", "PRODUCT_STOCK_DRIFT",
    "MOVEMENT_ARITHMETIC", "MOVEMENT_CHAIN_BREAK",
]
STOCK_WORKFLOW_CHECKS = ["INVENTORY_MOVEMENT_MISMATCH", "PRODUCT_STOCK_DRIFT", "MOVEMENT_ARITHMETIC", "MOVEMENT_CHAIN_BREAK"]
PRODUCT_WORKFLOW_CHECKS = ["PRODUCT_SKU_DUPLICATE", "PRODUCT_SKU_INVALID", "PRODUCT_MISSING_INFO"]
CUSTOMER_WORKFLOW_CHECKS = ["CUSTOMER_MISSING_INFO", "SALE_INVALID_CUSTOMER"]
IMPORT_CHECKS = {
    "PRODUCTS": PRODUCT_WORKFLOW_CHECKS + ["PRODUCT_STOCK_DRIFT", "INVENTORY_MOVEMENT_MISMATCH"],
    "INVENTORY": STOCK_WORKFLOW_CHECKS,
    "CUSTOMERS": ["CUSTOMER_MISSING_INFO", "SALE_INVALID_CUSTOMER"],
    "SALES": SALE_WORKFLOW_CHECKS + ["REPORT_TOTAL_MISMATCH"],
}


# =====================================================================
# Persisting findings as issues (no duplicates)
# =====================================================================

def _in_scope(issue: DataQualityIssue, scope: Optional[Scope], scope_by: str) -> bool:
    """Was this issue's record actually re-checked by a scoped run?"""
    if scope is None:
        return True
    d = json.loads(issue.details_json or "{}")
    if scope_by == "product":
        if scope.product_ids is None:
            return False
        ids = set(d.get("product_ids", [])) | ({d["product_id"]} if d.get("product_id") else set())
        return bool(ids & scope.product_ids)
    if scope_by == "sale":
        return scope.sale_ids is not None and issue.record_id in scope.sale_ids
    if scope_by == "customer":
        return scope.customer_ids is not None and issue.record_id in scope.customer_ids
    if scope_by == "sale_or_customer":
        if scope.sale_ids is not None:
            return issue.record_id in scope.sale_ids
        return scope.customer_ids is not None and d.get("customer_id") in scope.customer_ids
    return False


def _add_event(db: Session, issue: DataQualityIssue, prev: Optional[IssueStatus], new: IssueStatus,
               note: str, user_id: Optional[str] = None) -> None:
    db.add(DataQualityIssueEvent(
        issue_id=issue.id, company_id=issue.company_id, user_id=user_id,
        previous_status=prev, new_status=new, note=note,
    ))


def _persist(db: Session, company_id: str, run: Optional[ReconciliationRun], findings: list,
             executed_codes: list, succeeded_codes: set, scope: Optional[Scope]) -> dict:
    now = datetime.utcnow()
    existing = {
        i.fingerprint: i for i in db.query(DataQualityIssue).filter(
            DataQualityIssue.company_id == company_id, DataQualityIssue.issue_code.in_(executed_codes)
        ).all()
    }
    found, new_count = set(), 0

    for f in findings:
        found.add(f.fingerprint)
        issue = existing.get(f.fingerprint)

        if issue is None:
            issue = DataQualityIssue(
                company_id=company_id, issue_code=f.code, issue_type=f.issue_type, severity=f.severity,
                module=f.module, record_type=f.record_type, record_id=f.record_id, record_label=f.record_label,
                description=f.description[:1000], details_json=json.dumps(f.details, default=str),
                fingerprint=f.fingerprint, status=IssueStatus.OPEN, detected_at=now, last_detected_at=now,
                run_id=run.id if run else None,
            )
            try:
                with db.begin_nested():
                    db.add(issue)
                    db.flush()
                    _add_event(db, issue, None, IssueStatus.OPEN, "Detected by reconciliation.")
                    db.flush()
            except IntegrityError:
                # Another run inserted the same problem at the same moment.
                issue = db.query(DataQualityIssue).filter(
                    DataQualityIssue.company_id == company_id, DataQualityIssue.fingerprint == f.fingerprint
                ).first()
                if issue is None:
                    continue
                existing[f.fingerprint] = issue
            else:
                existing[f.fingerprint] = issue
                new_count += 1
                continue

        issue.last_detected_at = now
        issue.severity = f.severity
        issue.description = f.description[:1000]
        issue.details_json = json.dumps(f.details, default=str)
        issue.record_label = f.record_label
        if issue.status == IssueStatus.RESOLVED:
            _add_event(db, issue, IssueStatus.RESOLVED, IssueStatus.OPEN,
                       "Reopened: the inconsistency was detected again.")
            issue.status = IssueStatus.OPEN
            issue.resolved_by, issue.resolved_at, issue.resolution_note = None, None, None
            new_count += 1
        # OPEN / INVESTIGATING stay as they are; IGNORED stays ignored.

    resolved_count = 0
    for fingerprint, issue in existing.items():
        if issue.status not in UNRESOLVED or fingerprint in found:
            continue
        check = CHECKS_BY_CODE.get(issue.issue_code)
        if not check or issue.issue_code not in succeeded_codes or not _in_scope(issue, scope, check.scope_by):
            continue
        _add_event(db, issue, issue.status, IssueStatus.RESOLVED,
                   "Automatically resolved: the inconsistency is no longer detected.")
        issue.status = IssueStatus.RESOLVED
        issue.resolved_by, issue.resolved_at = None, now
        issue.resolution_note = "Automatically resolved: the inconsistency is no longer detected."
        resolved_count += 1

    db.flush()
    return {"issues_detected": len(found), "new_issues": new_count, "issues_resolved": resolved_count}


def run_reconciliation(db: Session, company_id: str, run: Optional[ReconciliationRun] = None,
                       scope: Optional[Scope] = None, check_codes: Optional[list] = None) -> dict:
    """Runs the selected checks and persists the outcome. One failing check
    never stops the others — it's recorded and its issues are left alone
    (not auto-resolved) because we couldn't verify them."""
    codes = check_codes or ALL_CHECK_CODES
    findings, results, succeeded = [], [], set()

    for code in codes:
        check = CHECKS_BY_CODE[code]
        try:
            with db.begin_nested():  # a failed query must not poison the whole transaction
                found = check.fn(db, company_id, scope)
            findings.extend(found)
            succeeded.add(code)
            results.append({"code": code, "label": check.label, "status": "OK", "found": len(found)})
        except Exception as exc:  # noqa: BLE001
            logger.exception("Data quality check %s failed", code)
            results.append({"code": code, "label": check.label, "status": "FAILED", "error": str(exc)[:300]})

    counts = _persist(db, company_id, run, findings, codes, succeeded, scope)
    counts["results"] = results
    counts["failed_checks"] = sum(1 for r in results if r["status"] == "FAILED")
    counts["total_checks"] = len(codes)
    return counts


def count_records(db: Session, company_id: str) -> int:
    products = db.query(func.count(Product.id)).filter(Product.company_id == company_id).scalar() or 0
    customers = db.query(func.count(Customer.id)).filter(
        Customer.company_id == company_id, Customer.is_deleted.is_(False)).scalar() or 0
    sales = db.query(func.count(Sale.id)).filter(Sale.company_id == company_id).scalar() or 0
    inventory = db.query(func.count(Inventory.id)).filter(Inventory.company_id == company_id).scalar() or 0
    movements = db.query(func.count(InventoryMovement.id)).join(
        Inventory, Inventory.id == InventoryMovement.inventory_id).filter(Inventory.company_id == company_id).scalar() or 0
    return products + customers + sales + inventory + movements


# =====================================================================
# Run lifecycle (manual / post-import) — runs in a background thread
# =====================================================================

def start_run(db: Session, company_id: str, user_id: Optional[str], trigger_type: str,
              check_codes: Optional[list] = None) -> tuple[ReconciliationRun, bool]:
    """Creates a RUNNING run, unless one is already in progress for this
    company (returns that one instead). Runs stuck RUNNING for too long
    (e.g. the server restarted mid-run) are marked FAILED so they can't
    block new runs forever."""
    cutoff = datetime.utcnow() - timedelta(minutes=STALE_RUN_MINUTES)
    for stale in db.query(ReconciliationRun).filter(
        ReconciliationRun.company_id == company_id, ReconciliationRun.status == RunStatus.RUNNING,
        ReconciliationRun.started_at < cutoff,
    ).all():
        stale.status = RunStatus.FAILED
        stale.completed_at = datetime.utcnow()
        stale.error_message = "Timed out: this run never reported completion (the server may have restarted)."

    active = db.query(ReconciliationRun).filter(
        ReconciliationRun.company_id == company_id, ReconciliationRun.status == RunStatus.RUNNING
    ).first()
    if active:
        db.commit()
        return active, False

    run = ReconciliationRun(
        company_id=company_id, triggered_by=user_id, trigger_type=trigger_type,
        status=RunStatus.RUNNING, started_at=datetime.utcnow(),
        check_codes_json=json.dumps(check_codes or ALL_CHECK_CODES),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run, True


def execute_run_in_background(run_id: str) -> None:
    """Entry point for FastAPI BackgroundTasks. Uses its OWN database session
    because the request that started it has already finished."""
    db = SessionLocal()
    try:
        run = db.query(ReconciliationRun).filter(ReconciliationRun.id == run_id).first()
        if run is None or run.status != RunStatus.RUNNING:
            return
        company_id, user_id = run.company_id, run.triggered_by
        codes = json.loads(run.check_codes_json) if run.check_codes_json else ALL_CHECK_CODES

        try:
            result = run_reconciliation(db, company_id, run=run, scope=None, check_codes=codes)
            run.records_checked = count_records(db, company_id)
            run.issues_detected = result["issues_detected"]
            run.new_issues = result["new_issues"]
            run.issues_resolved = result["issues_resolved"]
            run.failed_checks = result["failed_checks"]
            run.check_results_json = json.dumps(result["results"])
            run.completed_at = datetime.utcnow()

            if result["failed_checks"] == result["total_checks"]:
                run.status = RunStatus.FAILED
                run.error_message = "Every check failed to run. See the per-check results."
            elif result["issues_detected"] > 0 or result["failed_checks"] > 0:
                run.status = RunStatus.COMPLETED_WITH_ISSUES
            else:
                run.status = RunStatus.COMPLETED

            _notify_and_audit(db, run)
            db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Reconciliation run %s failed", run_id)
            db.rollback()
            run = db.query(ReconciliationRun).filter(ReconciliationRun.id == run_id).first()
            if run:
                run.status = RunStatus.FAILED
                run.error_message = str(exc)[:1000]
                run.completed_at = datetime.utcnow()
                db.add(AuditLog(
                    company_id=run.company_id, user_id=run.triggered_by, action="Reconciliation Failed",
                    resource_type="ReconciliationRun", resource_id=run.id, entity_name="Data Quality",
                    details=str(exc)[:480], status=AuditStatus.FAILED, browser="background task",
                ))
                db.commit()
    finally:
        db.close()


def _notify_and_audit(db: Session, run: ReconciliationRun) -> None:
    from app.services.notifications import notify_roles, resolve_condition_notifications

    open_errors = db.query(func.count(DataQualityIssue.id)).filter(
        DataQualityIssue.company_id == run.company_id, DataQualityIssue.status.in_(UNRESOLVED),
        DataQualityIssue.severity == IssueSeverity.ERROR,
    ).scalar() or 0
    key = "data_quality:open_errors"
    if open_errors:
        notify_roles(
            db, run.company_id, NotificationType.SYSTEM_ALERT, title="Data quality errors found",
            message=f"{open_errors} unresolved data-quality error(s). Open Data Quality to review them.",
            priority=NotificationPriority.HIGH, resource_type="DataQualityRun", resource_id=run.id,
            dedupe_key=key,
        )
    else:
        resolve_condition_notifications(db, run.company_id, key)

    db.add(AuditLog(
        company_id=run.company_id, user_id=run.triggered_by, action="Reconciliation Completed",
        resource_type="ReconciliationRun", resource_id=run.id, entity_name="Data Quality",
        details=(f"{run.status.value}: {run.records_checked} records checked, {run.issues_detected} issue(s) "
                 f"present, {run.new_issues} new, {run.issues_resolved} auto-resolved, "
                 f"{run.failed_checks} failed check(s)")[:500],
        status=AuditStatus.FAILED if run.status == RunStatus.FAILED else AuditStatus.SUCCESS,
        browser="background task",
    ))


# =====================================================================
# Cheap automatic checks wired into existing workflows
# =====================================================================

def auto_check(db: Session, company_id: str, check_codes: list, *, product_ids=None, sale_ids=None,
               customer_ids=None) -> None:
    """Re-checks ONLY the records a workflow just touched. Never raises:
    a data-quality problem must never break the sale/adjustment/edit the
    user just completed (that work is already committed)."""
    try:
        scope = Scope(
            product_ids=set(product_ids) if product_ids is not None else None,
            sale_ids=set(sale_ids) if sale_ids is not None else None,
            customer_ids=set(customer_ids) if customer_ids is not None else None,
        )
        run_reconciliation(db, company_id, run=None, scope=scope, check_codes=check_codes)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("Automatic data quality check failed (ignored)")


# =====================================================================
# Reading: summary, listing, details
# =====================================================================

def get_summary(db: Session, company_id: str) -> dict:
    unresolved = db.query(
        DataQualityIssue.record_type, DataQualityIssue.record_id, DataQualityIssue.severity, DataQualityIssue.module,
    ).filter(DataQualityIssue.company_id == company_id, DataQualityIssue.status.in_(UNRESOLVED)).all()

    error_records = {(r.record_type, r.record_id) for r in unresolved if r.severity == IssueSeverity.ERROR}
    warning_records = {(r.record_type, r.record_id) for r in unresolved if r.severity == IssueSeverity.WARNING} - error_records

    by_module = defaultdict(lambda: {"errors": 0, "warnings": 0})
    for r in unresolved:
        by_module[r.module]["errors" if r.severity == IssueSeverity.ERROR else "warnings"] += 1

    status_counts = {s.value: 0 for s in IssueStatus}
    for status, count in db.query(DataQualityIssue.status, func.count(DataQualityIssue.id)).filter(
        DataQualityIssue.company_id == company_id).group_by(DataQualityIssue.status).all():
        status_counts[status.value] = count

    last_run = db.query(ReconciliationRun).filter(
        ReconciliationRun.company_id == company_id, ReconciliationRun.status != RunStatus.RUNNING,
    ).order_by(ReconciliationRun.started_at.desc()).first()
    running = db.query(ReconciliationRun).filter(
        ReconciliationRun.company_id == company_id, ReconciliationRun.status == RunStatus.RUNNING
    ).order_by(ReconciliationRun.started_at.desc()).first()

    checked = last_run.records_checked if last_run and last_run.status != RunStatus.FAILED else 0
    return {
        "records_checked": checked,
        "valid_records": max(0, checked - len(error_records) - len(warning_records)),
        "records_with_warnings": len(warning_records),
        "records_with_errors": len(error_records),
        "unresolved_issues": len(unresolved),
        "last_reconciliation_at": last_run.completed_at if last_run else None,
        "last_run": last_run,
        "running_run": running,
        "status_counts": status_counts,
        "by_module": [{"module": m, **v} for m, v in sorted(by_module.items())],
    }


def build_issue_context(db: Session, company_id: str, issue: DataQualityIssue) -> dict:
    """Live supporting data for the issue-details view. Every query is
    filtered by company_id, so this can never leak another company's records."""
    details = json.loads(issue.details_json or "{}")
    ctx: dict = {"details": details, "record": {}, "movements": [], "related_sales": [], "line_items": []}

    product_id = details.get("product_id")
    if product_id:
        product = db.query(Product).filter(Product.id == product_id, Product.company_id == company_id).first()
        if product:
            inv = db.query(Inventory).filter(Inventory.product_id == product.id, Inventory.company_id == company_id).first()
            ctx["record"] = {
                "Product": product.name, "SKU": product.sku, "Status": product.status.value,
                "Product record stock": product.stock_quantity,
                "Inventory stock": inv.current_stock if inv else "no inventory row",
            }
            if inv:
                for mv in db.query(InventoryMovement).filter(InventoryMovement.inventory_id == inv.id).order_by(
                        InventoryMovement.created_at.desc()).limit(15).all():
                    ctx["movements"].append({
                        "id": mv.id, "created_at": mv.created_at, "movement_type": mv.movement_type.value,
                        "quantity_changed": mv.quantity_changed, "previous_quantity": mv.previous_quantity,
                        "updated_quantity": mv.updated_quantity, "reason": mv.reason,
                    })
            for item, sale in db.query(SaleItem, Sale).join(Sale, Sale.id == SaleItem.sale_id).filter(
                    SaleItem.product_id == product.id, Sale.company_id == company_id
            ).order_by(Sale.sale_date.desc()).limit(10).all():
                ctx["related_sales"].append({
                    "sale_id": sale.id, "invoice_number": sale.invoice_number, "sale_date": sale.sale_date,
                    "quantity": item.quantity, "total": _money(item.total),
                })

    if issue.record_type == "Sale":
        sale = db.query(Sale).filter(Sale.id == issue.record_id, Sale.company_id == company_id).first()
        if sale:
            ctx["record"].update({
                "Invoice": sale.invoice_number, "Customer": sale.customer_name, "Sale date": sale.sale_date.isoformat(),
                "Stored total": _money(sale.total_amount),
            })
            for item, p in db.query(SaleItem, Product).outerjoin(Product, Product.id == SaleItem.product_id).filter(
                    SaleItem.sale_id == sale.id).all():
                ctx["line_items"].append({
                    "product": p.name if p else "(missing product)", "quantity": item.quantity,
                    "unit_price": _money(item.unit_price), "total": _money(item.total),
                })
    elif issue.record_type == "Customer":
        cust = db.query(Customer).filter(Customer.id == issue.record_id, Customer.company_id == company_id).first()
        if cust:
            ctx["record"] = {"Customer": cust.full_name, "Code": cust.customer_code, "Email": cust.email or "—",
                             "Phone": cust.phone or "—"}
            for s in db.query(Sale).filter(Sale.customer_id == cust.id, Sale.company_id == company_id).order_by(
                    Sale.sale_date.desc()).limit(10).all():
                ctx["related_sales"].append({
                    "sale_id": s.id, "invoice_number": s.invoice_number, "sale_date": s.sale_date,
                    "quantity": None, "total": _money(s.total_amount),
                })
    elif issue.record_type == "Report":
        ctx["record"] = {k.replace("_", " ").title(): v for k, v in details.items()}
    return ctx
