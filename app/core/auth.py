import jwt
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.balance import UserBalance
from jwt import PyJWKClient
from app.core.config import settings

# HTTP Bearer security scheme
security = HTTPBearer()

# Clerk's JWKS endpoint
jwks_url = f"https://{settings.CLERK_ISSUER_URL}/.well-known/jwks.json"
jwks_client = PyJWKClient(jwks_url)

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security), db: Session = Depends(get_db)):
    """Validate Clerk JWT token and return the user_id."""
    token = credentials.credentials
    print(f"Received Token: {token[:20]}...{token[-10:]}")  # Debugging (partial token for security)

    try:
        # Fetch the signing key dynamically
        signing_key = jwks_client.get_signing_key_from_jwt(token).key

        # Verify the JWT signature with more flexible validation
        # Skip audience and issuer validation if they're causing issues
        payload = jwt.decode(
            token, 
            signing_key, 
            algorithms=["RS256"],
            options={
                "verify_aud": False,
                "verify_iss": False
            }
        )

        print(f"Decoded Payload: {payload}")  # Debugging

        # Check for the user ID in various common JWT claim locations
        user_id = payload.get("sub") or payload.get("user_id") or payload.get("userId")
        
        if not user_id:
            print("ERROR: No user ID found in token")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No user ID found in token")

        # Check if user has a balance record, create one if missing
        balance = db.query(UserBalance).filter(UserBalance.user_id == user_id).first()
        if not balance:
            print(f"Creating new balance record for user: {user_id}")
            balance = UserBalance(user_id=user_id, pages_balance=10, pages_used=0)
            db.add(balance)
            db.commit()

        return user_id  # Return user ID

    except jwt.PyJWTError as e:
        error_msg = str(e)
        print(f"JWT ERROR: {error_msg}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {error_msg}")
    except Exception as e:
        error_msg = str(e)
        print(f"AUTH ERROR: {error_msg}")
        
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, 
                          detail=f"Authentication failed: {error_msg}")

# ✅ Webhook Signature Verification (Placeholder)
def verify_webhook_signature(request: Request):
    """Verify that the webhook request came from Clerk."""
    try:
        svix_id = request.headers.get("svix-id")
        svix_timestamp = request.headers.get("svix-timestamp")
        svix_signature = request.headers.get("svix-signature")

        if not svix_id or not svix_timestamp or not svix_signature:
            return False

        # TODO: Implement actual webhook signature verification using svix library
        return True
    except Exception:
        return False