import logging
from typing import Optional, List, Dict, Any
import os
import json
import requests
from app.core.config import settings

logger = logging.getLogger(__name__)

class EmailService:
    """
    Service for handling email operations using Resend API directly.
    """
    
    def __init__(self):
        self.sender = "notifications@docs.ucraft.io"
        self.reply_to = "info@docs.ucraft.io"
        self.admin_email = "info@ucraft.io"
        self.api_key = settings.RESEND_API_KEY
        self.api_url = "https://api.resend.com/emails"
        
    def send_payment_notification(
        self,
        to_email: str,
        order_id: str,
        amount: float,
        pages: int,
        user_id: Optional[str] = None,
        user_name: Optional[str] = None
    ) -> bool:
        """
        Send payment notification email to customer and admin.
        
        Args:
            to_email: Customer email address
            order_id: Unique order ID
            amount: Payment amount
            pages: Number of pages purchased
            user_id: Optional user ID
            user_name: Optional user name
            
        Returns:
            bool: True if emails were sent successfully, False otherwise
        """
        try:
            # 1. Send notification to the customer
            customer_success = self._send_customer_notification(
                to_email=to_email,
                order_id=order_id,
                amount=amount,
                pages=pages,
                user_id=user_id,
                user_name=user_name
            )
            
            # 2. Send notification to the admin
            admin_success = self._send_admin_notification(
                customer_email=to_email,
                order_id=order_id,
                amount=amount,
                pages=pages,
                user_id=user_id,
                user_name=user_name
            )
            
            return customer_success and admin_success
            
        except Exception as e:
            logger.error(f"Failed to send payment notifications: {str(e)}")
            return False
    
    def _send_email(self, to: str, subject: str, html_content: str, reply_to: Optional[str] = None) -> bool:
        """
        Generic method to send an email using the Resend API directly.
        """
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "from": self.sender,
                "to": to,
                "subject": subject,
                "html": html_content
            }
            
            if reply_to:
                payload["reply_to"] = reply_to
                
            response = requests.post(
                self.api_url,
                headers=headers,
                json=payload
            )
            
            if response.status_code == 200:
                response_data = response.json()
                logger.info(f"Email sent to {to}, id: {response_data.get('id', 'unknown')}")
                return True
            else:
                logger.error(f"Failed to send email: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error sending email: {str(e)}")
            return False
    
    def _send_customer_notification(
        self,
        to_email: str,
        order_id: str,
        amount: float,
        pages: int,
        user_id: Optional[str] = None,
        user_name: Optional[str] = None
    ) -> bool:
        """Send payment notification to the customer."""
        try:
            subject = f"Your Translation Order #{order_id}"
            
            # Create HTML email content for customer
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                <div style="background-color: #f8f9fa; padding: 20px; border-radius: 10px;">
                    <h1 style="color: #1a73e8; margin-bottom: 20px;">Your Translation Order</h1>
                    
                    <div style="background-color: white; padding: 15px; border-radius: 5px; margin-bottom: 20px;">
                        <p><strong>Order ID:</strong> {order_id}</p>
                        <p><strong>Pages:</strong> {pages}</p>
                        <p><strong>Amount:</strong> {amount} GEL</p>
                    </div>
                    
                    <div style="background-color: #e8f5e9; padding: 15px; border-radius: 5px;">
                        <p><strong>Payment Instructions</strong></p>
                        <p><strong>Bank Account:</strong></p>
                        <p style="font-family: monospace; background-color: white; padding: 10px; border-radius: 4px;">
                            GE33BG0000000549780308
                        </p>
                        <p style="margin-top: 10px;"><strong>Order Reference:</strong> {order_id}</p>
                    </div>
                    
                    <div style="margin-top: 20px; padding: 15px; border-radius: 5px; background-color: #f5f5f5;">
                        <p>Once your payment is processed, the pages will be added to your balance automatically.</p>
                        <p>If you have any questions, please reply to this email.</p>
                    </div>
                </div>
            </div>
            """
            
            return self._send_email(
                to=to_email,
                subject=subject,
                html_content=html_content,
                reply_to=self.reply_to
            )
            
        except Exception as e:
            logger.error(f"Failed to send customer email: {str(e)}")
            return False
    
    def _send_admin_notification(
        self,
        customer_email: str,
        order_id: str,
        amount: float,
        pages: int,
        user_id: Optional[str] = None,
        user_name: Optional[str] = None
    ) -> bool:
        """Send payment notification to the admin with approval links."""
        try:
            subject = f"New Translation Order #{order_id} - Admin Action Required"
            
            # Add confirmation link (this would typically be a link to your admin panel)
            confirmation_base_url = f"{settings.API_BASE_URL}/api/balance/admin/confirm"
            confirm_url = f"{confirmation_base_url}?order_id={order_id}"
            
            # Create HTML email content for admin with approval buttons
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                <div style="background-color: #f8f9fa; padding: 20px; border-radius: 10px;">
                    <h1 style="color: #1a73e8; margin-bottom: 20px;">New Translation Order - ACTION REQUIRED</h1>
                    
                    <div style="background-color: white; padding: 15px; border-radius: 5px; margin-bottom: 20px;">
                        <p><strong>Order ID:</strong> {order_id}</p>
                        <p><strong>Pages:</strong> {pages}</p>
                        <p><strong>Amount:</strong> {amount} GEL</p>
                    </div>
                    
                    <div style="background-color: white; padding: 15px; border-radius: 5px; margin-bottom: 20px;">
                        <p><strong>Customer:</strong> {user_name or "Customer"}</p>
                        <p><strong>Email:</strong> {customer_email}</p>
                        {f"<p><strong>Customer ID:</strong> {user_id}</p>" if user_id else ""}
                    </div>
                    
                    <div style="background-color: #e8f5e9; padding: 15px; border-radius: 5px; margin-bottom: 20px;">
                        <p><strong>Bank Account:</strong></p>
                        <p style="font-family: monospace; background-color: white; padding: 10px; border-radius: 4px;">
                            GE33BG0000000549780308
                        </p>
                        <p style="margin-top: 10px;"><strong>Order Reference:</strong> {order_id}</p>
                    </div>
                    
                    <div style="background-color: #ffecb3; padding: 15px; border-radius: 5px; margin-bottom: 20px;">
                        <p><strong>Admin Action Required</strong></p>
                        <p>Once payment is received, click the button below to confirm and add pages to the user's balance:</p>
                        <a href="{confirm_url}&action=confirm" style="display: inline-block; background-color: #4CAF50; color: white; padding: 10px 20px; margin: 10px 0; text-decoration: none; border-radius: 4px;">
                            Confirm Payment
                        </a>
                        <p>If you need to reject this payment, use this link:</p>
                        <a href="{confirm_url}&action=reject" style="display: inline-block; background-color: #f44336; color: white; padding: 10px 20px; margin: 10px 0; text-decoration: none; border-radius: 4px;">
                            Reject Payment
                        </a>
                    </div>
                    
                    <div style="font-size: 12px; color: #666; margin-top: 20px;">
                        <p>You can also use the following command in the backend to confirm this payment:</p>
                        <code style="background: #f5f5f5; padding: 5px; display: block;">
                            python -m scripts.confirm_payment --order-id={order_id}
                        </code>
                    </div>
                </div>
            </div>
            """
            
            return self._send_email(
                to=self.admin_email,
                subject=subject,
                html_content=html_content,
                reply_to=customer_email
            )
            
        except Exception as e:
            logger.error(f"Failed to send admin email: {str(e)}")
            return False

# Create a singleton instance
email_service = EmailService()