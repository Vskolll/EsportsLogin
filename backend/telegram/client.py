from telethon import TelegramClient
from backend.config import API_ID, API_HASH
import uuid
import os

os.makedirs("sessions", exist_ok=True)

def create_client():
    session = f"sessions/{uuid.uuid4().hex}"
    client = TelegramClient(session, API_ID, API_HASH)
    # best-effort record of session path for LoginState.create
    try:
        client._session_path = session
    except Exception:
        pass
    return client
