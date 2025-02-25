import json
import jwt
import requests
from datetime import datetime
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.balance import UserBalance
from jwt import PyJWKClient

# HTTP Bearer security scheme
security = HTTPBearer()

# Cache for Clerk's JWKS (JSON Web Key Set)
jwks_cache = {
    'keys': None,
    'last_updated': None
}

def get_jwks():
    """Fetch and cache Clerk's JWKS keys for token verification."""
    now = datetime.now()

    # Use cached keys if available and not expired
    if jwks_cache['keys'] and jwks_cache['last_updated'] and (now - jwks_cache['last_updated']).total_seconds() < 86400:
        return jwks_cache['keys']

    # Fetch JWKS keys from Clerk
    jwks_url = "https://model-walrus-20.clerk.accounts.dev/.well-known/jwks.json"
    response = requests.get(jwks_url)
    response.raise_for_status()

    # Update cache
    jwks_cache['keys'] = response.json().get('keys', [])
    jwks_cache['last_updated'] = now

    return jwks_cache['keys']

jwks_url = "https://model-walrus-20.clerk.accounts.dev/.well-known/jwks.json"
jwks_client = PyJWKClient(jwks_url)

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security), db: Session = Depends(get_db)):
    """Validate Clerk JWT token and return the user_id."""
    token = credentials.credentials
    print(f"Received Token: {token[:20]}...{token[-10:]}")  # ✅ Debugging (partial token for security)

    try:
        # ✅ Fetch the signing key dynamically
        signing_key = jwks_client.get_signing_key_from_jwt(token).key

        # ✅ Verify the JWT signature (without `aud` and `iss`)
        payload = jwt.decode(token, signing_key, algorithms=["RS256"])

        print(f"Decoded Payload: {payload}")  # ✅ Debugging

        # Ensure `sub` field exists
        user_id = payload.get("sub")
        if not user_id or not user_id.startswith("user_"):
            print(f"ERROR: Invalid subject in token: {user_id}")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid subject in token")

        # ✅ Check if user has a balance record, create one if missing
        balance = db.query(UserBalance).filter(UserBalance.user_id == user_id).first()
        if not balance:
            print(f"Creating new balance record for user: {user_id}")
            balance = UserBalance(user_id=user_id, pages_balance=10, pages_used=0)
            db.add(balance)
            db.commit()

        return user_id  # ✅ Return user ID

    except jwt.PyJWTError as e:
        error_msg = str(e)
        print(f"JWT ERROR: {error_msg}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {error_msg}")
    except Exception as e:
        error_msg = str(e)
        print(f"AUTH ERROR: {error_msg}")
        
        # Check for specific error types and provide more helpful messages
        if "JWK" in error_msg or "signing key" in error_msg:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, 
                               detail="Token signature verification failed - check Clerk configuration")
        if "expired" in error_msg:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, 
                               detail="Token has expired - please login again")
                               
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