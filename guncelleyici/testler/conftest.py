"""Tests of the Googlebook OS updater (guncelleyici/BENIOKU.md). They use real inputs: the
recovery's own shell and toybox versions (araclar.sh), the real erofs tools, the pristine mica
images and the pipeline's work tree; a test whose input is missing is skipped with the reason.
Scratch data goes to pytest's temporary directory (--basetemp to keep it off /tmp)."""
import os
import sys

# no bytecode next to the tools; the one file pytest wrote for this conftest is removed
sys.dont_write_bytecode = True
_cache = os.path.join(os.path.dirname(os.path.abspath(__file__)), "__pycache__")
if os.path.isdir(_cache):
    for _n in os.listdir(_cache):
        if _n.startswith("conftest."):
            os.remove(os.path.join(_cache, _n))
    if not os.listdir(_cache):
        os.rmdir(_cache)
