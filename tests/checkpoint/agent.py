"""Classical chess search with conservative, root-only opponent adaptation."""

from __future__ import annotations

import math
import time
from collections import Counter
from dataclasses import dataclass, field

import chess

VALUES = (0, 100, 320, 335, 500, 950, 0)
MATE = 30_000
INF = 32_000
MAX_PLY = 96
TT_SIZE = 65_536
PRIOR = (0.17, 0.17, 0.17, 0.17, 0.17, 0.15)
POLICIES = ("noisy", "material", "tactical", "shallow", "deep", "unknown")
ADAPTIVE = False
NORMAL_MARGIN = 35
WINNING_MARGIN = 10
SWINDLE_MARGIN = 60
EXPLOIT_WEIGHT = 0.25
MIN_OBSERVATIONS = 4
MODEL_FRACTION = 0.012
PROFILE_MIN_SECONDS = 0.5
Key = tuple[int, int, int, int, int, int, int, int, bool, int, int | None]


def position_key(board: chess.Board) -> Key:
    return (board.pawns, board.knights, board.bishops, board.rooks, board.queens,
            board.kings, board.occupied_co[chess.WHITE], board.occupied_co[chess.BLACK],
            board.turn, board.clean_castling_rights(),
            board.ep_square if board.has_legal_en_passant() else None)


def material(board: chess.Board) -> int:
    return sum(VALUES[p] * (len(board.pieces(p, board.turn))
                           - len(board.pieces(p, not board.turn))) for p in range(1, 6))


def evaluate(board: chess.Board) -> int:
    """Colour-symmetric tapered evaluation, from the mover's perspective."""
    phase = min(24, (board.knights | board.bishops).bit_count()
                + 2 * board.rooks.bit_count() + 4 * board.queens.bit_count())
    total = 0
    for colour in (chess.WHITE, chess.BLACK):
        own = board.occupied_co[colour]
        pawns = board.pawns & own
        enemy_pawns = board.pawns & board.occupied_co[not colour]
        king = board.king(colour)
        enemy_king = board.king(not colour)
        score = 0
        files = [(pawns & chess.BB_FILES[f]).bit_count() for f in range(8)]
        for square in chess.scan_forward(own):
            piece = board.piece_type_at(square)
            if piece is None:
                continue
            rank = chess.square_rank(square) if colour else 7 - chess.square_rank(square)
            file = chess.square_file(square)
            centre = 7 - abs(2 * file - 7) - abs(2 * rank - 7)
            score += VALUES[piece]
            if piece == chess.PAWN:
                score += rank * 6 + max(0, centre) * 2
                adjacent = (chess.BB_FILES[file - 1] if file else 0)
                adjacent |= chess.BB_FILES[file + 1] if file < 7 else 0
                if not pawns & adjacent:
                    score -= 13
                if files[file] > 1:
                    score -= 11
                front = ((chess.BB_ALL << (8 * (chess.square_rank(square) + 1)))
                         & chess.BB_ALL if colour else (1 << (8 * chess.square_rank(square))) - 1)
                if not enemy_pawns & (adjacent | chess.BB_FILES[file]) & front:
                    score += (rank * rank * (40 - phase)) // 24
                    if king is not None and enemy_king is not None:
                        score += (chess.square_distance(enemy_king, square)
                                  - chess.square_distance(king, square)) * (24 - phase) // 6
            elif piece in (chess.KNIGHT, chess.BISHOP):
                score += centre * (5 if piece == chess.KNIGHT else 3)
            elif piece == chess.ROOK:
                if files[file] == 0:
                    score += 12 if enemy_pawns & chess.BB_FILES[file] else 24
                if rank == 6:
                    score += 18
            elif piece == chess.KING:
                score += (centre * 7 * (24 - phase) - centre * 4 * phase) // 24
                if phase > 8:
                    score += (chess.BB_KING_ATTACKS[square] & pawns).bit_count() * phase // 3
            if piece != chess.PAWN:
                attacks = board.attacks_mask(square)
                if piece != chess.KING:
                    score += (attacks & ~own).bit_count() * (2 if piece == chess.QUEEN else 3)
                if enemy_king is not None:
                    pressure = (attacks & chess.BB_KING_ATTACKS[enemy_king]).bit_count()
                    score += pressure * phase // 4
        if (board.bishops & own).bit_count() >= 2:
            score += 28
        total += score if colour == board.turn else -score
    return total


