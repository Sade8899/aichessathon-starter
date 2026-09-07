"""A test-only UCI opponent that speaks the harness `Agent` interface.

This exists so the public engine baselines on the leaderboard can be played locally
under the competition referee. It is never imported by `agent.py`, never shipped, and
nothing in `harness/` is modified: `harness.referee.play_match` only needs an object
with `start`, `move`, `stop` and `stderr_tail`, and `UciAgent` supplies exactly that.

Two fairness details matter and are handled here rather than left to chance.

Clocks. The referee hands the mover its own remaining milliseconds and nothing else,
but a UCI engine time manager wants `wtime` and `btime` together. A `ClockBook` is
shared between the two seats of one game; each seat records its own clock as the
referee reports it, so from the second move on each `go` line carries both clocks as
the referee actually holds them. Before a side has moved, its base time is used.

Process cleanup. `stop()` always terminates the child, even when the engine ignored
`quit`, so a crashed or wedged engine cannot leak into the next game of a match.
"""

from __future__ import annotations

import contextlib
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Queue
from typing import IO

import chess

from harness.sandbox import AgentFailure

QUIT_GRACE_S = 0.5
KILL_GRACE_S = 2.0
STDERR_TAIL_CAP = 4096
MOVE_GRACE_S = 5.0


@dataclass
class ClockBook:
    """The two clocks of one game, as the referee reported them to each seat."""

    base_ms: int
    increment_ms: int
    remaining_ms: dict[bool, int] = field(default_factory=dict)

    def record(self, colour: bool, value_ms: int) -> None:
        self.remaining_ms[colour] = value_ms

    def read(self, colour: bool) -> int:
        return self.remaining_ms.get(colour, self.base_ms)


class UciAgent:
    """One UCI engine process, driven through the harness `Agent` protocol."""

    def __init__(
        self,
        executable: Path,
        book: ClockBook,
        *,
        name: str = "",
        options: dict[str, str] | None = None,
    ) -> None:
        self.executable = Path(executable).resolve()
        self.book = book
        self.name = name or self.executable.stem
        self.options = dict(options or {})
        self.stderr_tail = ""
        self.identified = ""
        self._process: subprocess.Popen[bytes] | None = None
        self._lines: Queue[str | None] = Queue()
        self._stderr: list[bytes] = []
        self._readers: list[threading.Thread] = []

    # ------------------------------------------------------------------ lifecycle

    def start(self, init_budget_s: float) -> None:
        try:
            process = subprocess.Popen(
                [str(self.executable)],
                cwd=str(self.executable.parent),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except OSError as error:
            raise AgentFailure("crash") from error
        self._process = process
        self._readers = [
            self._reader(process.stdout, "stdout"),
            self._reader(process.stderr, "stderr"),
        ]
        deadline = time.monotonic() + init_budget_s
        self._send("uci")
        while True:
            line = self._await(deadline)
            if line is None:
                raise AgentFailure("init")
            if line.startswith("id name "):
                self.identified = line[len("id name ") :].strip()
            if line.strip() == "uciok":
                break
        for key, value in self.options.items():
            self._send(f"setoption name {key} value {value}")
        self._send("ucinewgame")
        if not self._ready(deadline):
            raise AgentFailure("init")

    def stop(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        try:
            if process.poll() is None:
                with contextlib.suppress(AgentFailure):
                    self._send_to(process, "quit")
                try:
                    process.wait(timeout=QUIT_GRACE_S)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=KILL_GRACE_S)
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    with contextlib.suppress(OSError):
                        stream.close()
            for reader in self._readers:
                reader.join(KILL_GRACE_S)
            self._readers = []
            self.stderr_tail = b"".join(self._stderr)[-STDERR_TAIL_CAP:].decode("utf-8", "replace")

    # ---------------------------------------------------------------------- moving

    def move(self, fen: str, time_left_ms: int) -> str:
        if self._process is None:
            raise RuntimeError("the engine moved before start")
        colour = chess.Board(fen).turn
        self.book.record(colour, time_left_ms)
        wtime = max(self.book.read(chess.WHITE), 1)
        btime = max(self.book.read(chess.BLACK), 1)
        increment = self.book.increment_ms
        self._send(f"position fen {fen}")
        self._send(f"go wtime {wtime} btime {btime} winc {increment} binc {increment}")
        deadline = time.monotonic() + (time_left_ms / 1000.0) + MOVE_GRACE_S
        while True:
            line = self._await(deadline)
            if line is None:
                raise AgentFailure("flag")
            if line.startswith("bestmove"):
                parts = line.split()
                if len(parts) < 2:
                    raise AgentFailure("illegal")
                return parts[1]

    # ------------------------------------------------------------------- plumbing

    def _ready(self, deadline: float) -> bool:
        self._send("isready")
        while True:
            line = self._await(deadline)
            if line is None:
                return False
            if line.strip() == "readyok":
                return True

    def _send(self, command: str) -> None:
        if self._process is None:
            raise AgentFailure("crash")
        self._send_to(self._process, command)

    @staticmethod
    def _send_to(process: subprocess.Popen[bytes], command: str) -> None:
        stream = process.stdin
        if stream is None:
            raise AgentFailure("crash")
        try:
            stream.write(command.encode() + b"\n")
            stream.flush()
        except (BrokenPipeError, OSError) as error:
            raise AgentFailure("crash") from error

    def _await(self, deadline: float) -> str | None:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                line = self._lines.get(timeout=remaining)
            except Empty:
                return None
            if line is None:
                raise AgentFailure("crash")
            if line.strip():
                return line.strip()

    def _reader(self, stream: IO[bytes] | None, which: str) -> threading.Thread:
        thread = threading.Thread(target=self._forward, args=(stream, which), daemon=True)
        thread.start()
        return thread

    def _forward(self, stream: IO[bytes] | None, which: str) -> None:
        if stream is None:
            return
        try:
            while True:
                raw = stream.readline()
                if not raw:
                    break
                if which == "stderr":
                    self._stderr.append(raw)
                    if len(self._stderr) > 512:
                        del self._stderr[:256]
                else:
                    self._lines.put(raw.decode("utf-8", "replace"))
        except (OSError, ValueError):
            pass
        finally:
            if which == "stdout":
                self._lines.put(None)
