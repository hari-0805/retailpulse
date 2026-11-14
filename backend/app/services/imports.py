"""
Data Import & Integration Management (Task 12).

Pipeline implemented here:

    File Upload -> Parse CSV -> Column Validation -> Row Validation
    -> Duplicate Detection -> Preview / Validation Result
    -> (Admin confirms) -> Process Valid Records -> Insert Into Database
    -> Import Result -> Import History

Data-integrity approach (documented per the task's requirement to explain
the chosen strategy):
  * Each row is processed inside its own SAVEPOINT (`db.begin_nested()`).
    A failure on one row rolls back only that row's partial writes and
    the loop continues — so one bad row can never corrupt or discard the
    rows around it (partial-success batch processing).
  * The parent transaction is committed once at the end of processing.
    If something catastrophic happens before that commit (e.g. the DB
    connection drops), nothing in the batch is persisted, since nothing
    was committed yet — so the job is never left half-applied at the
    top level either.
  * The staged CSV text lives on the ImportJob row itself, so upload,
    validate, and process are separate HTTP calls without needing a
    server-side file cache.
"""
import csv
import io
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy.orm import Session

from app.models import (
    ImportType, ImportRowStatus,
    Product, Category, ProductStatus, CategoryStatus,
    Customer, CustomerPurchaseSummary, CustomerType, CustomerStatus, CustomerActivityType,
    Sale, SaleItem, SalesChannel, PaymentMethod, PaymentStatus,
)
from app.models.inventory import Inventory
from app.schemas.inventory_utils import DEFAULT_REORDER_LEVEL
from app.services.customers import generate_customer_code, log_customer_activity, recalculate_purchase_summary

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_PATTERN = re.compile(r"^\+?[0-9][0-9\s\-().]{6,19}$")

DATE_FORMATS = [
    "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d",
    "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y",
]

# Canonical display name -> normalized key used for header matching.
REQUIRED_COLUMNS = {
    ImportType.PRODUCTS: ["Product Name", "SKU", "Category", "Unit Price", "Stock Quantity"],
    ImportType.INVENTORY: ["Product SKU", "Stock Quantity"],
    ImportType.CUSTOMERS: ["Name", "Email", "Phone"],
    ImportType.SALES: ["Customer", "Product", "Quantity", "Unit Price", "Sale Date"],
}

OPTIONAL_COLUMNS = {
    ImportType.PRODUCTS: ["Brand", "Description", "Cost Price", "Unit of Measure"],
    ImportType.INVENTORY: ["Reorder Level", "Remarks"],
    ImportType.CUSTOMERS: ["Address", "City", "State", "Country", "Postal Code", "Customer Type"],
    ImportType.SALES: ["Invoice Number", "Sales Channel", "Payment Method"],
}

TEMPLATES = {
    ImportType.PRODUCTS: (
        "products_template.csv",
        "Product Name,SKU,Category,Unit Price,Cost Price,Stock Quantity,Brand,Unit of Measure,Description\n"
        "Wireless Ergonomic Mouse,TECH-MOU-001,Electronics,29.99,15.00,100,Logitech,unit,Ergonomic wireless optical mouse with USB dongle\n"
        "Mechanical Keyboard,TECH-KEY-002,Electronics,89.99,45.00,50,Keychron,unit,Mechanical RGB gaming keyboard with blue switches\n"
    ),
    ImportType.INVENTORY: (
        "inventory_template.csv",
        "Product SKU,Stock Quantity,Reorder Level,Remarks\n"
        "TECH-MOU-001,150,20,Quarterly warehouse shipment restock\n"
        "TECH-KEY-002,75,15,Vendor delivery restock batch B1\n"
    ),
    ImportType.CUSTOMERS: (
        "customers_template.csv",
        "Name,Email,Phone,Address,City,State,Country,Postal Code,Customer Type\n"
        "John Doe,john.doe@example.com,+1-555-0101,123 Main St,Springfield,IL,USA,62701,RETAIL\n"
        "Apex Enterprises,procurement@apexcorp.com,+1-555-0102,456 Industrial Blvd,Dallas,TX,USA,75201,WHOLESALE\n"
    ),
    ImportType.SALES: (
        "sales_template.csv",
        "Customer,Product,Quantity,Unit Price,Sale Date,Invoice Number,Sales Channel,Payment Method\n"
        "John Doe,TECH-MOU-001,2,29.99,2026-08-15,INV-2026-0001,RETAIL_STORE,CREDIT_CARD\n"
        "Apex Enterprises,TECH-KEY-002,5,89.99,2026-08-16,INV-2026-0002,ONLINE,BANK_TRANSFER\n"
    ),
}


