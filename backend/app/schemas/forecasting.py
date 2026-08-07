from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field

from app.models import ForecastPeriod, RecommendationType


class ForecastGenerateRequest(BaseModel):
    forecast_period: ForecastPeriod
    period_start: Optional[date] = None   # required when forecast_period == CUSTOM
    period_end: Optional[date] = None     # required when forecast_period == CUSTOM
    category_id: Optional[str] = None     # limit generation to one category; omit for all


class ProductForecastRow(BaseModel):
    forecast_id: str
    product_id: str
    product_name: str
    sku: str
    brand: Optional[str] = None
    category_id: str
    category_name: str
    current_stock: int
    historical_sales: int
    predicted_demand: int
    forecast_period: ForecastPeriod
    period_start: datetime
    period_end: datetime
    confidence_score: Decimal
    expected_growth_percentage: Decimal
    recommendation: RecommendationType
    generated_at: datetime


class ProductForecastListResponse(BaseModel):
    items: list[ProductForecastRow]
    total: int


class CategoryForecastRow(BaseModel):
    forecast_id: str
    category_id: str
    category_name: str
    total_historical_sales: int
    predicted_demand: int
    expected_growth_percentage: Decimal
    generated_at: datetime


class CategoryForecastListResponse(BaseModel):
    items: list[CategoryForecastRow]


class ForecastAccuracyPoint(BaseModel):
    period: str
    historical_sales: int
    prediction: int
    accuracy: Decimal


class ForecastGenerateResponse(BaseModel):
    products_forecasted: int
    categories_forecasted: int
    skipped_no_history: int


# ---------- Analytics dashboard ----------

class ForecastKPIs(BaseModel):
    total_predicted_demand: int
    products_expected_to_run_out: int
    high_growth_products: int
    slow_moving_products: int
    forecast_accuracy: Decimal   # average accuracy (0-1) across forecast_history entries


class HistoricalVsForecastPoint(BaseModel):
    period: str
    historical_sales: int
    predicted_demand: int


class ProductDemandTrendPoint(BaseModel):
    period: str
    predicted_demand: int


class CategoryDemandTrendRow(BaseModel):
    category_name: str
    predicted_demand: int
    historical_sales: int


class TopPredictedProductRow(BaseModel):
    product_name: str
    predicted_demand: int


class SeasonalPatternPoint(BaseModel):
    month: str
    total_sales: int


class ForecastAnalyticsSummary(BaseModel):
    kpis: ForecastKPIs
    historical_vs_forecast: list[HistoricalVsForecastPoint]
    product_demand_trend: list[ProductDemandTrendPoint]
    category_demand_trend: list[CategoryDemandTrendRow]
    top_predicted_products: list[TopPredictedProductRow]
    seasonal_pattern: list[SeasonalPatternPoint]


class RecommendationRow(BaseModel):
    forecast_id: str
    product_id: str
    product_name: str
    sku: str
    category_name: str
    current_stock: int
    reorder_level: Optional[int] = None
    predicted_demand: int
    recommendation: RecommendationType
