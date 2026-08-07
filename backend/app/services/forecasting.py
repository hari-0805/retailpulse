from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    Sale, SaleItem, Product, ProductStatus, Category, Inventory,
    DemandForecast, ForecastHistory, ForecastPeriod, RecommendationType,
    Notification, NotificationType,
)

PERIOD_DAYS = {
    ForecastPeriod.NEXT_7_DAYS: 7,
    ForecastPeriod.NEXT_30_DAYS: 30,
    ForecastPeriod.NEXT_90_DAYS: 90,
}

# How far back we look for the "recent" and "prior" trend windows, used to
# derive a simple growth rate (a lightweight stand-in for a full regression).
TREND_WINDOW_DAYS = 30
# How much historical data we require before we'll even attempt a forecast.
MIN_HISTORY_DAYS = 14
GROWTH_NOTIFICATION_THRESHOLD = Decimal("25")  # % growth that counts as "significant"


def _daily_sales(db: Session, company_id: str, product_id: str, start: datetime, end: datetime) -> int:
    total = db.query(func.coalesce(func.sum(SaleItem.quantity), 0)).join(
        Sale, SaleItem.sale_id == Sale.id
    ).filter(
        Sale.company_id == company_id, SaleItem.product_id == product_id,
        Sale.sale_date >= start, Sale.sale_date < end,
    ).scalar()
    return int(total or 0)


def _compute_recommendation(current_stock: int, reorder_level: int, predicted_demand: int) -> RecommendationType:
    if current_stock <= 0:
        return RecommendationType.IMMEDIATE_RESTOCK_REQUIRED
    if current_stock < predicted_demand and current_stock <= reorder_level:
        return RecommendationType.IMMEDIATE_RESTOCK_REQUIRED
    if current_stock <= reorder_level:
        return RecommendationType.REORDER_SOON
    if current_stock > predicted_demand * 2 and current_stock > reorder_level * 3:
        return RecommendationType.OVERSTOCK_RISK
    return RecommendationType.STOCK_HEALTHY


