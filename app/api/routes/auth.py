# Updated auth.py with the duplicate /me/balance route removed

from fastapi import APIRouter, Depends, HTTPException, status, Request, Security
from sqlalchemy.orm import Session
from typing import Dict, Any
from fastapi.security import OAuth2PasswordBearer
from app.core.database import get_db
from app.core.auth import get_current_user, verify_webhook_signature
from app.models.balance import UserBalance

from pydantic import BaseModel

router = APIRouter()

class WebhookEvent(BaseModel):
    data: Dict[str, Any]
    object: str
    type: str

@router.post("/webhook")
async def webhook_handler(request: Request, event: WebhookEvent, db: Session = Depends(get_db)):
    """Handle Clerk webhooks for user events."""
    # Verify the webhook signature
    if not verify_webhook_signature(request):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")
    
    # Process different event types
    if event.type == "user.created":
        user_id = event.data.get("id")
        if user_id:
            # Create a new balance record for the user
            balance = UserBalance(
                user_id=user_id,
                pages_balance=10,  # Default starting balance
                pages_used=0
            )
            db.add(balance)
            db.commit()
            return {"success": True, "message": "User balance created"}
            
    elif event.type == "user.deleted":
        user_id = event.data.get("id")
        if user_id:
            # Delete user balance
            db.query(UserBalance).filter(UserBalance.user_id == user_id).delete()
            db.commit()
            return {"success": True, "message": "User balance deleted"}
    
    # Return success for other events we don't need to process
    return {"success": True, "message": "Event received"}

# The /me/balance route has been removed from here since it's in balance.py
# This resolves the route conflict

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/token")

def get_current_user(token: str = Security(oauth2_scheme)):
    """
    Retrieve the current user from the authentication token.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        user_id: str = payload.get("sub")

        if user_id is None:
            raise HTTPException(status_code=401, detail="User ID not found in token")

        return user_id  # ✅ Return the user ID

    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")