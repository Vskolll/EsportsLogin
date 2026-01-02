from fastapi import WebSocket
import logging

log = logging.getLogger("ws")

class WSManager:
    def __init__(self):
        self.connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.append(ws)
        log.info("WS connected")

    def disconnect(self, ws: WebSocket):
        self.connections.remove(ws)
        log.info("WS disconnected")

    async def broadcast(self, data: dict):
        log.info("Broadcasting message")
        for ws in self.connections:
            await ws.send_json(data)

ws_manager = WSManager()
