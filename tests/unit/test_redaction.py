"""Secrets in stored payloads are masked on read; the bridge scrubs them once a command finishes."""

import json
import re
from pathlib import Path

import pytest

from app.core.redaction import MASK, SECRET_KEY, redact, redact_json


def test_a_password_is_masked_wherever_it_sits():
    payload = {"username": "ann", "password": "hunter2", "nested": {"passwd": "x", "keep": 1}, "list": [{"secret": "s"}]}
    assert redact(payload) == {
        "username": "ann", "password": MASK, "nested": {"passwd": MASK, "keep": 1}, "list": [{"secret": MASK}],
    }


@pytest.mark.parametrize("key", ["password", "Password", "PASSWD", "new_password", "passwd", "api_secret", "auth_token", "token"])
def test_secret_looking_keys_are_masked(key):
    assert redact({key: "value"})[key] == MASK


@pytest.mark.parametrize("key", ["username", "email", "idempotency_key", "subject", "key", "dept_id", "note"])
def test_ordinary_keys_are_left_alone(key):
    assert redact({key: "value"})[key] == "value"


def test_empty_and_null_secrets_stay_as_they_are():
    """Nothing to hide, and masking would wrongly suggest a password was set."""
    assert redact({"password": None, "passwd": ""}) == {"password": None, "passwd": ""}


def test_redact_does_not_mutate_its_input():
    original = {"password": "p"}
    redact(original)
    assert original == {"password": "p"}


def test_redact_json_round_trips_and_masks():
    assert json.loads(redact_json('{"password": "p", "a": 1}')) == {"password": MASK, "a": 1}
    assert redact_json(None) is None


def test_a_payload_that_is_not_json_is_withheld_not_shown():
    assert redact_json("not json at all, password=hunter2") == f'"{MASK}"'


def test_the_python_and_php_secret_patterns_are_the_same():
    """bridge/plugin.php scrubs the stored payload with SECRET_KEY; the API masks with this one. They must agree,
    or a key one side hides the other would show."""
    php = (Path(__file__).resolve().parents[2] / "bridge" / "plugin.php").read_text()
    match = re.search(r"const SECRET_KEY = '/(.+)/i';", php)
    assert match, "SECRET_KEY constant not found in bridge/plugin.php"
    assert match.group(1) == SECRET_KEY
