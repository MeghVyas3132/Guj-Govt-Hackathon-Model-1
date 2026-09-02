"""The documentation has to describe the API that exists.

Prose drifts silently. An endpoint gets renamed, the guide keeps the old path,
and the first person to find out is an integrator at 2am who concludes the
registry is broken. These tests make that a build failure instead.

Only the documents an outsider reads are checked. `docs/superpowers/plans/` is a
historical record of how the system was built -- those documents are *supposed*
to describe the code as it was at the time, so holding them to the current API
would be wrong.
"""

import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = json.loads((ROOT / "docs" / "api" / "openapi.json").read_text())

# The documents an integrator or an evaluator actually reads.
PUBLIC_DOCS = [
    ROOT / "README.md",
    *sorted((ROOT / "docs" / "api").glob("*.md")),
    *sorted(ROOT.glob("docs/*.md")),
]

_PATH = re.compile(r"/api/v1/[A-Za-z0-9_./{}-]*")
_PLACEHOLDER = re.compile(r"^\{.*\}$|^<.*>$|^\$")


def _templates() -> list[list[str]]:
    return [p.strip("/").split("/") for p in SPEC["paths"]]


def _matches_a_route(candidate: str) -> bool:
    """Does this documented path correspond to a real route?

    Compared segment by segment so a worked example with a real id in it
    (`/api/v1/connectors/rto/sync`) still matches its template
    (`/api/v1/connectors/{code}/sync`). A template segment matches anything; a
    literal segment must match exactly.
    """
    parts = candidate.strip("/").split("/")
    for template in _templates():
        if len(template) != len(parts):
            continue
        if all(
            t.startswith("{") or t == p for t, p in zip(template, parts, strict=True)
        ):
            return True
    return False


def _documented_paths(doc: pathlib.Path) -> set[str]:
    paths = set()
    for hit in _PATH.findall(doc.read_text()):
        cleaned = hit.rstrip(".,;:`)\"'").split("?")[0].split("#")[0]
        # Source files, not routes: the guides refer to app/api/v1/routers/*.py.
        if cleaned.endswith(".py") or "/routers" in cleaned:
            continue
        paths.add(cleaned.rstrip("/"))
    return paths


@pytest.mark.parametrize("doc", PUBLIC_DOCS, ids=lambda d: d.name)
def test_every_documented_endpoint_exists(doc: pathlib.Path):
    """An endpoint named in the docs must be in the published spec."""
    if not doc.exists():
        pytest.skip(f"{doc.name} not present")
    missing = sorted(p for p in _documented_paths(doc) if not _matches_a_route(p))
    assert not missing, (
        f"{doc.relative_to(ROOT)} documents endpoints that do not exist: {missing}. "
        f"Either the docs are stale or the route was renamed."
    )


def test_every_endpoint_group_is_documented_somewhere():
    """The reverse drift: a whole feature shipped and nobody wrote it down.

    Checked at the router-prefix level rather than per path, because not every
    individual route needs prose -- but a group of them with no mention anywhere
    means a feature an integrator cannot discover.
    """
    prose = "\n".join(d.read_text() for d in PUBLIC_DOCS if d.exists())
    groups = {p.strip("/").split("/")[2] for p in SPEC["paths"] if p.startswith("/api/v1/")}
    undocumented = sorted(g for g in groups if g not in prose)
    assert not undocumented, (
        f"These endpoint groups appear nowhere in the public docs: {undocumented}"
    )


def test_the_spec_is_the_one_the_docs_link_to():
    """The committed spec is the artifact other teams code against."""
    assert (ROOT / "docs" / "api" / "openapi.json").exists()
    assert SPEC["info"]["title"] == "Sentinel CCTV Registry"
    assert len(SPEC["paths"]) > 40


@pytest.mark.parametrize("doc", PUBLIC_DOCS, ids=lambda d: d.name)
def test_no_unresolved_placeholders_in_public_docs(doc: pathlib.Path):
    """A shipped document with a TODO in it is a promise nobody kept."""
    if not doc.exists():
        pytest.skip(f"{doc.name} not present")
    text = doc.read_text()
    for marker in ("TODO", "TBD", "FIXME", "XXX", "<placeholder>", "coming soon"):
        assert marker not in text, f"{doc.relative_to(ROOT)} still contains {marker!r}"
