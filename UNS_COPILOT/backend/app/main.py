from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import AsyncSessionLocal, create_tables
from app.routers import conversations, users
from app.seed import seed_default_users

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_tables()
    async with AsyncSessionLocal() as session:
        await seed_default_users(session, settings.seed_users.split(","))
    yield


app = FastAPI(
    title="UNS Copilot",
    description="Natural-language chat backend over UNS_SILVER, via controlled tool-calling",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(users.router)
app.include_router(conversations.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
