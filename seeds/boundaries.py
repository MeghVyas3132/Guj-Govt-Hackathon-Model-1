"""Load the Gujarat district polygons into `admin_boundaries`.

Run with `python -m seeds.boundaries`.

`data/gujarat_districts.geojson` is regenerated with:

    BASE=https://raw.githubusercontent.com/datta07/INDIAN-SHAPEFILES/master
    curl -sL "$BASE/STATES/GUJARAT/GUJARAT_DISTRICTS.geojson" \
      -o /tmp/gj_districts_raw.geojson
    python3 - <<'EOF'
    import json
    src = json.load(open("/tmp/gj_districts_raw.geojson"))
    features = [
        f for f in src["features"]
        if (f["properties"].get("stname") or "").upper().startswith("GUJARAT")
    ]
    assert features, "no Gujarat features -- check the property key"
    json.dump({"type": "FeatureCollection", "features": features},
              open("data/gujarat_districts.geojson", "w"), separators=(",", ":"))
    print(f"{len(features)} districts written")
    EOF

The plan pointed at `INDIA/india_district.geojson` in the same repository, which now
404s; the state-scoped file above is the live path and is already Gujarat-only, so the
filter is a no-op assertion that the source has not silently changed under us. Names
follow the 2011 Census spellings ("Ahmadabad", "Kachchh", "The Dangs"), so match on
`code` -- the census district code -- rather than on the display name.
"""

import asyncio
import json
from pathlib import Path

from geoalchemy2.shape import from_shape
from shapely.geometry import MultiPolygon, shape
from sqlalchemy import delete

from app.core.db import SessionLocal
from app.models.admin_boundary import AdminBoundary

GEOJSON = Path(__file__).resolve().parent.parent / "data" / "gujarat_districts.geojson"
NAME_KEYS = ("district", "DISTRICT", "dtname", "NAME_2")
CODE_KEYS = ("dtcode11", "DISTRICT_C", "censuscode")


def _name(properties: dict) -> str:
    for key in NAME_KEYS:
        if properties.get(key):
            return str(properties[key]).title()
    raise KeyError(f"No district name found in {list(properties)}")


def _code(properties: dict) -> str | None:
    for key in CODE_KEYS:
        if properties.get(key):
            return str(properties[key])
    return None


async def main() -> None:
    collection = json.loads(GEOJSON.read_text())
    async with SessionLocal() as session:
        # Replace rather than append: re-running the seed must not leave two copies of
        # every district, which would double every coverage count computed against them.
        await session.execute(delete(AdminBoundary).where(AdminBoundary.level == "district"))
        for feature in collection["features"]:
            geometry = shape(feature["geometry"])
            if geometry.geom_type == "Polygon":
                geometry = MultiPolygon([geometry])
            session.add(
                AdminBoundary(
                    level="district",
                    name=_name(feature["properties"]),
                    code=_code(feature["properties"]),
                    geom=from_shape(geometry, srid=4326),
                )
            )
        await session.commit()
    print(f"Loaded {len(collection['features'])} districts")


if __name__ == "__main__":
    asyncio.run(main())
