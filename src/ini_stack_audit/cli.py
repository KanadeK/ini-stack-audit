import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .core import InputError, audit, load_contract, read_text


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Audit an ordered Python ConfigParser file stack")
    parser.add_argument("files", nargs="+", type=Path, help="lowest precedence first")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--interpolation", choices=("basic", "extended", "none"), default="basic")
    parser.add_argument("--case-sensitive", action="store_true")
    parser.add_argument("--contract", type=Path)
    parser.add_argument(
        "--show-values", action="store_true", help="include raw and resolved values"
    )
    parser.add_argument("--fail-on-override", action="store_true")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--output", type=Path, help="create a new report file; never overwrite")
    args = parser.parse_args(argv)
    try:
        if len(args.files) > 64:
            raise InputError("at most 64 layers are supported")
        contract = load_contract(args.contract) if args.contract else None
        result = audit(
            [(path.name, read_text(path)) for path in args.files],
            interpolation=args.interpolation,
            case_sensitive=args.case_sensitive,
            contract=contract,
            show_values=args.show_values,
        )
        if args.format == "json":
            output = json.dumps(result, indent=2, ensure_ascii=True) + "\n"
        else:
            summary = result["summary"]
            lines = [
                f"{summary['settings']} settings; {summary['overrides']} overridden slots; "
                f"{summary['errors']} errors; values {'included' if args.show_values else 'hidden'}"
            ]
            for row in result["settings"]:
                label = json.dumps([row["section"], row["option"]], ensure_ascii=True)
                source = row["source"]
                origin = json.dumps(source, sort_keys=True, ensure_ascii=True)
                lines.append(f"{label} <- {origin}: {row.get('error', row['status'])}")
                if args.show_values and "value" in row:
                    lines.append("  value=" + json.dumps(row["value"], ensure_ascii=True))
            for row in result["overrides"]:
                lines.append("override " + json.dumps(row, ensure_ascii=True))
            for check in result["checks"]:
                lines.append("check " + json.dumps(check, ensure_ascii=True))
            output = "\n".join(lines) + "\n"
        if args.output:
            with args.output.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(output)
        else:
            sys.stdout.write(output)
        return int(
            bool(
                result["summary"]["errors"]
                or (args.fail_on_override and result["summary"]["overrides"])
            )
        )
    except (InputError, OSError) as exc:
        message = str(exc) if isinstance(exc, InputError) else type(exc).__name__
        print(f"ini-stack-audit: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
