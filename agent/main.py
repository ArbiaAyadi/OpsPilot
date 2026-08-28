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

from agent.config           import HOST, PORT, DIST_DIR, SSL_KEYFILE, SSL_CERTFILE
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

    # ← AJOUT : filtre le bruit ConnectionResetError [WinError 10054] --
    # bug connu et documenté d'asyncio ProactorEventLoop sur Windows,
    # spécifiquement lié à uvicorn + SSL (confirmé : github.com/Kludex/
    # uvicorn/discussions/2105 et 2133, entre autres -- plusieurs issues
    # ouvertes depuis des années, toujours sans correctif upstream officiel).
    # Se produit quand un client (navigateur, WebSocket) ferme abruptement
    # sa connexion et que Windows a déjà coupé le socket avant qu'asyncio
    # ne tente son propre socket.shutdown() de nettoyage -- une purge
    # interne sur une connexion déjà morte, pas une vraie erreur : le
    # serveur continue de fonctionner normalement après chaque occurrence
    # (confirmé dans les logs reçus). Seule CETTE exception précise est
    # filtrée -- tout le reste continue de remonter et de s'afficher
    # normalement, rien n'est masqué en dehors de ce bruit spécifique.
    def _filtrer_bruit_windows_ssl(loop, context):
        exception = context.get("exception")
        if isinstance(exception, ConnectionResetError):
            return
        loop.default_exception_handler(context)
    loop.set_exception_handler(_filtrer_bruit_windows_ssl)

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

# ← AJOUT : router d'authentification -- SÉPARÉ de agent.routes (voir
# auth_routes.py), inclus ici directement, jamais derrière la dependency
# get_current_user qui protège router ci-dessus. C'est le seul endroit de
# toute l'API où l'utilisateur n'est pas encore authentifié.
from auth_routes import router as auth_router
app.include_router(auth_router)


# WebSocket
# ← MODIFIÉ : vérifie la session AVANT d'accepter la connexion -- sans ça,
# les pages étaient protégées mais le flux de données temps réel restait
# ouvert à tout le monde, une incohérence de sécurité. ws.cookies lit le
# même cookie posé par auth_routes.py au login (COOKIE_NAME identique des
# deux côtés). Fermeture avec le code 1008 (Policy Violation, RFC 6455) --
# code standard pour une connexion refusée pour raison d'autorisation,
# reconnu par les clients WebSocket. N'importe le rien à
# agent.websocket_handler (handle_connection) -- la vérification se fait
# entièrement ici, avant de lui transmettre la main.
from auth_routes import COOKIE_NAME
import database


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    token   = ws.cookies.get(COOKIE_NAME)
    session = database.get_session(token) if token else None
    if not session:
        await ws.close(code=1008)
        return
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
    # ← AJOUT : HTTPS activé UNIQUEMENT si les deux variables sont
    # renseignées ET que les fichiers existent réellement sur disque --
    # sinon repli explicite sur HTTP (comportement inchangé) avec un
    # avertissement clair, jamais un crash silencieux au démarrage si mal
    # configuré (ex: chemin qui contient une faute de frappe).
    ssl_kwargs = {}
    if SSL_KEYFILE and SSL_CERTFILE:
        from pathlib import Path as _Path
        if _Path(SSL_KEYFILE).exists() and _Path(SSL_CERTFILE).exists():
            ssl_kwargs = {"ssl_keyfile": SSL_KEYFILE, "ssl_certfile": SSL_CERTFILE}
            print(f"[OpsPilot] HTTPS active -- https://{HOST}:{PORT}")
        else:
            print(f"[OpsPilot] ⚠ SSL_KEYFILE/SSL_CERTFILE definis mais introuvables sur disque -- repli sur HTTP")

    uvicorn.run("agent.main:app", host=HOST, port=PORT, reload=False, log_level="info", **ssl_kwargs)