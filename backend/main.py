from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.database import Base, engine
from app.config import settings
from app.routers import auth, categories, products, dashboard, sales, notifications

# Creates tables that don't exist yet (including `sales`, `sale_items`, and
# `notifications`, new in Task 3). It will NOT alter tables that already
# exist — for those, run the guarded ALTER TABLE statements below.
Base.metadata.create_all(bind=engine)

# Task 2: entity_name on the already-existing audit_logs table.
# Task 3: low_stock_threshold / is_out_of_stock on the already-existing
# products table. create_all() can't add columns to a table it didn't
# create, so these run on every startup — safe to run repeatedly.
with engine.connect() as conn:
    conn.execute(text(
        "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS entity_name VARCHAR(255)"
    ))
    conn.execute(text(
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS low_stock_threshold INTEGER NOT NULL DEFAULT 10"
    ))
    conn.execute(text(
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS is_out_of_stock BOOLEAN NOT NULL DEFAULT FALSE"
    ))
    conn.commit()

app = FastAPI(
    title="RetailPulse Analytics API",
    description="Company onboarding, product/category management, and sales transactions",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(categories.router)
app.include_router(products.router)
app.include_router(dashboard.router)
app.include_router(sales.router)
app.include_router(notifications.router)


@app.get("/health")
def health_check():
    return {"status": "ok"}