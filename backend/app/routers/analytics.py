import csv
import io
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, case
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_company_id, require_roles
from app.audit import log_action
from app.models import Sale, SaleItem, Product, Category, User, UserRole
from app.models.inventory import Inventory, StockStatus
from app.schemas.analytics import AnalyticsSummary, AnalyticsAuditEvent

router = APIRouter(prefix="/analytics", tags=["analytics"])

ANALYTICS_ROLES = [UserRole.COMPANY_ADMIN, UserRole.SUPER_ADMIN, UserRole.ANALYST]


def _sales_base_query(
    db: Session, company_id: str,
    date_from: Optional[date], date_to: Optional[date],
    category_id: Optional[str], product_id: Optional[str], brand: Optional[str],
    sales_channel: Optional[str], payment_method: Optional[str],
):
    """
    Sale + SaleItem joined query with every dashboard filter applied.
    Returns a query already joined on SaleItem/Product so callers can
    group/aggregate on whichever columns they need.
    """
    query = (
        db.query(Sale, SaleItem, Product)
        .join(SaleItem, SaleItem.sale_id == Sale.id)
        .join(Product, Product.id == SaleItem.product_id)
        .filter(Sale.company_id == company_id)
    )
    if date_from:
        query = query.filter(Sale.sale_date >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        query = query.filter(Sale.sale_date <= datetime.combine(date_to, datetime.max.time()))
    if category_id:
        query = query.filter(SaleItem.category_id == category_id)
    if product_id:
        query = query.filter(SaleItem.product_id == product_id)
    if brand:
        query = query.filter(Product.brand == brand)
    if sales_channel:
        query = query.filter(Sale.sales_channel == sales_channel)
    if payment_method:
        query = query.filter(Sale.payment_method == payment_method)
    return query


def _inventory_base_query(
    db: Session, company_id: str,
    category_id: Optional[str], product_id: Optional[str], brand: Optional[str],
):
    """Inventory + Product joined query. Date/channel/payment filters don't
    apply to inventory (it has no such dimensions) — only product/category/brand do."""
    query = (
        db.query(Inventory, Product)
        .join(Product, Product.id == Inventory.product_id)
        .filter(Inventory.company_id == company_id)
    )
    if category_id:
        query = query.filter(Product.category_id == category_id)
    if product_id:
        query = query.filter(Product.id == product_id)
    if brand:
        query = query.filter(Product.brand == brand)
    return query


def _build_summary(
    db: Session, company_id: str,
    date_from: Optional[date], date_to: Optional[date],
    category_id: Optional[str], product_id: Optional[str], brand: Optional[str],
    sales_channel: Optional[str], payment_method: Optional[str],
    granularity: str,
) -> AnalyticsSummary:
    sales_rows = _sales_base_query(
        db, company_id, date_from, date_to, category_id, product_id, brand,
        sales_channel, payment_method,
    ).all()

    # De-duplicate sales for order-level aggregates (a sale can appear once
    # per matching line item after the join).
    unique_sales = {}
    for sale, _item, _product in sales_rows:
        unique_sales[sale.id] = sale

    total_revenue = sum((s.total_amount for s in unique_sales.values()), Decimal("0"))
    total_orders = len(unique_sales)
    total_products_sold = sum((item.quantity for _s, item, _p in sales_rows), 0)
    average_order_value = (total_revenue / total_orders) if total_orders else Decimal("0")

    inv_rows = _inventory_base_query(db, company_id, category_id, product_id, brand).all()
    total_inventory_value = sum(
        (inv.current_stock * product.unit_price for inv, product in inv_rows), Decimal("0")
    )
    low_stock_products = sum(1 for inv, _p in inv_rows if inv.stock_status == StockStatus.LOW_STOCK)
    out_of_stock_products = sum(1 for inv, _p in inv_rows if inv.stock_status == StockStatus.OUT_OF_STOCK)
    total_categories = db.query(Category).filter(Category.company_id == company_id).count()

    # --- Revenue trend ---
    trunc_unit = {"daily": "day", "weekly": "week", "monthly": "month"}.get(granularity, "day")
    trend_map: dict[str, dict] = {}
    for sale in unique_sales.values():
        bucket = sale.sale_date
        if trunc_unit == "day":
            key = bucket.strftime("%Y-%m-%d")
        elif trunc_unit == "week":
            key = f"{bucket.isocalendar()[0]}-W{bucket.isocalendar()[1]:02d}"
        else:
            key = bucket.strftime("%Y-%m")
        entry = trend_map.setdefault(key, {"revenue": Decimal("0"), "orders": 0})
        entry["revenue"] += sale.total_amount
        entry["orders"] += 1
    revenue_trend = [
        {"period": k, "revenue": v["revenue"], "orders": v["orders"]}
        for k, v in sorted(trend_map.items())
    ]

    # --- Top products ---
    product_map: dict[str, dict] = {}
    for _s, item, product in sales_rows:
        entry = product_map.setdefault(product.id, {
            "product_id": product.id, "product_name": product.name, "sku": product.sku,
            "quantity_sold": 0, "revenue": Decimal("0"),
        })
        entry["quantity_sold"] += item.quantity
        entry["revenue"] += item.total
    top_products = sorted(product_map.values(), key=lambda r: r["revenue"], reverse=True)[:10]

    # --- Top categories ---
    category_map: dict[str, dict] = {}
    for _s, item, _product in sales_rows:
        cat = db.query(Category).filter(Category.id == item.category_id).first()
        cat_name = cat.name if cat else "Uncategorized"
        entry = category_map.setdefault(item.category_id, {
            "category_id": item.category_id, "category_name": cat_name,
            "revenue": Decimal("0"), "quantity_sold": 0,
        })
        entry["revenue"] += item.total
        entry["quantity_sold"] += item.quantity
    top_categories = sorted(category_map.values(), key=lambda r: r["revenue"], reverse=True)[:10]

    # --- Payment method / channel breakdowns ---
    payment_map: dict[str, dict] = {}
    channel_map: dict[str, dict] = {}
    for sale in unique_sales.values():
        pm = sale.payment_method.value
        pentry = payment_map.setdefault(pm, {"payment_method": pm, "revenue": Decimal("0"), "orders": 0})
        pentry["revenue"] += sale.total_amount
        pentry["orders"] += 1

        ch = sale.sales_channel.value
        centry = channel_map.setdefault(ch, {"sales_channel": ch, "revenue": Decimal("0"), "orders": 0})
        centry["revenue"] += sale.total_amount
        centry["orders"] += 1

    # --- Inventory breakdowns ---
    inv_cat_map: dict[str, dict] = {}
    for inv, product in inv_rows:
        cat = db.query(Category).filter(Category.id == product.category_id).first()
        cat_name = cat.name if cat else "Uncategorized"
        entry = inv_cat_map.setdefault(product.category_id, {
            "category_id": product.category_id, "category_name": cat_name,
            "quantity": 0, "value": Decimal("0"),
        })
        entry["quantity"] += inv.current_stock
        entry["value"] += inv.current_stock * product.unit_price

    status_map: dict[str, int] = {}
    for inv, _p in inv_rows:
        status_map[inv.stock_status.value] = status_map.get(inv.stock_status.value, 0) + 1

    top_low_stock = sorted(
        [
            {
                "product_id": p.id, "product_name": p.name, "sku": p.sku,
                "available_stock": inv.available_stock, "reorder_level": inv.reorder_level,
            }
            for inv, p in inv_rows if inv.stock_status == StockStatus.LOW_STOCK
        ],
        key=lambda r: r["available_stock"],
    )[:10]

    out_of_stock = [
        {"product_id": p.id, "product_name": p.name, "sku": p.sku, "updated_at": inv.updated_at}
        for inv, p in inv_rows if inv.stock_status == StockStatus.OUT_OF_STOCK
    ]

    return AnalyticsSummary(
        kpis={
            "total_revenue": total_revenue,
            "total_orders": total_orders,
            "total_products_sold": total_products_sold,
            "average_order_value": average_order_value,
            "total_inventory_value": total_inventory_value,
            "low_stock_products": low_stock_products,
            "out_of_stock_products": out_of_stock_products,
            "total_categories": total_categories,
        },
        revenue_trend=revenue_trend,
        top_products=top_products,
        top_categories=top_categories,
        by_payment_method=list(payment_map.values()),
        by_sales_channel=list(channel_map.values()),
        inventory_by_category=list(inv_cat_map.values()),
        inventory_status_summary=[{"status": k, "count": v} for k, v in status_map.items()],
        top_low_stock=top_low_stock,
        out_of_stock=out_of_stock,
        inventory_value_by_category=list(inv_cat_map.values()),
    )


@router.get("/summary", response_model=AnalyticsSummary)
def analytics_summary(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    category_id: Optional[str] = None,
    product_id: Optional[str] = None,
    brand: Optional[str] = None,
    sales_channel: Optional[str] = None,
    payment_method: Optional[str] = None,
    granularity: str = Query("daily", pattern="^(daily|weekly|monthly)$"),
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    current_user: User = Depends(require_roles(ANALYTICS_ROLES)),
):
    return _build_summary(
        db, company_id, date_from, date_to, category_id, product_id, brand,
        sales_channel, payment_method, granularity,
    )


@router.post("/audit", status_code=status.HTTP_204_NO_CONTENT)
def analytics_audit_event(
    payload: AnalyticsAuditEvent,
    request: Request,
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    current_user: User = Depends(require_roles(ANALYTICS_ROLES)),
):
    allowed_actions = {"Dashboard Viewed", "Dashboard Filters Applied"}
    if payload.action not in allowed_actions:
        raise HTTPException(status_code=400, detail="Unsupported audit action")
    log_action(
        db, request, payload.action,
        company_id=company_id, user_id=current_user.id,
        details=payload.details,
    )


@router.get("/export")
def export_analytics(
    format: str = Query(..., pattern="^(csv|pdf)$"),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    category_id: Optional[str] = None,
    product_id: Optional[str] = None,
    brand: Optional[str] = None,
    sales_channel: Optional[str] = None,
    payment_method: Optional[str] = None,
    request: Request = None,
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    current_user: User = Depends(require_roles(ANALYTICS_ROLES)),
):
    summary = _build_summary(
        db, company_id, date_from, date_to, category_id, product_id, brand,
        sales_channel, payment_method, "daily",
    )

    log_action(
        db, request, "Report Exported",
        company_id=company_id, user_id=current_user.id,
        details=f"format={format}",
    )

    if format == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["KPI", "Value"])
        for key, value in summary.kpis.model_dump().items():
            writer.writerow([key, value])
        writer.writerow([])
        writer.writerow(["Top Products", "SKU", "Qty Sold", "Revenue"])
        for row in summary.top_products:
            writer.writerow([row.product_name, row.sku, row.quantity_sold, row.revenue])
        writer.writerow([])
        writer.writerow(["Revenue Trend (Period)", "Revenue", "Orders"])
        for row in summary.revenue_trend:
            writer.writerow([row.period, row.revenue, row.orders])
        buffer.seek(0)
        return StreamingResponse(
            iter([buffer.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=analytics_report.csv"},
        )

    # PDF export
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Spacer, Paragraph
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import cm
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="PDF export requires the 'reportlab' package. Run: pip install reportlab",
        )

    pdf_buffer = io.BytesIO()
    doc = SimpleDocTemplate(pdf_buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = [Paragraph("RetailPulse Analytics Report", styles["Title"]), Spacer(1, 0.5 * cm)]

    kpi_data = [["KPI", "Value"]] + [[k.replace("_", " ").title(), str(v)] for k, v in summary.kpis.model_dump().items()]
    kpi_table = Table(kpi_data, colWidths=[8 * cm, 6 * cm])
    kpi_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2563eb")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    elements += [kpi_table, Spacer(1, 0.8 * cm), Paragraph("Top Products", styles["Heading2"])]

    top_data = [["Product", "SKU", "Qty Sold", "Revenue"]] + [
        [row.product_name, row.sku, str(row.quantity_sold), str(row.revenue)]
        for row in summary.top_products
    ]
    top_table = Table(top_data, colWidths=[6 * cm, 3 * cm, 3 * cm, 3 * cm])
    top_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2563eb")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
    ]))
    elements.append(top_table)

    doc.build(elements)
    pdf_buffer.seek(0)
    return StreamingResponse(
        pdf_buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=analytics_report.pdf"},
    )
