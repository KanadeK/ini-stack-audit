# Contributing

Open an issue with a minimal public fixture before proposing a substantial
change. Never attach production secrets. Install `python -m pip install -e ".[dev]"`,
then run `python -m pytest`, `python -m ruff check .`, and
`python -m ruff format --check .`. Changes to parsing need a regression test and
an explanation of the relevant upstream semantics. Keep the CLI exit contract
and report format stable within a release series. Tests must not require network
access. CI does not commit or push generated files.