@dataclass(frozen=True)
class Pattern:
    tactical: bool
    closed: bool
    attack: bool
    endgame: bool
    conversion: bool
    swindle: bool


def recognise(board: chess.Board, score: int) -> Pattern:
    nonpawns = (board.occupied & ~board.pawns & ~board.kings).bit_count()
    locked = ((board.pawns & board.occupied_co[chess.WHITE]) << 8
              & board.pawns & board.occupied_co[chess.BLACK]).bit_count()
    pressure = 0
    for colour in (chess.WHITE, chess.BLACK):
        king = board.king(colour)
        if king is not None:
            pressure += sum(board.is_attacked_by(not colour, s)
                            for s in chess.scan_forward(chess.BB_KING_ATTACKS[king]))
    captures = sum(1 for _ in board.generate_legal_captures())
    return Pattern(board.is_check() or captures >= 4, locked >= 2, pressure >= 4,
                   nonpawns <= 4 or not board.queens, score > 250, score < -250)


def pack_mate(score: int, ply: int) -> int:
    return score + ply if score > MATE - MAX_PLY else (
        score - ply if score < -MATE + MAX_PLY else score)


def unpack_mate(score: int, ply: int) -> int:
    return score - ply if score > MATE - MAX_PLY else (
        score + ply if score < -MATE + MAX_PLY else score)


@dataclass
class Entry:
    key: Key
    clock: int
    context: int
    depth: int
    bound: int
    score: int
    move: chess.Move
    age: int


@dataclass
class Reply:
    move: str
    static: float
    quiet: float
    shallow: float
    deep: float
    exchange: float
    attack: float
    complexity: float
    depth: int = 0
    quiet_known: bool = True
    shallow_known: bool = True


def distribution(values: list[float], temperature: float) -> list[float]:
    top = max(values)
    weights = [math.exp(max(-30.0, (v - top) / temperature)) for v in values]
    total = sum(weights)
    return [0.96 * w / total + 0.04 / len(values) for w in weights]


def policies(replies: list[Reply]) -> list[list[float]]:
    count = len(replies)
    result = [[1.0 / count] * count]
    for column, temperature in zip(("static", "quiet", "shallow", "deep"),
                                   (22.0, 40.0, 30.0, 20.0), strict=True):
        if ((column == "quiet" and not all(r.quiet_known for r in replies))
                or (column == "shallow" and not all(r.shallow_known for r in replies))
                or (column == "deep" and not all(r.depth for r in replies))):
            result.append([1.0 / count] * count)
            continue
        values = [float(getattr(r, column)) for r in replies]
        ranks: dict[float, float] = {}
        for index, value in enumerate(sorted(values)):
            ranks.setdefault(value, index / count)
        ranked = [ranks[value] for value in values]
        scores = [v + 10 * rank + (8 * r.exchange if column == "static" else
                                  6 * r.attack + 3 * r.complexity)
                  for v, rank, r in zip(values, ranked, replies, strict=True)]
        result.append(distribution(scores, temperature))
    result.append([0.5 / count + 0.125 * sum(result[k][i] for k in range(1, 5))
                   for i in range(count)])
    return result


