# website/main.py
from fastapi import FastAPI, Depends, Request, Form, HTTPException, Path
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from database import get_db, engine
from models import Base, User, ChatRoom
from auth import get_current_user, create_session, destroy_session, verify_jwt, hash_password, verify_password
from fastapi.templating import Jinja2Templates
from fastapi import APIRouter
from fastapi.staticfiles import StaticFiles

Base.metadata.create_all(bind=engine)
app = FastAPI()
templates = Jinja2Templates(directory="templates")
router = APIRouter()
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    user = None
    try:
        user = get_current_user(request, db)
    except HTTPException:
        pass  # Игнорируем ошибку 401

    if user:
        return RedirectResponse(url="/chats")  # Перенаправляем аутентифицированных пользователей

    return templates.TemplateResponse("index.html", {"request": request, "user": user})


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Неверный email или пароль")
    
    response = RedirectResponse(url="/chats", status_code=303)  # Редирект на список чатов
    create_session(response, user.id)
    return response


@app.get("/logout")
def logout():
    response = RedirectResponse(url="/", status_code=303)
    destroy_session(response)
    return response
    

@router.get("/auth/verify")
def verify_token_route(token: str = Depends(verify_jwt)):
    return {"valid": True, "user": token}

app.include_router(router)

@app.post("/register")
def register(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    existing_user = db.query(User).filter(User.email == email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email уже зарегистрирован")
    new_user = User(email=email, hashed_password=hash_password(password))
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    response = RedirectResponse(url="/", status_code=303)
    create_session(response, new_user.id)
    return response


@app.get("/chats", response_class=HTMLResponse)
def chat_list(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    chats = db.query(ChatRoom).filter(ChatRoom.owner_id == user.id).all()
    return templates.TemplateResponse("chats.html", {"request": request, "user": user, "chats": chats})


@app.get("/chats/create", response_class=HTMLResponse)
def create_chat_page(request: Request, user: User = Depends(get_current_user)):
    return templates.TemplateResponse("create_chat.html", {"request": request, "user": user})


@app.post("/chats/create")
def create_chat(request: Request, name: str = Form(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    new_chat = ChatRoom(name=name, owner_id=user.id)
    db.add(new_chat)
    db.commit()
    return RedirectResponse(url="/chats", status_code=303)


@app.post("/chats/{chat_id}/delete")
def delete_chat(
    request: Request, 
    chat_id: int = Path(...), 
    db: Session = Depends(get_db), 
    user: User = Depends(get_current_user)
):
    chat = db.query(ChatRoom).filter(ChatRoom.id == chat_id, ChatRoom.owner_id == user.id).first()
    if not chat:
        raise HTTPException(status_code=404, detail="Чат не найден или у вас нет прав на его удаление")

    db.delete(chat)
    db.commit()
    
    return RedirectResponse(url="/chats", status_code=303)