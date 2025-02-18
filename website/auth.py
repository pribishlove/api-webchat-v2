# website/auth.py
import jwt
from datetime import datetime, timedelta
from fastapi import Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from database import get_db
from models import User
from fastapi.security import OAuth2PasswordBearer
import hashlib

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
SECRET_KEY = "your_secret_key"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def verify_password(password: str, hashed_password: str) -> bool:
    return hash_password(password) == hashed_password


def create_jwt_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def verify_jwt(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def get_current_user(request: Request, db: Session = Depends(get_db)):
    user_id = request.cookies.get("session")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return db.query(User).filter(User.id == user_id).first()


def create_session(response: JSONResponse, user_id: int):
    response.set_cookie(key="session", value=str(user_id), httponly=True)


def destroy_session(response: JSONResponse):
    response.delete_cookie("session")