from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Request, Header, Response
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
import time
import uuid
import os
from datetime import datetime
import jwt
import logging
from app.core.database import get_db
from app.core.auth import get_current_user, security, jwks_client
from app.models.payment import Payment
from app.services.balance import balance_service
from app.services.email_service import email_service

logger = logging.getLogger("api")

router = APIRouter()

class BalanceResponse(BaseModel):
    userId: str
    pagesBalance: int
    pagesUsed: int
    lastUsed: Optional[str]  # ✅ Fix: Allow `None` if not set

@router.get("/me/balance", response_model=BalanceResponse)
async def get_balance(
    response: Response,
    request: Request,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get user balance information.
    Enhanced with better auth error handling.
    """
    try:
        # Track the request start time
        start_time = time.time()
        
        # Check for auth-related headers from client
        is_retry = request.headers.get("X-Balance-Retry", "false") == "true"
        if is_retry:
            logger.info(f"Balance retry request after auth issue for user: {current_user}")
        
        if not current_user:
            logger.warning("Balance request without valid authentication")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required"
            )
            
        # Log the current user for debugging
        logger.info(f"Getting balance for user: {current_user}")
        
        # Get user balance
        balance = balance_service.get_user_balance(db, current_user)
        
        # Check if this is the default balance
        is_default = getattr(balance, 'is_default', False)
        if is_default:
            logger.warning(f"Returning default balance for user: {current_user}")
            response.headers["X-Default-Balance"] = "true"
        
        # Calculate time remaining for token if available
        try:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header.replace("Bearer ", "")
                _, time_remaining = get_token_expiration(token)
                if time_remaining is not None:
                    # Add token expiration info to response
                    if time_remaining < 300:  # Less than 5 minutes
                        response.headers["X-Token-Expiring-Soon"] = "true"
                    response.headers["X-Token-Expires-In"] = str(int(time_remaining))
        except Exception as token_error:
            logger.error(f"Error checking token expiration: {token_error}")
        
        # Add timing information
        duration = round((time.time() - start_time) * 1000)
        response.headers["X-Request-Duration"] = str(duration)
        logger.info(f"Balance retrieved in {duration}ms for user: {current_user}")
        
        # Return balance information
        return {
            "userId": balance.user_id,
            "pagesBalance": balance.pages_balance,
            "pagesUsed": balance.pages_used,
            "lastUsed": balance.last_used.isoformat() if balance.last_used else None
        }
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        # Log the error
        logger.error(f"Error getting balance: {str(e)}")
        
        # Return a proper error response
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve balance: {str(e)}"
        )
    
@router.get("/debug/balance")
async def debug_balance(
    request: Request,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    """Debug endpoint for balance check - logs auth headers and returns default balance."""
    try:
        # Log headers for debugging
        print("DEBUG - Request headers:")
        for header, value in request.headers.items():
            print(f"  {header}: {value}")
        
        # Try to extract user ID from token if present
        user_id = None
        if authorization and authorization.startswith("Bearer "):
            token = authorization.replace("Bearer ", "")
            try:
                signing_key = jwks_client.get_signing_key_from_jwt(token).key
                payload = jwt.decode(token, signing_key, algorithms=["RS256"])
                user_id = payload.get("sub")
                print(f"DEBUG - Extracted user_id: {user_id}")
            except Exception as e:
                print(f"DEBUG - Token validation error: {str(e)}")
        
        # Return debug info and default balance
        if user_id:
            # Get real balance if we have a user ID
            balance = balance_service.get_user_balance(db, user_id)
            return {
                "debug": True,
                "authenticated": True,
                "userId": balance.user_id,
                "pagesBalance": balance.pages_balance,
                "pagesUsed": balance.pages_used,
                "lastUsed": balance.last_used.isoformat() if balance.last_used else None
            }
        else:
            # Return dummy balance for unauthenticated requests
            return {
                "debug": True,
                "authenticated": False,
                "message": "No valid authentication found",
                "userId": "anonymous",
                "pagesBalance": 0,
                "pagesUsed": 0
            }
            
    except Exception as e:
        return {
            "debug": True,
            "error": str(e),
            "authenticated": False
        }
        
@router.get("/public/balance")
async def public_balance(
    user_id: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Public endpoint for balance check with optional user_id parameter.
    This can be used by the frontend when authentication isn't working properly.
    """
    try:
        if user_id and user_id.startswith("user_"):
            # Get real balance if we have a valid user ID
            balance = balance_service.get_user_balance(db, user_id)
            return {
                "userId": balance.user_id,
                "pagesBalance": balance.pages_balance,
                "pagesUsed": balance.pages_used,
                "lastUsed": balance.last_used.isoformat() if balance.last_used else None
            }
        else:
            # Return default balance for new users
            return {
                "userId": "anonymous",
                "pagesBalance": 10,
                "pagesUsed": 0,
                "lastUsed": None
            }
    except Exception as e:
        print(f"Public balance error: {str(e)}")
        return {
            "userId": "anonymous",
            "pagesBalance": 10,
            "pagesUsed": 0,
            "lastUsed": None,
            "error": str(e)
        }

class AddPagesRequest(BaseModel):
    pages: int
    paymentId: Optional[str] = None

@router.post("/add-pages")
async def add_pages(
    request: AddPagesRequest,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Add pages to user balance."""
    if request.pages <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pages must be greater than 0"
        )
    
    result = balance_service.add_pages_to_balance(db, current_user, request.pages)
    
    # ✅ Fix: Validate `paymentId` before creating payment entry
    if request.paymentId:
        existing_payment = db.query(Payment).filter(Payment.id == request.paymentId).first()
        if existing_payment:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Payment already exists"
            )

        payment = Payment(
            userId=current_user,
            orderId=request.paymentId,
            amount=request.pages * 0.1,  # $0.10 per page
            pages=request.pages,
            status="COMPLETED"
        )
        db.add(payment)
        db.commit()

    return {
        "success": result["success"],
        "addedPages": result["addedPages"],
        "newBalance": result["newBalance"]
    }

class CalculateRequiredPagesRequest(BaseModel):
    content: str

@router.post("/calculate-required")
async def calculate_required_pages(
    request: CalculateRequiredPagesRequest,
    current_user: str = Depends(get_current_user)
):
    """Calculate required pages for content."""
    required_pages = balance_service.calculate_required_pages(request.content)
    
    return {
        "requiredPages": required_pages
    }

class PagesPurchaseRequest(BaseModel):
    pages: int
    email: Optional[str] = None

@router.post("/purchase/pages")
async def purchase_pages(
    request: PagesPurchaseRequest,
    background_tasks: BackgroundTasks,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a payment request for purchasing multiple pages."""
    try:
        # Validate request
        if request.pages < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Number of pages must be at least 1"
            )
            
        PRICE_PER_PAGE = 1  # 1 GEL per page
        pages = request.pages
        amount = pages * PRICE_PER_PAGE
        
        # Generate a unique order ID
        timestamp = int(time.time())
        random_suffix = uuid.uuid4().hex[:8]
        order_id = f"ORD-{timestamp}-{random_suffix}"
        
        # Create payment record
        payment = Payment(
            userId=current_user,
            pages=pages,
            amount=amount,
            status="PENDING",
            orderId=order_id
        )
        db.add(payment)
        db.commit()
        db.refresh(payment)
        
        # Get the user's email from the request or use a default
        email_to = request.email or "customer@example.com"
        
        # Send email notification in the background
        background_tasks.add_task(
            email_service.send_payment_notification,
            to_email=email_to,
            order_id=order_id,
            amount=amount,
            pages=pages,
            user_id=current_user
        )
        
        return {
            "success": True,
            "payment": {
                "orderId": payment.orderId,
                "amount": amount,
                "pages": pages,
                "bankAccount": "GE33BG0000000549780308"
            }
        }
    
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process payment: {str(e)}"
        )

