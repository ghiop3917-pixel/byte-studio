#!/bin/sh
set -eu
cd "$(dirname "$0")"
python -m ruff check .
python -m coverage run -m unittest discover -s tests
python -m coverage report