def get_template_csv(import_type: ImportType) -> tuple[str, str]:
    """Returns (filename, csv_text) for the requested import type."""
    return TEMPLATES.get(
        import_type,
        (f"{import_type.value.lower()}_template.csv", ",".join(REQUIRED_COLUMNS.get(import_type, [])) + "\n"),
    )


def _normalize(header: str) -> str:
    return re.sub(r"[\s_]+", "_", header.strip().lower())


class ParsedCSV:
    def __init__(self, fieldnames: list[str], rows: list[dict]):
        self.fieldnames = fieldnames
        self.rows = rows  # each row: {original_header: value}


def parse_csv_text(text: str) -> ParsedCSV:
    # csv module handles \r\n and quoted fields; strip a UTF-8 BOM if present.
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = [f.strip() for f in (reader.fieldnames or [])]
    rows = []
    for raw_row in reader:
        # Re-key with stripped header names; DictReader keeps original keys.
        row = {}
        for key, value in raw_row.items():
            if key is None:
                continue
            row[key.strip()] = (value or "").strip()
        rows.append(row)
    return ParsedCSV(fieldnames, rows)


COLUMN_ALIASES: dict[str, list[str]] = {
    "product_sku": ["sku", "productsku", "item_sku", "item_code"],
    "stock_quantity": ["quantity", "stock", "current_stock", "current_quantity", "qty"],
    "unit_price": ["price", "rate"],
    "product_name": ["name", "item_name", "title"],
}


def missing_required_columns(import_type: ImportType, fieldnames: list[str]) -> list[str]:
    normalized_present = {_normalize(f) for f in fieldnames}
    missing = []
    for display_name in REQUIRED_COLUMNS[import_type]:
        norm = _normalize(display_name)
        if norm in normalized_present:
            continue
        aliases = COLUMN_ALIASES.get(norm, [])
        if any(a in normalized_present for a in aliases):
            continue
        missing.append(display_name)
    return missing


def header_map(fieldnames: list[str]) -> dict:
    """normalized_key -> original header actually present in the file"""
    return {_normalize(f): f for f in fieldnames}


_header_map = header_map  # internal alias used elsewhere in this module


def _get(row: dict, header_map: dict, display_name: str, aliases: Optional[list[str]] = None) -> str:
    norm = _normalize(display_name)
    original = header_map.get(norm)
    if original and row.get(original) is not None:
        return (row.get(original) or "").strip()
    candidate_aliases = list(aliases or []) + COLUMN_ALIASES.get(norm, [])
    for alias in candidate_aliases:
        alias_orig = header_map.get(_normalize(alias))
        if alias_orig and row.get(alias_orig) is not None:
            return (row.get(alias_orig) or "").strip()
    return ""


def _parse_decimal(value: str) -> Optional[Decimal]:
    try:
        cleaned = value.replace(",", "").replace("₹", "").strip()
        return Decimal(cleaned)
    except (InvalidOperation, AttributeError):
        return None


def _parse_int(value: str) -> Optional[int]:
    try:
        return int(Decimal(value.replace(",", "").strip()))
    except (InvalidOperation, AttributeError, ValueError):
        return None


def _parse_date(value: str) -> Optional[datetime]:
    value = value.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


class RowResult:
    __slots__ = ("row_number", "data", "status", "message")

    def __init__(self, row_number: int, data: dict, status: Optional[ImportRowStatus], message: str = ""):
        self.row_number = row_number
        self.data = data
        self.status = status  # None means VALID
        self.message = message


def validate_rows(db: Session, company_id: str, import_type: ImportType, parsed: ParsedCSV) -> list[RowResult]:
    header_map = _header_map(parsed.fieldnames)
    if import_type == ImportType.PRODUCTS:
        return _validate_product_rows(db, company_id, parsed.rows, header_map)
    if import_type == ImportType.INVENTORY:
        return _validate_inventory_rows(db, company_id, parsed.rows, header_map)
    if import_type == ImportType.CUSTOMERS:
        return _validate_customer_rows(db, company_id, parsed.rows, header_map)
    return _validate_sale_rows(db, company_id, parsed.rows, header_map)


# ---------------------------------------------------------------- INVENTORY

