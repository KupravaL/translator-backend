from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import JSONResponse
import logging
import re
import json
import time
from datetime import datetime

# Configure logger specifically for this module
logger = logging.getLogger("auth_middleware")

class AuthMiddleware(BaseHTTPMiddleware):
    """
    Enhanced middleware to handle authentication issues gracefully.
    Improved handling of token expiration, especially for status endpoints.
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
            r"^/api/history/.*$",
        ]
        
        # Endpoints that can return partial data with auth warnings
        self.partial_auth_endpoints = [
            r"^/api/documents/status/.*$",
            r"^/api/balance/me/balance$",
        ]
        
        logger.info("Enhanced AuthMiddleware initialized")
    
    async def dispatch(self, request: Request, call_next):
        """Process requests and handle authentication errors."""
        # Track request time for logging and timeouts
        start_time = time.time()
        
        # Get the path for matching
        path = request.url.path
        method = request.method
        
        # Log incoming requests with more details for debugging auth issues
        logger.debug(f"Incoming {method} request for {path}")
        
        # Add X-Request-Time header to help track potential timeout issues
        request.headers.__dict__["_list"].append(
            (b"x-request-time", str(int(start_time * 1000)).encode())
        )
        
        # Check if this request has special auth-related headers from frontend
        is_status_check = False
        status_check_count = 0
        
        if request.headers.get("x-is-status-check") == "true":
            is_status_check = True
            # Get retry count if available
            try:
                status_check_count = int(request.headers.get("x-check-count", "0"))
            except (ValueError, TypeError):
                status_check_count = 0
                
            logger.info(f"Status check request for {path} (retry count: {status_check_count})")
        
        # Check if this is a public endpoint that should work even with an expired token
        is_public_endpoint = any(re.match(pattern, path) for pattern in self.public_endpoints)
        needs_token_status = any(re.match(pattern, path) for pattern in self.token_status_endpoints)
        can_return_partial = any(re.match(pattern, path) for pattern in self.partial_auth_endpoints)
        
        # Check for bearer token and extract expiration if present
        token_info = self._extract_token_info(request)
        
        # If token is about to expire (< 5 min remaining) and this is a status check,
        # we should add a special header to the request to signal pending expiration
        if token_info and token_info.get("expires_in_seconds", 0) < 300 and is_status_check:
            # Add X-Token-Expiring-Soon header to the request
            request.headers.__dict__["_list"].append(
                (b"x-token-expiring-soon", b"true")
            )
            logger.warning(f"Token for {path} expires in {token_info.get('expires_in_seconds')}s")
        
        # Pass along to the next middleware/route handler
        response = await call_next(request)
        
        # Track response time for logging
        duration_ms = round((time.time() - start_time) * 1000)
        
        # Log the response details
        status_code = response.status_code
        logger.debug(f"Response: {status_code} for {path} in {duration_ms}ms")
        
        # If the response status code is 401 or 403 (Unauthorized/Forbidden)
        if status_code in (401, 403):
            logger.warning(f"Auth error ({status_code}) for {path}")
            
            # Get the response body to check for specific errors
            response_body = b""
            async for chunk in response.body_iterator:
                response_body += chunk
            
            # Check if it's a token expiration
            try:
                error_data = json.loads(response_body.decode("utf-8"))
                error_detail = error_data.get("detail", "")
                is_token_expired = "expired" in error_detail.lower()
                
                # Add more detailed logging
                logger.warning(f"Auth failure for {path}: {error_detail}")
                
                # For public endpoints, return a default response instead of 401/403
                if is_public_endpoint:
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
                
                # For endpoints that can return partial data with auth warnings
                if can_return_partial and is_status_check:
                    logger.info(f"Providing partial auth response for status check: {path}")
                    
                    # Extract process ID from path for status endpoints
                    if "/documents/status/" in path:
                        try:
                            process_id = path.split("/")[-1]
                            # Return a partial status with auth warning
                            return JSONResponse(
                                status_code=200,  # Return 200 instead of 401/403
                                content={
                                    "processId": process_id,
                                    "status": "pending",
                                    "progress": 0,
                                    "currentPage": 0,
                                    "totalPages": 0,
                                    "authError": True,
                                    "fileName": None
                                },
                                headers={
                                    "X-Token-Expired": "true",
                                    "X-Auth-Warning": "Token expired or invalid"
                                }
                            )
                        except Exception as e:
                            logger.error(f"Error creating partial status response: {e}")
                    
                    # For balance endpoint
                    elif path == "/api/balance/me/balance":
                        # Return a default balance with auth warning
                        return JSONResponse(
                            status_code=200,  # Return 200 instead of 401/403
                            content={
                                "userId": "anonymous",
                                "pagesBalance": 10,
                                "pagesUsed": 0,
                                "lastUsed": None,
                                "authError": True
                            },
                            headers={
                                "X-Token-Expired": "true",
                                "X-Auth-Warning": "Token expired or invalid"
                            }
                        )
                        
                # For status check endpoints that need token status, add additional headers
                if needs_token_status and is_token_expired:
                    # Return the error but with special headers
                    logger.info(f"Adding token status headers to response for {path}")
                    return JSONResponse(
                        status_code=401,
                        content={
                            "detail": error_detail,
                            "tokenExpired": True
                        },
                        headers={
                            "X-Token-Expired": "true",
                            "X-Auth-Refresh-Required": "true"
                        }
                    )
                
            except Exception as e:
                logger.error(f"Error processing auth response: {str(e)}")
                # Reconstruct the original response
                return Response(
                    content=response_body,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    media_type=response.media_type
                )
        
        # Check if we should add token expiration warnings to successful responses
        if status_code == 200 and token_info:
            expires_in = token_info.get("expires_in_seconds", 0)
            
            # If token is about to expire, add warning headers
            if expires_in < 600:  # Less than 10 minutes remaining
                logger.info(f"Adding token expiring soon header for {path}, expires in {expires_in}s")
                # Add the headers to the response
                headers = dict(response.headers)
                headers["X-Token-Expiring-Soon"] = "true"
                headers["X-Token-Expires-In"] = str(expires_in)
                
                # Special handling for streaming responses vs. regular responses
                if hasattr(response, "body") and response.body is not None:
                    # Regular response - we can modify and return
                    return Response(
                        content=response.body,
                        status_code=response.status_code,
                        headers=headers,
                        media_type=response.media_type
                    )
                else:
                    # For streaming responses, we might not be able to modify them easily
                    # Just pass through with the original headers
                    logger.warning(f"Cannot add token headers to streaming response for {path}")
                    return response
            
        # For all other cases, just return the original response
        return response
    
    def _extract_token_info(self, request: Request):
        """Extract information from the authorization token if present."""
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None
        
        # Extract the token
        token = auth_header.replace("Bearer ", "")
        if not token:
            return None
        
        try:
            # Split the token and decode the payload part
            # Note: This doesn't verify the signature, just extracts the data
            payload_part = token.split('.')[1]
            
            # Add necessary padding for base64 decoding
            padding_needed = 4 - (len(payload_part) % 4)
            if padding_needed < 4:
                payload_part += '=' * padding_needed
            
            # Decode the payload
            import base64
            decoded_bytes = base64.b64decode(payload_part)
            payload = json.loads(decoded_bytes)
            
            # Extract relevant info
            now = int(time.time())
            exp = payload.get('exp', 0)
            iat = payload.get('iat', 0)
            
            # Calculate time remaining
            expires_in_seconds = max(0, exp - now)
            
            return {
                "sub": payload.get("sub"),
                "iss": payload.get("iss"),
                "iat": iat,
                "exp": exp,
                "expires_in_seconds": expires_in_seconds,
                "issued_at_time": datetime.fromtimestamp(iat).isoformat() if iat else None,
                "expires_at_time": datetime.fromtimestamp(exp).isoformat() if exp else None,
            }
        except Exception as e:
            logger.error(f"Error extracting token info: {str(e)}")
            return None