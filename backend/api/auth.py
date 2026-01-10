# backend/routes/auth.py
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession
from telethon import TelegramClient
import asyncio
import json
import logging

from backend.config import API_ID, API_HASH
from backend.storage.login_state import LoginState
from backend.telegram.qr_login import create_qr_login
from backend.telegram.listener import setup_message_listener
from backend.storage.ws_manager import ws_manager

log = logging.getLogger("auth")

router = APIRouter()
state = LoginState()


# =======================
# MODELS
# =======================

class StartResponse(BaseModel):
    login_id: str
    qr_url: str
    expires_at: int


class PasswordRequest(BaseModel):
    login_id: str
    password: str


class ListenRequest(BaseModel):
    login_id: str


class UnlistenRequest(BaseModel):
    login_id: str


class ImportSessionRequest(BaseModel):
    login_id: str
    session_string: str


# =======================
# START LOGIN
# =======================

@router.post("/start", response_model=StartResponse)
async def start_login():
    log.info("Starting QR login")

    client, qr = await create_qr_login()
    login_id = qr.token.hex()

    state.create(login_id, client, qr)

    async def _monitor_qr():
        try:
            await qr.wait()
            log.info(f"QR login completed for {login_id}")
            state.set_status(login_id, "authorized")

            # сохраняем StringSession сразу
            try:
                sess = client.session.save()
                state.set_session_string(login_id, sess)
            except Exception as e:
                log.debug(f"Failed to save session string: {e}")

            try:
                await ws_manager.broadcast({"type": "authorized", "login_id": login_id})
            except Exception as e:
                log.debug(f"Failed to broadcast authorized event: {e}")

        except SessionPasswordNeededError:
            log.info(f"QR login requires 2FA password for {login_id}")
            state.set_status(login_id, "need_password")
            try:
                await ws_manager.broadcast({"type": "need_password", "login_id": login_id})
            except Exception as e:
                log.debug(f"Failed to broadcast need_password event: {e}")

        except Exception as e:
            log.debug(f"QR monitor error for {login_id}: {e}")

    asyncio.create_task(_monitor_qr())

    return StartResponse(
        login_id=login_id,
        qr_url=qr.url,
        expires_at=int(qr.expires.timestamp()),
    )


# =======================
# STATUS (logging only)
# =======================

@router.get("/status/{login_id}")
async def check_status(login_id: str):
    log.debug(f"Status check requested for {login_id} - logging only")
    item = await state.get(login_id)
    if not item:
        return Response(status_code=204)
    log.info(f"Status endpoint hit for {login_id}; listener_started={item.get('listener_started', False)}")
    return Response(status_code=204)


# =======================
# SEND 2FA PASSWORD
# =======================

@router.post("/password")
async def send_password(data: PasswordRequest):
    log.info(f"Sending 2FA password for login {data.login_id}")

    item = await state.get(data.login_id)
    if not item:
        raise HTTPException(status_code=404, detail="Login not found")

    client = item.get("client")
    if not client:
        raise HTTPException(status_code=500, detail="Client not available")

    try:
        await client.sign_in(password=data.password)
        state.set_status(data.login_id, "authorized")

        # сохранить session_string
        try:
            sess = client.session.save()
            state.set_session_string(data.login_id, sess)
        except Exception as e:
            log.debug(f"Failed to save session string after password: {e}")

        try:
            await ws_manager.broadcast({"type": "authorized", "login_id": data.login_id})
        except Exception as e:
            log.debug(f"Failed to broadcast authorized after password: {e}")

        return {"status": "ok"}

    except Exception as e:
        log.error(f"2FA failed: {e}")
        raise HTTPException(status_code=400, detail="Invalid password")


# =======================
# LIST LOGINS
# =======================

@router.get("/logins")
async def list_logins():
    out = []
    for login_id in list(state.data.keys()):
        base = dict(state.data.get(login_id, {}))
        username = None

        try:
            item = await state.get(login_id)
            if item:
                client = item.get("client")
                if client:
                    try:
                        me = await client.get_me()
                        if me:
                            username = getattr(me, "username", None) or getattr(me, "first_name", None)
                    except Exception:
                        username = None
        except Exception:
            username = None

        # убираем не-сериализуемые поля
        base.pop("client", None)
        base.pop("listener_handler", None)
        base.pop("qr", None)

        entry = {"login_id": login_id, **base, "username": username}
        out.append(entry)

    return out


# =======================
# START LISTENING
# =======================

