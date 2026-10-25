"""Read-only reports for the standard ConfigParser dialect."""

from __future__ import annotations

import configparser
import io
import json
import math
import stat
from pathlib import Path

MAX_FILE = 1024 * 1024
MAX_TOTAL = 8 * MAX_FILE
MAX_ENTRIES = 10000
MAX_VALUE = 256 * 1024


class InputError(ValueError):
    """Invalid input, with a value-free diagnostic."""


class ExpansionLimit(ValueError):
    """A single interpolated value exceeds the supported size."""


class _Fragments(list):
    def __init__(self):
        super().__init__()
        self.length = 0

    def append(self, value):
        self.length += len(value)
        if self.length > MAX_VALUE:
            raise ExpansionLimit()
        super().append(value)


class _BoundedInterpolation:
    def before_get(self, parser, section, option, value, defaults):
        parts = _Fragments()
        # Use the native algorithm and recursion limit; bound fragments as it runs.
        # This protected CPython hook is tested across supported Python versions.
        self._interpolate_some(parser, option, parts, value, section, defaults, 1)
        return "".join(parts)


class _Basic(_BoundedInterpolation, configparser.BasicInterpolation):
    pass


class _Extended(_BoundedInterpolation, configparser.ExtendedInterpolation):
    pass


def parser_for(mode: str, case_sensitive: bool) -> configparser.ConfigParser:
    choices = {"basic": _Basic, "extended": _Extended, "none": lambda: None}
    if mode not in choices:
        raise InputError("unknown interpolation mode")
    parser = configparser.ConfigParser(interpolation=choices[mode]())
    if case_sensitive:
        parser.optionxform = str
    return parser


