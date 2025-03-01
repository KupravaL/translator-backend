from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import JSONResponse
import logging
import re
import json

# Configure logger specifically for this module
logger = logging.getLogger("auth_middleware")

class AuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware to handle authentication issues gracefully, particularly for token expiration.
    This middleware intercepts 401 errors and manages public endpoint access.
    """
    
    def __init__(self, app):
        super().__init__(app)
        # Define list of endpoints that should work even with expired tokens
        # These are endpoints that show public information or have fallback behavior
        self.public_endpoints = [
            r"^/api/health$",
            r"^/api/balance/public/balance$",
            r"^/api/balance/debug/balance$",
        ]
        
        # Define list of endpoints that need special handling for expired tokens
        # For these endpoints, we'll include token status in the response
        self.token_status_endpoints = [
            r"^/api/balance/me/balance$",
            r"^/api/documents/status/.*$",
            r"^/api/documents/result/.*$",
        ]
        
        logger.info("AuthMiddleware initialized")
    
    async def dispatch(self, request: Request, call_next):
        """Process requests and handle authentication errors."""
        # Get the path for matching
        path = request.url.path
        
        # Check if this is a public endpoint that should work even with an expired token
        is_public_endpoint = any(re.match(pattern, path) for pattern in self.public_endpoints)
        needs_token_status = any(re.match(pattern, path) for pattern in self.token_status_endpoints)
        
        # Pass along to the next middleware/route handler
        response = await call_next(request)
        
        # If the response status code is 401 (Unauthorized)
        if response.status_code == 401:
            logger.debug(f"Got 401 response for {path}, checking if token expired")
            
            # Get the response body
            response_body = b""
            async for chunk in response.body_iterator:
                response_body += chunk
            
            # Check if it's a token expiration
            try:
                error_data = json.loads(response_body.decode("utf-8"))
                error_detail = error_data.get("detail", "")
                is_token_expired = "expired" in error_detail.lower()
                
                # Log the authorization failure
                logger.warning(f"Authorization failed for {path}: {error_detail}")
                
                if is_public_endpoint:
                    # For public endpoints, we'll return a default response rather than a 401
                    logger.info(f"Providing public response for {path} despite auth failure")
                    
                    if path == "/api/balance/public/balance":
                        # Default anonymous balance
                        return JSONResponse(
                            status_code=200,
                            content={
                                "userId": "anonymous",
                                "pagesBalance": 10,
                                "pagesUsed": 0,
                                "lastUsed": None,
                                "isPublicFallback": True
                            }
                        )
                    
                    # Other public endpoints can have their own fallback logic here
                
                if needs_token_status and is_token_expired:
                    # For endpoints that need token status, return the error but add a special flag
                    logger.info(f"Adding tokenExpired flag to response for {path}")
                    return JSONResponse(
                        status_code=401,
                        content={
                            "detail": error_detail,
                            "tokenExpired": True
                        },
                        headers={"X-Token-Expired": "true"}
                    )
                
            except Exception as e:
                logger.error(f"Error processing auth response: {str(e)}")
                # Reconstruct the original response if there was an error
                return Response(
                    content=response_body,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    media_type=response.media_type
                )
        
        # For all other cases, just return the original response
        return response