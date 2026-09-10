"""Test-only abrupt process failure at the real storage boundary. Not a product backdoor."""

import os
import sys
from pathlib import Path

import eas_hmi.operations.transaction as tx
from eas_hmi.operations.edit import move

root, mode = Path(sys.argv[1]), sys.argv[2]
real_write, real_replace = tx.atomic_write, tx.os.replace


def injected_write(path, data):
    phase = "commit" if path.parent.name == "commits" else path.name
    if mode == "before-" + phase:
        os._exit(73)
    real_write(path, data)
    if mode == "after-" + phase:
        os._exit(73)


def injected_replace(source, destination):
    if mode == "head-temp" and Path(destination).name == "HEAD.json":
        os._exit(73)
    return real_replace(source, destination)


tx.atomic_write = injected_write
tx.os.replace = injected_replace
tx.Store(root).transaction("crash probe", lambda p: move(p, ["BOX_A"], dx=40), expected_revision=0)
raise SystemExit("Failure injection did not run")
