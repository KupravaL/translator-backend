from sqlalchemy import Column, String, Integer, DateTime
from sqlalchemy.sql import func
from app.core.database import Base

class UserBalance(Base):
    __tablename__ = "user_balances"

    user_id = Column(String, primary_key=True, nullable=False)  # ✅ Fixed primary key
    pages_balance = Column(Integer, default=10, nullable=False)  # ✅ Fixed column names
    pages_used = Column(Integer, default=0, nullable=False)
    last_used = Column(DateTime, server_default=func.now(), onupdate=func.now())
    created_at = Column(DateTime, server_default=func.now())

    def __repr__(self):
        return f"<UserBalance(user_id={self.user_id}, pages_balance={self.pages_balance})>"