def read_text(path: Path) -> str:
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            raise InputError("input must be a regular file")
        with path.open("rb") as stream:
            data = stream.read(MAX_FILE + 1)
        if len(data) > MAX_FILE:
            raise InputError("input exceeds 1 MiB")
        return data.decode("utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise InputError(f"cannot read input ({type(exc).__name__})") from None


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError("duplicate contract JSON member")
        result[key] = value
    return result


def load_contract(path: Path) -> dict:
    try:
        return validate_contract(json.loads(read_text(path), object_pairs_hook=_pairs))
    except (ValueError, RecursionError) as exc:
        if isinstance(exc, InputError):
            raise
        raise InputError("invalid contract JSON") from None


def validate_contract(contract: object) -> dict:
    if not isinstance(contract, dict) or set(contract) != {"schema", "checks"}:
        raise InputError("contract must contain exactly schema and checks")
    if type(contract["schema"]) is not int or contract["schema"] != 1:
        raise InputError("unsupported contract schema")
    checks = contract["checks"]
    if not isinstance(checks, list) or len(checks) > MAX_ENTRIES:
        raise InputError("contract checks must be an array of at most 10000 entries")
    seen = set()
    for check in checks:
        if not isinstance(check, dict) or not {"section", "option", "type"} <= check.keys():
            raise InputError("each check requires section, option, and type")
        if set(check) - {"section", "option", "type", "required", "min", "max"}:
            raise InputError("unknown contract check field")
        if any(not isinstance(check[k], str) or not check[k] for k in ("section", "option")):
            raise InputError("section and option must be nonempty strings")
        identity = (check["section"], check["option"])
        if identity in seen:
            raise InputError("duplicate contract check")
        seen.add(identity)
        if check["type"] not in ("string", "int", "float", "bool"):
            raise InputError("unsupported contract type")
        if type(check.get("required", True)) is not bool:
            raise InputError("required must be boolean")
        for field in ("min", "max"):
            if field in check:
                bound = check[field]
                if check["type"] not in ("int", "float") or type(bound) not in (int, float):
                    raise InputError("min and max must be numbers on numeric checks")
                if isinstance(bound, float) and not math.isfinite(bound):
                    raise InputError("bounds must be finite")
        if "min" in check and "max" in check and check["min"] > check["max"]:
            raise InputError("minimum exceeds maximum")
    return contract


def audit(
    layers: list[tuple[str, str]],
    *,
    interpolation="basic",
    case_sensitive=False,
    contract=None,
    show_values=False,
) -> dict:
    """Audit ordered (label, text) layers. Last explicit assignment wins per slot."""
    if not isinstance(layers, list) or not 1 <= len(layers) <= 64:
        raise InputError("provide between 1 and 64 layers")
    if sum(len(text.encode("utf-8")) for _, text in layers) > MAX_TOTAL:
        raise InputError("combined layers exceed 8 MiB")
    combined = parser_for(interpolation, case_sensitive)
    histories: dict[tuple[str, str], list[dict]] = {}
    raw_values: dict[tuple[str, str], str] = {}
    layer_info = []
    for number, (label, text) in enumerate(layers, 1):
        if len(text.encode("utf-8")) > MAX_FILE:
            raise InputError(f"layer {number} exceeds 1 MiB")
        identifier = f"layer-{number}"
        layer_info.append({"id": identifier, "label": label})
        own = parser_for("none", case_sensitive)
        try:
            own.read_file(io.StringIO(text), source=identifier)
            combined.read_file(io.StringIO(text), source=identifier)
        except configparser.Error as exc:
            line = getattr(exc, "lineno", None)
            suffix = f" at line {line}" if line is not None else ""
            raise InputError(f"{identifier}: {type(exc).__name__}{suffix}") from None
        # Clear only this private parser's defaults so public items() gives explicit
        # section assignments. Avoid depending on ConfigParser's internal mappings.
        defaults = dict(own.defaults())
        own.defaults().clear()
        assignments = [("DEFAULT", key, value) for key, value in defaults.items()]
        assignments += [
            (section, key, value)
            for section in own.sections()
            for key, value in own.items(section, raw=True)
        ]
        for section, key, value in assignments:
            slot = (section, key)
            previous = raw_values.get(slot)
            event = {"layer": identifier, "section": section}
            if slot in raw_values:
                event["same_as_previous"] = previous == value
            if show_values:
                event["raw"] = value
            histories.setdefault(slot, []).append(event)
            raw_values[slot] = value
        if len(raw_values) > MAX_ENTRIES:
            raise InputError("too many explicit settings")
    if contract is not None:
        contract = validate_contract(contract)
        normalized = [(c["section"], combined.optionxform(c["option"])) for c in contract["checks"]]
        if len(set(normalized)) != len(normalized):
            raise InputError("contract checks collide after option normalization")
    resolved = {}
    records = []
    errors = 0
    total_resolved = 0
    sections = ["DEFAULT", *sorted(combined.sections())]
    for section in sections:
        for key in sorted(combined[section]):
            if len(records) >= MAX_ENTRIES:
                raise InputError("too many effective settings after DEFAULT inheritance")
            slot = (section, key)
            origin_slot = slot if slot in histories else ("DEFAULT", key)
            history = histories[origin_slot]
            record = {
                "section": section,
                "option": key,
                "source": {"layer": history[-1]["layer"], "section": origin_slot[0]},
                "inherited": origin_slot != slot,
                "history": history,
                "status": "ok",
            }
            try:
                value = combined.get(section, key)
                if len(value) > MAX_VALUE:
                    raise ExpansionLimit()
                total_resolved += len(value)
                if total_resolved > MAX_TOTAL:
                    raise InputError("combined resolved values exceed 8 MiB")
                record["interpolated"] = value != combined.get(section, key, raw=True)
                resolved[slot] = value
                if show_values:
                    record["value"] = value
            except (configparser.Error, ExpansionLimit) as exc:
                record["status"] = "error"
                record["error"] = type(exc).__name__
                errors += 1
            records.append(record)
    checks = []
    for specification in (contract or {"checks": []})["checks"]:
        section = specification["section"]
        key = combined.optionxform(specification["option"])
        slot = (section, key)
        result = {"section": section, "option": key, "type": specification["type"], "status": "ok"}
        if not combined.has_option(section, key):
            if specification.get("required", True):
                result.update(status="error", error="missing")
            else:
                result["status"] = "skipped"
        elif slot not in resolved:
            result.update(status="error", error="unresolved")
        else:
            try:
                value = resolved[slot]
                if specification["type"] == "int":
                    value = int(value)
                elif specification["type"] == "float":
                    value = float(value)
                    if not math.isfinite(value):
                        raise ValueError()
                elif specification["type"] == "bool":
                    value = configparser.ConfigParser.BOOLEAN_STATES[value.lower()]
                if "min" in specification and value < specification["min"]:
                    result.update(status="error", error="below_minimum")
                if "max" in specification and value > specification["max"]:
                    result.update(status="error", error="above_maximum")
            except (ValueError, KeyError):
                result.update(status="error", error="invalid_type")
        checks.append(result)
    overrides = [
        {"section": section, "option": key, "assignments": len(history), "history": history}
        for (section, key), history in sorted(histories.items())
        if len(history) > 1
    ]
    contract_errors = sum(c["status"] == "error" for c in checks)
    return {
        "schema": 1,
        "dialect": "Python ConfigParser",
        "interpolation": interpolation,
        "case_sensitive": case_sensitive,
        "values_included": show_values,
        "layers": layer_info,
        "settings": records,
        "overrides": overrides,
        "checks": checks,
        "summary": {
            "settings": len(records),
            "overrides": len(overrides),
            "resolution_errors": errors,
            "contract_errors": contract_errors,
            "errors": errors + contract_errors,
        },
    }
