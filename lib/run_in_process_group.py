#!/usr/bin/env python3
"""Run a command as the leader of a new process group."""

import os
import sys


if len(sys.argv) < 2:
    raise SystemExit("usage: run_in_process_group.py COMMAND [ARG ...]")

os.setsid()
os.execvp(sys.argv[1], sys.argv[1:])
