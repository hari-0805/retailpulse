export type UserRole = "SUPER_ADMIN" | "COMPANY_ADMIN" | "ANALYST" | "VIEWER";
export type UserStatus = "ACTIVE" | "INACTIVE" | "SUSPENDED";

export interface Company {
  id: string;
  name: string;
  industry?: string | null;
  email: string;
  address?: string | null;
  phone?: string | null;
  created_at: string;
}

export interface User {
  id: string;
  name: string;
  email: string;
  role: UserRole;
  status: UserStatus;
  last_login?: string | null;
  created_at: string;
  company: Company;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export interface RegisterResponse {
  company: Company;
  user: User;
  tokens: TokenResponse;
}

export interface CompanyRegisterPayload {
  company_name: string;
  industry?: string;
  company_email: string;
  company_address?: string;
  company_phone?: string;
  owner_name: string;
  owner_email: string;
  password: string;
  confirm_password: string;
}

export interface LoginPayload {
  email: string;
  password: string;
}
