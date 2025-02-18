import requests
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException

app = FastAPI()

# Хранилище активных WebSocket-подключений
class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, room_name: str):
        await websocket.accept()
        if room_name not in self.active_connections:
            self.active_connections[room_name] = []
        self.active_connections[room_name].append(websocket)

    def disconnect(self, websocket: WebSocket, room_name: str):
        if room_name in self.active_connections:
            self.active_connections[room_name].remove(websocket)
            if not self.active_connections[room_name]:
                del self.active_connections[room_name]  # Удаляем комнату, если никого нет

    async def broadcast(self, room_name: str, message: str):
        if room_name in self.active_connections:
            for connection in self.active_connections[room_name]:
                await connection.send_text(message)


manager = ConnectionManager()


def verify_jwt_remote(token: str):
    """ Проверяем JWT через API микросервиса website """
    response = requests.get("http://website:8000/auth/verify", headers={"Authorization": f"Bearer {token}"})
    if response.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid token")
    return response.json()["user"]


@app.websocket("/ws/{room_name}")
async def websocket_endpoint(websocket: WebSocket, room_name: str, token: str):
    """ WebSocket-эндпоинт для работы с чатом """
    try:
        user = verify_jwt_remote(token)  # Проверяем токен через website
        await manager.connect(websocket, room_name)
        await manager.broadcast(room_name, f"🔵 {user['email']} подключился к {room_name}")

        while True:
            message = await websocket.receive_text()
            await manager.broadcast(room_name, f"{user['email']}: {message}")

    except WebSocketDisconnect:
        manager.disconnect(websocket, room_name)
        await manager.broadcast(room_name, f"🔴 {user['email']} отключился от {room_name}")
