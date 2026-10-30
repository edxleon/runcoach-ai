"""What PyPI and the MCP registry are handed must agree with the package.

The registry accepts `server.json` only if the PyPI README of that exact
version carries an `mcp-name:` line naming it, and a client installs the
version `server.json` pins. Each of these lives in a different file; nothing
but this test sees all of them at once before a release does."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _project() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def _server() -> dict:
    return json.loads((ROOT / "server.json").read_text(encoding="utf-8"))


def test_the_pypi_readme_names_the_registry_entry():
    readme = (ROOT / _project()["readme"]).read_text(encoding="utf-8")
    # The registry wants a boundary after the name: a newline, a space, a tag
    # or `-->`. A name glued to a full stop does not match there.
    names = re.findall(r"mcp-name: (\S+?)(?:\s|-->|<|$)", readme)
    assert names == [_server()["name"]]


def test_server_json_points_at_this_package_and_version():
    server, project = _server(), _project()
    (package,) = server["packages"]
    assert package["registryType"] == "pypi"
    assert package["identifier"] == project["name"]
    assert server["version"] == package["version"] == project["version"]
    assert package["packageArguments"] == [{"type": "positional", "value": "mcp"}]
    assert server["name"] == f"io.github.edxleon/{project['name']}"


def test_the_registry_description_fits():
    assert len(_server()["description"]) <= 100


def test_the_pypi_readme_links_absolutely():
    """PyPI renders the README without the repository around it: a relative
    image or link there is a broken picture on the package page."""
    readme = (ROOT / _project()["readme"]).read_text(encoding="utf-8")
    targets = re.findall(r'(?:src|href)="([^"]+)"|\]\(([^)]+)\)', readme)
    for target in (a or b for a, b in targets):
        assert target.startswith("https://"), target
