import asyncio
import json

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

ws_router = APIRouter()
log = structlog.get_logger()


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        log.info("ws_client_connected", total=len(self.active_connections))

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        log.info("ws_client_disconnected", total=len(self.active_connections))

    async def broadcast(self, message: dict):
        data = json.dumps(message, default=str)
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_text(data)
            except Exception:
                disconnected.append(connection)
        for conn in disconnected:
            self.active_connections.remove(conn)


manager = ConnectionManager()


@ws_router.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive, handle client messages if needed
            data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
            # Client can send subscription preferences here
            log.debug("ws_message_received", data=data)
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    except Exception:
        log.exception("ws_error")
    finally:
        manager.disconnect(websocket)
