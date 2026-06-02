from contextlib import asynccontextmanager
from fastapi import FastAPI
from config import settings

from models.base import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    await init_db()
    yield


app = FastAPI(title="SearchV2", version="0.1.0", lifespan=lifespan)

# Routers registered as phases are implemented
from api import tome, observation, mission, knowledge, research, events

app.include_router(tome.router, prefix="/api", tags=["tome"])
app.include_router(observation.router, prefix="/api", tags=["observation"])
app.include_router(mission.router, prefix="/api", tags=["mission"])
app.include_router(knowledge.router, prefix="/api", tags=["knowledge"])
app.include_router(research.router, prefix="/api", tags=["research"])
app.include_router(events.router, prefix="/api", tags=["events"])