# Keeping the single page endpoint for backward compatibility
class SinglePagePurchaseRequest(BaseModel):
    email: Optional[str] = None

@router.post("/purchase/single-page")
async def purchase_single_page(
    request: SinglePagePurchaseRequest,
    background_tasks: BackgroundTasks,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a payment request for a single page purchase (legacy endpoint)."""
    # Reuse the multi-page endpoint with pages=1
    pages_request = PagesPurchaseRequest(pages=1, email=request.email)
    return await purchase_pages(
        request=pages_request,
        background_tasks=background_tasks,
        current_user=current_user,
        db=db
    )

@router.get("/admin/confirm")
async def admin_confirm_payment(
    order_id: str,
    action: str = "confirm",
    db: Session = Depends(get_db)
):
    """
    Admin endpoint for confirming or rejecting payments.
    This endpoint is called from the admin email links.
    """
    try:
        # Find the payment by order ID
        payment = db.query(Payment).filter(Payment.orderId == order_id).first()
        
        if not payment:
            return Response(
                content=f"""
                <html>
                    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                        <div style="background-color: #ffebee; padding: 20px; border-radius: 10px;">
                            <h1>Error: Payment Not Found</h1>
                            <p>Order ID: {order_id}</p>
                            <p>No payment record was found with this order ID.</p>
                        </div>
                    </body>
                </html>
                """,
                media_type="text/html"
            )
        
        # Check if payment is already processed
        if payment.status != "PENDING":
            status_text = "Confirmed" if payment.status == "COMPLETED" else "Rejected"
            return Response(
                content=f"""
                <html>
                    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                        <div style="background-color: #e0f7fa; padding: 20px; border-radius: 10px;">
                            <h1>Payment Already Processed</h1>
                            <p>Order ID: {payment.orderId}</p>
                            <p>This payment has already been {status_text.lower()}.</p>
                            <p>Current status: {payment.status}</p>
                        </div>
                    </body>
                </html>
                """,
                media_type="text/html"
            )
        
        # Process the confirmation or rejection
        if action.lower() == "confirm":
            # Update payment status
            payment.status = "COMPLETED"
            db.commit()
            
            # Add pages to user balance
            result = balance_service.add_pages_to_balance(db, payment.userId, payment.pages)
            
            return Response(
                content=f"""
                <html>
                    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                        <div style="background-color: #e8f5e9; padding: 20px; border-radius: 10px;">
                            <h1>Payment Confirmed Successfully</h1>
                            <p>Order ID: {payment.orderId}</p>
                            <p>Added {payment.pages} pages to user {payment.userId}'s balance.</p>
                            <p>New balance: {result["newBalance"]} pages</p>
                        </div>
                    </body>
                </html>
                """,
                media_type="text/html"
            )
        elif action.lower() == "reject":
            # Update payment status
            payment.status = "REJECTED"
            db.commit()
            
            return Response(
                content=f"""
                <html>
                    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                        <div style="background-color: #fff8e1; padding: 20px; border-radius: 10px;">
                            <h1>Payment Rejected</h1>
                            <p>Order ID: {payment.orderId}</p>
                            <p>The payment for {payment.pages} pages has been marked as rejected.</p>
                        </div>
                    </body>
                </html>
                """,
                media_type="text/html"
            )
        else:
            return Response(
                content=f"""
                <html>
                    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                        <div style="background-color: #ffebee; padding: 20px; border-radius: 10px;">
                            <h1>Invalid Action</h1>
                            <p>The action '{action}' is not recognized.</p>
                            <p>Valid actions are 'confirm' or 'reject'.</p>
                        </div>
                    </body>
                </html>
                """,
                media_type="text/html"
            )
            
    except Exception as e:
        return Response(
            content=f"""
            <html>
                <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                    <div style="background-color: #ffebee; padding: 20px; border-radius: 10px;">
                        <h1>Error Processing Payment</h1>
                        <p>An error occurred while processing the payment confirmation:</p>
                        <p>{str(e)}</p>
                    </div>
                </body>
            </html>
            """,
            media_type="text/html"
        )