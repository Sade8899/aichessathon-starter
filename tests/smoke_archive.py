"""Validate the extracted submission with the unchanged gate and full-clock games."""

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

import chess

from harness.referee import FAILED_TERMINATIONS, play_match
from harness.rules import BASE_MS, INCREMENT_MS, MAX_UNZIPPED_BYTES
from harness.sandbox import local


def main() -> None:
    path = Path("/workspace/artifacts/submission.zip")
    with tempfile.TemporaryDirectory() as temporary, zipfile.ZipFile(path) as archive:
        root = Path(temporary)
        names = archive.namelist()
        assert names == ["agent.py"], names
        assert len(names) == len({name.casefold() for name in names})
        for name in names:
            member = PurePosixPath(name)
            assert not member.is_absolute() and ".." not in member.parts
            assert "\\" not in name and ":" not in name
        size = sum(info.file_size for info in archive.infolist())
        assert size < MAX_UNZIPPED_BYTES
        assert archive.read("agent.py") == Path("/workspace/agent.py").read_bytes()
        archive.extractall(root)
        initialization_code = """
import json, resource, time
started = time.perf_counter()
import agent
elapsed = time.perf_counter() - started
assert elapsed < 90
rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
assert rss < 2048
print(json.dumps({'seconds': elapsed, 'peak_rss_mb': rss, 'module': agent.__file__}))
"""
        initialized = subprocess.run(
            [sys.executable, "-c", initialization_code],
            cwd=root,
            text=True,
            capture_output=True,
            check=True,
        )
        initialization = json.loads(initialized.stdout)
        assert Path(initialization["module"]).resolve() == root / "agent.py"
        for name in ("harness", "baselines"):
            shutil.copytree(Path("/workspace") / name, root / name)
        for name in ("Makefile", "pyproject.toml", "uv.lock"):
            shutil.copyfile(Path("/workspace") / name, root / name)
        gate = subprocess.run(["make", "gate"], cwd=root, text=True, capture_output=True)
        if gate.returncode:
            print("\n".join((gate.stdout + gate.stderr).splitlines()[-80:]))
            raise AssertionError("extracted official gate failed")
        results = []
        for white in (True, False):
            own = local(root)
            opponent = local(root / "baselines/greedy")
            board = chess.Board()
            for move in ("e2e4", "e7e5", "g1f3", "b8c6"):
                board.push_uci(move)
            result = play_match(
                own if white else opponent,
                opponent if white else own,
                BASE_MS,
                INCREMENT_MS,
                start_fen=board.fen(),
            )
            assert result.termination not in FAILED_TERMINATIONS, result
            results.append(
                {
                    "colour": "white" if white else "black",
                    "result": result.result,
                    "termination": result.termination,
                }
            )
        print(
            json.dumps(
                {
                    "archive": str(path),
                    "members": names,
                    "unzipped_bytes": size,
                    "zip_bytes": path.stat().st_size,
                    "initialization": initialization,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "extracted_official_gate": "passed",
                    "full_clock_games": results,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
