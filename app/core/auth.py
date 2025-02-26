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

from app.core.config import settings

# HTTP Bearer security scheme
security = HTTPBearer(auto_error=False)  # Changed to not auto error

# Clerk's JWKS URL to fetch the public keys
jwks_url = f"https://{settings.CLERK_ISSUER_URL}/.well-known/jwks.json"
jwks_client = PyJWKClient(jwks_url)

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
    try:
        clerk_jwks_url = f"https://{settings.CLERK_ISSUER_URL}/.well-known/jwks.json"
        print(f"Fetching JWKS from: {clerk_jwks_url}")
        response = requests.get(clerk_jwks_url)
        response.raise_for_status()

        # Update cache
        jwks_cache['keys'] = response.json().get('keys', [])
        jwks_cache['last_updated'] = now

        return jwks_cache['keys']
    except Exception as e:
        print(f"Error fetching JWKS: {e}")
        # Return empty list if fetch fails
        return []

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security), db: Session = Depends(get_db)):
    """Validate Clerk JWT token and return the user_id."""
    # If no credentials, return None which triggers a 401
    if not credentials:
        print("No authentication credentials provided")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="No authentication credentials provided"
        )
    
    token = credentials.credentials
    print(f"Received Token (partially redacted): {token[:10]}...{token[-5:]}")

    try:
        # Fetch the signing key dynamically
        try:
            signing_key = jwks_client.get_signing_key_from_jwt(token).key
        except Exception as e:
            print(f"Failed to get signing key: {e}")
            # Fallback to manual key fetch
            jwks_keys = get_jwks()
            if not jwks_keys:
                raise HTTPException(status_code=401, detail="Failed to retrieve JWKS keys")
            
            # Decode token header to get the key ID
            unverified_header = jwt.get_unverified_header(token)
            key_id = unverified_header.get('kid')
            
            if not key_id:
                raise HTTPException(status_code=401, detail="No key ID found in token header")
            
            # Find the right key
            rsa_key = None
            for key in jwks_keys:
                if key.get('kid') == key_id:
                    rsa_key = key
                    break
            
            if not rsa_key:
                raise HTTPException(status_code=401, detail="No matching key found")
            
            # Convert JWK to PEM format
            signing_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(rsa_key))

        # Try to decode the token with various options
        try:
            # First try with full validation
            payload = jwt.decode(
                token, 
                signing_key, 
                algorithms=["RS256"],
                audience=settings.CLERK_AUDIENCE,
                issuer=f"https://{settings.CLERK_ISSUER_URL}"
            )
        except jwt.PyJWTError as e:
            print(f"Full validation failed, trying with relaxed options: {e}")
            # Then try with more relaxed options
            payload = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                options={
                    "verify_aud": False,
                    "verify_iss": False
                }
            )

        print(f"Decoded Payload (partial): {str(payload)[:200]}...")

        # Ensure `sub` field exists - this is standard in Clerk tokens
        user_id = payload.get("sub")
        if not user_id:
            print(f"No user_id found in token")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid subject in token")

        # Check if user has a balance record, create one if missing
        balance = db.query(UserBalance).filter(UserBalance.user_id == user_id).first()
        if not balance:
            print(f"Creating new balance record for user: {user_id}")
            balance = UserBalance(user_id=user_id, pages_balance=10, pages_used=0)
            db.add(balance)
            db.commit()

        return user_id  # Return user ID

    except jwt.ExpiredSignatureError:
        print("Token expired")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired")
    except jwt.InvalidTokenError as e:
        print(f"Invalid token: {e}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {str(e)}")
    except Exception as e:
        error_msg = str(e)
        print(f"Authentication error: {error_msg}")
        
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