Initial release: ordered ConfigParser overlay provenance, DEFAULT inheritance,
raw-assignment history, eager validation of final lazy interpolation, and typed
access contracts. Values are hidden unless `--show-values` is requested.

37 tests passed locally on Windows/Python 3.13. Tests include native-parser
comparisons, bounded interpolation, redaction, CLI exit codes and input
immutability. Runtime is standard-library only. CI covers Windows/Linux and
Python 3.11/3.13.

Limits: Python ConfigParser dialect, UTF-8 inputs, explicit file order, bounded
sizes, no environment injection, no configuration editing or complete
interpolation dependency graph. See README for exact limits and privacy scope.
