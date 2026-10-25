# ini-stack-audit

Explain which file supplied each effective setting in a Python ConfigParser INI
stack. Find overridden assignments, inherited DEFAULT values, lazy interpolation
failures, and invalid typed reads before starting an application. Reports hide
raw and resolved values unless you explicitly request them. Runtime uses only
the Python standard library and never edits inputs or contacts a network.

## Install and run

Requires Python 3.11 or later:

```sh
python -m pip install "git+https://github.com/KanadeK/ini-stack-audit.git@v0.1.0"
git clone https://github.com/KanadeK/ini-stack-audit.git
cd ini-stack-audit
ini-stack-audit examples/base.ini examples/site.ini --contract examples/contract.json
```

The example exits 0 with 9 effective settings, 2 overridden slots, and no errors.
`server.url` was assigned in the first layer, but its interpolation uses the
second layer's host and port. `server.port` is inherited from DEFAULT in layer 2.
Add `--show-values` to see `http://staging.invalid:8443` in this synthetic example.
Add `--fail-on-override` to get exit 1 for the two overwritten slots.

To install from a checkout use `python -m pip install .`. The alternate entry
point is `python -m ini_stack_audit`. Uninstall with
`python -m pip uninstall ini-stack-audit`; delete report files you no longer need.
The tool creates no persistent cache or implicit files.

## File order and reports

```sh
ini-stack-audit defaults.ini site.ini local.ini --format json
ini-stack-audit defaults.ini site.ini --interpolation extended --case-sensitive
ini-stack-audit defaults.ini site.ini --output new-report.json --format json
```

Lowest precedence comes first. Files are mandatory: unlike `ConfigParser.read`,
missing files are errors. Duplicate sections/options **within a file** are
rejected; the same explicit setting in different files is a reported override.
Section names are case-sensitive. Option names are lowercased unless
`--case-sensitive` is supplied. Multiline values and `=`/`:` delimiters follow
the standard library. UTF-8 with an optional BOM is supported.

Interpolation modes: `basic` (default, `%(key)s`), `extended`
(`${section:key}`), or `none` (literal text). No environment variables are
implicitly injected. Every final effective value, including DEFAULT itself, is
read to expose errors that ConfigParser normally defers until `get()`.

Each setting reports section, normalized option, assignment source, inheritance,
assignment history and resolution status. Source identifies the **raw assignment**,
not a dependency graph of interpolated references. A default may be overridden
by an explicit section setting; the latter remains explicit even if the two
values are initially identical. Override history is per explicit section/key slot.
`same_as_previous` distinguishes an unchanged reassignment from a changed one.
The report counts diagnostic errors; a resolution error and a failed contract
check for the same setting are separate diagnostics.

Input files are identified by positional IDs and basenames, not absolute paths.
Keys, section names, labels, and equality information remain visible. This is
value suppression, **not anonymization**. `--show-values` includes overwritten
raw values as well as final values: treat those reports as sensitive.
Parse and interpolation errors never echo source values, even in error messages.
Output is escaped to avoid terminal control sequences. `--output` creates a new
file and refuses to overwrite one that already exists.

## Typed access contracts

```json
{
  "schema": 1,
  "checks": [
    {"section": "server", "option": "port", "type": "int", "min": 1, "max": 65535},
    {"section": "server", "option": "debug", "type": "bool"},
    {"section": "server", "option": "label", "type": "string", "required": false}
  ]
}
```

Pass this JSON file with `--contract`. Checks are required by default. Optional
missing keys are `skipped`; present optional keys are still validated. `int` and
`float` use Python conversions; non-finite floats are rejected. `bool` accepts
ConfigParser's `1/0`, `yes/no`, `true/false`, and `on/off` case-insensitively.
`string` accepts any resolved string including empty. Numeric `min`/`max` are
inclusive. Unknown fields, duplicate JSON members, and checks colliding after
option normalization are errors. There is no schema inference or mutation.

| Exit | Meaning |
|---|---|
| 0 | All effective reads and contract checks passed |
| 1 | Resolution/contract errors, or overrides with `--fail-on-override` |
| 2 | Malformed input, parser/contract format error, resource bound, or I/O failure |

## Boundaries

This implements the standard **Python ConfigParser** dialect, not all INI
variants. No value-less options, custom default-section names, inline comments,
application-specific converters, environment overrides, or parser plugins.
Use [ConfigObj](https://configobj.readthedocs.io/en/latest/configobj.html) if you
need a different configuration system; this tool is for auditing existing
ConfigParser stacks without changing their loader.

Limits: 64 layers, 1 MiB per input, 8 MiB combined layer bytes, 10,000 explicit
and effective settings, 10,000 contract checks, 256 KiB per resolved value and
8 MiB total resolved characters. Native ConfigParser recursion limits also apply.
Excessive single-value interpolation becomes an `ExpansionLimit` diagnostic;
aggregate limits stop the audit. The interpolation guard uses a protected
standard-library method, covered by the supported-version CI matrix. Inputs
should remain unchanged during reads; hostile filesystem races are outside scope.

## Development

```sh
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m build
```

Tests compare successful reads with native ConfigParser and exercise DEFAULT
inheritance, interpolation cycles/escapes, multiline values, typed contracts,
redaction, size guards, CLI exit codes, and unchanged input bytes. CI runs on
Windows/Linux and Python 3.11/3.13. MIT; no runtime dependencies or vendored code.

## 中文说明

按顺序传入基础、站点和本地 INI 文件，查看每个生效配置来自哪一层、是否继承
DEFAULT、哪些配置被覆盖，以及插值和类型读取是否会失败。默认不显示任何配置值；
`--show-values` 会显示最终值及被覆盖的原始值，分享报告前请检查。

工具使用 Python 原生 ConfigParser 语义，不支持所有 INI 方言。缺失文件、文件内部
重复键和格式错误会退出 2；读取或类型检查失败退出 1；通过退出 0。
示例可直接运行，配置及报告均在本机处理，输入文件不会被修改。