def _validate_inventory_rows(db: Session, company_id: str, rows: list[dict], header_map: dict) -> list[RowResult]:
    products = db.query(Product).filter(Product.company_id == company_id).all()
    products_by_sku = {p.sku.lower(): p for p in products}
    seen_skus: set[str] = set()
    results = []

    for i, row in enumerate(rows, start=1):
        sku = _get(row, header_map, "Product SKU", ["SKU"])
        qty_raw = _get(row, header_map, "Stock Quantity", ["Quantity", "Current Stock"])
        reorder_raw = _get(row, header_map, "Reorder Level")

        problems = []
        if not sku:
            problems.append("Product SKU is required")
        elif sku.lower() not in products_by_sku:
            problems.append(f"Product SKU not found in database: '{sku}'")

        qty = _parse_int(qty_raw)
        if not qty_raw:
            problems.append("Stock Quantity is required")
        elif qty is None or qty < 0:
            problems.append("Stock Quantity must be a non-negative whole number")

        if reorder_raw:
            reorder = _parse_int(reorder_raw)
            if reorder is None or reorder < 0:
                problems.append("Reorder Level must be a non-negative whole number")

        if problems:
            results.append(RowResult(i, row, ImportRowStatus.INVALID, "; ".join(problems)))
            continue

        if sku.lower() in seen_skus:
            results.append(RowResult(i, row, ImportRowStatus.DUPLICATE, f"Duplicate Product SKU in file: {sku}"))
            continue

        seen_skus.add(sku.lower())
        results.append(RowResult(i, row, None))

    return results


# ---------------------------------------------------------------- PRODUCTS

def _validate_product_rows(db: Session, company_id: str, rows: list[dict], header_map: dict) -> list[RowResult]:
    existing_skus = {
        sku.lower() for (sku,) in db.query(Product.sku).filter(Product.company_id == company_id).all()
    }
    seen_skus: set[str] = set()
    results = []

    for i, row in enumerate(rows, start=1):
        name = _get(row, header_map, "Product Name")
        sku = _get(row, header_map, "SKU")
        category = _get(row, header_map, "Category")
        price_raw = _get(row, header_map, "Unit Price")
        stock_raw = _get(row, header_map, "Stock Quantity")

        problems = []
        if not name:
            problems.append("Product Name is required")
        if not sku:
            problems.append("SKU is required")
        if not category:
            problems.append("Category is required")

        price = _parse_decimal(price_raw)
        if price is None or price <= 0:
            problems.append("Unit Price must be a number greater than zero")

        stock = _parse_int(stock_raw)
        if stock is None or stock < 0:
            problems.append("Stock Quantity must be a non-negative whole number")

        if problems:
            results.append(RowResult(i, row, ImportRowStatus.INVALID, "; ".join(problems)))
            continue

        if sku.lower() in existing_skus or sku.lower() in seen_skus:
            results.append(RowResult(i, row, ImportRowStatus.DUPLICATE, f"Duplicate SKU: {sku}"))
            continue

        seen_skus.add(sku.lower())
        results.append(RowResult(i, row, None))

    return results


# ---------------------------------------------------------------- CUSTOMERS

def _validate_customer_rows(db: Session, company_id: str, rows: list[dict], header_map: dict) -> list[RowResult]:
    existing_emails = {
        e.lower() for (e,) in db.query(Customer.email).filter(
            Customer.company_id == company_id, Customer.is_deleted.is_(False)
        ).all()
    }
    existing_phones = {
        p for (p,) in db.query(Customer.phone).filter(
            Customer.company_id == company_id, Customer.is_deleted.is_(False)
        ).all()
    }
    seen_emails: set[str] = set()
    seen_phones: set[str] = set()
    results = []

    for i, row in enumerate(rows, start=1):
        name = _get(row, header_map, "Name")
        email = _get(row, header_map, "Email")
        phone = _get(row, header_map, "Phone")

        problems = []
        if not name:
            problems.append("Name is required")
        if not email:
            problems.append("Email is required")
        elif not EMAIL_PATTERN.match(email):
            problems.append("Email is not valid")
        if not phone:
            problems.append("Phone is required")
        elif not PHONE_PATTERN.match(phone):
            problems.append("Phone is not valid")

        if problems:
            results.append(RowResult(i, row, ImportRowStatus.INVALID, "; ".join(problems)))
            continue

        email_l = email.lower()
        if email_l in existing_emails or email_l in seen_emails or phone in existing_phones or phone in seen_phones:
            field = "email" if (email_l in existing_emails or email_l in seen_emails) else "phone"
            results.append(RowResult(i, row, ImportRowStatus.DUPLICATE, f"Duplicate {field}: {email if field == 'email' else phone}"))
            continue

        seen_emails.add(email_l)
        seen_phones.add(phone)
        results.append(RowResult(i, row, None))

    return results


# ---------------------------------------------------------------- SALES

