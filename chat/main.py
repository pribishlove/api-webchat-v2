#chat/main.py
import requests, json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Query, Request
from typing import Dict, List, Set
import jwt
from datetime import datetime
import logging
import httpx

logger = logging.getLogger(__name__)

app = FastAPI()

# Секретный ключ должен совпадать с ключом в website
SECRET_KEY = "your_secret_key"
ALGORITHM = "HS256"

# Хранилище активных подключений: {room_name: [websocket]}
connected_clients: Dict[str, List[WebSocket]] = {}

class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[int, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, room_id: int, user_email: str):  # Изменяем параметры
        await websocket.accept()
        if room_id not in self.active_connections:
            self.active_connections[room_id] = []
        self.active_connections[room_id].append(websocket)
        # Убираем broadcast отсюда, так как он будет вызываться в websocket_endpoint

    def disconnect(self, websocket: WebSocket, room_id: int):
        if room_id in self.active_connections:
            self.active_connections[room_id].remove(websocket)
            if not self.active_connections[room_id]:
                del self.active_connections[room_id]

    async def broadcast(self, room_id: int, message: str):
        if room_id in self.active_connections:
            for connection in self.active_connections[room_id]:
                await connection.send_text(message)

manager = ConnectionManager()

async def verify_token(token: str, room_name: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        
        # Проверяем срок действия токена
        if payload.get("exp") and datetime.utcnow().timestamp() > payload["exp"]:
            raise HTTPException(status_code=401, detail="Token expired")
        
        # Проверяем, что токен выдан для этой комнаты
        if payload.get("chat_name") != room_name:
            raise HTTPException(status_code=403, detail="Token is not valid for this chat")
        
        return payload
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

def cleanup_empty_rooms():
    """Очистка пустых комнат"""
    empty_rooms = [room for room, clients in connected_clients.items() if not clients]
    for room in empty_rooms:
        del connected_clients[room]

@app.post("/ws/{room_name}/close")
async def close_room(room_name: str, request: Request):
    try:
        data = await request.json()
        if room_name in connected_clients:
            close_message = json.dumps({
                "type": "chat_deleted",
                "message": data.get("message", "Чат был удален создателем")
            })
            print(f"Sending close message to {len(connected_clients[room_name])} clients") # Для отладки
            
            # Отправляем сообщение всем клиентам
            for websocket in connected_clients[room_name]:
                try:
                    await websocket.send_text(close_message)
                    print(f"Close message sent to client") # Для отладки
                except Exception as e:
                    print(f"Error sending close message: {e}") # Для отладки
            
            # Закрываем все соединения
            for websocket in connected_clients[room_name]:
                try:
                    await websocket.close()
                except Exception as e:
                    print(f"Error closing websocket: {e}") # Для отладки
            
            # Очищаем список клиентов
            connected_clients[room_name] = []
            del connected_clients[room_name]
        
        return {"status": "success"}
    except Exception as e:
        print(f"Error in close_room: {e}") # Для отладки
        return {"status": "error", "message": str(e)}

@app.post("/notify_delete/{room_name}")
async def notify_delete(room_name: str):
    """Уведомление о удалении комнаты"""
    if room_name in connected_clients:
        message = {
            "type": "chat_deleted",
            "message": "Чат был удален создателем"
        }
        
        # Отправляем уведомление всем в комнате
        for websocket in connected_clients[room_name][:]:
            try:
                await websocket.send_json(message)
                await websocket.close(code=1000, reason="Chat deleted")
            except:
                pass
        
        # Удаляем комнату
        del connected_clients[room_name]
        
        return {"status": "success"}
    return {"status": "room not found"}

async def check_chat_exists(room_name: str) -> bool:
    """Проверка существования чата через API website"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"http://website:8000/api/chat/{room_name}/exists")
            if response.status_code == 200:
                data = response.json()
                return data.get("exists", False)
            return False
    except Exception as e:
        logger.error(f"Error checking chat existence: {e}")
        return False

async def broadcast_message(room_name: str, message: dict):
    """Отправка сообщения всем подключенным клиентам"""
    if room_name not in connected_clients:
        return
        
    # Проверяем существование чата перед отправкой
    chat_exists = await check_chat_exists(room_name)
    if not chat_exists:
        # Отправляем уведомление об удалении чата
        for client in connected_clients[room_name][:]:
            try:
                await client.send_json({
                    "type": "system",
                    "message": "Чат был удален. Соединение закрывается."
                })
                await client.close(code=4005)
            except Exception as e:
                logger.error(f"Error closing connection: {e}")
        
        # Удаляем комнату
        del connected_clients[room_name]
        return

    # Отправляем сообщение если чат существует
    for client in connected_clients[room_name][:]:
        try:
            await client.send_json(message)
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            if client in connected_clients[room_name]:
                connected_clients[room_name].remove(client)

@app.websocket("/ws/{room_name}")
async def websocket_endpoint(
    websocket: WebSocket,
    room_name: str
):
    await websocket.accept()
    user_email = None
    
    try:
        # Проверяем существование чата при подключении
        if not await check_chat_exists(room_name):
            await websocket.send_json({
                "type": "system",
                "message": "Чат не существует"
            })
            await websocket.close(code=4006)
            return

        # Ждем сообщение с авторизацией
        auth_data = await websocket.receive_json()
        if auth_data.get('type') != 'authorization':
            await websocket.close(code=4000)
            return
            
        token = auth_data.get('token')
        if not token:
            await websocket.close(code=4001)
            return

        # Проверяем токен
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            if payload['chat_name'] != room_name:
                await websocket.close(code=4002)
                return
                
            user_email = payload['email']
        except jwt.InvalidTokenError:
            await websocket.close(code=4003)
            return

        # Добавляем в комнату
        if room_name not in connected_clients:
            connected_clients[room_name] = []
        connected_clients[room_name].append(websocket)

        # Отправляем уведомление о подключении нового пользователя
        await broadcast_message(room_name, {
            "type": "system",
            "message": f"Пользователь {user_email} присоединился к чату"
        })

        # Основной цикл чата
        try:
            while True:
                data = await websocket.receive_json()
                # Проверяем существование чата перед отправкой каждого сообщения
                if not await check_chat_exists(room_name):
                    await websocket.send_json({
                        "type": "system",
                        "message": "Чат был удален. Соединение закрывается."
                    })
                    await websocket.close(code=4005)
                    return

                await broadcast_message(room_name, {
                    "type": "message",
                    "user": user_email,
                    "message": data["message"]
                })
        except WebSocketDisconnect:
            # Отправляем уведомление об отключении пользователя
            if room_name in connected_clients:
                connected_clients[room_name].remove(websocket)
                await broadcast_message(room_name, {
                    "type": "system",
                    "message": f"Пользователь {user_email} покинул чат"
                })
                if not connected_clients[room_name]:
                    del connected_clients[room_name]
    except Exception as e:
        logger.error(f"Error in websocket connection: {e}")
        if user_email and room_name in connected_clients:
            connected_clients[room_name].remove(websocket)
            await broadcast_message(room_name, {
                "type": "system",
                "message": f"Пользователь {user_email} отключился из-за ошибки"
            })
        await websocket.close(code=4004)

@app.post("/ws/{room_name}/broadcast")
async def broadcast_message(room_name: str, message: dict):
    if room_name in connected_clients:
        # Отправляем сообщение всем подключенным клиентам
        for websocket in connected_clients[room_name][:]:  # Создаем копию списка
            try:
                await websocket.send_json(message)
                if message.get("type") == "chat_deleted":
                    await websocket.close(code=1000, reason="Chat deleted")
            except Exception as e:
                print(f"Error broadcasting message: {e}")
        
        # Если это сообщение об удалении, очищаем список клиентов
        if message.get("type") == "chat_deleted":
            connected_clients[room_name] = []
            if room_name in connected_clients:
                del connected_clients[room_name]
        
        return {"status": "success"}
    return {"status": "room not found"}