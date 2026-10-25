import configparser
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ini_stack_audit.core import InputError, audit, load_contract


def rows(result):
    return {(r["section"], r["option"]): r for r in result["settings"]}


def contract(*checks):
    return {"schema": 1, "checks": list(checks)}


def test_overlay_default_inheritance_and_native_parity():
    layers = [
        ("base", "[DEFAULT]\nport=80\nhost=local\n[s]\nurl=%(host)s:%(port)s\n"),
        ("site", "[DEFAULT]\nport=443\n[s]\nhost=remote\n"),
    ]
    result = audit(layers, show_values=True)
    native = configparser.ConfigParser()
    for label, text in layers:
        native.read_string(text, label)
    for (section, key), record in rows(result).items():
        assert record["value"] == native.get(section, key)
    assert rows(result)["s", "url"]["value"] == "remote:443"
    assert rows(result)["s", "url"]["source"]["layer"] == "layer-1"
    assert rows(result)["s", "port"]["source"] == {"section": "DEFAULT", "layer": "layer-2"}
    assert rows(result)["s", "port"]["inherited"] is True
    assert result["summary"]["overrides"] == 1


def test_explicit_same_default_is_not_mistaken_for_inherited():
    result = audit(
        [("one", "[DEFAULT]\na=1\n[s]\na=1\n"), ("two", "[DEFAULT]\na=2\n")], show_values=True
    )
    record = rows(result)["s", "a"]
    assert record["value"] == "1"
    assert not record["inherited"]
    assert record["source"]["layer"] == "layer-1"


def test_same_value_and_changed_assignments():
    result = audit([("a", "[s]\nx=one\n"), ("b", "[s]\nx=one\n"), ("c", "[s]\nx=two\n")])
    history = result["overrides"][0]["history"]
    assert history[1]["same_as_previous"] is True
    assert history[2]["same_as_previous"] is False
    assert "raw" not in history[0]


@pytest.mark.parametrize(
    "mode,text,expected",
    [
        ("basic", "[s]\na=100%%\n", "100%"),
        ("none", "[s]\na=%(unknown)s\n", "%(unknown)s"),
        ("extended", "[defaults]\nx=ok\n[s]\na=${defaults:x}!\n", "ok!"),
        ("basic", "[s]\na=first\n    second\n", "first\nsecond"),
    ],
)
def test_interpolation_modes_and_multiline(mode, text, expected):
    result = audit([("x", text)], interpolation=mode, show_values=True)
    assert rows(result)["s", "a"]["value"] == expected


@pytest.mark.parametrize(
    "text,error",
    [
        ("[s]\na=%(unknown)s\n", "InterpolationMissingOptionError"),
        ("[s]\na=secret%broken\n", "InterpolationSyntaxError"),
        ("[s]\na=%(b)s\nb=%(a)s\n", "InterpolationDepthError"),
    ],
)
def test_every_lazy_interpolation_error_is_found(text, error):
    result = audit([("fixture", text)])
    assert result["summary"]["errors"] >= 1
    assert rows(result)["s", "a"]["error"] == error
    assert "secret" not in json.dumps(result)


def test_expansion_limit_is_diagnostic_instead_of_allocating_unbounded_value():
    text = "[s]\na=" + "x" * 200000 + "\nb=%(a)s%(a)s\n"
    result = audit([("fixture", text)])
    assert rows(result)["s", "b"]["error"] == "ExpansionLimit"


def test_redaction_including_overridden_values_and_errors():
    secret = "FAKE_PRIVATE_TEST_VALUE"
    result = audit([("one", f"[s]\np={secret}\n"), ("two", "[s]\np=hidden\nbroken=raw%fail\n")])
    assert secret not in json.dumps(result)
    assert "hidden" not in json.dumps(result)
    assert "raw%fail" not in json.dumps(result)
    assert result["values_included"] is False


@pytest.mark.parametrize("text", ["[s]\na=1\na=2\n", "password=never-print-this\n", "[s]\na\n"])
def test_parse_error_has_no_value_leak(text):
    with pytest.raises(InputError) as error:
        audit([("private", text)])
    assert "never-print-this" not in str(error.value)


