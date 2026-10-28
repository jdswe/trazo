import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(shutil.which("gjs"), "gjs is not installed")
class ExtensionTest(unittest.TestCase):
    def test_cursor_ownership_rules(self):
        result = subprocess.run(["gjs", "-m", str(ROOT / "tests/js/ownership_test.js")],
                                capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
