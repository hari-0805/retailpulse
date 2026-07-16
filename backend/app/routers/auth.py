from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Company, User, RefreshToken, UserRole, UserStatus
from app.schemas import (
    CompanyRegisterRequest, RegisterResponse, LoginRequest, TokenResponse,
    RefreshRequest, UserOut,
)
from app.security import (
    hash_password, verify_password, create_access_token,
    generate_refresh_token, refresh_token_expiry,
)
from app.audit import log_action
from app.dependencies import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


def _issue_tokens(db: Session, user: User) -> TokenResponse:
    access_token = create_access_token(user.id, user.company_id, user.role.value)
    refresh_token_value = generate_refresh_token()

    db.add(RefreshToken(
        user_id=user.id,
        token=refresh_token_value,
        expires_at=refresh_token_expiry(),
    ))
    db.commit()

    return TokenResponse(access_token=access_token, refresh_token=refresh_token_value)


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
def register_company(payload: CompanyRegisterRequest, request: Request, db: Session = Depends(get_db)):
    # Prevent duplicate company registration (by company email)
    if db.query(Company).filter(Company.email == payload.company_email).first():
        raise HTTPException(status_code=400, detail="A company with this email is already registered")

    # Email uniqueness across users (owner email)
    if db.query(User).filter(User.email == payload.owner_email).first():
        raise HTTPException(status_code=400, detail="This email is already in use")

    company = Company(
        name=payload.company_name,
        industry=payload.industry,
        email=payload.company_email,
        address=payload.company_address,
        phone=payload.company_phone,
    )
    db.add(company)
    db.flush()  # get company.id before commit

    admin_user = User(
        company_id=company.id,
        name=payload.owner_name,
        email=payload.owner_email,
        password=hash_password(payload.password),
        role=UserRole.COMPANY_ADMIN,
        status=UserStatus.ACTIVE,
    )
    db.add(admin_user)
    db.commit()
    db.refresh(company)
    db.refresh(admin_user)

    tokens = _issue_tokens(db, admin_user)

    log_action(db, request, "Company Registered", company_id=company.id, user_id=admin_user.id)

    return RegisterResponse(company=company, user=admin_user, tokens=tokens)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()

    if not user or not verify_password(payload.password, user.password):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if user.status != UserStatus.ACTIVE:
        raise HTTPException(status_code=403, detail="Account is not active")

    user.last_login = datetime.utcnow()
    db.commit()

    tokens = _issue_tokens(db, user)

    log_action(db, request, "User Login", company_id=user.company_id, user_id=user.id)

    return tokens


@router.post("/refresh", response_model=TokenResponse)
def refresh_token(payload: RefreshRequest, db: Session = Depends(get_db)):
    stored = db.query(RefreshToken).filter(RefreshToken.token == payload.refresh_token).first()

    if not stored or stored.revoked or stored.expires_at < datetime.utcnow():
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    user = db.query(User).filter(User.id == stored.user_id).first()
    if not user or user.status != UserStatus.ACTIVE:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    # Rotate refresh token: revoke old, issue new
    stored.revoked = True
    db.commit()

    return _issue_tokens(db, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    payload: RefreshRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stored = db.query(RefreshToken).filter(
        RefreshToken.token == payload.refresh_token,
        RefreshToken.user_id == current_user.id,
    ).first()

    if stored:
        stored.revoked = True
        db.commit()

    log_action(db, request, "User Logout", company_id=current_user.company_id, user_id=current_user.id)
    return None


@router.get("/me", response_model=UserOut)
def get_profile(current_user: User = Depends(get_current_user)):
    return current_user
