"""Run preserved correctness gates on a frozen experiment in writable /tmp."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from quiet_checks import sources


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "candidate"
    assert name in ("control", "candidate")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "agent.py").write_text(sources()[name])
        for directory in ("tests", "harness", "baselines"):
            shutil.copytree(
                directory, root / directory, ignore=shutil.ignore_patterns("__pycache__")
            )
        for filename in ("Makefile", "pyproject.toml", "uv.lock"):
            shutil.copyfile(filename, root / filename)
        env = os.environ | {"PYTHONPATH": str(root)}
        commands = [
            ["python", "tests/verify.py"],
            ["python", "tests/determinism.py"],
            ["python", "tests/quiet_checks.py", "passive"],
            ["make", "gate"],
        ]
        if name == "control":
            commands += [
                ["python", "tests/passive_units.py"],
                ["python", "tests/numba_validation.py", "check"],
            ]
        for command in commands:
            result = subprocess.run(
                command, cwd=root, env=env, capture_output=True, text=True, timeout=300
            )
            print(
                json.dumps({"name": name, "command": command, "exit_code": result.returncode}),
                flush=True,
            )
            if result.returncode:
                print("\n".join((result.stdout + result.stderr).splitlines()[-80:]), flush=True)
                raise AssertionError(command)


if __name__ == "__main__":
    main()
