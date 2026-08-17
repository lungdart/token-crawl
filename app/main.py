from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import logs
from app.config import settings
from app import db
from app.gen import llm, services
from app.world import floors


@asynccontextmanager
async def lifespan(app: FastAPI):
    logs.configure(settings.log_level)
    db.init()
    briefs = floors.load_floors()
    # Each floor's stock lines are written now rather than mid-fight. First boot pays for
    # them once; after that they are cached and this is free.
    services.warm_response_banks(briefs)
    yield
    llm.close_backend()  # shut the connections to OpenRouter


def create_app() -> FastAPI:
    application = FastAPI(title="Token Crawl", lifespan=lifespan)
    from app.web.routes import router

    application.include_router(router)
    application.mount("/static", StaticFiles(directory="app/web/static"), name="static")
    return application


app = create_app()
