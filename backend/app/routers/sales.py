from datetime import datetime, date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status, Query
from sqlalchemy import func, or_, asc, desc
from sqlalchemy.exc import IntegrityError, DataError
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import (
    Sale, SaleItem, Product, ProductStatus, User, UserRole,
    SalesChannel, PaymentMethod,
)
from app.schemas import (
    SaleCreate, SaleUpdate, SaleOut, SaleListResponse, SalesDashboardSummary,
)
from app.dependencies import require_roles, get_current_company_id
from app.audit import log_action
from app.inventory import check_stock_alerts

router = APIRouter(prefix="/sales", tags=["sales"])

# Task 3: Company Admins AND Analysts can manage sales (wider than the
# admin-only Product/Category modules from Task 2).
SALES_ROLES = [UserRole.COMPANY_ADMIN, UserRole.SUPER_ADMIN, UserRole.ANALYST]

SORT_FIELDS = {
    "date": Sale.sale_date,
    "invoice": Sale.invoice_number,
    "total": Sale.total_amount,
}


def _generate_invoice_number(db: Session, company_id: str) -> str:
    year = datetime.utcnow().year
    prefix = f"INV-{year}-"

    count = db.query(func.count(Sale.id)).filter(
        Sale.company_id == company_id,
        Sale.invoice_number.like(f"{prefix}%"),
    ).scalar() or 0

    for attempt in range(10):
        candidate = f"{prefix}{count + 1 + attempt:06d}"
        exists = db.query(Sale.id).filter(
            Sale.company_id == company_id, Sale.invoice_number == candidate
        ).first()
        if not exists:
            return candidate

    raise HTTPException(status_code=500, detail="Could not generate a unique invoice number, please retry")


def _build_items(db: Session, company_id: str, items_payload, products_cache: dict):
    """
    Validates each line item against its product (active, sufficient stock,
    discount within line value), auto-fills category, deducts stock, and
    returns (SaleItem objects not yet attached to a Sale, total_amount).
    Mutates products_cache in place so callers can run stock-alert checks
    after commit.
    """
    sale_items = []
    total_amount = Decimal("0")

    for line in items_payload:
        if not line.product_id:
            raise HTTPException(status_code=400, detail="Every line item must have a product selected")

        try:
            product = db.query(Product).filter(
                Product.id == line.product_id, Product.company_id == company_id
            ).first()
        except DataError:
            db.rollback()
            raise HTTPException(status_code=400, detail=f"Invalid product reference: {line.product_id}")
        if not product:
            raise HTTPException(status_code=400, detail=f"Product not found: {line.product_id}")

        if product.status != ProductStatus.ACTIVE:
            raise HTTPException(
                status_code=400,
                detail=f"'{product.name}' is inactive and cannot be sold",
            )

        if line.quantity > product.stock_quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient stock for '{product.name}'. Available: {product.stock_quantity}",
            )

        unit_price = line.unit_price if line.unit_price is not None else product.unit_price
        line_value = line.quantity * unit_price
        if line.discount > line_value:
            raise HTTPException(
                status_code=400,
                detail=f"Discount cannot exceed total product value for '{product.name}'",
            )

        line_total = line_value - line.discount + line.tax

        product.stock_quantity -= line.quantity
        products_cache[product.id] = product

        sale_items.append(SaleItem(
            product_id=product.id,
            category_id=product.category_id,
            quantity=line.quantity,
            unit_price=unit_price,
            discount=line.discount,
            tax=line.tax,
            total=line_total,
        ))
        total_amount += line_total

    return sale_items, total_amount


def _serialize_list_item(sale: Sale) -> dict:
    return {
        "id": sale.id,
        "invoice_number": sale.invoice_number,
        "customer_name": sale.customer_name,
        "sale_date": sale.sale_date,
        "sales_channel": sale.sales_channel,
        "payment_method": sale.payment_method,
        "total_amount": sale.total_amount,
        "item_count": len(sale.items),
    }


