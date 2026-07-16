# RetailPulse Analytics — Task 1: Company Onboarding & Authentication

## Full file structure

```
retailpulse/
├── backend/
│   ├── main.py                  ← FastAPI entrypoint (run: uvicorn main:app)
│   ├── requirements.txt
│   ├── .env                     ← create from .env.example
│   └── app/
│       ├── __init__.py
│       ├── config.py             Settings loaded from .env
│       ├── database.py           SQLAlchemy engine/session
│       ├── models.py             Company, User, RefreshToken, AuditLog tables
│       ├── schemas.py            Pydantic request/response validation
│       ├── security.py           bcrypt hashing, JWT creation
│       ├── dependencies.py       get_current_user, require_roles, get_current_company_id
│       ├── audit.py              Audit log writer
│       └── routers/
│           ├── __init__.py
│           └── auth.py           /auth/register, /login, /refresh, /logout, /me
│
└── frontend/
    ├── index.html
    ├── package.json
    ├── tsconfig.json
    ├── vite.config.ts
    ├── tailwind.config.js
    ├── postcss.config.js
    ├── .env                      ← create from .env.example
    └── src/
        ├── main.tsx
        ├── App.tsx
        ├── index.css              Tailwind directives + shared component classes
        ├── vite-env.d.ts
        ├── api/
        │   ├── client.ts          Axios instance, auto-refresh on 401
        │   └── auth.ts            registerCompany, login, logout, getProfile
        ├── types/
        │   └── index.ts
        ├── context/
        │   └── AuthContext.tsx    Session state
        ├── components/
        │   └── ProtectedRoute.tsx Route guard
        └── pages/
            ├── Register.tsx
            ├── Login.tsx
            ├── ForgotPassword.tsx (placeholder)
            ├── Dashboard.tsx
            └── Profile.tsx
```

**Note on backend structure:** `main.py` sits directly in `backend/`, not inside `app/`. That's why the run command is `uvicorn main:app`, not `uvicorn app.main:app`. Everything else stays inside the `app/` package and is imported with absolute paths (e.g. `from app.database import Base`), which works as long as you run uvicorn from the `backend/` folder.

## Setup

### Backend
```
cd backend
pip install -r requirements.txt
copy .env.example .env
```
Edit `.env` with your PostgreSQL connection string (`DATABASE_URL`), then:
```
uvicorn main:app --reload --port 8000
```
API runs at `http://localhost:8000` (interactive docs at `/docs`).

> This version has **no `venv`** — dependencies install to your global Python. That's fine as a single-project setup; if you ever work on a second Python project on this machine with conflicting package versions, revisit isolating with `python -m venv venv`.

### Frontend
```
cd frontend
npm install
copy .env.example .env
npm run dev
```
Runs at `http://localhost:5173`.

> Make sure `backend/.env`'s `FRONTEND_ORIGIN` matches whatever port Vite actually starts on (check the terminal output — if 5173 is already taken by a leftover process, Vite silently moves to 5174, and CORS will then reject requests until you update `FRONTEND_ORIGIN` and restart uvicorn).

## Styling
Tailwind CSS throughout — no MUI, no separate per-component stylesheets. Shared patterns (`.btn-primary`, `.btn-outline`, `.form-input`, `.form-label`, `.form-error-text`) live in `src/index.css` under `@layer components`; everything else uses Tailwind utility classes directly in JSX.

## Requirement → implementation map

| Requirement | Where |
|---|---|
| Company registration creates company + admin | `app/routers/auth.py::register_company` |
| JWT access + refresh tokens | `app/security.py`; refresh tokens are opaque, DB-stored, rotated on every refresh |
| Role-based access | `UserRole` enum + `app/dependencies.py::require_roles([...])` |
| Company isolation | `app/dependencies.py::get_current_company_id` — required on every future company-scoped route |
| Validations | `app/schemas.py` (Pydantic) + DB unique constraints |
| Audit logging | `app/audit.py::log_action`, called on register/login/logout |
| User profile | `GET /auth/me` |

## Verified working
Company registration, login, JWT + refresh rotation, PostgreSQL tables, and audit log entries have all been confirmed working end-to-end.

## Not yet implemented
- Real password-reset flow (placeholder screen only)
- Alembic migrations (currently relies on `Base.metadata.create_all()`, which won't alter existing tables — fine for now, revisit before your first schema change against real data)
