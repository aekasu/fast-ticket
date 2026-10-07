"""The settings table the API reads is the one the bridge writes."""

import re
from pathlib import Path

from app.modules.settings.sections import SECTIONS

PHP = (Path(__file__).resolve().parents[2] / "bridge" / "handlers" / "settings.php").read_text()


def _php_sections() -> dict[str, dict[str, str]]:
    body = PHP.split("const SECTIONS = [", 1)[1].split("\n    ];", 1)[0]
    out: dict[str, dict[str, str]] = {}
    for name, block in re.findall(r"'(\w+)' => \[(.*?)\n        \],", body, re.S):
        out[name] = dict(re.findall(r"'([\w-]+)' => '(\w+)'", block))
    return out


def test_the_php_handler_knows_exactly_the_keys_and_kinds_the_api_does():
    assert _php_sections() == SECTIONS


def test_every_kind_is_one_the_handler_understands():
    assert {kind for keys in SECTIONS.values() for kind in keys.values()} == {"str", "int", "check", "flag", "list"}
