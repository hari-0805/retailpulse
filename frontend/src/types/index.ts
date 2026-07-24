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


// ---------- Catalog Types ----------

export type ProductStatus = "ACTIVE" | "INACTIVE";

export interface Category {
  id: string;
  name: string;
  description?: string | null;
  status: "ACTIVE" | "INACTIVE";
  product_count?: number;
  created_at: string;
  updated_at: string;
}

export interface CategoryPayload {
  name: string;
  description?: string;
  status?: "ACTIVE" | "INACTIVE";
}

export interface Product {
  id: string;
  name: string;
  sku: string;
  brand?: string | null;
  description?: string | null;
  unit_price: number;
  cost_price: number;
  stock_quantity: number;
  unit_of_measure: string;
  status: ProductStatus;
  category: { id: string; name: string };
  created_at: string;
  updated_at: string;
  low_stock_threshold: number;
  is_out_of_stock: boolean;
}

export interface ProductPayload {
  name: string;
  sku: string;
  category_id: string;
  brand?: string;
  description?: string;
  unit_price: number;
  cost_price: number;
  stock_quantity: number;
  unit_of_measure: string;
  status?: ProductStatus;
  low_stock_threshold?: number;
}

export interface ProductListResponse {
  items: Product[];
  total: number;
}

export interface ProductListParams {
  search?: string;
  category_id?: string;
  status?: ProductStatus;
  sort_by?: "name" | "price" | "recent";
  sort_dir?: "asc" | "desc";
  page?: number;
  page_size?: number;
}

export interface ProductOption {
  id: string;
  name: string;
  sku: string;
  unit_price: number;
  unit_of_measure: string;
  category: { id: string; name: string };
}


// ---------- Sales Types ----------

export type SalesChannel = "RETAIL_STORE" | "ONLINE_STORE" | "MARKETPLACE";
export type PaymentMethod = "CASH" | "CARD" | "UPI" | "BANK_TRANSFER";

export interface SaleItem {
  id: string;
  sale_id: string;
  product_id: string;
  category_id: string;
  quantity: number;
  unit_price: number;
  discount: number;
  tax: number;
  total: number;
  product: { id: string; name: string; sku: string };
  category: { id: string; name: string };
}

export interface Sale {
  id: string;
  invoice_number: string;
  customer_name: string;
  sale_date: string;
  sales_channel: SalesChannel;
  payment_method: PaymentMethod;
  total_amount: number;
  items: SaleItem[];
  created_by?: string;
  created_at: string;
  updated_at: string;
}

export interface SaleItemPayload {
  product_id: string;
  quantity: number;
  unit_price: number;
  discount: number;
  tax: number;
}

export interface SalePayload {
  customer_name: string;
  sale_date?: string;
  sales_channel: SalesChannel;
  payment_method: PaymentMethod;
  items: SaleItemPayload[];
}

export interface SaleListItem {
  id: string;
  invoice_number: string;
  customer_name: string;
  sale_date: string;
  sales_channel: SalesChannel;
  payment_method: PaymentMethod;
  total_amount: number;
  item_count: number;
}

export interface SaleListResponse {
  items: SaleListItem[];
  total: number;
}

export interface SaleListParams {
  search?: string;
  date_from?: string;
  date_to?: string;
  category_id?: string;
  sales_channel?: SalesChannel;
  payment_method?: PaymentMethod;
  sort_by?: "date" | "invoice" | "total";
  sort_dir?: "asc" | "desc";
  page?: number;
  page_size?: number;
}

export interface SalesDashboardSummary {
  total_sales: number;
  total_revenue: number;
  total_orders: number;
  average_order_value: number;
}


// ---------- Notification Types ----------

export interface AppNotification {
  id: string;
  company_id: string;
  product_id?: string | null;
  type: "LOW_STOCK" | "OUT_OF_STOCK" | "MANUAL_ADJUSTMENT";
  message: string;
  is_read: boolean;
  created_at: string;
}

export interface DashboardSummary {
  total_products: number;
  active_products: number;
  inactive_products: number;
  total_categories: number;
}


// ---------- Inventory Types ----------

export interface Inventory {
  id: string;
  company_id: string;
  product_id: string;
  current_stock: number;
  reserved_stock: number;
  available_stock: number;
  reorder_level: number;
  stock_status: "In Stock" | "Low Stock" | "Out of Stock";
  updated_at: string;
  product: {
    id: string;
    name: string;
    sku: string;
    brand?: string | null;
    unit_of_measure: string;
    category: { id: string; name: string };
  };
}

export interface InventoryListResponse {
  items: Inventory[];
  total: number;
}

export interface StockAdjustmentPayload {
  quantity: number;
  adjustment_type: "Stock Addition" | "Stock Removal" | "Manual Adjustment";
  reason: string;
  remarks?: string;
}

export interface InventoryMovement {
  id: string;
  inventory_id: string;
  movement_type: "Sale" | "Manual Adjustment" | "Stock Addition" | "Stock Removal";
  quantity_changed: number;
  previous_quantity: number;
  updated_quantity: number;
  reason: string;
  remarks?: string | null;
  performer?: {
    id: string;
    name: string;
    email: string;
  } | null;
  created_at: string;
}

export interface CategoryStockSummary {
  category_name: string;
  total_stock: number;
}

export interface StockStatusSummary {
  status: string;
  count: number;
}

export interface InventoryDashboardSummary {
  total_products: number;
  total_inventory_quantity: number;
  low_stock_products: number;
  out_of_stock_products: number;
  category_summary: CategoryStockSummary[];
  status_summary: StockStatusSummary[];
}
