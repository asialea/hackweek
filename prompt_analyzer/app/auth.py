from datetime import datetime, timedelta
from typing import Optional
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import os
import jwt

SECRET = os.environ.get('JWT_SECRET', 'dev-secret-change-me')
ALGORITHM = os.environ.get('JWT_ALGORITHM', 'HS256')

security = HTTPBearer()
# A non-fatal bearer that doesn't raise if no header is present
security_optional = HTTPBearer(auto_error=False)

def create_jwt(sub: str, expires_minutes: int = 60):
    payload = { 'sub': sub, 'exp': datetime.utcnow() + timedelta(minutes=expires_minutes) }
    token = jwt.encode(payload, SECRET, algorithm=ALGORITHM)
    return token

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    print(f"DEBUG: Received token: {token[:50]}...") # DEBUG
    try:
        payload = jwt.decode(token, SECRET, algorithms=[ALGORITHM])
        user_id = payload.get('sub')
        print(f"DEBUG: Token decoded successfully, user_id: {user_id}") # DEBUG
        if not user_id:
            print("DEBUG: No user_id in token payload") # DEBUG
            raise HTTPException(status_code=401, detail='Invalid token payload')
        return {'user_id': user_id}
    except jwt.ExpiredSignatureError:
        print("DEBUG: Token expired") # DEBUG
        raise HTTPException(status_code=401, detail='Token expired')
    except jwt.InvalidTokenError as e:
        print(f"DEBUG: Invalid token error: {e}") # DEBUG
        raise HTTPException(status_code=401, detail='Invalid token')


def get_current_user_optional(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_optional)):
    # If no credentials were provided, return None rather than raising
    if not credentials:
        return None
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET, algorithms=[ALGORITHM])
        user_id = payload.get('sub')
        if not user_id:
            return None
        return {'user_id': user_id}
    except jwt.ExpiredSignatureError:
        # treat expired token as no user
        return None
    except jwt.InvalidTokenError:
        return None