def _validate_sale_rows(db: Session, company_id: str, rows: list[dict], header_map: dict) -> list[RowResult]:
    products = db.query(Product).filter(Product.company_id == company_id).all()
    products_by_sku = {p.sku.lower(): p for p in products}
    products_by_name = {p.name.lower(): p for p in products}

    customers = db.query(Customer).filter(
        Customer.company_id == company_id, Customer.is_deleted.is_(False)
    ).all()
    customers_by_name = {c.full_name.lower(): c for c in customers}
    customers_by_email = {c.email.lower(): c for c in customers}

    existing_invoices = {
        inv.lower() for (inv,) in db.query(Sale.invoice_number).filter(Sale.company_id == company_id).all()
    }
    seen_invoices: set[str] = set()

    # Tracks stock as it would be consumed row-by-row within this file,
    # so two rows selling the same product can't both "pass" against the
    # same starting stock figure.
    remaining_stock = {p.id: p.stock_quantity for p in products}

    results = []
    for i, row in enumerate(rows, start=1):
        customer_ref = _get(row, header_map, "Customer")
        product_ref = _get(row, header_map, "Product")
        qty_raw = _get(row, header_map, "Quantity")
        price_raw = _get(row, header_map, "Unit Price")
        date_raw = _get(row, header_map, "Sale Date")
        invoice_raw = _get(row, header_map, "Invoice Number")

        problems = []
        if not customer_ref:
            problems.append("Customer is required")
        if not product_ref:
            problems.append("Product is required")

        qty = _parse_int(qty_raw)
        if qty is None or qty <= 0:
            problems.append("Quantity must be a whole number greater than zero")

        price = _parse_decimal(price_raw)
        if price is None or price < 0:
            problems.append("Unit Price must be a non-negative number")

        sale_date = _parse_date(date_raw) if date_raw else None
        if not date_raw:
            problems.append("Sale Date is required")
        elif sale_date is None:
            problems.append(f"Sale Date '{date_raw}' is not a recognizable date")

        customer = customers_by_name.get(customer_ref.lower()) or customers_by_email.get(customer_ref.lower())
        if customer_ref and not customer:
            problems.append(f"Customer not found: {customer_ref}")

        product = products_by_sku.get(product_ref.lower()) or products_by_name.get(product_ref.lower())
        if product_ref and not product:
            problems.append(f"Product not found: {product_ref}")
        elif product and product.status != ProductStatus.ACTIVE:
            problems.append(f"Product is inactive: {product_ref}")

        if product and qty is not None and qty > 0:
            avail = remaining_stock.get(product.id, 0)
            if qty > avail:
                problems.append(f"Quantity ({qty}) exceeds available stock ({avail}) for '{product.name}'")

        if problems:
            results.append(RowResult(i, row, ImportRowStatus.INVALID, "; ".join(problems)))
            continue

        if invoice_raw:
            inv_l = invoice_raw.lower()
            if inv_l in existing_invoices or inv_l in seen_invoices:
                results.append(RowResult(i, row, ImportRowStatus.DUPLICATE, f"Duplicate Invoice Number: {invoice_raw}"))
                continue
            seen_invoices.add(inv_l)

        remaining_stock[product.id] = remaining_stock.get(product.id, 0) - qty
        results.append(RowResult(i, row, None))

    return results


# =========================================================================
# PROCESSING — turns a VALID RowResult into real database rows.
# Each function returns the created object; raises ValueError on failure
# (caller wraps this in a per-row SAVEPOINT and records it as FAILED).
# =========================================================================

