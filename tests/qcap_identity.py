"""Prove the QCAP candidate's two recorded hashes are one source under two newline styles."""

import ast
import hashlib
import json
import marshal
import sys
from pathlib import Path

CRLF_SHA = "8e7001995c76c1d3a3ad31b7054351b9436d2e64be4078ba53a5d24c9c7a33b3"
LF_SHA = "be5da8696f01924e2f752b1686e1d6a94b38dc72fff86a1f7fedb05f006c56e0"
CONTROL_SHA = "59f99079f1db99221683dd3f06391f4fc502c1dae11fb712b08170242649830a"
CRLF = Path("tests/qcap_candidate") / CRLF_SHA / "agent.py"
LF = Path("tests/qcap_candidate") / LF_SHA / "agent.py"
CONTROL = Path("tests/numba_checkpoint") / CONTROL_SHA / "agent.py"


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def code_identity(raw: bytes, name: str) -> str:
    """Hash the compiled code object, which no newline style can influence."""
    return digest(marshal.dumps(compile(raw.decode(), name, "exec")))


def loader() -> None:
    """Confirm the harness accepts the exact validation bytes and still splits the pair."""
    import numba_validation as numeric
    import qgen_checks

    qgen_checks.MANIFEST = Path("tests/qcap_validation.json")
    loaded = qgen_checks.sources()
    assert digest(loaded["candidate"].encode()) == LF_SHA
    assert digest(loaded["control"].encode()) == CONTROL_SHA
    modules = qgen_checks.engines()
    control, candidate = modules["control"], modules["candidate"]
    assert candidate.PASSIVE is True and candidate.ADAPTIVE is False
    assert hasattr(candidate, "captures_and_promotions")
    assert not hasattr(control, "captures_and_promotions")
    # Two of the cheapest suite positions, against the node counts already recorded.
    recorded = {0: 1238, 2: 1388}
    rows = []
    for index, nodes in recorded.items():
        fen = numeric.suite()[index]
        first, second = qgen_checks.fixed(control, fen, 2), qgen_checks.fixed(candidate, fen, 2)
        assert all(first[k] == second[k] for k in ("move", "scores", "nodes")), fen
        assert second["nodes"] == nodes, (index, second["nodes"], nodes)
        rows.append({"position": index, "move": second["move"], "nodes": second["nodes"]})
    print(
        "LOADER "
        + json.dumps(
            {
                "manifest": "tests/qcap_validation.json",
                "candidate_sha256": LF_SHA,
                "control_sha256": CONTROL_SHA,
                "exact_bytes_accepted": True,
                "fixed_depth_spot_checks": rows,
            }
        ),
        flush=True,
    )


def main() -> None:
    crlf, lf, control = CRLF.read_bytes(), LF.read_bytes(), CONTROL.read_bytes()
    assert digest(crlf) == CRLF_SHA, digest(crlf)
    assert digest(lf) == LF_SHA, digest(lf)
    assert digest(control) == CONTROL_SHA, digest(control)

    # The whole difference is the carriage returns: converting one gives the other exactly.
    assert crlf.replace(b"\r\n", b"\n") == lf
    assert lf.replace(b"\n", b"\r\n") == crlf
    assert b"\r" not in lf and b"\r" not in control
    assert crlf.count(b"\r\n") == crlf.count(b"\r") == crlf.count(b"\n")
    assert len(crlf) - len(lf) == crlf.count(b"\r\n")
    assert lf.splitlines(keepends=False) == crlf.splitlines(keepends=False)

    # Same parse tree and same bytecode, so the two hashes cannot behave differently.
    assert ast.dump(ast.parse(crlf.decode())) == ast.dump(ast.parse(lf.decode()))
    codes = {name: code_identity(raw, "agent") for name, raw in (("crlf", crlf), ("lf", lf))}
    assert codes["crlf"] == codes["lf"], codes
    control_code = code_identity(control, "agent")
    assert control_code != codes["lf"], "control and candidate must not be the same program"

    manifest = json.loads(Path("tests/qcap_validation.json").read_text())
    assert manifest["control_sha256"] == CONTROL_SHA
    assert manifest["candidate_sha256"] == LF_SHA
    assert digest(Path(manifest["candidate_path"]).read_bytes()) == LF_SHA
    assert digest(Path(manifest["control_path"]).read_bytes()) == CONTROL_SHA
    assert manifest["candidate_crlf_sha256"] == CRLF_SHA

    print(
        json.dumps(
            {
                "crlf_sha256": CRLF_SHA,
                "lf_sha256": LF_SHA,
                "control_sha256": CONTROL_SHA,
                "carriage_returns_removed": crlf.count(b"\r\n"),
                "bytes": {"crlf": len(crlf), "lf": len(lf), "control": len(control)},
                "newline_conversion_is_the_only_difference": True,
                "identical_ast": True,
                "code_object_sha256": codes["lf"],
                "control_code_object_sha256": control_code,
                "manifest_pins_exact_bytes": True,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "loader":
        loader()
    else:
        main()
