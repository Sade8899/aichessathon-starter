"""Bayesian, bound, completed-iteration, and lifecycle invariants."""

import ast
import copy
import inspect
import json
import math
import tempfile
from pathlib import Path

import chess

import agent
from harness.sandbox import local


def evidence(
    scores: list[int | None], gains: list[int], bounds: list[int] | None = None, depth: int = 3
) -> agent.ReplySet:
    moves = tuple(chess.Move.from_uci(m) for m in ("e7e5", "d7d5", "c7c5"))
    records = {
        move: agent.ReplyEvidence(
            move, gain, int(i == 1) * 3, depth, score, bounds[i] if bounds else 0
        )
        for i, (move, gain, score) in enumerate(zip(moves, gains, scores, strict=True))
    }
    return agent.ReplySet(depth, moves, records)


def unchanged_search() -> None:
    current_tree = ast.parse(Path("agent.py").read_text())
    baseline_tree = ast.parse(Path("tests/checkpoint/agent.py").read_text())
    for tree_nodes, names in (
        (
            (current_tree.body, baseline_tree.body),
            ("evaluate", "material", "position_key", "recognise", "pack_mate", "unpack_mate"),
        ),
        (
            (
                next(
                    n
                    for n in current_tree.body
                    if isinstance(n, ast.ClassDef) and n.name == "Engine"
                ).body,
                next(
                    n
                    for n in baseline_tree.body
                    if isinstance(n, ast.ClassDef) and n.name == "Engine"
                ).body,
            ),
            ("order", "quiesce", "enter", "leave", "drawn", "reconstruct", "finish"),
        ),
    ):
        for name in names:
            functions = [
                next(n for n in nodes if isinstance(n, ast.FunctionDef) and n.name == name)
                for nodes in tree_nodes
            ]
            assert ast.dump(functions[0]) == ast.dump(functions[1]), name


def units() -> None:
    assert {n for n in vars(agent.OpponentModel) if not n.startswith("_")} == {
        "observe",
        "predict",
        "confidence",
        "reset",
    }
    tree = ast.parse(inspect.getsource(agent.OpponentModel))
    assert not any(
        isinstance(n, ast.Attribute)
        and n.attr in {"search", "quiesce", "push", "pop", "choose", "perf_counter"}
        for n in ast.walk(tree)
    )
    sample = evidence([-100, 8, -300], [500, 0, 0])
    original = copy.deepcopy(sample)
    model = agent.OpponentModel()
    for _ in range(30):
        model.observe(sample, sample.moves[0])
    state = model.confidence()
    assert state.informative == 30 and state.posterior[1] > 0.7, state
    assert state.value > 0.3, state
    prediction = model.predict(sample)
    assert math.isclose(sum(prediction.probabilities), 1)
    assert sample == original
    before = model.confidence()
    for _ in range(10):
        model.observe(None, sample.moves[0])
    assert model.confidence().informative == before.informative
    assert model.confidence().value < before.value
    for _ in range(30):
        model.observe(sample, sample.moves[0])
    before = model.confidence()
    model.observe(sample, sample.moves[2])
    assert model.confidence().posterior[5] > before.posterior[5]
    assert model.confidence().change > 0
    model.reset()
    assert model.confidence().informative == 0
    missing = evidence([None, None, None], [0, 0, 0])
    for _ in range(10):
        model.observe(missing, missing.moves[0])
    assert model.confidence().informative == 0
    forced = agent.ReplySet(
        3, (sample.moves[0],), {sample.moves[0]: sample.replies[sample.moves[0]]}
    )
    model.observe(forced, sample.moves[0])
    assert model.confidence().informative == 0
    equal = evidence([0, 0, 0], [0, 0, 0])
    equal = agent.ReplySet(
        3, equal.moves, {m: agent.ReplyEvidence(m, 0, 0, 3, 0, 0) for m in equal.moves}
    )
    model.observe(equal, equal.moves[0])
    assert model.confidence().informative == 0
    exact_model, bound_model = agent.OpponentModel(), agent.OpponentModel()
    bound = evidence([-100, 8, -300], [500, 0, 0], [-1, 0, -1])
    exact_model.observe(sample, sample.moves[0])
    bound_model.observe(bound, sample.moves[0])
    assert bound_model.confidence().reliability < exact_model.confidence().reliability
    uncertain = evidence([0, 0, 0], [0, 0, 0], [-1, -1, -1])
    assert all(math.isclose(p, 1 / 3) for p in agent.interval_policy(uncertain, 25))


