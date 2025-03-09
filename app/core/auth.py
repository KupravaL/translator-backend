import json
import jwt
import requests
from datetime import datetime, timedelta
from fastapi import Depends, HTTPException, status, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.balance import UserBalance
from jwt import PyJWKClient, ExpiredSignatureError, InvalidTokenError
import logging

from app.core.config import settings

# Configure logging
logger = logging.getLogger("auth")

# HTTP Bearer security scheme with auto_error=True to enforce authentication
security = HTTPBearer(auto_error=True)

# Check if the issuer URL already has https:// 
if settings.CLERK_ISSUER_URL.startswith("https://"):
    jwks_url = f"{settings.CLERK_ISSUER_URL}/.well-known/jwks.json"
else:
    jwks_url = f"https://{settings.CLERK_ISSUER_URL}/.well-known/jwks.json"
    
jwks_client = PyJWKClient(jwks_url)

logger.info(jwks_client.fetch_data)

# Cache for Clerk's JWKS (JSON Web Key Set)
jwks_cache = {
    'keys': None,
    'last_updated': None
}

def get_jwks():
    """Fetch and cache Clerk's JWKS keys for token verification."""
    now = datetime.now()

    # Use cached keys if available and not expired (cache for 12 hours)
    if jwks_cache['keys'] and jwks_cache['last_updated'] and (now - jwks_cache['last_updated']).total_seconds() < 43200:  # 12 hours
        return jwks_cache['keys']

    # Fetch JWKS keys from Clerk with retries
    max_retries = 3
    for attempt in range(max_retries):
        try:
            if settings.CLERK_ISSUER_URL.startswith("https://"):
                clerk_jwks_url = f"{settings.CLERK_ISSUER_URL}/.well-known/jwks.json"
            else:
                clerk_jwks_url = f"https://{settings.CLERK_ISSUER_URL}/.well-known/jwks.json"
                
            # Inside get_jwks function, after determining the URL
            logger.debug(f"JWKS URL (parsed): {clerk_jwks_url}")    
            logger.info(f"Fetching JWKS from: {clerk_jwks_url}")
            response = requests.get(clerk_jwks_url, timeout=5)  # Add timeout
            response.raise_for_status()

            # Update cache
            jwks_cache['keys'] = response.json().get('keys', [])
            jwks_cache['last_updated'] = now
            logger.info(f"Successfully updated JWKS cache with {len(jwks_cache['keys'])} keys")
            return jwks_cache['keys']
        except Exception as e:
            if attempt == max_retries - 1:  # Last attempt
                logger.error(f"Error fetching JWKS after {max_retries} attempts: {e}")
            else:
                logger.warning(f"JWKS fetch attempt {attempt+1} failed: {e}. Retrying...")
                
    # Return cached keys if available, even if expired (better than nothing)
    if jwks_cache['keys']:
        logger.warning("Using expired JWKS cache as fallback")
        return jwks_cache['keys']
        
    # Return empty list if fetch fails and no cache is available
    logger.error("Failed to retrieve JWKS keys and no cache available")
    return []

def get_token_expiration(token):
    """Get the expiration time from a token without full validation."""
    try:
        # Just decode without verification to extract the expiration
        unverified_payload = jwt.decode(token, options={"verify_signature": False})
        exp = unverified_payload.get('exp')
        
        if exp:
            # Convert to datetime
            exp_datetime = datetime.fromtimestamp(exp)
            now = datetime.now()
            
            # Calculate time remaining
            time_remaining = (exp_datetime - now).total_seconds()
            
            return exp_datetime, time_remaining
        return None, None
    except Exception as e:
        logger.error(f"Error extracting token expiration: {e}")
        return None, None

