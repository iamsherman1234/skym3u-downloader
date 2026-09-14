#!/usr/bin/env bash
# Backwards compatibility wrapper
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
bash "$SCRIPT_DIR/run_colab.sh" "$@"
