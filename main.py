import uvicorn
import time
import logging
import os
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from app.api.routes import auth, documents, export, balance
from app.core.config import settings
from app.api.routes.google_auth import router as google_auth_router
from starlette.middleware.sessions import SessionMiddleware
import asyncio
from app.core.auth_middleware import AuthMiddleware  # Import from the correct location

# Configure logging with more detail
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] [%(name)s] %(message)s',  # Include module name in logs
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(),  # Log to console
        logging.FileHandler("api.log")  # Also log to file
    ]
)

# Set specific log levels for different modules
logging.getLogger("auth").setLevel(logging.DEBUG)
logging.getLogger("auth_middleware").setLevel(logging.DEBUG)
logging.getLogger("balance").setLevel(logging.INFO)
logging.getLogger("translation").setLevel(logging.INFO)

logger = logging.getLogger("api")
logger.info("Application starting up...")

class TimeoutMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        path = request.url.path
        method = request.method
        
        # Log the incoming request
        logger.info(f"Received {method} request for {path}")
        
        # Handle file uploads specially
        if path == "/api/documents/translate" and method == "POST":
            # Use a short timeout for the translation request - since we want to
            # initiate the translation and return the process ID quickly, not wait
            # for the entire translation to complete
            timeout = 10  # 10 seconds for upload
        elif "/api/documents/status/" in path:
            timeout = settings.STATUS_CHECK_TIMEOUT
        else:
            timeout = settings.DEFAULT_TIMEOUT
            
        try:
            # Execute the request with timeout
            request_task = asyncio.create_task(call_next(request))
            response = await asyncio.wait_for(request_task, timeout=timeout)
            
            # Log the completed request
            duration = time.time() - start_time
            logger.info(f"Completed {method} request for {path} in {duration:.2f}s")
            
            return response
        except asyncio.TimeoutError:
            duration = time.time() - start_time
            logger.warning(f"Timeout after {duration:.2f}s for {method} request to {path}")
            
            # For status check endpoints, return a default response
            if "/api/documents/status/" in path:
                process_id = path.split("/")[-1]
                return JSONResponse(
                    status_code=200,
                    content={
                        "processId": process_id,
                        "status": "pending",
                        "progress": 0,
                        "currentPage": 0,
                        "totalPages": 0,
                        "fileName": None,
                        "isTimeout": True
                    }
                )
                
            # For translation requests that timeout, return a specific message
            if path == "/api/documents/translate" and method == "POST":
                return JSONResponse(
                    status_code=408,  # Request Timeout
                    content={
                        "success": False,
                        "error": "The server timed out while processing the file. Your file might be too large or complex.",
                        "type": "TIMEOUT_ERROR"
                    }
                )
                
            # For other requests, return a timeout error
            return JSONResponse(
                status_code=408,  # Request Timeout
                content={"detail": "Request timed out. The server is processing your request."}
            )
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Error after {duration:.2f}s for {method} request to {path}: {str(e)}")
            
            return JSONResponse(
                status_code=500,
                content={"detail": f"An internal server error occurred: {str(e)}"}
            )

app = FastAPI(
    title="Document Translation API",
    description="API for document translation service",
    version="1.0.0"
)

# Add our auth middleware first (before the timeout middleware)
logger.info("Adding AuthMiddleware to the application")
app.add_middleware(AuthMiddleware)

# Add the timeout middleware
app.add_middleware(TimeoutMiddleware)

# Configure CORS - Important for frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add session middleware
app.add_middleware(
    SessionMiddleware,
    secret_key="your-secret-key-here",  # Use a secure key in production
)

# Important: Order matters for routes
app.include_router(balance.router, prefix="/api/balance", tags=["Balance"])
app.include_router(auth.router, prefix="/api/auth", tags=["Authentication"])
app.include_router(documents.router, prefix="/api/documents", tags=["Documents"])
app.include_router(export.router, prefix="/api/export", tags=["Export"])
app.include_router(google_auth_router)

# Add middleware to set security headers
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    
    # Set COOP header to allow popups
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
    
    # Set COEP header for added security but allow credentials
    response.headers["Cross-Origin-Embedder-Policy"] = "credentialless"
    
    return response

# Exception handler for authentication errors
@app.exception_handler(401)
async def unauthorized_exception_handler(request: Request, exc):
    logger.warning(f"401 error handler: {str(exc)}")
    return JSONResponse(
        status_code=401,
        content={"detail": str(exc)},
        headers={"X-Token-Expired": "true"} if "expired" in str(exc).lower() else {}
    )

@app.get("/")
async def root():
    return {"message": "Welcome to DocTranslate API"}

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "version": "1.0.0",
        "environment": os.environ.get("ENVIRONMENT", "development"),
        "cors_origins": settings.CORS_ORIGINS,
        "clerk_issuer": settings.CLERK_ISSUER_URL
    }

# Debug endpoint to check CORS configuration
@app.options("/debug-cors")
async def debug_cors():
    return {}

if __name__ == "__main__":
    # Use an absolute path to the module
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)