from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from backend.storage.ws_manager import ws_manager
import logging
log = logging.getLogger("ws")

router = APIRouter()

@router.websocket("/ws/messages")
async def ws_messages(ws: WebSocket):
    log.info("WS client connected")
    await ws_manager.connect(ws)

    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        log.info("WS client disconnected")
        ws_manager.disconnect(ws)

