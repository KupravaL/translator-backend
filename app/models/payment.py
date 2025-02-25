from sqlalchemy import Column, String, Integer, Float, DateTime, ForeignKey, Index
from sqlalchemy.sql import func
from sqlalchemy.ext.declarative import declared_attr
from app.core.database import Base
import uuid

def generate_cuid():
    """Generate a cuid-like ID."""
    return str(uuid.uuid4())

class Payment(Base):
    __tablename__ = "payments"

    id = Column(String, primary_key=True, default=generate_cuid)
    userId = Column(String, index=True, nullable=False)
    orderId = Column(String, unique=True, index=True, nullable=False)
    amount = Column(Float, nullable=False)
    pages = Column(Integer, nullable=False)
    status = Column(String, nullable=False)  # PENDING, COMPLETED, CANCELLED
    createdAt = Column(DateTime, server_default=func.now())
    updatedAt = Column(DateTime, server_default=func.now(), onupdate=func.now())

    @declared_attr
    def __table_args__(cls):
        return (
            Index(f"ix_{cls.__tablename__}_user_id", "userId"),
            Index(f"ix_{cls.__tablename__}_order_id", "orderId"),
        )