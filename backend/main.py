from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import Base, engine
from app.config import settings
from app.routers import auth

# Creates tables that don't exist yet. For altering existing tables in
# production, use Alembic migrations rather than editing tables by hand.
Base.metadata.create_all(bind=engine)

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


@app.get("/health")
def health_check():
    return {"status": "ok"}
