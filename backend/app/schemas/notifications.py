from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.models import NotificationType


class NotificationProductRef(BaseModel):
    id: str
    name: str
    sku: str

    class Config:
        from_attributes = True


class NotificationCustomerRef(BaseModel):
    id: str
    customer_code: str
    full_name: str

    class Config:
        from_attributes = True


class NotificationOut(BaseModel):
    id: str
    type: NotificationType
    message: str
    is_read: bool
    product: Optional[NotificationProductRef] = None
    customer: Optional[NotificationCustomerRef] = None
    created_at: datetime

    class Config:
        from_attributes = True
