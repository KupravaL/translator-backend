from sqlalchemy.orm import Session
from app.models.balance import UserBalance
from typing import Dict, Any
import math
from app.core.config import settings

class BalanceService:
    @staticmethod
    def get_user_balance(db: Session, user_id: str) -> UserBalance:
        """Get user balance or create if not exists."""
        balance = db.query(UserBalance).filter(UserBalance.user_id == user_id).first()
        
        if not balance:
            # Create default balance
            balance = UserBalance(
                user_id=user_id,
                pages_balance=100,  # Increased from 10 to 100 for testing 
                pages_used=0
            )
            db.add(balance)
            db.commit()
            db.refresh(balance)
        
        return balance
    
    @staticmethod
    def calculate_required_pages(content: str) -> int:
        """Calculate required pages based on content length."""
        # Use a larger character count per page to reduce cost
        chars_per_page = 3000  # Increased from 1500 to 3000
        required_pages = max(1, math.ceil(len(content) / chars_per_page))
        return required_pages
    
    @staticmethod
    def check_balance_for_content(db: Session, user_id: str, content: str) -> Dict[str, Any]:
        """Check if user has enough balance for given content."""
        balance = BalanceService.get_user_balance(db, user_id)
        required_pages = BalanceService.calculate_required_pages(content)
        
        return {
            "hasBalance": balance.pages_balance >= required_pages,
            "availablePages": balance.pages_balance,
            "requiredPages": required_pages
        }
    
    @staticmethod
    def check_balance_for_pages(db: Session, user_id: str, pages: int) -> Dict[str, Any]:
        """Check if user has enough balance for given number of pages."""
        balance = BalanceService.get_user_balance(db, user_id)
        
        return {
            "hasBalance": balance.pages_balance >= pages,
            "availablePages": balance.pages_balance,
            "requiredPages": pages
        }
    
    @staticmethod
    def deduct_pages_for_translation(db: Session, user_id: str, content: str) -> Dict[str, Any]:
        """Deduct pages from user balance after translation."""
        balance = BalanceService.get_user_balance(db, user_id)
        deducted_pages = BalanceService.calculate_required_pages(content)
        
        if balance.pages_balance < deducted_pages:
            return {
                "success": False,
                "error": f"Insufficient balance. Required: {deducted_pages} pages, Available: {balance.pages_balance} pages",
                "remainingBalance": balance.pages_balance
            }
        
        # Update balance
        balance.pages_balance -= deducted_pages
        balance.pages_used += deducted_pages
        db.commit()
        db.refresh(balance)
        
        return {
            "success": True,
            "deductedPages": deducted_pages,
            "remainingBalance": balance.pages_balance
        }
    
    @staticmethod
    def add_pages_to_balance(db: Session, user_id: str, pages: int) -> Dict[str, Any]:
        """Add pages to user balance."""
        balance = BalanceService.get_user_balance(db, user_id)
        
        # Update balance
        balance.pages_balance += pages
        db.commit()
        db.refresh(balance)
        
        return {
            "success": True,
            "addedPages": pages,
            "newBalance": balance.pages_balance
        }

balance_service = BalanceService()