@router.post("/listen")
async def start_listen(data: ListenRequest):
    log.info(f"Start listening request for {data.login_id}")

    item = await state.get(data.login_id)
    if not item:
        raise HTTPException(status_code=404, detail="Login not found")

    client = item.get("client")
    if not client:
        raise HTTPException(status_code=500, detail="Client not available")

    if item.get("listener_started"):
        return {"status": "already_listening"}

    try:
        handler = setup_message_listener(client, ws_manager, data.login_id)
    except Exception as e:
        log.error(f"Failed to attach listener: {e}")
        raise HTTPException(status_code=500, detail="Failed to start listener")

    state.set_listener_started(data.login_id, True)
    item["listener_handler"] = handler

    return {"status": "ok"}


# =======================
# WAKE
# =======================

@router.post("/wake")
async def wake_session(data: ListenRequest):
    log.info(f"Wake request for {data.login_id}")

    item = await state.get(data.login_id)
    if not item:
        raise HTTPException(status_code=404, detail="Login not found")

    client = item.get("client")
    if not client:
        raise HTTPException(status_code=500, detail="Client not available")

    # Telethon: is_connected() это метод
    try:
        if not client.is_connected():
            await client.connect()
    except Exception as e:
        log.error(f"Wake: failed to connect client for {data.login_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to connect client")

    try:
        await client.get_me()
    except Exception as e:
        log.debug(f"Wake: get_me failed for {data.login_id}: {e}")

    try:
        if item.get("listener_started") and not item.get("listener_handler"):
            handler = setup_message_listener(client, ws_manager, data.login_id)
            item["listener_handler"] = handler
    except Exception as e:
        log.debug(f"Wake: failed to reattach listener for {data.login_id}: {e}")

    return {"status": "ok"}


# =======================
# UNLISTEN
# =======================

@router.post("/unlisten")
async def stop_listen(data: UnlistenRequest):
    log.info(f"Stop listening request for {data.login_id}")

    item = await state.get(data.login_id)
    if not item:
        raise HTTPException(status_code=404, detail="Login not found")

    client = item.get("client")
    handler = item.get("listener_handler")

    if not client or not handler:
        state.set_listener_started(data.login_id, False)
        item.pop("listener_handler", None)
        return {"status": "not_listening"}

    try:
        client.remove_event_handler(handler)
    except Exception as e:
        log.error(f"Failed to remove handler: {e}")
        raise HTTPException(status_code=500, detail="Failed to stop listener")

    item.pop("listener_handler", None)
    state.set_listener_started(data.login_id, False)
    return {"status": "ok"}


# =======================
# EXPORT TELETHON SESSION
# =======================

@router.get("/session/{login_id}")
async def export_telethon_session(login_id: str):
    item = await state.get(login_id)
    if not item:
        raise HTTPException(status_code=404, detail="login_id not found")

    sess = item.get("session_string")
    client = item.get("client")

    # если ещё не сохраняли — попробуем сохранить сейчас
    if not sess and client:
        try:
            sess = client.session.save()
            state.set_session_string(login_id, sess)
        except Exception as e:
            log.error(f"Export session failed for {login_id}: {e}")

    if not sess:
        raise HTTPException(status_code=404, detail="session_string not available")

    payload = {"version": 1, "login_id": login_id, "session_string": sess}
    content = json.dumps(payload, ensure_ascii=False, indent=2)

    headers = {"Content-Disposition": f'attachment; filename="tg-session-{login_id}.json"'}
    return Response(content, media_type="application/json", headers=headers)


# =======================
# IMPORT TELETHON SESSION
# =======================

@router.post("/session/import")
async def import_telethon_session(data: ImportSessionRequest):
    login_id = (data.login_id or "").strip()
    session_string = (data.session_string or "").strip()

    if not login_id or not session_string:
        raise HTTPException(status_code=400, detail="login_id and session_string are required")

    # отключим старый client если был
    old = state.data.get(login_id)
    if old and old.get("client"):
        try:
            await old["client"].disconnect()
        except Exception:
            pass

    client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
    await client.connect()

    try:
        me = await client.get_me()
    except Exception:
        me = None

    if not me:
        try:
            await client.disconnect()
        except Exception:
            pass
        raise HTTPException(status_code=400, detail="Session is not authorized / invalid")

    # upsert
    state.data[login_id] = {
        "client": client,
        "status": "authorized",
        "listener_started": False,
        "listener_handler": None,
        "session_string": session_string,
        "created_at": state.data.get(login_id, {}).get("created_at", 0) or 0,
        "updated_at": 0,
    }
    state.set_session_string(login_id, session_string)
    state.set_status(login_id, "authorized")

    try:
        await ws_manager.broadcast({"type": "session_imported", "login_id": login_id})
    except Exception:
        pass

    username = getattr(me, "username", None) or getattr(me, "first_name", None) or "unknown"
    return {"status": "ok", "login_id": login_id, "username": username}
