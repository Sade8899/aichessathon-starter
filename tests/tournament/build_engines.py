"""Build pinned sparring tools inside the development image only."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path


def run(args: list[str], cwd: Path | None = None) -> None:
    print(json.dumps({"command": args, "cwd": str(cwd)}), flush=True)
    subprocess.run(args, cwd=cwd, check=True)


def main() -> None:
    prefix = Path("/opt/chessathon")
    binary_dir = prefix / "bin"
    binary_dir.mkdir(parents=True, exist_ok=True)
    engines = json.loads(Path("/opt/development/engines.lock.json").read_text())["engines"]
    for entry in engines:
        name = entry["name"]
        source = prefix / "sources" / name
        source.mkdir(parents=True)
        run(["git", "init", "-q", str(source)])
        run(["git", "remote", "add", "origin", entry["url"]], source)
        run(["git", "fetch", "--depth", "1", "origin", "refs/tags/" + entry["tag"]], source)
        resolved = subprocess.check_output(
            ["git", "rev-parse", "FETCH_HEAD^{commit}"], cwd=source, text=True
        ).strip()
        assert resolved == entry["commit"], (entry, resolved)
        run(["git", "checkout", "--detach", entry["commit"]], source)
        run(["git", "submodule", "update", "--init", "--recursive", "--depth", "1"], source)
        if name == "loki":
            command = [
                "make",
                "-j2",
                "CXXFLAGS=-std=c++17 -O3 -march=x86-64 -mtune=generic -DIS_64BIT -DNDEBUG -pthread",
            ]
            run(command, source)
            output = source / "Loki3"
        elif name == "shallowblue":
            command = [
                "make",
                "-j2",
                "CC_FLAGS=-std=c++11 -O3 -march=x86-64 -mtune=generic "
                "-flto -pthread -fno-exceptions",
            ]
            run(command, source)
            output = source / "shallowblue"
        elif name.startswith("zagreus"):
            command = [
                "cmake",
                "-S",
                ".",
                "-B",
                "build",
                "-DCMAKE_BUILD_TYPE=Release",
                "-DMARCH_VALUE=x86-64",
                "-DMTUNE_VALUE=generic",
            ]
            run(command, source)
            run(["cmake", "--build", "build", "-j2"], source)
            output = source / "build/Zagreus"
        else:
            command = ["make", "-j2"]
            run(command, source)
            output = source / "fastchess"
        assert output.is_file(), output
        target = binary_dir / name
        shutil.copy2(output, target)
        target.chmod(0o755)
        entry["binary_sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
        entry["build_command"] = command
        entry["submodules"] = subprocess.check_output(
            ["git", "submodule", "status", "--recursive"], cwd=source, text=True
        ).splitlines()
        entry["license_files"] = {
            str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in source.glob("*LICENSE*")
            if p.is_file()
        }
    manifest = {
        "engines": engines,
        "compiler": subprocess.check_output(["g++", "--version"], text=True).splitlines()[0],
    }
    (prefix / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print("BUILT " + json.dumps(manifest), flush=True)


if __name__ == "__main__":
    main()
