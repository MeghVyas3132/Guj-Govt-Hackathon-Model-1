"""Seed the place aliases the geocoder resolves names through.

These are starting data, not limits. A department whose cameras are named after
villages nobody here has heard of adds rows through the API; no deploy.

Each alias carries how it was established, so a lookup is never mistaken for a
guess when someone audits why a camera sits where it does.
"""

import asyncio

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models.admin_boundary import AdminBoundary
from app.models.source_connector import PlaceAlias

# alias -> (district name in the 2011 Census boundary data, how it was established)
ALIASES: dict[str, tuple[str, str]] = {
    # Spellings that differ from the census form
    "ahmedabad": ("Ahmadabad", "common spelling of the census name"),
    "amdavad": ("Ahmadabad", "common spelling of the census name"),
    "mehsana": ("Mahesana", "common spelling of the census name"),
    "dahod": ("Dohad", "common spelling of the census name"),
    "banaskantha": ("Banas Kantha", "common spelling of the census name"),
    "banas kantha": ("Banas Kantha", "census name"),
    "sabarkantha": ("Sabar Kantha", "common spelling of the census name"),
    "panchmahal": ("Panch Mahals", "common spelling of the census name"),
    "kutch": ("Kachchh", "common spelling of the census name"),
    "kuchchh": ("Kachchh", "common spelling of the census name"),
    "dangs": ("The Dangs", "common spelling of the census name"),
    # Towns and talukas
    "bilimora": ("Navsari", "town in Navsari district"),
    "gandevi": ("Navsari", "taluka of Navsari district"),
    "khapariya": ("Navsari", "village in Gandevi taluka"),
    "khaparia": ("Navsari", "village in Gandevi taluka"),
    "khergam": ("Navsari", "two Khergams exist, both in Navsari"),
    "kheram": ("Navsari", "reading of Khergam; both Khergams are in Navsari"),
    "dhanori": ("Navsari", "village in Gandevi taluka"),
    "tankal": ("Navsari", "village in Chikhli taluka"),
    "gandhidham": ("Kachchh", "town in Kachchh district"),
    "bhuj": ("Kachchh", "district headquarters"),
    "adalaj": ("Gandhinagar", "town in Gandhinagar district"),
    "dehgam": ("Gandhinagar", "taluka of Gandhinagar district"),
    "kalol": ("Gandhinagar", "town in Gandhinagar district"),
    "veraval": ("Gir Somnath", "town in Gir Somnath district"),
    "dolatpara": ("Junagadh", "locality in Junagadh"),
    "timbavadi": ("Junagadh", "locality in Junagadh"),
    "majewadi": ("Junagadh", "locality in Junagadh"),
    "mervada": ("Banas Kantha", "Morvada, Vav taluka; the BK prefix agrees"),
    "morvada": ("Banas Kantha", "village in Vav taluka"),
    # Ahmedabad landmarks that name no city
    "chiman bhai": ("Ahmadabad", "Chimanbhai bridge, Ahmedabad"),
    "chimanbhai": ("Ahmadabad", "Chimanbhai bridge, Ahmedabad"),
    "janpath": ("Ahmadabad", "locality in Ahmedabad"),
    "paldi": ("Ahmadabad", "locality in Ahmedabad"),
    "visat": ("Ahmadabad", "Visat junction, Ahmedabad"),
    "cn vidhyalaya": ("Ahmadabad", "school in Ahmedabad"),
    "vastrapur": ("Ahmadabad", "locality in Ahmedabad"),
    "sarkhej": ("Ahmadabad", "locality in Ahmedabad"),
    "ongc": ("Ahmadabad", "ONGC office, Chandkheda, Ahmedabad"),
    "o n g c": ("Ahmadabad", "ONGC office, Chandkheda, Ahmedabad"),
    "delight": ("Ahmadabad", "Delight, Ambawadi, Ahmedabad"),
    "suvidha park": ("Ahmadabad", "Suvidha Park, Shahibagh, Ahmedabad"),
}


async def main() -> None:
    async with SessionLocal() as session:
        districts = {
            name: bid
            for bid, name in (
                await session.execute(
                    select(AdminBoundary.id, AdminBoundary.name).where(
                        AdminBoundary.level == "district"
                    )
                )
            ).all()
        }
        if not districts:
            raise SystemExit("No districts loaded. Run: python -m seeds.boundaries")

        existing = {
            row.alias
            for row in (await session.execute(select(PlaceAlias))).scalars().all()
        }
        added = skipped = 0
        for alias, (district, source) in ALIASES.items():
            if alias in existing:
                continue
            boundary_id = districts.get(district)
            if boundary_id is None:
                skipped += 1
                continue
            session.add(
                PlaceAlias(alias=alias, boundary_id=boundary_id, source=source)
            )
            added += 1
        await session.commit()
    print(f"Place aliases: {added} added, {skipped} skipped (district not loaded)")


if __name__ == "__main__":
    asyncio.run(main())
