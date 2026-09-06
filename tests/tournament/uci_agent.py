"""Development-only UCI transport around the real Chessathon agent process."""

import argparse
import json
import os
import signal
import sys
import tempfile
import threading
import time
from pathlib import Path

import chess

from harness.sandbox import AgentFailure, local

BRIDGE = """
import importlib.util
import json
import os
import resource
import signal
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("chessathon_player", {source!r})
player = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = player
spec.loader.exec_module(player)
stopping = False
metrics = Path({metrics!r})

def interrupt(signum, frame):
    global stopping
    stopping = True
signal.signal(signal.SIGUSR1, interrupt)

if hasattr(player, 'Engine'):
    base_tick = player.Engine.tick
    def tick(self):
        if stopping:
            self.deadline = 0.0
        base_tick(self)
    player.Engine.tick = tick

def get_move(fen: str, time_left_ms: int) -> str:
    global stopping
    stopping = False
    metrics.write_text(json.dumps({{"searching": True}}))
    move = player.get_move(fen, time_left_ms)
    engine = getattr(player, '_engine', None)
    stats = dict(getattr(engine, 'stats', {{}}))
    scores = getattr(engine, 'completed_scores', {{}})
    stats.update(clock_ms=time_left_ms, searching=False, move=move,
                 peak_rss_mb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
                 score=max(scores.values()) if scores else 0,
                 age=getattr(engine, 'age', 0))
    temporary = metrics.with_suffix('.next')
    temporary.write_text(json.dumps(stats))
    os.replace(temporary, metrics)
    return move
"""


class UCI:
    def __init__(self, source: Path) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="chessathon-uci-")
        self.directory = Path(self.temporary.name)
        self.metrics = self.directory / "metrics.json"
        (self.directory / "agent.py").write_text(
            BRIDGE.format(source=str(source.resolve()), metrics=str(self.metrics))
        )
        self.board = chess.Board()
        self.worker = local(self.directory)
        self.worker.start(90)
        self.thread: threading.Thread | None = None
        self.stopped = threading.Event()
        self.output_lock = threading.Lock()
        self.failure: BaseException | None = None

    def emit(self, line: str) -> None:
        with self.output_lock:
            print(line, flush=True)

    def stop(self) -> None:
        self.stopped.set()
        if self.thread is None or not self.thread.is_alive():
            return
        limit = time.monotonic() + 1.0
        while self.thread.is_alive() and time.monotonic() < limit:
            if self.metrics.exists():
                try:
                    searching = json.loads(self.metrics.read_text()).get("searching", False)
                except json.JSONDecodeError:
                    searching = False
                if searching and self.worker._process is not None:
                    os.kill(self.worker._process.pid, signal.SIGUSR1)
                    break
            self.thread.join(0.002)
        self.thread.join(1.0)
        if self.thread.is_alive():
            raise RuntimeError("agent did not acknowledge stop")

    def reset(self) -> None:
        self.stop()
        self.worker.stop()
        self.metrics.unlink(missing_ok=True)
        self.worker = local(self.directory)
        self.worker.start(90)
        self.board = chess.Board()
        self.failure = None

    def position(self, tokens: list[str]) -> None:
        self.stop()
        if tokens[0] == "startpos":
            board, offset = chess.Board(), 1
        elif tokens[0] == "fen":
            board, offset = chess.Board(" ".join(tokens[1:7])), 7
        else:
            raise ValueError("expected startpos or fen")
        if len(tokens) > offset:
            assert tokens[offset] == "moves"
            for uci in tokens[offset + 1 :]:
                board.push_uci(uci)
        self.board = board

    def search(self, board: chess.Board, clock: int, infinite: bool) -> None:
        try:
            move = self.worker.move(board.fen(), clock)
            legal = chess.Move.from_uci(move)
            if legal not in board.legal_moves:
                raise ValueError(f"agent returned illegal move {move}")
            stats = json.loads(self.metrics.read_text())
            elapsed = stats.get("seconds", 0.0)
            nodes = int(stats.get("nodes", 0))
            self.emit(
                f"info depth {int(stats.get('depth', 0))} nodes {nodes} "
                f"nps {int(nodes / max(elapsed, 0.000001))} time {int(elapsed * 1000)} "
                f"score cp {int(stats.get('score', 0))} pv {move}"
            )
            self.emit("info string chessathon " + json.dumps(stats, separators=(",", ":")))
            if infinite:
                self.stopped.wait()
            self.emit("bestmove " + move)
        except (AgentFailure, ValueError, OSError, RuntimeError) as error:
            self.failure = error
            self.emit("info string ERROR " + str(error))
            # Do not turn a crashed or illegal agent into a successful fallback game.
            os._exit(1)

    def go(self, tokens: list[str]) -> None:
        self.stop()
        if not any(self.board.legal_moves):
            self.emit("bestmove 0000")
            return
        values = {}
        for name in ("wtime", "btime", "movetime"):
            if name in tokens:
                values[name] = int(tokens[tokens.index(name) + 1])
        key = "wtime" if self.board.turn else "btime"
        clock = values.get(key, values.get("movetime", 120000))
        # Increment belongs to the referee after the move, never to this call.
        self.stopped.clear()
        self.metrics.unlink(missing_ok=True)
        process = self.worker._process
        if process is not None and hasattr(os, "sched_setaffinity"):
            os.sched_setaffinity(process.pid, os.sched_getaffinity(0))
        self.thread = threading.Thread(
            target=self.search, args=(self.board.copy(), clock, "infinite" in tokens)
        )
        self.thread.start()

    def close(self) -> None:
        self.stop()
        self.worker.stop()
        self.temporary.cleanup()

    def loop(self) -> None:
        try:
            for line in sys.stdin:
                tokens = line.split()
                if not tokens:
                    continue
                command = tokens[0]
                if command == "uci":
                    self.emit("id name Chessathon Python adapter")
                    self.emit("id author local development")
                    self.emit("option name Threads type spin default 1 min 1 max 1")
                    self.emit("uciok")
                elif command == "isready":
                    self.emit("readyok")
                elif command == "ucinewgame":
                    self.reset()
                elif command == "position":
                    self.position(tokens[1:])
                elif command == "go":
                    self.go(tokens[1:])
                elif command == "stop":
                    self.stop()
                elif command == "quit":
                    break
        finally:
            self.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", type=Path, required=True)
    arguments = parser.parse_args()
    UCI(arguments.agent).loop()


if __name__ == "__main__":
    main()
