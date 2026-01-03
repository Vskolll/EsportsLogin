from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel
from telethon.errors import SessionPasswordNeededError
import asyncio
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

    # Start background task that waits for the QR login to complete and
    # notifies connected frontends (via ws_manager) about password requirement
    # or successful authorization. This avoids requiring frontend polling.
    async def _monitor_qr():
        try:
            # wait() will raise SessionPasswordNeededError if 2FA is required
            await qr.wait()
            # login completed successfully
            log.info(f"QR login completed for {login_id}")
            state.set_status(login_id, "authorized")
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
    """Status endpoint: logging-only.

    This endpoint no longer performs any client/cloud operations or
    starts listeners. It only logs that a status check was requested and
    returns 204 No Content. The frontend should not poll this endpoint.
    Admin tooling can still hit it for debugging/logging.
    """
    log.debug(f"Status check requested for {login_id} - logging only")

    # Try to resolve stored item for better logging, but don't perform any
    # calls to the Telegram client or mutate state here.
    try:
        item = await state.get(login_id)
    except Exception:
        item = None

    if not item:
        log.warning(f"Status check: login not found {login_id}")
        return Response(status_code=204)

    log.info(
        f"Status endpoint hit for {login_id}; listener_started={item.get('listener_started', False)}"
    )
    return Response(status_code=204)



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
        # mark authorized and notify frontends so they can redirect
        state.set_status(data.login_id, "authorized")
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


@router.post("/wake")
async def wake_session(data: ListenRequest):
    """Attempt to (re)connect the Telegram client for a given login_id and
    reattach message listener if it was previously started.

    This is a lightweight utility for debugging/unsticking sessions that may
    have dropped their connection or whose handlers were not attached after
    a restart.
    """
    log.info(f"Wake request for {data.login_id}")

    item = await state.get(data.login_id)
    if not item:
        log.warning("Login not found for wake")
        raise HTTPException(status_code=404)

    client = item.get("client")

    # Try to ensure the client is connected
    try:
        if client and not getattr(client, "is_connected", False):
            await client.connect()
        elif not client:
            # state.get should have created a client, but defensively try again
            # (this will also store the runtime client in LoginState).
            item = await state.get(data.login_id)
            client = item.get("client")
            if client and not getattr(client, "is_connected", False):
                await client.connect()
    except Exception as e:
        log.error(f"Wake: failed to connect client for {data.login_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to connect client")

    # Perform a light RPC to prime the connection (get_me is cheap)
    try:
        if client:
            await client.get_me()
    except Exception as e:
        # don't fail the whole call; just log for debugging
        log.debug(f"Wake: get_me failed for {data.login_id}: {e}")

    # If listener was intended to be running but handler not present, reattach it
    try:
        if item.get("listener_started") and not item.get("listener_handler"):
            handler = setup_message_listener(client, ws_manager, data.login_id)
            item["listener_handler"] = handler
    except Exception as e:
        log.debug(f"Wake: failed to reattach listener for {data.login_id}: {e}")

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