@dataclass
class Scope:
    belief: list[float] = field(default_factory=lambda: list(PRIOR))
    informative: int = 0
    disagreement: float = 0.0
    last_regret: float = 0.0

    @property
    def entropy(self) -> float:
        return -sum(p * math.log(p) for p in self.belief) / math.log(6)

    @property
    def confidence(self) -> float:
        return max(0.0, (1 - self.entropy) * (1 - self.belief[5])
                   * (1 - self.disagreement))

    def observe(self, replies: list[Reply], move: str) -> None:
        if len(replies) < 3:
            return
        indices = [i for i, reply in enumerate(replies) if reply.move == move]
        if not indices or max(r.deep for r in replies) - min(r.deep for r in replies) < 35:
            return
        index = indices[0]
        likelihoods = [p[index] for p in policies(replies)]
        dominant = max(range(5), key=lambda k: self.belief[k])
        contradiction = (self.belief[dominant] > 0.65
                         and likelihoods[dominant] < 0.15 / len(replies))
        self.disagreement = 0.8 * self.disagreement + 0.2 * float(contradiction)
        self.last_regret = max(r.deep for r in replies) - replies[index].deep
        posterior = [(0.97 * p + 0.03 * prior) * likelihood
                     for p, prior, likelihood in zip(self.belief, PRIOR, likelihoods, strict=True)]
        if contradiction:
            posterior[5] *= 2.5
        total = sum(posterior)
        self.belief = [p / total for p in posterior]
        self.informative += 1

    def expected(self, replies: list[Reply]) -> float:
        mixture = policies(replies)
        return -sum(r.deep * sum(self.belief[k] * mixture[k][i] for k in range(6))
                    for i, r in enumerate(replies))


class Deadline(Exception):
    pass


