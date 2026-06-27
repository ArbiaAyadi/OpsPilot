
import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Ajouter le dossier parent au path pour les imports
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "proxmox"))

from agent.config           import HOST, PORT, DIST_DIR
from agent.surveillance     import demarrer as demarrer_surveillance, set_ws_queue
from agent.websocket_handler import handle_connection, broadcaster
from agent.rules_engine     import generer_regles_ia
import agent.surveillance as surveillance  # module entier : dernier_etat est reassigne par le thread


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Base de donnees
    try:
        from database import init_db
        db_ok = init_db()
        print(f"[OpsPilot] DB: {'PostgreSQL' if db_ok else 'Memoire (fallback)'}")
    except Exception as e:
        print(f"[OpsPilot] DB: Memoire (fallback) — {e}")

    # Queue WebSocket partagee
    ws_queue = asyncio.Queue()
    set_ws_queue(ws_queue)

    # Thread surveillance
    loop = asyncio.get_event_loop()
    demarrer_surveillance(loop)

    # Broadcaster WebSocket
    asyncio.create_task(broadcaster(ws_queue))

    # Generer les regles IA au demarrage (apres 15s)
    async def _init_regles():
        await asyncio.sleep(15)
        if surveillance.dernier_etat:
            print("[AI Rules] Generation initiale des regles...")
            await generer_regles_ia(surveillance.dernier_etat)
        else:
            print("[AI Rules] Pas de donnees cluster -- regles statiques utilisees")

    asyncio.create_task(_init_regles())

    print(f"[OpsPilot] http://{HOST}:{PORT}")
    yield


# ── Application FastAPI ───────────────────────────────────────────────────────
app = FastAPI(title="OpsPilot", version="6.0-modular", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes REST
from agent.routes import router
app.include_router(router)


# WebSocket
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await handle_connection(ws)


# Servir le frontend React
if DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(DIST_DIR / "assets")), name="assets")

    @app.get("/")
    def index():
        return FileResponse(str(DIST_DIR / "index.html"))

    @app.get("/{path:path}")
    def spa(path: str):
        f = DIST_DIR / path
        return FileResponse(str(f)) if f.exists() and f.is_file() else FileResponse(str(DIST_DIR / "index.html"))
else:
    @app.get("/")
    def no_build():
        return {"message": "Run: cd frontend && npm run build"}


if __name__ == "__main__":
    uvicorn.run("agent.main:app", host=HOST, port=PORT, reload=False, log_level="info")