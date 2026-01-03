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
        # Iterate over a copy since we may remove dead connections during send
        for ws in list(self.connections):
            try:
                await ws.send_json(data)
            except Exception as e:
                # If a connection fails, try to close and remove it so future
                # broadcasts won't repeatedly fail.
                log.debug(f"WS send failed, disconnecting client: {e}")
                try:
                    await ws.close()
                except Exception:
                    pass
                try:
                    # remove from our list
                    self.disconnect(ws)
                except Exception:
                    pass

ws_manager = WSManager()
