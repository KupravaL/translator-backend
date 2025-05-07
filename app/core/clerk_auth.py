import os
from fastapi import Request, HTTPException
from clerk import ClerkClient

clerk_client = ClerkClient(api_key=os.environ["CLERK_SECRET_KEY"])

async def get_current_user_id(request: Request) -> str:
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header")
    token = auth_header.split(" ")[1]
    try:
        session = clerk_client.verify_token(token)
        return session["sub"]  # Clerk user ID
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token") 