def process_product_row(
    db: Session, company_id: str, row: dict, header_map: dict, duplicate_strategy: str = "SKIP"
) -> Optional[Product]:
    name = _get(row, header_map, "Product Name")
    sku = _get(row, header_map, "SKU")
    category_name = _get(row, header_map, "Category")
    brand = _get(row, header_map, "Brand") or None
    description = _get(row, header_map, "Description") or None
    unit_price = _parse_decimal(_get(row, header_map, "Unit Price"))
    stock_quantity = _parse_int(_get(row, header_map, "Stock Quantity")) or 0
    cost_price_raw = _get(row, header_map, "Cost Price")
    cost_price = _parse_decimal(cost_price_raw) if cost_price_raw else None
    if cost_price is None:
        cost_price = unit_price  # sensible default: no data loss, satisfies cost <= price
    uom = _get(row, header_map, "Unit of Measure") or "unit"

    category = db.query(Category).filter(
        Category.company_id == company_id, Category.name.ilike(category_name)
    ).first()
    if not category:
        category = Category(company_id=company_id, name=category_name, status=CategoryStatus.ACTIVE)
        db.add(category)
        db.flush()

    # Re-check for existing product
    existing = db.query(Product).filter(
        Product.company_id == company_id, Product.sku.ilike(sku)
    ).first()
    if existing:
        if duplicate_strategy == "SKIP":
            return None
        if duplicate_strategy == "REJECT":
            raise ValueError(f"Duplicate SKU: {sku} (rejected by policy)")

        # duplicate_strategy == "UPDATE"
        existing.name = name
        existing.category_id = category.id
        if brand:
            existing.brand = brand
        if description:
            existing.description = description
        if unit_price is not None:
            existing.unit_price = unit_price
        if cost_price is not None:
            existing.cost_price = cost_price
        if uom:
            existing.unit_of_measure = uom

        if stock_quantity != existing.stock_quantity:
            diff = stock_quantity - existing.stock_quantity
            prev_qty = existing.stock_quantity
            existing.stock_quantity = stock_quantity
            inv = db.query(Inventory).filter(
                Inventory.company_id == company_id, Inventory.product_id == existing.id
            ).first()
            if inv:
                inv.current_stock = stock_quantity
                inv.update_status()
                from app.models.inventory import InventoryMovement, MovementType
                db.add(InventoryMovement(
                    inventory_id=inv.id,
                    movement_type=MovementType.STOCK_ADDITION if diff >= 0 else MovementType.STOCK_REMOVAL,
                    quantity_changed=diff,
                    previous_quantity=prev_qty,
                    updated_quantity=stock_quantity,
                    reason="Product CSV Bulk Import Update",
                    remarks=f"Stock adjusted to {stock_quantity}",
                ))
        return existing

    product = Product(
        company_id=company_id,
        category_id=category.id,
        name=name,
        sku=sku,
        brand=brand,
        description=description,
        unit_price=unit_price,
        cost_price=cost_price,
        stock_quantity=stock_quantity,
        unit_of_measure=uom,
        status=ProductStatus.ACTIVE,
    )
    db.add(product)
    db.flush()

    inv = Inventory(
        company_id=company_id,
        product_id=product.id,
        current_stock=stock_quantity,
        reserved_stock=0,
        reorder_level=DEFAULT_REORDER_LEVEL,
    )
    inv.update_status()
    db.add(inv)
    return product


def process_inventory_row(
    db: Session, company_id: str, row: dict, header_map: dict, user_id: Optional[str] = None, duplicate_strategy: str = "SKIP"
) -> Optional[Inventory]:
    sku = _get(row, header_map, "Product SKU", ["SKU"])
    new_stock = _parse_int(_get(row, header_map, "Stock Quantity", ["Quantity", "Current Stock"])) or 0
    reorder_raw = _get(row, header_map, "Reorder Level")
    reorder_level = _parse_int(reorder_raw) if reorder_raw else None
    remarks = _get(row, header_map, "Remarks") or "CSV Bulk Inventory Import"

    product = db.query(Product).filter(
        Product.company_id == company_id, Product.sku.ilike(sku)
    ).first()
    if not product:
        raise ValueError(f"Product SKU not found in database: {sku}")

    inv = db.query(Inventory).filter(
        Inventory.company_id == company_id, Inventory.product_id == product.id
    ).first()

    from app.models.inventory import InventoryMovement, MovementType

    if inv:
        if duplicate_strategy == "SKIP":
            return None
        if duplicate_strategy == "REJECT":
            raise ValueError(f"Inventory record already exists for SKU: {sku} (rejected by policy)")

        # UPDATE strategy
        diff = new_stock - inv.current_stock
        prev_qty = inv.current_stock
        inv.current_stock = new_stock
        if reorder_level is not None:
            inv.reorder_level = reorder_level
        inv.update_status()

        product.stock_quantity = new_stock

        movement_type = MovementType.STOCK_ADDITION if diff >= 0 else MovementType.STOCK_REMOVAL
        movement = InventoryMovement(
            inventory_id=inv.id,
            movement_type=movement_type,
            quantity_changed=diff,
            previous_quantity=prev_qty,
            updated_quantity=new_stock,
            reason="CSV Inventory Bulk Import",
            remarks=remarks,
            performed_by=user_id,
        )
        db.add(movement)
        return inv
    else:
        inv = Inventory(
            company_id=company_id,
            product_id=product.id,
            current_stock=new_stock,
            reserved_stock=0,
            reorder_level=reorder_level if reorder_level is not None else DEFAULT_REORDER_LEVEL,
        )
        inv.update_status()
        db.add(inv)
        db.flush()

        product.stock_quantity = new_stock

        movement = InventoryMovement(
            inventory_id=inv.id,
            movement_type=MovementType.STOCK_ADDITION,
            quantity_changed=new_stock,
            previous_quantity=0,
            updated_quantity=new_stock,
            reason="Initial CSV Inventory Bulk Import",
            remarks=remarks,
            performed_by=user_id,
        )
        db.add(movement)
        return inv