class Engine:
    def __init__(self) -> None:
        self.table: list[Entry | None] = [None] * TT_SIZE
        self.history: dict[tuple[bool, int, int], int] = {}
        self.killers: dict[int, tuple[chess.Move | None, chess.Move | None]] = {}
        self.seen: Counter[Key] = Counter()
        self.context = 0
        self.duplicates = 0
        self.pending: chess.Board | None = None
        self.evidence: list[Reply] = []
        self.scope = Scope()
        self.age = 0
        self.nodes = 0
        self.modelling = False
        self.collect = False
        self.deadline = 0.0
        self.pattern = Pattern(False, False, False, False, False, False)
        self.reply_scores: dict[tuple[chess.Move, chess.Move], dict[int, int]] = {}
        self.root_move = chess.Move.null()
        self.stats: dict[str, float] = {}

    def tick(self) -> None:
        self.nodes += 1
        if (self.modelling or self.nodes % 16 == 0) and time.perf_counter() >= self.deadline:
            raise Deadline

    def enter(self, board: chess.Board) -> Key:
        key = position_key(board)
        old = self.seen[key]
        self.duplicates += int(old == 1)
        self.context ^= hash((key, old)) ^ hash((key, old + 1))
        self.seen[key] += 1
        return key

    def leave(self, key: Key) -> None:
        old = self.seen[key]
        self.duplicates -= int(old == 2)
        self.context ^= hash((key, old)) ^ hash((key, old - 1))
        self.seen[key] -= 1
        if not self.seen[key]:
            del self.seen[key]

    def drawn(self, board: chess.Board) -> bool:
        if (board.halfmove_clock >= 100 or self.seen[position_key(board)] >= 3
                or board.is_insufficient_material()):
            return True
        if board.halfmove_clock < 99 and not self.duplicates:
            return False
        # The referee claims a draw even when it is available on the next move.
        for move in board.legal_moves:
            if board.is_zeroing(move):
                continue
            board.push(move)
            claim = self.seen[position_key(board)] >= 2 or (
                board.halfmove_clock >= 100 and any(board.legal_moves))
            board.pop()
            if claim:
                return True
        return False

    def order(self, board: chess.Board, moves: list[chess.Move],
              preferred: chess.Move | None, ply: int) -> list[chess.Move]:
        killers = self.killers.get(ply, (None, None))

        def priority(move: chess.Move) -> int:
            if move == preferred:
                return 1_000_000
            piece = board.piece_type_at(move.from_square) or 0
            victim = board.piece_type_at(move.to_square) or (1 if board.is_en_passant(move) else 0)
            if victim or move.promotion:
                return 100_000 + 16 * VALUES[victim] - VALUES[piece] + VALUES[move.promotion or 0]
            value = self.history.get((board.turn, move.from_square, move.to_square), 0)
            if move in killers:
                value += 80_000
            if self.pattern.closed and piece == chess.PAWN:
                value += 100
                if (chess.BB_PAWN_ATTACKS[board.turn][move.to_square]
                        & board.occupied_co[not board.turn]):
                    value += 100
            if self.pattern.endgame and piece == chess.KING:
                file, rank = chess.square_file(move.to_square), chess.square_rank(move.to_square)
                value += 14 - abs(2 * file - 7) - abs(2 * rank - 7)
            if (ply == 0 and (self.pattern.tactical or self.pattern.attack)
                    and board.gives_check(move)):
                value += 300
            return value

        return sorted(moves, key=priority, reverse=True)

    def quiesce(self, board: chess.Board, alpha: int, beta: int, ply: int) -> int:
        self.tick()
        check = board.is_check()
        moves = list(board.legal_moves) if check else None
        if check and not moves:
            return -MATE + ply
        if self.drawn(board):
            return 0
        if not check and not any(board.generate_legal_moves()):
            return 0
        if ply >= MAX_PLY:
            return evaluate(board)
        if not check:
            stand = evaluate(board)
            if stand >= beta:
                return stand
            alpha = max(alpha, stand)
            forcing = ply < 3 and (self.pattern.tactical or self.pattern.attack)
            moves = [m for m in board.legal_moves if board.is_capture(m) or m.promotion
                     or (forcing and board.gives_check(m))]
        assert moves is not None
        for move in self.order(board, moves, None, ply):
            board.push(move)
            key = self.enter(board)
            try:
                score = -self.quiesce(board, -beta, -alpha, ply + 1)
            finally:
                self.leave(key)
                board.pop()
            if score >= beta:
                return score
            alpha = max(alpha, score)
        return alpha

    def search(self, board: chess.Board, depth: int, alpha: int, beta: int, ply: int) -> int:
        if depth <= 0:
            return self.quiesce(board, alpha, beta, ply)
        self.tick()
        moves = list(board.legal_moves)
        if not moves:
            return -MATE + ply if board.is_check() else 0
        if self.drawn(board):
            return 0
        if ply >= MAX_PLY:
            return evaluate(board)
        key = position_key(board)
        slot = hash(key) % TT_SIZE
        entry = self.table[slot]
        context = self.context ^ hash((self.pattern.tactical or self.pattern.attack,
                                       max(0, 3 - ply)))
        preferred = None
        if entry is not None and entry.key == key:
            preferred = entry.move
            if (entry.depth >= depth and entry.clock == board.halfmove_clock
                    and entry.context == context):
                value = unpack_mate(entry.score, ply)
                if entry.bound == 0 or (entry.bound == 1 and value >= beta) or (
                        entry.bound == -1 and value <= alpha):
                    return value
        original = alpha
        best = -INF
        best_move = moves[0]
        for index, move in enumerate(self.order(board, moves, preferred, ply)):
            quiet = not board.is_capture(move) and not move.promotion
            board.push(move)
            child = self.enter(board)
            try:
                if index == 0:
                    score = -self.search(board, depth - 1, -beta, -alpha, ply + 1)
                else:
                    score = -self.search(board, depth - 1, -alpha - 1, -alpha, ply + 1)
                    if alpha < score < beta:
                        score = -self.search(board, depth - 1, -beta, -alpha, ply + 1)
            finally:
                self.leave(child)
                board.pop()
            if self.collect and ply == 1 and alpha < score < beta:
                started = time.perf_counter()
                self.reply_scores.setdefault((self.root_move, move), {})[depth] = score
                self.stats["model_seconds"] += time.perf_counter() - started
            if score > best:
                best, best_move = score, move
            alpha = max(alpha, score)
            if alpha >= beta:
                if quiet:
                    previous = self.killers.get(ply, (None, None))
                    if move != previous[0]:
                        self.killers[ply] = (move, previous[0])
                    hkey = (board.turn, move.from_square, move.to_square)
                    self.history[hkey] = min(20_000, self.history.get(hkey, 0) + depth * depth)
                break
        bound = -1 if best <= original else (1 if best >= beta else 0)
        if entry is None or entry.age != self.age or depth >= entry.depth:
            self.table[slot] = Entry(key, board.halfmove_clock, context, depth, bound,
                                     pack_mate(best, ply), best_move, self.age)
        return best

    def reconstruct(self, board: chess.Board) -> str | None:
        if self.pending is None:
            return None
        target = position_key(board)
        for move in self.pending.legal_moves:
            self.pending.push(move)
            matches = (position_key(self.pending) == target
                       and self.pending.halfmove_clock == board.halfmove_clock
                       and self.pending.fullmove_number == board.fullmove_number)
            self.pending.pop()
            if matches:
                return move.uci()
        return None

    def profile(self, board: chess.Board, root: chess.Move, stop: float) -> list[Reply]:
        replies: list[Reply] = []
        for move in board.legal_moves:
            if time.perf_counter() >= stop:
                return []
            exchange = float(board.is_capture(move))
            attack = float(board.gives_check(move))
            board.push(move)
            static = -material(board)
            scores = self.reply_scores.get((root, move), {})
            quiet = scores.get(1, static)
            shallow = scores.get(2, quiet)
            depth = max(scores, default=0)
            deep = scores.get(depth, shallow)
            complexity = (board.occupied & ~board.pawns & ~board.kings).bit_count() / 8
            board.pop()
            replies.append(Reply(move.uci(), static, quiet, shallow, deep,
                                 exchange, attack, complexity, depth,
                                 quiet_known=1 in scores, shallow_known=False))
        if stop - time.perf_counter() < 0.006:
            return replies
        previous_deadline = self.deadline
        self.deadline = stop - 0.0005
        self.modelling = True
        try:
            for stage in range(3):
                for reply in replies:
                    self.tick()
                    board.push_uci(reply.move)
                    key = self.enter(board)
                    try:
                        if stage == 0:
                            reply.quiet = -self.quiesce(board, -INF, INF, 2)
                            reply.quiet_known = True
                        elif stage == 1:
                            reply.shallow = -self.policy_search(board, 1, -INF, INF)
                            reply.shallow_known = True
                        elif reply.depth < 2:
                            reply.deep = -self.search(board, 1, -INF, INF, 2)
                            reply.depth = max(reply.depth, 2)
                    finally:
                        self.leave(key)
                        board.pop()
        except Deadline:
            pass
        finally:
            self.deadline = previous_deadline
            self.modelling = False
        return replies

    def policy_search(self, board: chess.Board, depth: int, alpha: int, beta: int) -> int:
        """A static-leaf shallow hypothesis, distinct from quiescent objective search."""
        self.tick()
        moves = list(board.legal_moves)
        if not moves:
            return -MATE if board.is_check() else 0
        if depth == 0:
            return material(board) + 4 * len(moves)
        best = -INF
        for move in moves:
            board.push(move)
            try:
                value = -self.policy_search(board, depth - 1, -beta, -alpha)
            finally:
                board.pop()
            best = max(best, value)
            alpha = max(alpha, value)
            if alpha >= beta:
                break
        return best

    def choose(self, fen: str, time_left_ms: int) -> str:
        started = time.perf_counter()
        board = chess.Board(fen)
        fallback = next(iter(board.legal_moves), None)
        if fallback is None:
            return "0000"
        self.stats = {"depth": 0, "model_seconds": 0, "adapted": 0, "nodes": 0}
        if time_left_ms <= 10:
            self.pending = None
            self.stats["seconds"] = time.perf_counter() - started
            return fallback.uci()
        available = time_left_ms / 1000
        budget = min(3.0, available / 32, max(0.001, available - 0.025))
        self.deadline = started + budget * 0.96
        self.collect = budget >= PROFILE_MIN_SECONDS
        soft = started + budget * 0.65
        self.nodes = 0
        self.age += 1
        observed = self.reconstruct(board)
        if observed is None:
            self.seen.clear()
            self.context = 0
            self.duplicates = 0
            self.scope = Scope()
        self.enter(board)
        self.history = {key: value // 2 for key, value in self.history.items() if value > 1}
        self.killers.clear()
        self.reply_scores.clear()
        self.pattern = recognise(board, evaluate(board))
        moves = self.order(board, list(board.legal_moves), None, 0)
        chosen = fallback
        completed: dict[chess.Move, int] = {}
        for depth in range(1, 65):
            current: dict[chess.Move, int] = {}
            root_best = -INF
            tolerance = SWINDLE_MARGIN
            try:
                for move in moves:
                    if time.perf_counter() >= self.deadline:
                        raise Deadline
                    self.root_move = move
                    board.push(move)
                    key = self.enter(board)
                    try:
                        # Inferior moves need only an upper bound below the safe set.
                        floor = max(-INF, root_best - tolerance - 1)
                        current[move] = -self.search(board, depth - 1, -INF, -floor, 1)
                        root_best = max(root_best, current[move])
                    finally:
                        self.leave(key)
                        board.pop()
            except Deadline:
                break
            completed = current
            moves.sort(key=lambda m: (completed[m],
                       int(not board.is_capture(m)) if self.pattern.swindle else 0), reverse=True)
            chosen = moves[0]
            self.stats["depth"] = depth
            if time.perf_counter() >= soft or abs(completed[chosen]) > MATE - MAX_PLY:
                break
        if time.perf_counter() - started < PROFILE_MIN_SECONDS:
            self.evidence = []
            return self.finish(board, chosen, started)
        modelling_at = time.perf_counter()
        elapsed = modelling_at - started
        model_left = max(0.0, elapsed * MODEL_FRACTION - self.stats["model_seconds"])
        model_stop = min(started + budget, modelling_at + model_left)
        if (observed is not None and self.evidence
                and len(self.evidence) <= 60 and model_left >= 0.001):
            self.scope.observe(self.evidence, observed)
        self.evidence = []
        if completed:
            best = completed[chosen]
            self.pattern = Pattern(self.pattern.tactical, self.pattern.closed, self.pattern.attack,
                                   self.pattern.endgame, best > 250, best < -250)
            tolerance = (WINNING_MARGIN if self.pattern.conversion else
                         SWINDLE_MARGIN if self.pattern.swindle else NORMAL_MARGIN)
            active = (ADAPTIVE and self.scope.informative >= MIN_OBSERVATIONS
                      and self.scope.confidence > 0.08
                      and not self.pattern.tactical and not self.pattern.attack
                      and abs(best) < MATE - MAX_PLY)
            safe = [m for m in moves if completed[m] >= best - tolerance] if active else [chosen]
            utility = float(best)
            for move in safe:
                if time.perf_counter() >= model_stop:
                    break
                board.push(move)
                key = self.enter(board)
                try:
                    replies = self.profile(board, move, model_stop)
                finally:
                    self.leave(key)
                    board.pop()
                if move == chosen:
                    self.evidence = replies
                if (active and replies
                        and all(r.depth >= 2 and abs(r.deep) < 29000 for r in replies)):
                    weight = min(EXPLOIT_WEIGHT, self.scope.confidence * EXPLOIT_WEIGHT)
                    expected = self.scope.expected(replies)
                    value = completed[move] + weight * max(0, min(150, expected - completed[move]))
                    if value > utility:
                        chosen, utility, self.evidence = move, value, replies
            self.stats["adapted"] = float(chosen != moves[0])
        self.stats["model_seconds"] += time.perf_counter() - modelling_at
        return self.finish(board, chosen, started)

    def finish(self, board: chess.Board, chosen: chess.Move, started: float) -> str:
        board.push(chosen)
        if board.halfmove_clock == 0:
            self.seen.clear()
            self.context = 0
            self.duplicates = 0
        self.enter(board)
        self.pending = board
        self.stats["seconds"] = time.perf_counter() - started
        self.stats["nodes"] = self.nodes
        return chosen.uci()


_engine = Engine()


def get_move(fen: str, time_left_ms: int) -> str:
    """Return a legal UCI move; persistent state belongs to this game only."""
    return _engine.choose(fen, time_left_ms)
