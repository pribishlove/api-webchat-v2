# website/main.py
from fastapi import FastAPI, Depends, Request, Form, HTTPException, Path, Query, WebSocket, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from database import get_db, engine
from models import Base, User, ChatRoom
from auth import get_current_user, create_session, destroy_session, verify_jwt, hash_password, verify_password, SECRET_KEY, ALGORITHM
from fastapi.templating import Jinja2Templates
from fastapi import APIRouter
from fastapi.staticfiles import StaticFiles
from jose import jwt, JWTError
from fastapi.security import OAuth2PasswordBearer
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
import httpx
import json
import secrets  # Для генерации секретного ключа
import asyncio
from datetime import datetime, timedelta
from typing import Optional

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)
Base.metadata.create_all(bind=engine)
app = FastAPI()

# Добавляем middleware для сессий
app.add_middleware(
    SessionMiddleware,
    secret_key="your-secret-key-here",  # Замените на свой секретный ключ
    session_cookie="session"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")
router = APIRouter()
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.middleware("http")
async def add_cache_control_header(request, call_next):
    response = await call_next(request)
    if "/static/" in request.url.path:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    user = None
    try:
        user = get_current_user(request)
    except HTTPException:
        pass  # Игнорируем ошибку 401

    if user:
        return RedirectResponse(url="/chats")  # Перенаправляем аутентифицированных пользователей

    return templates.TemplateResponse("index.html", {"request": request, "user": user})


@app.post("/login")
def login(
    request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Неверный email или пароль")

    response = RedirectResponse(url="/chats", status_code=303)
    access_token = create_session(response, user)  # Передаем весь объект user вместо только id
    response.set_cookie(key="access_token", value=access_token, httponly=True, samesite="Lax")

    return response


@app.post("/logout")  # Изменяем метод с GET на POST
def logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(key="access_token")  # Удаляем токен из cookies
    return response
    

@router.get("/auth/verify")
def verify_token_route(token: str = Query(...)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return {"valid": True, "user": payload}
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


app.include_router(router)


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})


@app.post("/register")
def register(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    # Проверяем, существует ли пользователь
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=400, detail="Email уже зарегистрирован")
    
    # Создаем нового пользователя
    hashed_password = hash_password(password)
    new_user = User(email=email, hashed_password=hashed_password)
    db.add(new_user)
    db.commit()
    
    # Перенаправляем на страницу входа
    return RedirectResponse(url="/", status_code=303)


@app.get("/chats", response_class=HTMLResponse)
def chat_list(request: Request, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    # Получаем только чаты, созданные текущим пользователем
    chats = db.query(ChatRoom).filter(ChatRoom.owner_id == user["user_id"]).all()
    return templates.TemplateResponse(
        "chats.html", 
        {
            "request": request, 
            "chats": chats, 
            "user": user
        }
    )


@app.get("/chats/create", response_class=HTMLResponse)
def create_chat_page(request: Request, user: dict = Depends(get_current_user)):  # Изменили тип user на dict
    return templates.TemplateResponse("create_chat.html", {"request": request, "user": user})


@app.post("/chats/create")
def create_chat(
    request: Request, 
    name: str = Form(...), 
    db: Session = Depends(get_db), 
    user: dict = Depends(get_current_user)
):
    # Проверяем длину названия чата
    if len(name) < 6:
        return RedirectResponse(
            url="/chats/create?error=Название чата должно содержать не менее 6 символов",
            status_code=303
        )

    # Создаем новый чат
    new_chat = ChatRoom(name=name, owner_id=user["user_id"])
    db.add(new_chat)
    db.commit()
    return RedirectResponse(url="/chats", status_code=303)

@app.post("/chats/{chat_id}/delete")
async def delete_chat(
    chat_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    chat = db.query(ChatRoom).filter(ChatRoom.id == chat_id).first()
    if not chat or chat.owner_id != user["user_id"]:
        raise HTTPException(status_code=403, detail="Нет прав на удаление чата")

    try:
        # Сначала отправляем уведомление всем пользователям чата
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                await client.post(
                    f"http://chat:8000/notify_delete/{chat.name}",
                    json={"message": "Чат был удален создателем"}
                )
            except Exception as e:
                print(f"Failed to notify chat service: {e}")

        # Удаляем чат из базы
        db.delete(chat)
        db.commit()
        
        return RedirectResponse(url="/chats", status_code=303)
    except Exception as e:
        print(f"Error deleting chat: {e}")
        db.rollback()
        return RedirectResponse(url="/chats?error=Ошибка при удалении чата", status_code=303)


@app.get("/chat/{room_name}", response_class=HTMLResponse)
async def chat(
    request: Request,
    room_name: str,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    # Проверяем существование чата
    chat = db.query(ChatRoom).filter(ChatRoom.name == room_name).first()
    if not chat:
        return RedirectResponse(
            url="/chats?error=Чат не существует или был удален",
            status_code=303
        )
    
    # Получаем пользователя из базы данных для получения email
    db_user = db.query(User).filter(User.id == user["user_id"]).first()
    
    return templates.TemplateResponse(
        "chat.html",
        {
            "request": request,
            "room_name": room_name,
            "user": {
                "email": db_user.email
            }
        }
    )


@app.get("/chats/search")
async def search_chats(
    request: Request,
    query: str,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    if len(query) < 6:
        return RedirectResponse(
            url="/chats?error=Поисковый запрос должен содержать не менее 6 символов",
            status_code=303
        )

    # Поиск чатов, содержащих запрос (без учета регистра)
    matching_chats = db.query(ChatRoom).filter(
        ChatRoom.name.ilike(f"%{query}%")
    ).all()

    return templates.TemplateResponse(
        "chats.html",
        {
            "request": request,
            "chats": matching_chats,
            "user": user,
            "search_query": query
        }
    )


@app.get("/chats/{room_name}")
def get_chat_by_name(
    room_name: str,
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme)
):
    try:
        # Если токен не передан через OAuth2, пробуем получить его из cookies
        if not token:
            raise HTTPException(status_code=401, detail="Token not found")
            
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        chat = db.query(ChatRoom).filter(ChatRoom.name == room_name).first()
        if not chat:
            # Пробуем найти чат по ID
            chat = db.query(ChatRoom).filter(ChatRoom.id == room_name).first()
            if not chat:
                raise HTTPException(status_code=404, detail="Chat not found")
        return {"id": chat.id, "name": chat.name, "owner_id": chat.owner_id}
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

# Обновим также функцию get_current_user, чтобы она возвращала email
def get_current_user(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Не авторизован")
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="Пользователь не найден")
    
    return {
        "user_id": user.id,
        "email": user.email  # Добавляем email в возвращаемый словарь
    }

@app.get("/get_chat_token/{room_name}")
async def get_chat_token(
    request: Request,
    room_name: str,
    db: Session = Depends(get_db),
    access_token: Optional[str] = Cookie(None)
):
    """Получение токена для WebSocket подключения"""
    try:
        if not access_token:
            raise HTTPException(status_code=401, detail="Не авторизован")

        # Проверяем токен пользователя
        try:
            payload = jwt.decode(access_token, SECRET_KEY, algorithms=[ALGORITHM])
            user_id = payload.get("user_id")
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                raise HTTPException(status_code=401, detail="Пользователь не найден")
        except jwt.JWTError:
            raise HTTPException(status_code=401, detail="Недействительный токен")

        # Проверяем существование чата
        chat = db.query(ChatRoom).filter(ChatRoom.name == room_name).first()
        if not chat:
            raise HTTPException(status_code=404, detail="Чат не найден")

        # Создаем токен для WebSocket
        token_data = {
            "user_id": user.id,
            "email": user.email,
            "chat_name": room_name,
            "exp": datetime.utcnow() + timedelta(hours=1)
        }
        
        token = jwt.encode(token_data, SECRET_KEY, algorithm=ALGORITHM)
        
        return {"token": token}
    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"Error generating chat token: {e}")
        raise HTTPException(status_code=500, detail="Ошибка генерации токена")

@app.get("/api/chat/{room_name}/exists")
async def check_chat_exists(
    room_name: str,
    db: Session = Depends(get_db)
):
    """Проверка существования чата"""
    chat = db.query(ChatRoom).filter(ChatRoom.name == room_name).first()
    return {"exists": chat is not None}