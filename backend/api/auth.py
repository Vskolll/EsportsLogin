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
