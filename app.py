from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Request
from config import settings

from models.base import init_db
from core.queue import worker as queue_worker
from core.rss import poller as rss_poller
from core.curiosity import scheduler as curiosity_scheduler
from core.belief import scheduler as belief_scheduler


async def verify_token(request: Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = auth[7:]
    if token != settings.secret_key:
        raise HTTPException(status_code=401, detail="Invalid token")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    await init_db()
    await queue_worker.start()
    await rss_poller.start()
    await curiosity_scheduler.start()
    await belief_scheduler.start()
    yield
    await belief_scheduler.stop()
    await curiosity_scheduler.stop()
    await rss_poller.stop()
    await queue_worker.stop()


app = FastAPI(title="SearchV2", version="0.1.0", lifespan=lifespan,
              dependencies=[Depends(verify_token)])

# Routers registered as phases are implemented
from api import tome, observation, mission, knowledge, research, events, retrieve

app.include_router(tome.router, prefix="/api", tags=["tome"])
app.include_router(observation.router, prefix="/api", tags=["observation"])
app.include_router(mission.router, prefix="/api", tags=["mission"])
app.include_router(knowledge.router, prefix="/api", tags=["knowledge"])
app.include_router(research.router, prefix="/api", tags=["research"])
app.include_router(events.router, prefix="/api", tags=["events"])
app.include_router(retrieve.router, prefix="/api", tags=["retrieve"])
