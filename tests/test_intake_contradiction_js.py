"""#114: правило противоречий intake (tools/intake_rules/contradiction.js) запускается в node. Только синтетика."""
import os
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node не установлен")
DIR = os.path.join(os.path.dirname(__file__), "..", "tools", "intake_rules")


def test_node_tests_pass():
    out = subprocess.run(["node", "--test", os.path.abspath(os.path.join(DIR, "contradiction.test.js"))], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout[-3000:] + out.stderr[-1000:]
