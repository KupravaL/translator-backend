from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from app.models.balance import UserBalance
from typing import Dict, Any
import math
import logging
from app.core.config import settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] [Balance] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("balance")

class BalanceService:
    @staticmethod
    def get_user_balance(db: Session, user_id: str) -> UserBalance:
        """Get user balance or create if not exists."""
        try:
            balance = db.query(UserBalance).filter(UserBalance.user_id == user_id).first()
            
            if not balance:
                logger.info(f"Creating new balance for user: {user_id}")
                # Create default balance
                balance = UserBalance(
                    user_id=user_id,
                    pages_balance=settings.DEFAULT_BALANCE_PAGES,
                    pages_used=0
                )
                db.add(balance)
                db.commit()
                db.refresh(balance)
            
            return balance
        except SQLAlchemyError as e:
            db.rollback()
            logger.error(f"Database error in get_user_balance: {str(e)}")
            # Create a temporary balance object without persisting to DB
            # This ensures the app doesn't crash but will retry DB operations later
            return UserBalance(
                user_id=user_id,
                pages_balance=settings.DEFAULT_BALANCE_PAGES,
                pages_used=0
            )
    
    @staticmethod
    def calculate_required_pages(content) -> int:
        """
        Calculate required pages based on content.
        
        Args:
            content: Can be either a string (for text content) or an integer/float (for estimated characters)
            
        Returns:
            int: Number of pages required
        """
        # Use a larger character count per page to reduce cost
        chars_per_page = 3000  # Increased from 1500 to 3000
        
        # Handle different input types
        if isinstance(content, str):
            # If content is a string, use its length
            content_length = len(content)
        elif isinstance(content, (int, float)):
            # If content is a number, use it directly as the character count
            content_length = content
        else:
            # For any other type, default to 1 page
            logger.warning(f"Unexpected content type in calculate_required_pages: {type(content)}")
            return 1
            
        required_pages = max(1, math.ceil(content_length / chars_per_page))
        return required_pages
    
    @staticmethod
    def check_balance_for_content(db: Session, user_id: str, content: str) -> Dict[str, Any]:
        """Check if user has enough balance for given content."""
        try:
            balance = BalanceService.get_user_balance(db, user_id)
            required_pages = BalanceService.calculate_required_pages(content)
            
            return {
                "hasBalance": balance.pages_balance >= required_pages,
                "availablePages": balance.pages_balance,
                "requiredPages": required_pages
            }
        except Exception as e:
            logger.error(f"Error checking balance for content: {str(e)}")
            # Default to no balance to prevent over-usage in case of errors
            return {
                "hasBalance": False,
                "availablePages": 0,
                "requiredPages": 1,
                "error": str(e)
            }
    
    @staticmethod
    def check_balance_for_pages(db: Session, user_id: str, pages: int) -> Dict[str, Any]:
        """Check if user has enough balance for given number of pages."""
        try:
            balance = BalanceService.get_user_balance(db, user_id)
            
            return {
                "hasBalance": balance.pages_balance >= pages,
                "availablePages": balance.pages_balance,
                "requiredPages": pages
            }
        except Exception as e:
            logger.error(f"Error checking balance for pages: {str(e)}")
            # Default to no balance to prevent over-usage in case of errors
            return {
                "hasBalance": False,
                "availablePages": 0,
                "requiredPages": pages,
                "error": str(e)
            }
    
    @staticmethod
    def deduct_pages_for_translation(db: Session, user_id: str, content: str) -> Dict[str, Any]:
        """Deduct pages from user balance after translation."""
        try:
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
            
            logger.info(f"Deducted {deducted_pages} pages from user {user_id}, new balance: {balance.pages_balance}")
            
            return {
                "success": True,
                "deductedPages": deducted_pages,
                "remainingBalance": balance.pages_balance
            }
        except SQLAlchemyError as e:
            db.rollback()
            logger.error(f"Database error in deduct_pages_for_translation: {str(e)}")
            return {
                "success": False,
                "error": f"Database error: {str(e)}",
                "remainingBalance": 0
            }
        except Exception as e:
            logger.error(f"Error in deduct_pages_for_translation: {str(e)}")
            return {
                "success": False,
                "error": f"Failed to deduct pages: {str(e)}",
                "remainingBalance": 0
            }
    
    @staticmethod
    def add_pages_to_balance(db: Session, user_id: str, pages: int) -> Dict[str, Any]:
        """Add pages to user balance."""
        try:
            balance = BalanceService.get_user_balance(db, user_id)
            
            # Update balance
            balance.pages_balance += pages
            db.commit()
            db.refresh(balance)
            
            logger.info(f"Added {pages} pages to user {user_id}, new balance: {balance.pages_balance}")
            
            return {
                "success": True,
                "addedPages": pages,
                "newBalance": balance.pages_balance
            }
        except SQLAlchemyError as e:
            db.rollback()
            logger.error(f"Database error in add_pages_to_balance: {str(e)}")
            return {
                "success": False,
                "error": f"Database error: {str(e)}",
                "newBalance": 0
            }
        except Exception as e:
            logger.error(f"Error in add_pages_to_balance: {str(e)}")
            return {
                "success": False,
                "error": f"Failed to add pages: {str(e)}",
                "newBalance": 0
            }
    
    @staticmethod
    def refund_pages_for_failed_translation(db: Session, user_id: str, pages: int) -> Dict[str, Any]:
        """
        Refund pages to user balance for a failed translation.
        This method should be called when a translation fails after pages were deducted.
        """
        try:
            balance = BalanceService.get_user_balance(db, user_id)
            
            # Update balance - add back the pages and reduce the usage count
            balance.pages_balance += pages
            balance.pages_used = max(0, balance.pages_used - pages)  # Prevent negative usage
            db.commit()
            db.refresh(balance)
            
            logger.info(f"Refunded {pages} pages to user {user_id}, new balance: {balance.pages_balance}")
            
            return {
                "success": True,
                "refundedPages": pages,
                "newBalance": balance.pages_balance
            }
        except SQLAlchemyError as e:
            db.rollback()
            logger.error(f"Database error in refund_pages_for_failed_translation: {str(e)}")
            return {
                "success": False,
                "error": f"Database error: {str(e)}",
                "newBalance": 0
            }
        except Exception as e:
            logger.error(f"Error in refund_pages_for_failed_translation: {str(e)}")
            return {
                "success": False,
                "error": f"Failed to refund pages: {str(e)}",
                "newBalance": 0
            }

balance_service = BalanceService()