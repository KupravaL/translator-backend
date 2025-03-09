# Enhanced auth middleware with Content-Length fix
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import JSONResponse, Response
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
    Fixed Content-Length header handling to prevent HTTP protocol errors.
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
            if hasattr(response, "body"):
                response_body = response.body
            else:
                # For streaming responses, try to get content another way
                try:
                    # Create a buffer to hold the content
                    buffer = []
                    
                    async def collect_body(message):
                        if message["type"] == "http.response.body":
                            buffer.append(message.get("body", b""))
                        await send(message)
                    
                    # Modified send function to capture the body
                    async def send(message):
                        nonlocal response_body
                        if message["type"] == "http.response.body":
                            response_body += message.get("body", b"")
                        
                    # This might not work for all response types
                    if hasattr(response, "body_iterator"):
                        async for chunk in response.body_iterator:
                            response_body += chunk
                except Exception as e:
                    logger.error(f"Error capturing response body: {str(e)}")
            
            # Check if it's a token expiration
            try:
                # Only try to decode if we have some body content
                if response_body:
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
                            json_content = {
                                "userId": "anonymous",
                                "pagesBalance": 10,
                                "pagesUsed": 0,
                                "lastUsed": None,
                                "isPublicFallback": True
                            }
                            
                            # Create proper JSONResponse with correct Content-Length
                            return JSONResponse(
                                status_code=200,
                                content=json_content
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
                                json_content = {
                                    "processId": process_id,
                                    "status": "pending",
                                    "progress": 0,
                                    "currentPage": 0,
                                    "totalPages": 0,
                                    "authError": True,
                                    "fileName": None
                                }
                                
                                # Create proper JSONResponse with correct Content-Length
                                return JSONResponse(
                                    status_code=200,
                                    content=json_content,
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
                            json_content = {
                                "userId": "anonymous",
                                "pagesBalance": 10,
                                "pagesUsed": 0,
                                "lastUsed": None,
                                "authError": True
                            }
                            
                            # Create proper JSONResponse with correct Content-Length
                            return JSONResponse(
                                status_code=200,
                                content=json_content,
                                headers={
                                    "X-Token-Expired": "true",
                                    "X-Auth-Warning": "Token expired or invalid"
                                }
                            )
                            
                    # For status check endpoints that need token status, add additional headers
                    if needs_token_status and is_token_expired:
                        # Return the error but with special headers
                        logger.info(f"Adding token status headers to response for {path}")
                        
                        json_content = {
                            "detail": error_detail,
                            "tokenExpired": True
                        }
                        
                        # Create proper JSONResponse with correct Content-Length
                        return JSONResponse(
                            status_code=401,
                            content=json_content,
                            headers={
                                "X-Token-Expired": "true",
                                "X-Auth-Refresh-Required": "true"
                            }
                        )
            except json.JSONDecodeError:
                logger.warning(f"Could not parse response body as JSON: {response_body}")
            except Exception as e:
                logger.error(f"Error processing auth response: {str(e)}")
        
        # Check if we should add token expiration warnings to successful responses
        if status_code == 200 and token_info:
            expires_in = token_info.get("expires_in_seconds", 0)
            
            # If token is about to expire, add warning headers
            if expires_in < 600:  # Less than 10 minutes remaining
                logger.info(f"Adding token expiring soon header for {path}, expires in {expires_in}s")
                
                # For responses that we can modify safely (with a body attribute)
                if hasattr(response, "body") and response.body is not None:
                    try:
                        # Get the original body content
                        original_body = response.body
                        
                        # Parse if it's JSON
                        content_type = response.headers.get("content-type", "")
                        if "application/json" in content_type.lower():
                            # Try to parse as JSON
                            try:
                                body_dict = json.loads(original_body.decode("utf-8"))
                                
                                # Create a new response with the same content but additional headers
                                new_headers = dict(response.headers)
                                new_headers["X-Token-Expiring-Soon"] = "true"
                                new_headers["X-Token-Expires-In"] = str(expires_in)
                                
                                # Return a new JSONResponse with the correct Content-Length
                                return JSONResponse(
                                    content=body_dict,
                                    status_code=response.status_code,
                                    headers=new_headers
                                )
                            except json.JSONDecodeError:
                                logger.warning(f"Could not parse JSON response for {path}")
                        
                        # For non-JSON responses, create a new Response with the same body
                        new_headers = dict(response.headers)
                        new_headers["X-Token-Expiring-Soon"] = "true"
                        new_headers["X-Token-Expires-In"] = str(expires_in)
                        
                        # Don't set Content-Length, let the Response calculate it
                        if "content-length" in new_headers:
                            del new_headers["content-length"]
                            
                        return Response(
                            content=original_body,
                            status_code=response.status_code,
                            headers=new_headers,
                            media_type=response.media_type
                        )
                    except Exception as e:
                        logger.error(f"Error modifying response for {path}: {str(e)}")
                        # If we can't modify, return the original
                        return response
                else:
                    # For streaming responses, log but don't modify
                    logger.warning(f"Cannot add token headers to streaming response for {path}")
            
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