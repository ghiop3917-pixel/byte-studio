#!/bin/sh
set -eu
cd "$(dirname "$0")"
CHECK_PYTHON=python
if [ -x .venv/bin/python ]; then CHECK_PYTHON=.venv/bin/python; fi
"$CHECK_PYTHON" -m ruff check .
"$CHECK_PYTHON" -m coverage run -m unittest discover -s tests
"$CHECK_PYTHON" -m coverage report
