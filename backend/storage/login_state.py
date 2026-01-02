import json
import os
import logging
from typing import Optional

from telethon import TelegramClient
from backend.config import API_ID, API_HASH

log = logging.getLogger("login_state")


class LoginState:
    """Persistent login state.

    Persists a mapping login_id -> { session: <path>, status, listener_started }
    and lazily creates/connects TelegramClient instances when requested.
    """

    def __init__(self, path: str = "storage/login_state.json"):
        self.path = path
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self.data: dict[str, dict] = {}
        # runtime-only client objects
        self._clients: dict[str, TelegramClient] = {}

        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception as e:
                log.error(f"Failed to load login state: {e}")
                self.data = {}
        else:
            # If no persisted mapping exists, try to discover existing session files
            # so sessions survive server restarts. We look into the top-level `sessions/`
            # folder and add entries for any .session files found.
            try:
                if os.path.isdir("sessions"):
                    for fn in os.listdir("sessions"):
                        if fn.endswith(".session"):
                            key = fn.split(".session")[0]
                            if key not in self.data:
                                self.data[key] = {
                                    "session": os.path.join("sessions", fn),
                                    "status": "unknown",
                                    "listener_started": False,
                                }
                    # persist discovery
                    self._save()
            except Exception:
                pass

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.error(f"Failed to save login state: {e}")

    def create(self, login_id: str, client: TelegramClient, qr=None):
        session = getattr(client, "_session_path", None)
        # Telethon's client.session.filename may exist; fall back to repr
        if not session:
            session = getattr(client.session, "filename", None) or getattr(client.session, "path", None)

        self.data[login_id] = {
            "session": session,
            "status": "waiting",
            "listener_started": False,
        }

        # keep runtime client available
        self._clients[login_id] = client

        self._save()

    def list(self):
        """Return shallow copy of stored items (without runtime clients)."""
        return [{"login_id": k, **v} for k, v in self.data.items()]

    async def get(self, login_id: str) -> Optional[dict]:
        """Return item dict including a connected TelegramClient under key 'client'.

        This will lazily create and connect the client if needed.
        """
        item = self.data.get(login_id)
        if not item:
            return None

        # If runtime client exists and is connected, return it
        client = self._clients.get(login_id)
        try:
            if client and getattr(client, "is_connected", False):
                out = dict(item)
                out["client"] = client
                return out
        except Exception:
            pass

        # Create TelegramClient instance from session path if needed
        session = item.get("session")
        if not session:
            return None

        client = TelegramClient(session, API_ID, API_HASH)
        # store runtime reference
        self._clients[login_id] = client

        # connect (may raise)
        try:
            await client.connect()
        except Exception as e:
            log.debug(f"Failed to connect client {login_id}: {e}")
            # still return item without a connected client
            out = dict(item)
            out["client"] = client
            return out

        out = dict(item)
        out["client"] = client
        return out

    def set_listener_started(self, login_id: str, started: bool):
        item = self.data.get(login_id)
        if not item:
            return
        item["listener_started"] = bool(started)
        self._save()

    def set_status(self, login_id: str, status: str):
        item = self.data.get(login_id)
        if not item:
            return
        item["status"] = status
        self._save()

    def remove(self, login_id: str):
        # remove runtime client if exists
        client = self._clients.pop(login_id, None)
        if client:
            try:
                # do not await here; caller should disconnect if needed
                client.disconnect()
            except Exception:
                pass

        if login_id in self.data:
            self.data.pop(login_id)
            self._save()

