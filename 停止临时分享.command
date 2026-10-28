#!/bin/zsh
cd -- "${0:A:h}" || exit 1
./.venv/bin/python3 share_studio.py stop