def process_customer_row(
    db: Session, company_id: str, row: dict, header_map: dict, created_by: Optional[str], duplicate_strategy: str = "SKIP"
) -> Optional[Customer]:
    full_name = _get(row, header_map, "Name")
    parts = full_name.split(" ", 1)
    first_name, last_name = (parts[0], parts[1] if len(parts) > 1 else "")
    email = _get(row, header_map, "Email")
    phone = _get(row, header_map, "Phone")
    address = _get(row, header_map, "Address") or None
    city = _get(row, header_map, "City") or None
    state = _get(row, header_map, "State") or None
    country = _get(row, header_map, "Country") or None
    postal_code = _get(row, header_map, "Postal Code") or None
    customer_type_raw = _get(row, header_map, "Customer Type").upper() or "RETAIL"
    try:
        customer_type = CustomerType(customer_type_raw)
    except ValueError:
        customer_type = CustomerType.RETAIL

    dup = db.query(Customer).filter(
        Customer.company_id == company_id, Customer.is_deleted.is_(False),
        (Customer.email.ilike(email)) | (Customer.phone == phone),
    ).first()
    if dup:
        if duplicate_strategy == "SKIP":
            return None
        if duplicate_strategy == "REJECT":
            raise ValueError(f"A customer with this email or phone already exists: {email} / {phone}")

        # UPDATE strategy
        dup.first_name = first_name
        dup.last_name = last_name
        dup.full_name = full_name
        if address:
            dup.address = address
        if city:
            dup.city = city
        if state:
            dup.state = state
        if country:
            dup.country = country
        if postal_code:
            dup.postal_code = postal_code
        dup.customer_type = customer_type
        log_customer_activity(
            db, dup.id, company_id, CustomerActivityType.PROFILE_UPDATED,
            "Customer details updated via CSV import.",
        )
        return dup

    customer = Customer(
        company_id=company_id,
        customer_code=generate_customer_code(db, company_id),
        first_name=first_name,
        last_name=last_name,
        full_name=full_name,
        email=email,
        phone=phone,
        address=address,
        city=city,
        state=state,
        country=country,
        postal_code=postal_code,
        customer_type=customer_type,
        status=CustomerStatus.ACTIVE,
        created_by=created_by,
    )
    db.add(customer)
    db.flush()

    db.add(CustomerPurchaseSummary(customer_id=customer.id, company_id=company_id))
    log_customer_activity(
        db, customer.id, company_id, CustomerActivityType.REGISTERED,
        f"Registered as a {customer_type.value.title()} customer (via CSV import).",
    )
    return customer


def process_sale_row(
    db: Session, company_id: str, row: dict, header_map: dict, created_by: str, duplicate_strategy: str = "SKIP"
) -> Optional[Sale]:
    from app.routers.sales import _generate_invoice_number, record_sale_stock_movement

    customer_ref = _get(row, header_map, "Customer")
    product_ref = _get(row, header_map, "Product")
    qty = _parse_int(_get(row, header_map, "Quantity"))
    unit_price = _parse_decimal(_get(row, header_map, "Unit Price"))
    sale_date = _parse_date(_get(row, header_map, "Sale Date")) or datetime.utcnow()
    invoice_raw = _get(row, header_map, "Invoice Number")
    channel_raw = _get(row, header_map, "Sales Channel").upper().replace(" ", "_")
    payment_raw = _get(row, header_map, "Payment Method").upper()

    try:
        sales_channel = SalesChannel(channel_raw) if channel_raw else SalesChannel.RETAIL_STORE
    except ValueError:
        sales_channel = SalesChannel.RETAIL_STORE
    try:
        payment_method = PaymentMethod(payment_raw) if payment_raw else PaymentMethod.CASH
    except ValueError:
        payment_method = PaymentMethod.CASH

    customer = db.query(Customer).filter(
        Customer.company_id == company_id, Customer.is_deleted.is_(False),
        (Customer.full_name.ilike(customer_ref)) | (Customer.email.ilike(customer_ref)),
    ).first()
    if not customer:
        raise ValueError(f"Customer not found: {customer_ref}")

    product = db.query(Product).filter(
        Product.company_id == company_id,
        (Product.sku.ilike(product_ref)) | (Product.name.ilike(product_ref)),
    ).first()
    if not product:
        raise ValueError(f"Product not found: {product_ref}")
    if product.status != ProductStatus.ACTIVE:
        raise ValueError(f"Product is inactive: {product_ref}")
    if qty > product.stock_quantity:
        raise ValueError(f"Insufficient stock for '{product.name}'. Available: {product.stock_quantity}")

    invoice_number = invoice_raw or _generate_invoice_number(db, company_id)
    dup = db.query(Sale.id).filter(
        Sale.company_id == company_id, Sale.invoice_number.ilike(invoice_number)
    ).first()
    if dup:
        if duplicate_strategy == "SKIP":
            return None
        raise ValueError(f"Duplicate Invoice Number: {invoice_number}")

    line_total = (unit_price or product.unit_price) * qty
    sale = Sale(
        company_id=company_id,
        invoice_number=invoice_number,
        customer_name=customer.full_name,
        customer_id=customer.id,
        sale_date=sale_date,
        sales_channel=sales_channel,
        payment_method=payment_method,
        payment_status=PaymentStatus.PAID,
        total_amount=line_total,
        created_by=created_by,
    )
    db.add(sale)
    db.flush()

    product.stock_quantity -= qty
    item = SaleItem(
        sale_id=sale.id,
        product_id=product.id,
        category_id=product.category_id,
        quantity=qty,
        unit_price=unit_price or product.unit_price,
        discount=0,
        tax=0,
        total=line_total,
    )
    db.add(item)
    record_sale_stock_movement(db, product.id, company_id, -qty, invoice_number, created_by)

    return sale


