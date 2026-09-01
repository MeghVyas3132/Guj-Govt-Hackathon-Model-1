"""Seed demonstration accounts, one per role.

These exist so the access model can be shown rather than described: log in as
each and the same page behaves differently.
"""

import asyncio
import os

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models.department import Department
from app.models.user import User

DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "Sentinel@2026")

USERS = [
    ("root@gujarat.gov.in", "State Administrator", "super_admin", None),
    ("mun.admin@gujarat.gov.in", "Municipal Administrator", "dept_admin", "MUN"),
    ("analyst@gujarat.gov.in", "Crime Analyst", "analyst", "POL"),
    ("viewer@gujarat.gov.in", "Control Room Operator", "viewer", "POL"),
]


async def main() -> None:
    async with SessionLocal() as session:
        departments = {
            d.code: d.id
            for d in (await session.execute(select(Department))).scalars().all()
        }
        created = 0
        for email, name, role, dept_code in USERS:
            existing = (
                await session.execute(select(User).where(User.email == email))
            ).scalar_one_or_none()
            if existing is not None:
                existing.role = role
                continue
            session.add(
                User(
                    email=email,
                    full_name=name,
                    role=role,
                    password_hash=hash_password(DEMO_PASSWORD),
                    department_id=departments.get(dept_code) if dept_code else None,
                )
            )
            created += 1
        await session.commit()
    print(f"Users: {created} created, {len(USERS) - created} updated")
    print(f"Password for all demo accounts: {DEMO_PASSWORD}")


if __name__ == "__main__":
    asyncio.run(main())