@router.get("", response_model=SaleListResponse)
def list_sales(
    search: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    category_id: Optional[str] = None,
    sales_channel: Optional[SalesChannel] = None,
    payment_method: Optional[PaymentMethod] = None,
    sort_by: str = Query("date", pattern="^(date|invoice|total)$"),
    sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    current_user: User = Depends(require_roles(SALES_ROLES)),
):
    query = db.query(Sale).options(joinedload(Sale.items)).filter(Sale.company_id == company_id)

    if search:
        like = f"%{search}%"
        matching_sale_ids = db.query(SaleItem.sale_id).join(
            Product, SaleItem.product_id == Product.id
        ).filter(Product.name.ilike(like)).subquery()

        query = query.filter(or_(
            Sale.invoice_number.ilike(like),
            Sale.customer_name.ilike(like),
            Sale.id.in_(db.query(matching_sale_ids.c.sale_id)),
        ))

    if category_id:
        matching_sale_ids = db.query(SaleItem.sale_id).filter(
            SaleItem.category_id == category_id
        ).subquery()
        query = query.filter(Sale.id.in_(db.query(matching_sale_ids.c.sale_id)))

    if date_from:
        query = query.filter(Sale.sale_date >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        query = query.filter(Sale.sale_date <= datetime.combine(date_to, datetime.max.time()))

    if sales_channel:
        query = query.filter(Sale.sales_channel == sales_channel)
    if payment_method:
        query = query.filter(Sale.payment_method == payment_method)

    total = query.distinct().count()

    sort_column = SORT_FIELDS[sort_by]
    order_fn = asc if sort_dir == "asc" else desc
    query = query.order_by(order_fn(sort_column)).distinct()

    sales = query.offset((page - 1) * page_size).limit(page_size).all()

    return SaleListResponse(items=[_serialize_list_item(s) for s in sales], total=total)


@router.post("", response_model=SaleOut, status_code=status.HTTP_201_CREATED)
def create_sale(
    payload: SaleCreate,
    request: Request,
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    current_user: User = Depends(require_roles(SALES_ROLES)),
):
    products_cache: dict = {}
    sale_items, total_amount = _build_items(db, company_id, payload.items, products_cache)

    invoice_number = _generate_invoice_number(db, company_id)

    sale = Sale(
        company_id=company_id,
        invoice_number=invoice_number,
        customer_name=payload.customer_name,
        sale_date=payload.sale_date or datetime.utcnow(),
        sales_channel=payload.sales_channel,
        payment_method=payload.payment_method,
        total_amount=total_amount,
        created_by=current_user.id,
    )
    db.add(sale)

    try:
        db.flush()
        for item in sale_items:
            item.sale_id = sale.id
            db.add(item)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Invoice number collision, please retry")

    db.refresh(sale)

    product_names = [p.name for p in products_cache.values()]
    log_action(db, request, "Sale Created", company_id=company_id, user_id=current_user.id,
               entity_name=f"{invoice_number} ({', '.join(product_names)})")

    for product in products_cache.values():
        check_stock_alerts(db, request, product, company_id, current_user.id)

    return db.query(Sale).options(
        joinedload(Sale.items).joinedload(SaleItem.product),
        joinedload(Sale.items).joinedload(SaleItem.category),
        joinedload(Sale.creator),
    ).filter(Sale.id == sale.id).first()


@router.get("/dashboard/summary", response_model=SalesDashboardSummary)
def get_sales_summary(
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    current_user: User = Depends(require_roles(SALES_ROLES)),
):
    total_orders = db.query(func.count(Sale.id)).filter(Sale.company_id == company_id).scalar() or 0
    total_revenue = db.query(func.coalesce(func.sum(Sale.total_amount), 0)).filter(
        Sale.company_id == company_id
    ).scalar() or Decimal("0")
    total_units = db.query(func.coalesce(func.sum(SaleItem.quantity), 0)).join(
        Sale, SaleItem.sale_id == Sale.id
    ).filter(Sale.company_id == company_id).scalar() or 0

    avg_order_value = (total_revenue / total_orders) if total_orders > 0 else Decimal("0")

    return SalesDashboardSummary(
        total_sales=total_units,
        total_revenue=total_revenue,
        total_orders=total_orders,
        average_order_value=avg_order_value,
    )


@router.get("/{sale_id}", response_model=SaleOut)
def get_sale(
    sale_id: str,
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    current_user: User = Depends(require_roles(SALES_ROLES)),
):
    sale = db.query(Sale).options(
        joinedload(Sale.items).joinedload(SaleItem.product),
        joinedload(Sale.items).joinedload(SaleItem.category),
        joinedload(Sale.creator),
    ).filter(Sale.id == sale_id, Sale.company_id == company_id).first()

    if not sale:
        raise HTTPException(status_code=404, detail="Sale not found")

    return sale


@router.put("/{sale_id}", response_model=SaleOut)
def update_sale(
    sale_id: str,
    payload: SaleUpdate,
    request: Request,
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    current_user: User = Depends(require_roles(SALES_ROLES)),
):
    sale = db.query(Sale).options(joinedload(Sale.items)).filter(
        Sale.id == sale_id, Sale.company_id == company_id
    ).first()
    if not sale:
        raise HTTPException(status_code=404, detail="Sale not found")

    products_cache: dict = {}

    if payload.items is not None:
        # Revert stock for the sale's current items before re-validating
        # the new set, so editing quantities on the same product works
        # correctly instead of comparing against already-reduced stock.
        for old_item in sale.items:
            product = db.query(Product).filter(Product.id == old_item.product_id).first()
            if product:
                product.stock_quantity += old_item.quantity
                products_cache[product.id] = product

        for old_item in list(sale.items):
            db.delete(old_item)
        db.flush()

        new_items, total_amount = _build_items(db, company_id, payload.items, products_cache)
        for item in new_items:
            item.sale_id = sale.id
            db.add(item)
        sale.total_amount = total_amount

        log_action(db, request, "Inventory Updated", company_id=company_id,
                   user_id=current_user.id, entity_name=sale.invoice_number)

    if payload.customer_name is not None:
        sale.customer_name = payload.customer_name
    if payload.sale_date is not None:
        sale.sale_date = payload.sale_date
    if payload.sales_channel is not None:
        sale.sales_channel = payload.sales_channel
    if payload.payment_method is not None:
        sale.payment_method = payload.payment_method

    db.commit()
    db.refresh(sale)

    log_action(db, request, "Sale Updated", company_id=company_id,
               user_id=current_user.id, entity_name=sale.invoice_number)

    for product in products_cache.values():
        check_stock_alerts(db, request, product, company_id, current_user.id)

    return db.query(Sale).options(
        joinedload(Sale.items).joinedload(SaleItem.product),
        joinedload(Sale.items).joinedload(SaleItem.category),
        joinedload(Sale.creator),
    ).filter(Sale.id == sale.id).first()


@router.delete("/{sale_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_sale(
    sale_id: str,
    request: Request,
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    current_user: User = Depends(require_roles(SALES_ROLES)),
):
    sale = db.query(Sale).options(joinedload(Sale.items)).filter(
        Sale.id == sale_id, Sale.company_id == company_id
    ).first()
    if not sale:
        raise HTTPException(status_code=404, detail="Sale not found")

    products_cache: dict = {}
    for item in sale.items:
        product = db.query(Product).filter(Product.id == item.product_id).first()
        if product:
            product.stock_quantity += item.quantity
            products_cache[product.id] = product

    invoice_number = sale.invoice_number
    db.delete(sale)
    db.commit()

    log_action(db, request, "Sale Deleted", company_id=company_id,
               user_id=current_user.id, entity_name=invoice_number)
    if products_cache:
        log_action(db, request, "Inventory Updated", company_id=company_id,
                   user_id=current_user.id, entity_name=invoice_number)

    for product in products_cache.values():
        check_stock_alerts(db, request, product, company_id, current_user.id)

    return None
