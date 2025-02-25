#!/usr/bin/env python3
"""
Payment confirmation script.
This script allows administrators to confirm payments from the command line.

Usage:
  python -m scripts.confirm_payment --order-id=ORDER_ID [--reject]
"""

import argparse
import sys
import os
from sqlalchemy.orm import Session
from typing import Optional

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.database import get_db, SessionLocal
from app.models.payment import Payment
from app.services.balance import balance_service


def confirm_payment(order_id: str, reject: bool = False) -> None:
    """
    Confirm or reject a payment.
    
    Args:
        order_id: The order ID to confirm
        reject: If True, reject the payment instead of confirming
    """
    db: Session = SessionLocal()
    
    try:
        # Find the payment
        payment = db.query(Payment).filter(Payment.orderId == order_id).first()
        
        if not payment:
            print(f"Error: No payment found with order ID {order_id}")
            return
        
        # Check if payment is already processed
        if payment.status != "PENDING":
            status_text = "confirmed" if payment.status == "COMPLETED" else "rejected"
            print(f"Payment {order_id} has already been {status_text}")
            print(f"Current status: {payment.status}")
            return
        
        # Process the payment
        if reject:
            # Reject the payment
            payment.status = "REJECTED"
            db.commit()
            print(f"Payment {order_id} has been rejected")
        else:
            # Confirm the payment and add pages to the user's balance
            payment.status = "COMPLETED"
            db.commit()
            
            # Add pages to the user's balance
            result = balance_service.add_pages_to_balance(db, payment.userId, payment.pages)
            
            print(f"Payment {order_id} has been confirmed")
            print(f"Added {payment.pages} pages to user {payment.userId}")
            print(f"New balance: {result['newBalance']} pages")
    
    except Exception as e:
        db.rollback()
        print(f"Error: {str(e)}")
    
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Confirm or reject a payment")
    parser.add_argument("--order-id", required=True, help="Payment order ID to confirm")
    parser.add_argument("--reject", action="store_true", help="Reject the payment instead of confirming")
    
    args = parser.parse_args()
    
    confirm_payment(args.order_id, args.reject)


if __name__ == "__main__":
    main()