"""The requirement scan and the pip installer behind install.py and the app's Install buttons."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from calibrator import requirements

ROOT = Path(__file__).resolve().parent.parent


class ScanTest(unittest.TestCase):
    def test_this_machine_has_what_is_needed(self):
        items = requirements.scan()
        self.assertTrue(all(item.ok for item in items if item.required), [i for i in items if not i.ok])
        self.assertEqual({i.name for i in items if i.required},
                         {"Windows 10 or 11", "Python 3.10 or newer", "Tkinter (draws the window)"})

    def test_missing_and_old_packages_are_offered(self):
        real = requirements.importlib.util.find_spec
        with mock.patch.object(requirements.importlib.util, "find_spec",
                               side_effect=lambda name: None if name == "PIL" else real(name)), \
                mock.patch.object(requirements.importlib.metadata, "version", return_value="6.1"):
            missing = requirements.missing_packages()
        self.assertIn("pillow>=11.0", missing)  # not installed
        self.assertIn("qrcode>=7.4", missing)  # installed, but 6.1 is too old


class InstallTest(unittest.TestCase):
    def test_per_user_install_outside_a_virtual_env(self):
        with mock.patch.object(sys, "prefix", "C:/Python"), mock.patch.object(sys, "base_prefix", "C:/Python"):
            self.assertIn("--user", requirements.pip_command(["qrcode>=7.4"]))
        with mock.patch.object(sys, "prefix", "C:/venv"), mock.patch.object(sys, "base_prefix", "C:/Python"):
            self.assertNotIn("--user", requirements.pip_command(["qrcode>=7.4"]))

    def test_windowless_python_installs_through_the_console_one(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ("python.exe", "pythonw.exe"):
                (Path(folder) / name).write_bytes(b"")
            with mock.patch.object(sys, "executable", str(Path(folder) / "pythonw.exe")):
                self.assertEqual(Path(requirements.pip_command([])[0]).name, "python.exe")

    def test_success_makes_packages_importable_right_away(self):
        finished = SimpleNamespace(returncode=0, stdout="Successfully installed qrcode-8.2\n", stderr="")
        with mock.patch.object(requirements.subprocess, "run", return_value=finished) as run, \
                mock.patch.object(requirements, "_refresh_paths") as refresh:
            worked, output = requirements.install(["qrcode>=7.4"])
        self.assertTrue(worked)
        self.assertIn("Successfully installed", output)
        refresh.assert_called_once()
        self.assertEqual(run.call_args.args[0][-1], "qrcode>=7.4")

    def test_install_script_check_mode(self):
        result = subprocess.run([sys.executable, str(ROOT / "install.py"), "--check"], capture_output=True,
                                text=True, encoding="utf-8", cwd=ROOT, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Python 3.10 or newer", result.stdout)


class FirstRunTest(unittest.TestCase):
    def test_first_run_saves_the_flag_that_skips_setup_next_time(self):
        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ, MONITOR_CALIBRATOR_HOME=home, PYTHONIOENCODING="utf-8")
            # "n" to every question, so it installs nothing and adds no shortcut.
            result = subprocess.run([sys.executable, str(ROOT / "install.py"), "--first-run"], input="n\nn\nn\n",
                                    capture_output=True, text=True, encoding="utf-8", cwd=ROOT, env=env, timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            settings = json.loads((Path(home) / "settings.json").read_text(encoding="utf-8"))
            self.assertIs(settings["setup_done"], True)
            # Calibrate.bat looks for exactly this text before deciding to skip setup.
            self.assertIn('"setup_done": true', (Path(home) / "settings.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
