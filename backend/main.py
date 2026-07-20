from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.database import Base, engine
from app.config import settings
from app.routers import auth, categories, products, dashboard

# Creates tables that don't exist yet (including the new `categories` and
# `products` tables added in Task 2). It will NOT alter tables that already
# exist from Task 1 — for those, run the guarded ALTER TABLE below.
Base.metadata.create_all(bind=engine)

# Task 2 adds `entity_name` to the already-existing `audit_logs` table.
# create_all() can't add columns to a table it didn't create, so this
# runs a defensive ALTER TABLE on every startup — safe to run repeatedly.
with engine.connect() as conn:
    conn.execute(text(
        "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS entity_name VARCHAR(255)"
    ))
    conn.commit()

app = FastAPI(
    title="RetailPulse Analytics API",
    description="Task 1: Company Onboarding & Authentication",
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


@app.get("/health")
def health_check():
    return {"status": "ok"}