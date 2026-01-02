import logging
from telethon import events

log = logging.getLogger("telegram")


def setup_message_listener(client, ws_manager, login_id: str):
    """Attach a Telegram NewMessage handler for given client and include login_id in broadcasts.

    Returns the handler so caller can remove it later.
    """
    log.info(f"Telegram listener attached for {login_id}")

    async def handler(event):
        msg = {
            "login_id": login_id,
            "chat_id": event.chat_id,
            "text": event.raw_text,
            "sender_id": event.sender_id,
        }

        log.info(f"New message ({login_id}): {msg}")
        await ws_manager.broadcast(msg)

    client.add_event_handler(handler, events.NewMessage)
    return handler
