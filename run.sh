#!/usr/bin/env bash
cd "$(dirname "$0")"
exec /usr/bin/python3 src/headset_mic_fixer.py "$@"
