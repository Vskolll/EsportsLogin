from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from backend.logging_config import setup_logging
    
from backend.api.auth import router as auth_router
from backend.api.ws import router as ws_router

app = FastAPI(title="Telegram QR Login")

setup_logging()

app.include_router(auth_router, prefix="/auth")
app.include_router(ws_router, prefix="/auth")

app.mount("/static", StaticFiles(directory="backend/frontend"), name="static")

@app.get("/")
def index():
    return FileResponse("backend/frontend/index.html")

@app.get("/admin")
def admin():
    return FileResponse("backend/frontend/admin.html")
