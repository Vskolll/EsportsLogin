# Telegram QR Web Login

Lightweight FastAPI service to perform Telegram QR logins and stream messages to an admin UI.

Quick start

1. Create `.env` with Telegram API credentials:

```
TG_API_ID=123456
TG_API_HASH=your_api_hash
```

2. Install dependencies (use virtualenv):

```bash
pip install -r requirements.txt
```

3. Run server:

```bash
uvicorn backend.app:app --reload
```

4. Open UI:

- User QR flow: http://localhost:8000/
- Admin UI (messages): http://localhost:8000/admin

Notes
- Session files are stored in `sessions/` (do not commit them). `.gitignore` already ignores this directory.
- Persistent mapping of discovered sessions is stored in `storage/login_state.json` (also ignored).
- For production: protect admin endpoints and websocket (add an admin token or reverse-proxy auth), and rotate sessions if exposed.
