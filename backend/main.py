import sys
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv()

from api.chat import router as chat_router
from api.project import router as project_router
from db.models import User
from db.session import SessionLocal

app = FastAPI(title="Taverna API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router, prefix="/api", tags=["chat"])
app.include_router(project_router, prefix="/api", tags=["project"])


@app.on_event("startup")
async def ensure_default_user():
    try:
        async with SessionLocal() as db:
            user = await db.scalar(select(User).where(User.name == "default"))
            if not user:
                db.add(User(name="default"))
                await db.commit()
    except Exception:
        # DB may be unavailable before migrations; keep API bootable.
        pass
