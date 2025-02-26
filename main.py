import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.api.routes import auth, documents, export, balance
from app.core.config import settings
from app.api.routes.google_auth import router as google_auth_router
from starlette.middleware.sessions import SessionMiddleware

app = FastAPI(
    title="Document Translation API",
    description="API for document translation service",
    version="1.0.0"
)

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
# Include balance router first to ensure its /me/balance endpoint takes precedence
app.include_router(balance.router, prefix="/api/balance", tags=["Balance"])
# Then include other routers
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
    return JSONResponse(
        status_code=401,
        content={"detail": str(exc)},
    )

@app.get("/")
async def root():
    return {"message": "Welcome to DocTranslate API"}

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "version": "1.0.0",
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