def test_case_sensitive_vs_default_behavior():
    text = "[s]\nA=one\na=two\n"
    with pytest.raises(InputError):
        audit([("x", text)])
    result = audit([("x", text)], case_sensitive=True, show_values=True)
    assert rows(result)["s", "A"]["value"] == "one"
    assert rows(result)["s", "a"]["value"] == "two"


@pytest.mark.parametrize(
    "kind,value,error",
    [
        ("int", "12", None),
        ("bool", "YES", None),
        ("bool", "off", None),
        ("float", "1.5", None),
        ("string", "", None),
        ("int", "1.5", "invalid_type"),
        ("bool", "perhaps", "invalid_type"),
        ("float", "nan", "invalid_type"),
        ("float", "inf", "invalid_type"),
    ],
)
def test_typed_reads(kind, value, error):
    result = audit(
        [("fixture", f"[s]\na={value}\n")],
        contract=contract({"section": "s", "option": "a", "type": kind}),
    )
    assert result["checks"][0].get("error") == error


def test_ranges_required_optional_and_default_checks():
    checks = contract(
        {"section": "s", "option": "port", "type": "int", "min": 1, "max": 65535},
        {"section": "s", "option": "missing", "type": "string"},
        {"section": "s", "option": "optional", "type": "string", "required": False},
        {"section": "DEFAULT", "option": "port", "type": "int", "min": 100000},
    )
    result = audit([("x", "[DEFAULT]\nport=70000\n[s]\n")], contract=checks)
    assert [c.get("error") for c in result["checks"]] == [
        "above_maximum",
        "missing",
        None,
        "below_minimum",
    ]
    assert result["checks"][2]["status"] == "skipped"


@pytest.mark.parametrize(
    "change",
    [
        {"min": True},
        {"max": float("nan")},
        {"min": 3, "max": 1},
        {"required": "true"},
        {"unknown": 1},
        {"type": "object"},
    ],
)
def test_bad_contract_rejected(change):
    check = {"section": "s", "option": "p", "type": "int"} | change
    with pytest.raises(InputError):
        audit([("x", "[s]\np=2\n")], contract=contract(check))


def test_normalized_contract_duplicates_rejected():
    checks = contract(
        {"section": "s", "option": "A", "type": "int"},
        {"section": "s", "option": "a", "type": "int"},
    )
    with pytest.raises(InputError, match="normalization"):
        audit([("x", "[s]\na=1\n")], contract=checks)


def test_broken_value_overridden_before_resolution_is_ok():
    result = audit([("one", "[s]\nx=%(missing)s\n"), ("two", "[s]\nx=valid\n")])
    assert result["summary"]["errors"] == 0


def test_duplicate_contract_json_rejected(tmp_path):
    path = tmp_path / "contract.json"
    path.write_text('{"schema":1,"checks":[],"schema":1}')
    with pytest.raises(InputError, match="duplicate"):
        load_contract(path)


def cli(*args):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    return subprocess.run(
        [sys.executable, "-m", "ini_stack_audit", *map(str, args)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_cli_examples_failure_exit_and_no_clobber(tmp_path):
    examples = Path(__file__).resolve().parents[1] / "examples"
    args = [examples / "base.ini", examples / "site.ini", "--contract", examples / "contract.json"]
    run = cli(*args, "--format", "json")
    assert run.returncode == 0, run.stderr
    data = json.loads(run.stdout)
    assert data["summary"]["overrides"] == 2
    assert "http://" not in run.stdout
    assert cli(*args, "--fail-on-override").returncode == 1
    output = tmp_path / "report.json"
    assert cli(*args, "--output", output).returncode == 0
    assert cli(*args, "--output", output).returncode == 2
    assert cli(tmp_path / "missing.ini").returncode == 2
    bad = tmp_path / "bad.ini"
    bad.write_text("[s]\np=TOPSECRET%bad\n")
    failed = cli(bad)
    assert failed.returncode == 1
    assert "TOPSECRET" not in failed.stdout + failed.stderr


def test_utf8_bom_and_input_unchanged(tmp_path):
    path = tmp_path / "input.ini"
    original = b"\xef\xbb\xbf[s]\r\nword = hello\r\n"
    path.write_bytes(original)
    assert cli(path).returncode == 0
    assert path.read_bytes() == original
