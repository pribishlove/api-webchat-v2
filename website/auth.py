# website/auth.py
import jwt
from datetime import datetime, timedelta
from fastapi import Request, HTTPException, Depends
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session
from database import get_db
from models import User, ChatRoom
from fastapi.security import OAuth2PasswordBearer
import hashlib
from jose import jwt, JWTError

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


def get_current_user(request: Request):
    token = request.cookies.get("access_token")  # Теперь берем токен из Cookies
    if not token:
        raise HTTPException(status_code=401, detail="Не найден токен авторизации")

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: int = payload.get("user_id")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Неверный токен")
        return {"user_id": user_id}
    except JWTError:
        raise HTTPException(status_code=401, detail="Токен недействителен")


def create_session(response: Response, user: User) -> str:  # Изменяем параметр с user_id на user
    expiration = datetime.utcnow() + timedelta(hours=2)
    token = jwt.encode(
        {
            "user_id": user.id,
            "email": user.email,  # Добавляем email в токен
            "exp": expiration
        },
        SECRET_KEY,
        algorithm=ALGORITHM
    )

    response.set_cookie(key="access_token", value=token, httponly=True, samesite="Lax")
    return token


def destroy_session(response: Response):
    response.delete_cookie(key="access_token")  # Изменяем с "session" на "access_token"


async def create_chat_token(user: User, chat_name: str, db: Session):
    # Проверяем существование чата
    chat = db.query(ChatRoom).filter(ChatRoom.name == chat_name).first()
    if not chat:
        raise HTTPException(status_code=404, detail="Чат не найден")

    # Создаем токен с email пользователя и названием чата
    token_data = {
        "user_id": user.id,
        "email": user.email,
        "chat_name": chat_name,
        "exp": datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    }
    
    return jwt.encode(token_data, SECRET_KEY, algorithm=ALGORITHM)