# Add function to log detailed token info
def log_token_details(token, source="auth"):
    """Log detailed token information for debugging purposes."""
    try:
        # Decode without verification
        unverified_payload = jwt.decode(token, options={"verify_signature": False})
        
        # Extract essential claims
        sub = unverified_payload.get('sub', 'unknown')
        iss = unverified_payload.get('iss', 'unknown')
        exp = unverified_payload.get('exp', 0)
        iat = unverified_payload.get('iat', 0)
        
        # Calculate times
        now = datetime.now()
        exp_time = datetime.fromtimestamp(exp) if exp else None
        iat_time = datetime.fromtimestamp(iat) if iat else None
        
        # Time remaining
        time_remaining = (exp_time - now).total_seconds() if exp_time else 0
        minutes = int(time_remaining // 60)
        seconds = int(time_remaining % 60)
        
        # Log the details
        logger.info(f"Token details ({source}):")
        logger.info(f"  Subject: {sub}")
        logger.info(f"  Issuer: {iss}")
        logger.info(f"  Issued: {iat_time} ({datetime.now() - iat_time if iat_time else 'unknown'} ago)")
        logger.info(f"  Expires: {exp_time} (in {minutes}m {seconds}s)")
        
        if time_remaining < 600:  # Less than 10 minutes
            logger.warning(f"Token expires soon: {minutes}m {seconds}s remaining")
        
        return {
            "sub": sub,
            "iss": iss,
            "exp": exp,
            "iat": iat,
            "expires_at": exp_time.isoformat() if exp_time else None,
            "issued_at": iat_time.isoformat() if iat_time else None,
            "time_remaining": time_remaining,
            "minutes_remaining": minutes,
            "seconds_remaining": seconds
        }
    except Exception as e:
        logger.error(f"Error logging token details: {e}")
        return None

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security), 
    db: Session = Depends(get_db),
    request: Request = None,
    response: Response = None
):
    """
    Enhanced function to validate Clerk JWT token and return the user_id.
    Includes improved token expiration handling and informative headers.
    """
    token = credentials.credentials
    
    # Log more detailed token information
    log_token_details(token, "request")
    
    # Check if token is about to expire
    exp_time, time_remaining = get_token_expiration(token)
    if exp_time and time_remaining is not None:
        if time_remaining < 0:
            logger.warning(f"Token already expired by {-time_remaining:.2f} seconds")
            # Add X-Token-Expired header to inform client
            if response:
                response.headers["X-Token-Expired"] = "true"
                # Don't add X-Token-Expires-In for expired tokens
        else:
            logger.info(f"Token expires in {time_remaining:.2f} seconds")
            
            # Set token expiration headers with different warning levels
            if response:
                # Add remaining time for all requests
                response.headers["X-Token-Expires-In"] = str(int(time_remaining))
                
                # Add different warning headers based on expiration time
                if time_remaining < 60:  # Less than 1 minute
                    response.headers["X-Token-Critical"] = "true"
                    response.headers["X-Token-Expiring-Soon"] = "true"
                    logger.warning("Token critically close to expiration")
                elif time_remaining < 300:  # Less than 5 minutes
                    response.headers["X-Token-Expiring-Soon"] = "true"
                    logger.warning("Token expiring soon")
                elif time_remaining < 600:  # Less than 10 minutes
                    response.headers["X-Token-Expiry-Warning"] = "true"
                    logger.info("Token expiration warning")
    
    # Check if this is a status endpoint request
    is_status_endpoint = False
    if request and request.url:
        path = request.url.path
        if '/documents/status/' in path or '/me/balance' in path:
            is_status_endpoint = True
            logger.info(f"Status endpoint request: {path}")
    
    try:
        # First try with the PyJWKClient
        try:
            signing_key = jwks_client.get_signing_key_from_jwt(token).key
            logger.debug("Successfully retrieved signing key from PyJWKClient")
        except Exception as e:
            logger.warning(f"PyJWKClient failed to get signing key: {e}")
            # Fallback to manual key fetch
            jwks_keys = get_jwks()
            if not jwks_keys:
                logger.error("Failed to retrieve JWKS keys")
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Failed to retrieve JWKS keys")
            
            # Decode token header to get the key ID
            unverified_header = jwt.get_unverified_header(token)
            key_id = unverified_header.get('kid')
            
            if not key_id:
                logger.error("No key ID found in token header")
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No key ID found in token header")
            
            # Find the right key
            rsa_key = None
            for key in jwks_keys:
                if key.get('kid') == key_id:
                    rsa_key = key
                    break
            
            if not rsa_key:
                logger.error(f"No matching key found for kid: {key_id}")
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No matching key found")
            
            # Convert JWK to PEM format
            signing_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(rsa_key))
            logger.debug("Successfully created signing key from JWK")

        # Try to decode the token with relaxed options first
        try:
            logger.debug("Attempting token decode with relaxed options")
            payload = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                options={
                    "verify_aud": False,
                    "verify_iss": False
                }
            )
            logger.debug("Token successfully validated with relaxed options")
        except jwt.PyJWTError as e:
            logger.warning(f"Relaxed validation failed: {e}")
            # Try with stricter validation
            try:
                logger.debug("Attempting token decode with strict options")
                payload = jwt.decode(
                    token, 
                    signing_key, 
                    algorithms=["RS256"],
                    audience=settings.CLERK_AUDIENCE,
                    issuer=f"https://{settings.CLERK_ISSUER_URL}"
                )
                logger.debug("Token successfully validated with strict options")
            except jwt.ExpiredSignatureError:
                logger.error("Token has expired")
                if response:
                    response.headers["X-Token-Expired"] = "true"
                
                # Special treatment for status endpoint requests
                if is_status_endpoint:
                    # For status endpoints, we want to include a helpful message
                    # that guides the frontend to refresh the token
                    logger.info("Status endpoint with expired token - adding helpful headers")
                    if response:
                        response.headers["X-Status-Auth-Error"] = "true"
                        response.headers["X-Auth-Action-Required"] = "refresh"
                
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, 
                    detail="Token has expired - please login again",
                    headers={"X-Token-Expired": "true"}
                )
            except jwt.PyJWTError as e:
                logger.error(f"Token validation failed: {str(e)}")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, 
                    # Continuing from previous code
                detail=f"Token validation failed: {str(e)}"
                )

        # Ensure `sub` field exists
        user_id = payload.get("sub")
        if not user_id:
            logger.error("Invalid subject in token")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid subject in token")

        # Check if user has a balance record, create one if missing
        try:
            balance = db.query(UserBalance).filter(UserBalance.user_id == user_id).first()
            if not balance:
                logger.info(f"Creating new balance for user: {user_id}")
                balance = UserBalance(user_id=user_id, pages_balance=10, pages_used=0)
                db.add(balance)
                db.commit()
        except Exception as db_error:
            logger.error(f"Database error checking user balance: {db_error}")
            # Continue without failing the request if DB error occurs
            # The balance API will handle missing records

        # If token is about to expire, add a special header for frontend to refresh
        if time_remaining is not None and time_remaining < 300 and response:  # Less than 5 minutes
            response.headers["X-Token-Refresh-Required"] = "true"
            logger.info(f"Added token refresh required header for user {user_id}")

        logger.info(f"Authentication successful for user: {user_id}")
        return user_id  # Return user ID

    except HTTPException:
        # Re-raise HTTP exceptions with the same status code
        raise
    except jwt.ExpiredSignatureError:
        logger.error("Token has expired")
        if response:
            response.headers["X-Token-Expired"] = "true"
            
        # For status endpoints, add additional headers to help frontend
        if is_status_endpoint and response:
            response.headers["X-Status-Auth-Error"] = "true"
            response.headers["X-Auth-Action-Required"] = "refresh"
            
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Token has expired - please login again",
            headers={"X-Token-Expired": "true"}
        )
    except jwt.InvalidTokenError as e:
        logger.error(f"Invalid token: {str(e)}")
        if response:
            response.headers["X-Invalid-Token"] = "true"
            
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {str(e)}")
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Authentication error: {error_msg}")
        
        # Check for specific error types and provide more helpful messages
        if "JWK" in error_msg or "signing key" in error_msg:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, 
                              detail="Token signature verification failed - check Clerk configuration")
        if "expired" in error_msg:
            if response:
                response.headers["X-Token-Expired"] = "true"
                # Add additional headers for status endpoints
                if is_status_endpoint:
                    response.headers["X-Status-Auth-Error"] = "true"
                    response.headers["X-Auth-Action-Required"] = "refresh"
                    
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, 
                              detail="Token has expired - please login again",
                              headers={"X-Token-Expired": "true"})
                              
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, 
                          detail=f"Authentication failed: {error_msg}")

# Webhook Signature Verification (Placeholder)
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
    except Exception as e:
        logger.error(f"Webhook verification error: {str(e)}")
        return False