import enum
from datetime import datetime

from sqlalchemy import (
    Column, String, DateTime, ForeignKey, Enum as SAEnum, Numeric, Integer,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.base import gen_uuid


class ForecastPeriod(str, enum.Enum):
    NEXT_7_DAYS = "NEXT_7_DAYS"
    NEXT_30_DAYS = "NEXT_30_DAYS"
    NEXT_90_DAYS = "NEXT_90_DAYS"
    CUSTOM = "CUSTOM"


class RecommendationType(str, enum.Enum):
    REORDER_SOON = "REORDER_SOON"
    OVERSTOCK_RISK = "OVERSTOCK_RISK"
    STOCK_HEALTHY = "STOCK_HEALTHY"
    IMMEDIATE_RESTOCK_REQUIRED = "IMMEDIATE_RESTOCK_REQUIRED"


class DemandForecast(Base):
    """
    A product-level row has both product_id and category_id set.
    A category-level (aggregate) row has product_id NULL and only
    category_id set. Re-generating a forecast for the same
    product/category + forecast_period updates this row in place
    (see app/services/forecasting.py) rather than creating a duplicate.
    """
    __tablename__ = "demand_forecasts"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    company_id = Column(UUID(as_uuid=False), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(UUID(as_uuid=False), ForeignKey("products.id", ondelete="CASCADE"), nullable=True, index=True)
    category_id = Column(UUID(as_uuid=False), ForeignKey("categories.id", ondelete="CASCADE"), nullable=True, index=True)

    forecast_period = Column(SAEnum(ForecastPeriod), nullable=False)
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)

    historical_sales = Column(Integer, nullable=False, default=0)
    predicted_demand = Column(Integer, nullable=False, default=0)
    confidence_score = Column(Numeric(5, 4), nullable=False, default=0)  # 0.0000 - 1.0000
    expected_growth_percentage = Column(Numeric(7, 2), nullable=False, default=0)

    # Product-level snapshot fields, used to compute the recommendation
    # without re-hitting the Inventory table on every read.
    current_stock = Column(Integer, nullable=True)
    reorder_level = Column(Integer, nullable=True)
    recommendation = Column(SAEnum(RecommendationType), nullable=True)

    generated_by = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    generated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    product = relationship("Product")
    category = relationship("Category")
    generator = relationship("User")
    history_entries = relationship("ForecastHistory", back_populates="forecast", cascade="all, delete-orphan")


class ForecastHistory(Base):
    """
    Accuracy audit trail. When a forecast is refreshed and its previous
    target period has fully elapsed, we compare what actually sold during
    that window against what was predicted and log the result here.
    """
    __tablename__ = "forecast_history"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    forecast_id = Column(UUID(as_uuid=False), ForeignKey("demand_forecasts.id", ondelete="CASCADE"), nullable=False, index=True)
    historical_sales = Column(Integer, nullable=False)   # actual sales observed during the forecasted window
    prediction = Column(Integer, nullable=False)         # what had been predicted for that window
    accuracy = Column(Numeric(5, 4), nullable=False)     # 0.0000 - 1.0000
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    forecast = relationship("DemandForecast", back_populates="history_entries")