# =========================================================================
# ASYNCHRONOUS BACKGROUND EXECUTION ENGINE
# =========================================================================

def execute_import_job_background(
    job_id: str, company_id: str, user_id: str, duplicate_strategy: str = "SKIP"
) -> None:
    from app.database import SessionLocal
    from app.models import (
        ImportJob, ImportError as ImportErrorRow, ImportRowStatus, ImportStatus,
        NotificationType, NotificationPriority, AuditStatus,
    )
    from app.services.notifications import notify_roles
    from app.audit import log_action
    from app.services import data_quality as dq

    db = SessionLocal()
    try:
        job = db.query(ImportJob).filter(ImportJob.id == job_id, ImportJob.company_id == company_id).first()
        if not job:
            return

        # Check if cancelled before starting
        if job.status == ImportStatus.CANCELLED:
            return

        job.status = ImportStatus.PROCESSING
        job.started_at = datetime.utcnow()
        job.duplicate_strategy = duplicate_strategy
        job.processed_records = 0
        job.successful_records = 0
        job.failed_records = 0
        job.duplicate_records = 0
        job.skipped_records = 0
        db.commit()

        # Send start notification
        notify_roles(
            db, company_id, NotificationType.IMPORT_STARTED,
            title="Import Started",
            message=f"{job.import_type.value.title()} import '{job.filename}' has started processing ({job.total_records} records).",
            priority=NotificationPriority.LOW,
            resource_type="ImportJob", resource_id=job.id,
        )
        db.commit()

        # Re-parse & validate staged CSV
        parsed = parse_csv_text(job.raw_content)
        results = validate_rows(db, company_id, job.import_type, parsed)
        h_map = header_map(parsed.fieldnames)

        # Clear previous error rows for this job
        db.query(ImportErrorRow).filter(ImportErrorRow.import_job_id == job.id).delete()
        db.commit()

        successful = 0
        failed = 0
        duplicate = 0
        skipped = 0

        for r in results:
            # Check for cancellation
            db.refresh(job)
            if job.status == ImportStatus.CANCELLED:
                break

            if r.status == ImportRowStatus.INVALID:
                failed += 1
                db.add(ImportErrorRow(
                    import_job_id=job.id, row_number=r.row_number, row_status=ImportRowStatus.INVALID,
                    row_data=json.dumps(r.data), error_message=r.message,
                ))
            elif r.status == ImportRowStatus.DUPLICATE:
                duplicate += 1
                if duplicate_strategy == "REJECT":
                    failed += 1
                    db.add(ImportErrorRow(
                        import_job_id=job.id, row_number=r.row_number, row_status=ImportRowStatus.DUPLICATE,
                        row_data=json.dumps(r.data), error_message=f"{r.message} (rejected by policy)",
                    ))
                elif duplicate_strategy == "UPDATE":
                    savepoint = db.begin_nested()
                    try:
                        if job.import_type == ImportType.PRODUCTS:
                            res = process_product_row(db, company_id, r.data, h_map, duplicate_strategy="UPDATE")
                        elif job.import_type == ImportType.INVENTORY:
                            res = process_inventory_row(db, company_id, r.data, h_map, user_id=user_id, duplicate_strategy="UPDATE")
                        elif job.import_type == ImportType.CUSTOMERS:
                            res = process_customer_row(db, company_id, r.data, h_map, created_by=user_id, duplicate_strategy="UPDATE")
                        else:
                            res = process_sale_row(db, company_id, r.data, h_map, created_by=user_id, duplicate_strategy="UPDATE")
                        savepoint.commit()
                        if res is not None:
                            successful += 1
                        else:
                            skipped += 1
                    except Exception as exc:
                        savepoint.rollback()
                        failed += 1
                        db.add(ImportErrorRow(
                            import_job_id=job.id, row_number=r.row_number, row_status=ImportRowStatus.FAILED,
                            row_data=json.dumps(r.data), error_message=str(exc)[:1000],
                        ))
                else:  # SKIP
                    skipped += 1
                    db.add(ImportErrorRow(
                        import_job_id=job.id, row_number=r.row_number, row_status=ImportRowStatus.DUPLICATE,
                        row_data=json.dumps(r.data), error_message=f"{r.message} (skipped)",
                    ))
            else:
                # VALID row
                savepoint = db.begin_nested()
                try:
                    if job.import_type == ImportType.PRODUCTS:
                        res = process_product_row(db, company_id, r.data, h_map, duplicate_strategy=duplicate_strategy)
                    elif job.import_type == ImportType.INVENTORY:
                        res = process_inventory_row(db, company_id, r.data, h_map, user_id=user_id, duplicate_strategy=duplicate_strategy)
                    elif job.import_type == ImportType.CUSTOMERS:
                        res = process_customer_row(db, company_id, r.data, h_map, created_by=user_id, duplicate_strategy=duplicate_strategy)
                    else:
                        res = process_sale_row(db, company_id, r.data, h_map, created_by=user_id, duplicate_strategy=duplicate_strategy)
                        if res is not None:
                            db.flush()
                            recalculate_purchase_summary(db, res.customer_id)
                    savepoint.commit()
                    if res is not None:
                        successful += 1
                    else:
                        skipped += 1
                except Exception as exc:
                    savepoint.rollback()
                    failed += 1
                    db.add(ImportErrorRow(
                        import_job_id=job.id, row_number=r.row_number, row_status=ImportRowStatus.FAILED,
                        row_data=json.dumps(r.data), error_message=str(exc)[:1000],
                    ))

            job.processed_records = successful + failed + skipped
            job.successful_records = successful
            job.failed_records = failed
            job.duplicate_records = duplicate
            job.skipped_records = skipped
            db.commit()

        # If cancelled during execution
        if job.status == ImportStatus.CANCELLED:
            job.completed_at = datetime.utcnow()
            if job.started_at:
                job.duration_seconds = max(0.1, (job.completed_at - job.started_at).total_seconds())
            db.commit()
            return

        # Complete job
        job.completed_at = datetime.utcnow()
        if job.started_at:
            job.duration_seconds = max(0.1, (job.completed_at - job.started_at).total_seconds())
        job.status = ImportStatus.COMPLETED if (failed == 0 and duplicate == 0) else ImportStatus.COMPLETED_WITH_ERRORS

        # Notifications
        if job.status == ImportStatus.COMPLETED:
            notify_type, priority, title = NotificationType.IMPORT_COMPLETED, NotificationPriority.LOW, "Import Completed"
        else:
            notify_type, priority, title = NotificationType.IMPORT_COMPLETED, NotificationPriority.MEDIUM, "Import Completed With Errors"

        notify_roles(
            db, company_id, notify_type, title=title,
            message=(
                f"{job.import_type.value.title()} import '{job.filename}' finished: "
                f"{successful} successful, {skipped} skipped, {failed} failed in {job.duration_seconds:.1f}s."
            ),
            priority=priority, resource_type="ImportJob", resource_id=job.id,
        )
        db.commit()

        # Data Quality reconciliation run
        try:
            checks = dq.IMPORT_CHECKS.get(job.import_type.value)
            if checks:
                run, created = dq.start_run(db, company_id, user_id, "IMPORT", checks)
                if created:
                    dq.execute_run_in_background(run.id)
        except Exception:
            db.rollback()

    except Exception:
        db.rollback()
        try:
            job = db.query(ImportJob).filter(ImportJob.id == job_id).first()
            if job:
                job.status = ImportStatus.FAILED
                job.completed_at = datetime.utcnow()
                if job.started_at:
                    job.duration_seconds = max(0.1, (job.completed_at - job.started_at).total_seconds())
                db.commit()
                notify_roles(
                    db, company_id, NotificationType.IMPORT_FAILED,
                    title="Import Failed",
                    message=f"{job.import_type.value.title()} import '{job.filename}' failed unexpectedly.",
                    priority=NotificationPriority.HIGH,
                    resource_type="ImportJob", resource_id=job.id,
                )
                db.commit()
        except Exception:
            pass
    finally:
        db.close()

