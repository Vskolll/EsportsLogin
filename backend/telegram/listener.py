import logging
from telethon import events

log = logging.getLogger("telegram")

def setup_message_listener(client, ws_manager):
    log.info("Telegram listener attached")

    @client.on(events.NewMessage)
    async def handler(event):
        msg = {
            "chat_id": event.chat_id,
            "text": event.raw_text,
            "sender_id": event.sender_id,
        }

        log.info(f"New message: {msg}")
        await ws_manager.broadcast(msg)
