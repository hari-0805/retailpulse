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
  stock_quantity: number;
  unit_of_measure: string;
  category: { id: string; name: string };
}


// ---------- Sales Types ----------

export type SalesChannel = "RETAIL_STORE" | "ONLINE_STORE" | "MARKETPLACE";
export type PaymentMethod = "CASH" | "CARD" | "UPI" | "BANK_TRANSFER";
export type PaymentStatus = "PENDING" | "PAID" | "PARTIALLY_PAID" | "REFUNDED";

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
  customer_id?: string | null;
  sale_date: string;
  sales_channel: SalesChannel;
  payment_method: PaymentMethod;
  payment_status: PaymentStatus;
  notes?: string | null;
  total_amount: number;
  items: SaleItem[];
  creator?: { id: string; name: string } | null;
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
  customer_id?: string;
  clear_customer?: boolean;
  sale_date?: string;
  sales_channel: SalesChannel;
  payment_method: PaymentMethod;
  payment_status?: PaymentStatus;
  notes?: string;
  items: SaleItemPayload[];
}

export interface SaleListItem {
  id: string;
  invoice_number: string;
  customer_name: string;
  sale_date: string;
  sales_channel: SalesChannel;
  payment_method: PaymentMethod;
  payment_status: PaymentStatus;
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
  product_id?: string;
  brand?: string;
  sales_channel?: SalesChannel;
  payment_method?: PaymentMethod;
  payment_status?: PaymentStatus;
  sort_by?: "date" | "invoice" | "total" | "customer";
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

export type StockStatus = "IN_STOCK" | "LOW_STOCK" | "OUT_OF_STOCK";

export type AdjustmentType = "STOCK_IN" | "STOCK_OUT" | "MANUAL_ADJUSTMENT";

export type AdjustmentDirection = "INCREASE" | "DECREASE";

export type MovementType = "SALE" | "MANUAL_ADJUSTMENT" | "STOCK_ADDITION" | "STOCK_REMOVAL";

export interface InventoryProduct {
  id: string;
  name: string;
  sku: string;
  brand?: string | null;
  unit_of_measure: string;
  category_id: string;
  category_name: string;
}

export interface InventoryItem {
  id: string;
  product: InventoryProduct;
  current_stock: number;
  reserved_stock: number;
  available_stock: number;
  reorder_level: number;
  stock_status: StockStatus;
  updated_at: string;
}

export interface InventoryListResponse {
  items: InventoryItem[];
  total: number;
}

export interface InventoryListParams {
  search?: string;
  category_id?: string;
  brand?: string;
  status?: StockStatus | "";
  sort_by: "name" | "stock" | "updated";
  sort_dir: "asc" | "desc";
  page: number;
  page_size: number;
}

export interface StockAdjustmentPayload {
  adjustment_type: AdjustmentType;
  direction?: AdjustmentDirection;
  quantity: number;
  reason: string;
  remarks?: string;
}

export interface MovementPerformer {
  id: string;
  name: string;
}

export interface Movement {
  id: string;
  movement_type: MovementType;
  quantity_changed: number;
  previous_quantity: number;
  updated_quantity: number;
  reason: string;
  remarks?: string | null;
  performed_by?: MovementPerformer | null;
  created_at: string;
}

export interface MovementListResponse {
  items: Movement[];
  total: number;
}

export interface CategoryBreakdown {
  category: string;
  quantity: number;
}

export interface StatusBreakdown {
  status: StockStatus;
  count: number;
}

export interface InventoryDashboardSummary {
  total_products: number;
  total_quantity: number;
  low_stock_count: number;
  out_of_stock_count: number;
  by_category: CategoryBreakdown[];
  by_status: StatusBreakdown[];
}

export interface AnalyticsFilters {
  date_from?: string;
  date_to?: string;
  category_id?: string;
  product_id?: string;
  brand?: string;
  sales_channel?: SalesChannel | "";
  payment_method?: PaymentMethod | "";
  customer_id?: string;
  granularity?: "daily" | "weekly" | "monthly";
}

export interface AnalyticsKPIs {
  total_revenue: number;
  total_orders: number;
  total_products_sold: number;
  average_order_value: number;
  total_discount: number;
  total_tax: number;
  total_inventory_value: number;
  low_stock_products: number;
  out_of_stock_products: number;
  total_categories: number;
}

export interface CustomerRevenueRow {
  customer_id: string | null;
  customer_name: string;
  orders: number;
  total_spend: number;
  average_order_value: number;
}

export interface RevenueTrendPoint {
  period: string;
  revenue: number;
  orders: number;
}

export interface TopProductRow {
  product_id: string;
  product_name: string;
  sku: string;
  quantity_sold: number;
  revenue: number;
}

export interface TopCategoryRow {
  category_id: string;
  category_name: string;
  revenue: number;
  quantity_sold: number;
}

export interface PaymentMethodBreakdown {
  payment_method: string;
  revenue: number;
  orders: number;
}

export interface SalesChannelBreakdown {
  sales_channel: string;
  revenue: number;
  orders: number;
}

export interface InventoryCategoryBreakdown {
  category_id: string;
  category_name: string;
  quantity: number;
  value: number;
}

export interface InventoryStatusBreakdown {
  status: string;
  count: number;
}

export interface LowStockRow {
  product_id: string;
  product_name: string;
  sku: string;
  available_stock: number;
  reorder_level: number;
}

export interface OutOfStockRow {
  product_id: string;
  product_name: string;
  sku: string;
  updated_at: string;
}

export interface AnalyticsSummary {
  kpis: AnalyticsKPIs;
  revenue_trend: RevenueTrendPoint[];
  top_products: TopProductRow[];
  top_categories: TopCategoryRow[];
  by_payment_method: PaymentMethodBreakdown[];
  by_sales_channel: SalesChannelBreakdown[];
  customer_revenue: CustomerRevenueRow[];
  inventory_by_category: InventoryCategoryBreakdown[];
  inventory_status_summary: InventoryStatusBreakdown[];
  top_low_stock: LowStockRow[];
  out_of_stock: OutOfStockRow[];
  inventory_value_by_category: InventoryCategoryBreakdown[];
}

// ---------- Task 6: Customers ----------

export type CustomerType = "RETAIL" | "WHOLESALE" | "CORPORATE";
export type CustomerStatus = "ACTIVE" | "INACTIVE";
export type CustomerGender = "MALE" | "FEMALE" | "OTHER" | "UNSPECIFIED";
export type CustomerSegment = "NEW" | "REGULAR" | "LOYAL" | "VIP";
export type CustomerActivityType =
  | "REGISTERED" | "PROFILE_UPDATED" | "FIRST_PURCHASE" | "LARGE_PURCHASE"
  | "DEACTIVATED" | "REACTIVATED" | "SEGMENT_CHANGED";

export interface CustomerProductRef { id: string; name: string; sku: string; }
export interface CustomerCategoryRef { id: string; name: string; }

export interface CustomerPurchaseSummary {
  total_orders: number;
  total_revenue: number;
  total_quantity: number;
  average_order_value: number;
  first_purchase_date: string | null;
  last_purchase_date: string | null;
  favorite_product: CustomerProductRef | null;
  favorite_category: CustomerCategoryRef | null;
}

export interface Customer {
  id: string;
  customer_code: string;
  first_name: string;
  last_name: string;
  full_name: string;
  email: string;
  phone: string;
  date_of_birth: string | null;
  gender: CustomerGender | null;
  address: string | null;
  city: string | null;
  state: string | null;
  country: string | null;
  postal_code: string | null;
  customer_type: CustomerType;
  preferred_channel: string | null;
  status: CustomerStatus;
  segment: CustomerSegment;
  purchase_summary: CustomerPurchaseSummary | null;
  created_at: string;
  updated_at: string;
}

export interface CustomerListItem {
  id: string;
  customer_code: string;
  full_name: string;
  email: string;
  phone: string;
  city: string | null;
  state: string | null;
  country: string | null;
  customer_type: CustomerType;
  status: CustomerStatus;
  segment: CustomerSegment;
  total_orders: number;
  total_revenue: number;
  last_purchase_date: string | null;
  created_at: string;
}

export interface CustomerListResponse {
  items: CustomerListItem[];
  total: number;
}

export interface CustomerListParams {
  search?: string;
  customer_type?: CustomerType | "";
  status?: CustomerStatus | "";
  city?: string;
  state?: string;
  country?: string;
  registered_from?: string;
  registered_to?: string;
  sort_by: "name" | "total_spend" | "total_orders" | "last_purchase" | "customer_since";
  sort_dir: "asc" | "desc";
  page: number;
  page_size: number;
}

export interface CustomerPayload {
  first_name: string;
  last_name: string;
  email: string;
  phone: string;
  date_of_birth?: string | null;
  gender?: CustomerGender | null;
  address?: string | null;
  city?: string | null;
  state?: string | null;
  country?: string | null;
  postal_code?: string | null;
  customer_type: CustomerType;
  preferred_channel?: string | null;
  status?: CustomerStatus;
}

export interface CustomerActivityEntry {
  id: string;
  activity_type: CustomerActivityType;
  description: string;
  created_at: string;
}

export interface CustomerRecentSale {
  id: string;
  invoice_number: string;
  sale_date: string;
  total_amount: number;
  item_count: number;
}

export interface CustomerProfile {
  customer: Customer;
  recent_activities: CustomerActivityEntry[];
  recent_transactions: CustomerRecentSale[];
}

export interface CustomerPurchasesResponse {
  items: CustomerRecentSale[];
  total: number;
}

export interface CustomerAnalyticsKPIs {
  total_customers: number;
  active_customers: number;
  new_customers: number;
  returning_customers: number;
  average_customer_spend: number;
  total_revenue_generated: number;
  average_purchase_frequency: number;
}

export interface CustomerGrowthPoint { period: string; new_customers: number; total_customers: number; }
export interface NewVsReturningPoint { period: string; new_customers: number; returning_customers: number; }
export interface RevenueByTypeRow { customer_type: CustomerType; revenue: number; customer_count: number; }
export interface TopCustomerRow { customer_id: string; customer_code: string; full_name: string; revenue: number; total_orders: number; }
export interface PurchaseFrequencyBucket { bucket: string; customer_count: number; }
export interface LocationRow { location: string; customer_count: number; }
export interface MonthlyAcquisitionPoint { period: string; new_customers: number; }
export interface SpendingDistributionBucket { bucket: string; customer_count: number; }
export interface SegmentBreakdown { segment: CustomerSegment; customer_count: number; }

export interface CustomerAnalyticsSummary {
  kpis: CustomerAnalyticsKPIs;
  growth_trend: CustomerGrowthPoint[];
  new_vs_returning: NewVsReturningPoint[];
  revenue_by_type: RevenueByTypeRow[];
  top_customers: TopCustomerRow[];
  purchase_frequency: PurchaseFrequencyBucket[];
  location_distribution: LocationRow[];
  monthly_acquisition: MonthlyAcquisitionPoint[];
  spending_distribution: SpendingDistributionBucket[];
  segment_breakdown: SegmentBreakdown[];
}

// ---------- Task 7: Demand Forecasting ----------

export type ForecastPeriod = "NEXT_7_DAYS" | "NEXT_30_DAYS" | "NEXT_90_DAYS" | "CUSTOM";
export type RecommendationType = "REORDER_SOON" | "OVERSTOCK_RISK" | "STOCK_HEALTHY" | "IMMEDIATE_RESTOCK_REQUIRED";

export interface ProductForecastRow {
  forecast_id: string;
  product_id: string;
  product_name: string;
  sku: string;
  brand: string | null;
  category_id: string;
  category_name: string;
  current_stock: number;
  historical_sales: number;
  predicted_demand: number;
  forecast_period: ForecastPeriod;
  period_start: string;
  period_end: string;
  confidence_score: number;
  expected_growth_percentage: number;
  recommendation: RecommendationType;
  generated_at: string;
}

export interface ProductForecastListResponse {
  items: ProductForecastRow[];
  total: number;
}

export interface CategoryForecastRow {
  forecast_id: string;
  category_id: string;
  category_name: string;
  total_historical_sales: number;
  predicted_demand: number;
  expected_growth_percentage: number;
  generated_at: string;
}

export interface ForecastGenerateResponse {
  products_forecasted: number;
  categories_forecasted: number;
  skipped_no_history: number;
}

export interface ForecastKPIs {
  total_predicted_demand: number;
  products_expected_to_run_out: number;
  high_growth_products: number;
  slow_moving_products: number;
  forecast_accuracy: number;
}

export interface HistoricalVsForecastPoint { period: string; historical_sales: number; predicted_demand: number; }
export interface ProductDemandTrendPoint { period: string; predicted_demand: number; }
export interface CategoryDemandTrendRow { category_name: string; predicted_demand: number; historical_sales: number; }
export interface TopPredictedProductRow { product_name: string; predicted_demand: number; }
export interface SeasonalPatternPoint { month: string; total_sales: number; }

export interface ForecastAnalyticsSummary {
  kpis: ForecastKPIs;
  historical_vs_forecast: HistoricalVsForecastPoint[];
  product_demand_trend: ProductDemandTrendPoint[];
  category_demand_trend: CategoryDemandTrendRow[];
  top_predicted_products: TopPredictedProductRow[];
  seasonal_pattern: SeasonalPatternPoint[];
}

export interface RecommendationRow {
  forecast_id: string;
  product_id: string;
  product_name: string;
  sku: string;
  category_name: string;
  current_stock: number;
  reorder_level: number | null;
  predicted_demand: number;
  recommendation: RecommendationType;
}

export interface ForecastProductListParams {
  forecast_period: ForecastPeriod;
  search?: string;
  category_id?: string;
  brand?: string;
  recommendation?: RecommendationType | "";
  sort_by: "predicted_demand" | "lowest_stock" | "growth" | "accuracy";
  sort_dir: "asc" | "desc";
  page: number;
  page_size: number;
}