def _forecast_one_product(
    db: Session, company_id: str, product: Product, period: ForecastPeriod,
    period_start: datetime, period_end: datetime, user_id: Optional[str],
) -> Optional[DemandForecast]:
    horizon_days = (period_end - period_start).days or 1

    history_start = period_start - timedelta(days=max(TREND_WINDOW_DAYS * 2, MIN_HISTORY_DAYS))
    total_history = _daily_sales(db, company_id, product.id, history_start, period_start)
    if total_history <= 0:
        return None  # no historical sales data -> nothing to forecast from

    recent_start = period_start - timedelta(days=TREND_WINDOW_DAYS)
    prior_start = period_start - timedelta(days=TREND_WINDOW_DAYS * 2)
    recent_sales = _daily_sales(db, company_id, product.id, recent_start, period_start)
    prior_sales = _daily_sales(db, company_id, product.id, prior_start, recent_start)

    avg_daily_recent = recent_sales / TREND_WINDOW_DAYS
    if prior_sales > 0:
        growth_rate = (recent_sales - prior_sales) / prior_sales
    else:
        growth_rate = 0.5 if recent_sales > 0 else 0.0
    growth_rate = max(-0.5, min(growth_rate, 1.0))  # clamp to a sane range

    predicted_demand = max(0, round(avg_daily_recent * horizon_days * (1 + growth_rate)))

    weeks_span = max((datetime.utcnow() - history_start).days // 7, 1)
    weeks_with_sales = db.query(func.count(func.distinct(
        func.date_trunc("week", Sale.sale_date)
    ))).join(SaleItem, SaleItem.sale_id == Sale.id).filter(
        Sale.company_id == company_id, SaleItem.product_id == product.id,
        Sale.sale_date >= history_start, Sale.sale_date < period_start,
    ).scalar() or 0
    confidence = min(Decimal("0.95"), Decimal("0.30") + Decimal("0.60") * Decimal(min(weeks_with_sales, 13)) / Decimal(13))

    inventory = db.query(Inventory).filter(
        Inventory.company_id == company_id, Inventory.product_id == product.id
    ).first()
    current_stock = inventory.available_stock if inventory else product.stock_quantity
    reorder_level = inventory.reorder_level if inventory else 10
    recommendation = _compute_recommendation(current_stock, reorder_level, predicted_demand)

    existing_query = db.query(DemandForecast).filter(
        DemandForecast.company_id == company_id, DemandForecast.product_id == product.id,
        DemandForecast.forecast_period == period,
    )
    if period == ForecastPeriod.CUSTOM:
        existing_query = existing_query.filter(DemandForecast.period_start == period_start)
    existing = existing_query.first()

    # Log accuracy for the previous forecast if its window has already elapsed.
    if existing and existing.period_end <= datetime.utcnow():
        actual = _daily_sales(db, company_id, product.id, existing.period_start, existing.period_end)
        denom = max(actual, existing.predicted_demand, 1)
        accuracy = Decimal(1) - (Decimal(abs(actual - existing.predicted_demand)) / Decimal(denom))
        accuracy = max(Decimal(0), min(accuracy, Decimal(1)))
        db.add(ForecastHistory(
            forecast_id=existing.id, historical_sales=actual,
            prediction=existing.predicted_demand, accuracy=accuracy,
        ))

    growth_pct = Decimal(growth_rate * 100).quantize(Decimal("0.01"))

    if existing:
        forecast = existing
        forecast.period_start = period_start
        forecast.period_end = period_end
        forecast.historical_sales = total_history
        forecast.predicted_demand = predicted_demand
        forecast.confidence_score = confidence
        forecast.expected_growth_percentage = growth_pct
        forecast.current_stock = current_stock
        forecast.reorder_level = reorder_level
        forecast.recommendation = recommendation
        forecast.generated_by = user_id
        forecast.generated_at = datetime.utcnow()
    else:
        forecast = DemandForecast(
            company_id=company_id, product_id=product.id, category_id=product.category_id,
            forecast_period=period, period_start=period_start, period_end=period_end,
            historical_sales=total_history, predicted_demand=predicted_demand,
            confidence_score=confidence, expected_growth_percentage=growth_pct,
            current_stock=current_stock, reorder_level=reorder_level,
            recommendation=recommendation, generated_by=user_id,
        )
        db.add(forecast)

    db.flush()
    _maybe_notify(db, company_id, product, forecast)
    return forecast


def _maybe_notify(db: Session, company_id: str, product: Product, forecast: DemandForecast):
    if forecast.recommendation == RecommendationType.IMMEDIATE_RESTOCK_REQUIRED:
        db.add(Notification(
            company_id=company_id, product_id=product.id,
            type=NotificationType.FORECAST_STOCK_RUNOUT,
            message=f"{product.name} is predicted to run out of stock before the forecast period ends.",
        ))
    if forecast.predicted_demand > (forecast.current_stock or 0):
        db.add(Notification(
            company_id=company_id, product_id=product.id,
            type=NotificationType.FORECAST_DEMAND_EXCEEDS_STOCK,
            message=f"Forecasted demand for {product.name} ({forecast.predicted_demand}) exceeds available stock ({forecast.current_stock or 0}).",
        ))
    if forecast.expected_growth_percentage >= GROWTH_NOTIFICATION_THRESHOLD:
        db.add(Notification(
            company_id=company_id, product_id=product.id,
            type=NotificationType.FORECAST_DEMAND_GROWTH,
            message=f"{product.name} shows {forecast.expected_growth_percentage}% demand growth in the latest forecast.",
        ))


def _resolve_period_window(period: ForecastPeriod, custom_start=None, custom_end=None):
    now = datetime.utcnow()
    if period == ForecastPeriod.CUSTOM:
        if not custom_start or not custom_end:
            raise ValueError("period_start and period_end are required for a CUSTOM forecast")
        start = datetime.combine(custom_start, datetime.min.time())
        end = datetime.combine(custom_end, datetime.max.time())
        if end <= start:
            raise ValueError("period_end must be after period_start")
        return start, end
    return now, now + timedelta(days=PERIOD_DAYS[period])


def generate_forecasts(
    db: Session, company_id: str, period: ForecastPeriod, user_id: Optional[str],
    category_id: Optional[str] = None, custom_start=None, custom_end=None,
) -> dict:
    period_start, period_end = _resolve_period_window(period, custom_start, custom_end)

    query = db.query(Product).filter(Product.company_id == company_id, Product.status == ProductStatus.ACTIVE)
    if category_id:
        query = query.filter(Product.category_id == category_id)
    products = query.all()

    forecasted = 0
    skipped = 0
    touched_category_ids = set()

    for product in products:
        result = _forecast_one_product(db, company_id, product, period, period_start, period_end, user_id)
        if result:
            forecasted += 1
            touched_category_ids.add(product.category_id)
        else:
            skipped += 1

    categories_forecasted = 0
    for category_id_ in touched_category_ids:
        if _forecast_category(db, company_id, category_id_, period, period_start, period_end, user_id):
            categories_forecasted += 1

    db.commit()
    return {
        "products_forecasted": forecasted,
        "categories_forecasted": categories_forecasted,
        "skipped_no_history": skipped,
    }


def _forecast_category(
    db: Session, company_id: str, category_id: str, period: ForecastPeriod,
    period_start: datetime, period_end: datetime, user_id: Optional[str],
) -> bool:
    product_rows = db.query(DemandForecast).filter(
        DemandForecast.company_id == company_id, DemandForecast.category_id == category_id,
        DemandForecast.product_id.isnot(None), DemandForecast.forecast_period == period,
        DemandForecast.period_start == period_start,
    ).all()
    if not product_rows:
        return False

    total_historical = sum(r.historical_sales for r in product_rows)
    total_predicted = sum(r.predicted_demand for r in product_rows)
    avg_growth = sum(r.expected_growth_percentage for r in product_rows) / len(product_rows)

    existing_query = db.query(DemandForecast).filter(
        DemandForecast.company_id == company_id, DemandForecast.category_id == category_id,
        DemandForecast.product_id.is_(None), DemandForecast.forecast_period == period,
    )
    if period == ForecastPeriod.CUSTOM:
        existing_query = existing_query.filter(DemandForecast.period_start == period_start)
    existing = existing_query.first()

    if existing:
        existing.period_start = period_start
        existing.period_end = period_end
        existing.historical_sales = total_historical
        existing.predicted_demand = total_predicted
        existing.expected_growth_percentage = avg_growth
        existing.generated_by = user_id
        existing.generated_at = datetime.utcnow()
    else:
        db.add(DemandForecast(
            company_id=company_id, product_id=None, category_id=category_id,
            forecast_period=period, period_start=period_start, period_end=period_end,
            historical_sales=total_historical, predicted_demand=total_predicted,
            confidence_score=Decimal("0"), expected_growth_percentage=avg_growth,
            generated_by=user_id,
        ))
    db.flush()
    return True
