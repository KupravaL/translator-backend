from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.balance import UserBalance

router = APIRouter()

@router.get("/balance/me/balance")
async def get_my_balance(db: Session = Depends(get_db)):
    user_id = "user123"  # TODO: Replace with actual user ID from auth
    balance = db.query(UserBalance).filter(UserBalance.user_id == user_id).first()
    if not balance:
        raise HTTPException(status_code=404, detail="User balance not found")
    return {"user_id": user_id, "balance": balance.pages_balance} 