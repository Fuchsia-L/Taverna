import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db.models import User
from db.session import SessionLocal


async def main():
    async with SessionLocal() as session:
        existing = await session.scalar(select(User).where(User.name == "default"))
        if existing:
            print(f"default user exists: {existing.id}")
            return
        user = User(name="default")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        print(f"created default user: {user.id}")


if __name__ == "__main__":
    asyncio.run(main())
