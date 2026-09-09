"""Play the manifested games and record every position for later labelling.

Platform semantics that are preserved here:

* the control's module state is reset between games and never carried across them;
* a fresh engine process is started for each game;
* the two sides never run concurrently inside one game, so a game occupies one core.

Nothing is labelled here. This step only produces legal positions plus the control's
own static evaluation, which becomes the baseline the residual is measured against.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import pathlib
import queue
import subprocess
import sys
import threading
import time

import chess

REPO = pathlib.Path(__file__).resolve().parent.parent
BIN = REPO / "tests" / "external_engines" / "bin"
MAX_PLIES = 300

for _var in (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "TORCH_NUM_THREADS",
):
    os.environ.setdefault(_var, "1")


class UciEngine:
    """A minimal UCI driver with real timeouts, so a wedged engine cannot hang a worker."""

    def __init__(self, exe: pathlib.Path) -> None:
        self.proc = subprocess.Popen(
            [str(exe)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            cwd=str(exe.parent),
        )
        self.lines: queue.Queue[str | None] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self.send("uci")
        self.until("uciok", 20)
        self.send("isready")
        self.until("readyok", 20)
        self.send("ucinewgame")

    def _pump(self) -> None:
        assert self.proc.stdout
        for line in self.proc.stdout:
            self.lines.put(line.rstrip("\r\n"))
        self.lines.put(None)

    def send(self, text: str) -> None:
        assert self.proc.stdin
        try:
            self.proc.stdin.write(text + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise RuntimeError("engine stdin closed") from exc

    def until(self, prefix: str, limit: float) -> str | None:
        deadline = time.time() + limit
        while time.time() < deadline:
            try:
                line = self.lines.get(timeout=max(0.02, deadline - time.time()))
            except queue.Empty:
                return None
            if line is None:
                return None
            if line.startswith(prefix):
                return line
        return None

    def bestmove(self, fen: str, moves: list[str], movetime_ms: int) -> str | None:
        position = f"position fen {fen}" + (f" moves {' '.join(moves)}" if moves else "")
        self.send(position)
        self.send(f"go movetime {movetime_ms}")
        line = self.until("bestmove", movetime_ms / 1000 + 15)
        if not line:
            return None
        parts = line.split()
        return parts[1] if len(parts) > 1 else None

    def close(self) -> None:
        try:
            self.send("quit")
            self.proc.wait(timeout=2)
        except Exception:  # noqa: BLE001
            try:
                self.proc.kill()
            except Exception:  # noqa: BLE001
                pass


def legal_uci(uci: str | None, board: chess.Board) -> bool:
    if not uci:
        return False
    try:
        return chess.Move.from_uci(uci) in board.legal_moves
    except ValueError:
        return False


def phase_of(board: chess.Board) -> tuple[str, int]:
    weights = {chess.KNIGHT: 1, chess.BISHOP: 1, chess.ROOK: 2, chess.QUEEN: 4}
    total = sum(
        weights[pt] * len(board.pieces(pt, colour))
        for pt in weights
        for colour in (chess.WHITE, chess.BLACK)
    )
    if total >= 16:
        return "opening", total
    if total >= 6:
        return "middlegame", total
    return "endgame", total


_agent = None


def agent_module():
    """Import the control agent, mirroring the platform: the agent root leads sys.path."""
    global _agent
    if _agent is None:
        if str(REPO) not in sys.path:
            sys.path.insert(0, str(REPO))
        import agent as module

        _agent = module
    return _agent


def reset_agent() -> None:
    """Drop all per-game state; module state must never cross a game boundary."""
    module = agent_module()
    module._engine = module.Engine()
    module._eval_table = [None] * len(module._eval_table)


def play_one(entry: dict) -> dict:
    module = agent_module()
    reset_agent()
    opening = entry["opening_fen"]
    board = chess.Board(opening)
    control_white = entry["control_is_white"]
    clock_ms = float(entry["control_clock_ms"])
    increment = entry["increment_ms"]
    movetime = entry["engine_movetime_ms"]
    exe = BIN / entry["opponent"]

    records: list[dict] = []
    played: list[str] = []
    failure: str | None = None
    retries: list[dict] = []
    control_times: list[float] = []
    engine = None
    started = time.perf_counter()
    try:
        engine = UciEngine(exe)
        while len(played) < MAX_PLIES and not board.is_game_over(claim_draw=True):
            control_to_move = board.turn == (chess.WHITE if control_white else chess.BLACK)
            phase, material = phase_of(board)
            # Every position is recorded, not only the ones the control moves from. A
            # leaf evaluator is asked about positions with either side to move, so the
            # corpus has to contain both.
            records.append(
                {
                    "ply": len(played),
                    "fen": board.fen(),
                    "side_to_move": "white" if board.turn else "black",
                    "phase": phase,
                    "material_phase": material,
                    "control_static_cp": int(module.cached_evaluate(board)),
                    "in_check": board.is_check(),
                    "control_to_move": control_to_move,
                }
            )
            if control_to_move:
                t0 = time.perf_counter()
                uci = module.get_move(board.fen(), int(clock_ms))
                spent = (time.perf_counter() - t0) * 1000.0
                control_times.append(spent)
                clock_ms -= spent
                if clock_ms <= 0:
                    failure = "control_flagged"
                    break
                clock_ms += increment
            else:
                # Older Rustic builds emit a garbage bestmove when the movetime is too
                # short to finish one iteration, so a bad reply is retried with more
                # time before it is called a failure. This is an infrastructure retry,
                # not a chess result.
                uci = engine.bestmove(opening, played, movetime)
                if not legal_uci(uci, board):
                    retries.append({"ply": len(played), "first_reply": uci})
                    uci = engine.bestmove(opening, played, movetime * 4)
                if uci is None:
                    failure = "engine_no_bestmove"
                    break
            try:
                move = chess.Move.from_uci(uci)
            except ValueError:
                failure = f"malformed:{'control' if control_to_move else 'engine'}"
                break
            if move not in board.legal_moves:
                failure = f"illegal:{'control' if control_to_move else 'engine'}"
                break
            board.push(move)
            played.append(uci)
    except Exception as exc:  # noqa: BLE001
        failure = f"exception:{type(exc).__name__}:{exc}"
    finally:
        if engine is not None:
            engine.close()

    result = board.result(claim_draw=True) if board.is_game_over(claim_draw=True) else "*"
    control_times.sort()
    return {
        "game_id": entry["game_id"],
        "pair_id": entry["pair_id"],
        "split": entry["split"],
        "opponent": entry["opponent"],
        "opponent_family": entry["opponent_family"],
        "held_out_family": entry["held_out_family"],
        "opening_id": entry["opening_id"],
        "opening_fen": opening,
        "control_is_white": control_white,
        "moves": played,
        "plies": len(played),
        "result": result,
        "failure": failure,
        "engine_retries": retries,
        "infrastructure_failure": bool(
            failure and failure.startswith(("engine_no_bestmove", "exception"))
        ),
        "wall_seconds": round(time.perf_counter() - started, 3),
        "control_move_ms_mean": (
            round(sum(control_times) / len(control_times), 2) if control_times else None
        ),
        "control_move_ms_p95": (
            round(control_times[int(len(control_times) * 0.95)], 2) if control_times else None
        ),
        "positions": records,
    }


def worker(entry: dict) -> dict:
    out = REPO / entry["output"]
    if out.exists():
        return {"game_id": entry["game_id"], "skipped": True}
    record = play_one(entry)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record), encoding="utf-8")
    return {
        "game_id": record["game_id"],
        "plies": record["plies"],
        "positions": len(record["positions"]),
        "result": record["result"],
        "failure": record["failure"],
        "wall_seconds": record["wall_seconds"],
        "skipped": False,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--manifest",
        type=pathlib.Path,
        default=REPO / "tests" / "results" / "nnue" / "game_manifest.json",
    )
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0, help="0 means every manifested game")
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--out", type=pathlib.Path, default=None)
    args = ap.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    entries = manifest["entries"][args.offset :]
    if args.limit:
        entries = entries[: args.limit]

    started = time.perf_counter()
    done = 0
    positions = 0
    failures: list[dict] = []
    results: list[dict] = []
    with mp.Pool(processes=args.workers) as pool:
        for res in pool.imap_unordered(worker, entries, chunksize=1):
            done += 1
            results.append(res)
            if not res.get("skipped"):
                positions += res.get("positions", 0)
                if res.get("failure"):
                    failures.append(res)
            if done % 25 == 0 or done == len(entries):
                rate = done / max(1e-9, time.perf_counter() - started)
                print(
                    f"{done}/{len(entries)} games  {positions:,} positions  "
                    f"{rate * 3600:,.0f} games/h  {len(failures)} failures",
                    flush=True,
                )

    elapsed = time.perf_counter() - started
    summary = {
        "workers": args.workers,
        "games_requested": len(entries),
        "games_done": done,
        "positions": positions,
        "elapsed_seconds": round(elapsed, 2),
        "games_per_hour": round(done / elapsed * 3600, 1),
        "positions_per_hour": round(positions / elapsed * 3600, 1),
        "failures": failures[:50],
        "failure_count": len(failures),
    }
    print(json.dumps(summary, indent=2))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(summary, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