def invariants() -> None:
    model = agent.OpponentModel()
    sample = evidence([-100, 8, -300], [500, 0, 0])
    for _ in range(50):
        model.observe(sample, sample.moves[0])
    roots = [chess.Move.from_uci(m) for m in ("e2e4", "d2d4", "c2c4")]
    quiet = agent.Pattern(False, False, False, False, False, False)
    exercised = 0
    for top in (-400, 0, 400):
        for loss in range(31):
            scores = {roots[0]: top, roots[1]: top - loss, roots[2]: top - 100}
            frame = evidence([-top - 300, -top + loss, -top - 300], [500, 0, 0])
            chosen, bonus = agent.adaptive_choice(
                scores, {roots[1]: frame}, roots[0], model, quiet, 5000
            )
            assert chosen in scores
            assert top - scores[chosen] <= agent.safe_margin(quiet, top)
            assert 0 <= bonus <= 12
            exercised += chosen != roots[0]
    assert exercised > 0, "adaptive invariant tests did not exercise a changed move"
    scores = {roots[0]: 0, roots[1]: -8}
    for pattern, clock in (
        (agent.Pattern(True, False, False, False, False, False), 5000),
        (quiet, 100),
    ):
        assert (
            agent.adaptive_choice(scores, {roots[1]: sample}, roots[0], model, pattern, clock)[0]
            == roots[0]
        )
    lower = evidence([-300, -300, -300], [500, 0, 0], [1, 1, 1])
    assert (
        agent.adaptive_choice(scores, {roots[1]: lower}, roots[0], model, quiet, 5000)[0]
        == roots[0]
    )
    absent = evidence([None, 8, -300], [500, 0, 0])
    assert (
        agent.adaptive_choice(scores, {roots[1]: absent}, roots[0], model, quiet, 5000)[0]
        == roots[0]
    )


class InterruptedEngine(agent.Engine):
    def __init__(self) -> None:
        super().__init__()
        self.third_roots = 0

    def search(self, board: chess.Board, depth: int, alpha: int, beta: int, ply: int) -> int:
        if ply == 1 and depth == 2:
            self.third_roots += 1
            if self.third_roots == 2:
                raise agent.Deadline
        return super().search(board, depth, alpha, beta, ply)


def lifecycle() -> None:
    tested = InterruptedEngine()
    agent._engine = tested
    move = agent.get_move(chess.STARTING_FEN, 120000)
    assert chess.Move.from_uci(move) in chess.Board().legal_moves
    assert tested.stats["depth"] == 2
    assert len(tested.completed_evidence) <= 3
    assert all(frame.depth == 1 for frame in tested.completed_evidence.values())
    assert tested.iteration_evidence == {}
    assert move in {m.uci() for m in tested.completed_scores}
    board = chess.Board()
    board.push_uci(move)
    opponent = next(iter(board.legal_moves))
    board.push(opponent)
    assert tested.reconstruct(board) == opponent.uci()
    agent.get_move(board.fen(), 5000)
    assert tested.age == 2
    agent.get_move(chess.STARTING_FEN, 5000)
    assert tested.model.confidence().informative == 0
    agent._engine = agent.Engine()
    assert agent._engine.age == 0 and not agent._engine.seen

    events: list[str] = []

    class AuditedModel(agent.OpponentModel):
        def observe(self, frame: agent.ReplySet | None, reply: chess.Move) -> None:
            events.append("observe")
            super().observe(frame, reply)

    class AuditedEngine(agent.Engine):
        def search(self, board: chess.Board, depth: int, alpha: int, beta: int, ply: int) -> int:
            events.append("search")
            return super().search(board, depth, alpha, beta, ply)

    audited = AuditedEngine()
    audited.model = AuditedModel()
    agent._engine = audited
    board = chess.Board()
    board.push_uci(agent.get_move(board.fen(), 5000))
    board.push(next(iter(board.legal_moves)))
    events.clear()
    agent.get_move(board.fen(), 5000)
    assert events[0] == "observe" and "search" in events

    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary)
        source = Path("agent.py").read_text()
        source += """
_real_move = get_move
def get_move(fen: str, time_left_ms: int) -> str:
    move = _real_move(fen, time_left_ms)
    print("AGE", _engine.age, flush=True)
    return move
"""
        (path / "agent.py").write_text(source)
        for _ in range(2):
            process = local(path)
            process.start(90)
            board = chess.Board()
            try:
                for _ in range(2):
                    reply = chess.Move.from_uci(process.move(board.fen(), 5000))
                    assert reply in board.legal_moves
                    board.push(reply)
                    board.push(next(iter(board.legal_moves)))
            finally:
                process.stop()
            assert process.stderr_tail.splitlines() == ["AGE 1", "AGE 2"]


if __name__ == "__main__":
    unchanged_search()
    units()
    invariants()
    lifecycle()
    print(
        json.dumps(
            {
                "bayesian": "passed",
                "safe_set_cases": 93,
                "completed_iteration": "passed",
                "interface_lifecycle": "passed",
            }
        )
    )
