# SPDX-License-Identifier: GPL-3.0-only
"""Swipe games for the touch panel: ``snake`` and ``game_2048``.

The rules live in plain classes (``SnakeGame``, ``Game2048``) with no drawing and a seedable RNG so they can be
unit-tested; the widgets only translate swipes into moves and paint the state. Both run only while fullscreen
(``on_tap: fullscreen``) and keep high scores in ``~/.local/state/wigix/highscores.json`` when it is writable.
"""
from __future__ import annotations

import json
import logging
import math
import pathlib
import random
import time

from . import Widget, widget
from ..render import THEME

log = logging.getLogger("wigix")

HIGHSCORES = pathlib.Path("~/.local/state/wigix/highscores.json").expanduser()
SWIPE_MIN = 40                                     # px of travel before a drag counts as a swipe
DIRS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}


# --- high scores ----------------------------------------------------------------------------------
def load_highscore(game: str) -> int:
    try:
        return int(json.loads(HIGHSCORES.read_text()).get(game, 0))
    except (OSError, ValueError, AttributeError):
        return 0


def save_highscore(game: str, score: int) -> None:
    try:
        try:
            data = json.loads(HIGHSCORES.read_text())
        except (OSError, ValueError):
            data = {}
        data[game] = score
        HIGHSCORES.parent.mkdir(parents=True, exist_ok=True)
        HIGHSCORES.write_text(json.dumps(data))
    except OSError as e:                           # read-only home: the in-memory score still works
        log.debug("could not save high score: %s", e)


def swipe_direction(dx, dy, minimum=SWIPE_MIN):
    """'up' | 'down' | 'left' | 'right' for a drag of (dx, dy) pixels, or None when it is too short."""
    if max(abs(dx), abs(dy)) < minimum:
        return None
    if abs(dx) >= abs(dy):
        return "right" if dx > 0 else "left"
    return "down" if dy > 0 else "up"


# --- game rules (pure) ----------------------------------------------------------------------------
class Game2048:
    """The classic 2048 rules on a ``size`` x ``size`` board. ``board[row][col]`` holds 0 or a tile value."""

    def __init__(self, size=4, rng=None):
        self.size = size
        self.rng = rng or random.Random()
        self.reset()

    def reset(self):
        self.board = [[0] * self.size for _ in range(self.size)]
        self.score = 0
        self._spawn()
        self._spawn()

    def _spawn(self):
        empty = [(r, c) for r in range(self.size) for c in range(self.size) if not self.board[r][c]]
        if empty:
            r, c = self.rng.choice(empty)
            self.board[r][c] = 4 if self.rng.random() < 0.1 else 2

    @staticmethod
    def _slide(line):
        """Slide one row to the left, merging equal neighbours once. Returns (new line, points scored)."""
        tiles = [v for v in line if v]
        out, points, i = [], 0, 0
        while i < len(tiles):
            if i + 1 < len(tiles) and tiles[i] == tiles[i + 1]:
                out.append(tiles[i] * 2)
                points += tiles[i] * 2
                i += 2
            else:
                out.append(tiles[i])
                i += 1
        return out + [0] * (len(line) - len(out)), points

    def _apply(self, direction):
        """The board after a move (without spawning) and its points; the live board is untouched."""
        b = [row[:] for row in self.board]
        if direction in ("up", "down"):
            b = [list(col) for col in zip(*b)]
        if direction in ("right", "down"):
            b = [row[::-1] for row in b]
        points = 0
        for i, row in enumerate(b):
            b[i], p = self._slide(row)
            points += p
        if direction in ("right", "down"):
            b = [row[::-1] for row in b]
        if direction in ("up", "down"):
            b = [list(row) for row in zip(*b)]
        return b, points

    def move(self, direction) -> bool:
        """Slide the board. A move that changes nothing is ignored (no spawn). Returns True if it moved."""
        b, points = self._apply(direction)
        if b == self.board:
            return False
        self.board, self.score = b, self.score + points
        self._spawn()
        return True

    @property
    def game_over(self):
        return not any(0 in row for row in self.board) and all(self._apply(d)[0] == self.board for d in DIRS)

    @property
    def best_tile(self):
        return max(max(row) for row in self.board)


class SnakeGame:
    """Snake on a ``cols`` x ``rows`` grid; hitting a wall or itself ends the game. ``snake[0]`` is the head."""

    def __init__(self, cols=24, rows=14, rng=None):
        self.cols, self.rows = cols, rows
        self.rng = rng or random.Random()
        self.reset()

    def reset(self):
        cx, cy = self.cols // 2, self.rows // 2
        self.snake = [(cx, cy), (cx - 1, cy), (cx - 2, cy)]
        self.direction = "right"
        self._queued = None                         # a turn made before the next step
        self.score = 0
        self.alive = True
        self.food = None
        self._place_food()

    def _place_food(self):
        free = [(x, y) for x in range(self.cols) for y in range(self.rows) if (x, y) not in self.snake]
        self.food = self.rng.choice(free) if free else None

    def turn(self, direction):
        """Queue a new heading. Reversing straight into the neck is ignored."""
        dx, dy = DIRS[direction]
        cur = DIRS[self.direction]
        if (dx, dy) != (-cur[0], -cur[1]):
            self._queued = direction

    def step(self) -> bool:
        """Advance one cell. Returns False (and ends the game) on a wall or self collision."""
        if not self.alive:
            return False
        if self._queued:
            self.direction, self._queued = self._queued, None
        dx, dy = DIRS[self.direction]
        head = (self.snake[0][0] + dx, self.snake[0][1] + dy)
        grows = head == self.food
        body = self.snake if grows else self.snake[:-1]   # the tail cell is free unless we just ate
        if not (0 <= head[0] < self.cols and 0 <= head[1] < self.rows) or head in body:
            self.alive = False
            return False
        self.snake.insert(0, head)
        if grows:
            self.score += 1
            self._place_food()
            if self.food is None:                   # the board is full: nothing left to eat
                self.alive = False
        else:
            self.snake.pop()
        return True


# --- widgets --------------------------------------------------------------------------------------
class SwipeGame(Widget):
    """Shared plumbing: swipe detection, fullscreen gating, high score and the header line."""
    interval = 3600.0
    wants_touch = True
    game_key = ""
    _full = False
    _start = None
    _fired = False

    def __init__(self, cfg, ctx):
        super().__init__(cfg, ctx)
        self.best = load_highscore(self.game_key)

    def _now(self):
        return time.time()

    def _record(self, score):
        if score > self.best:
            self.best = score
            save_highscore(self.game_key, score)

    # subclasses implement
    def on_swipe(self, direction): ...
    def on_game_tap(self): ...

    def on_touch(self, x, y, down):
        if down and self._start is None:
            self._start, self._fired = (x, y), False
        elif down and not self._fired:                # act as soon as the drag is long enough
            d = swipe_direction(x - self._start[0], y - self._start[1])
            if d and self._full:
                self._fired = True
                self.on_swipe(d)
        if not down and self._start is not None:
            if not self._fired and swipe_direction(x - self._start[0], y - self._start[1]) is None:   # a tap
                if self._full:
                    self.on_game_tap()
                else:                                 # small view: a tap opens fullscreen (or the configured action)
                    run = self.ctx.get("run_action")
                    if run:
                        run(self.cfg.get("on_tap", "fullscreen"), self)
            self._start = None

    def draw(self, cv, box):
        self._full = False
        self._paint(cv, box, compact=True)

    def draw_full(self, cv, box):
        self._full = True
        self._paint(cv, box, compact=False)

    def _paint(self, cv, box, compact):
        raise NotImplementedError

    def header(self, cv, box, title, score):
        x0, y0, _, _ = box
        cv.text((x0 + 14, y0 + 8), f"{title}  {score}", size=22, bold=True)
        cv.text((x0 + 14, y0 + 36), f"best {self.best}", size=16, color=THEME["dim"])

    def banner(self, cv, rect, lines):
        """Dim the play area and print centred lines (text, size, color)."""
        x0, y0, x1, y1 = rect
        cv.d.rounded_rectangle(rect, radius=10, fill=(0, 0, 0))
        cy = (y0 + y1) // 2 - 14 * (len(lines) - 1)
        for text, size, color in lines:
            cv.text(((x0 + x1) // 2, cy), text, size=size, color=color, anchor="mm", max_width=x1 - x0 - 12)
            cy += size + 8


def _ordered(x0, y0, x1, y1):
    """A rectangle PIL accepts even when padding made it inside-out (tiny widgets)."""
    return x0, y0, max(x0, x1), max(y0, y1)


def _fit(box, header, aspect):
    """The largest centred rect of width/height ``aspect`` below a header strip of ``header`` px."""
    x0, y0, x1, y1 = box
    w, h = max(1, x1 - x0), max(1, y1 - y0 - header)
    pw, ph = (w, w / aspect) if w / h < aspect else (h * aspect, h)
    pw, ph = max(1, int(pw)), max(1, int(ph))
    px = x0 + (w - pw) // 2
    py = y0 + header + (h - ph) // 2
    return px, py, px + pw, py + ph


@widget("snake")
class Snake(SwipeGame):
    """Snake: swipe to steer, tap to restart after game over. Runs only while fullscreen.
    Config: ``speed`` (seconds per move, default 0.2). Use ``on_tap: fullscreen`` to open it."""
    game_key = "snake"
    fields = [{"name": "speed", "type": "float", "default": 0.2, "help": "seconds per move"}]
    COLS, ROWS = 24, 14

    def __init__(self, cfg, ctx):
        super().__init__(cfg, ctx)
        self.game = SnakeGame(self.COLS, self.ROWS)
        self.started = False                         # waits for the first swipe
        self._last_tick = 0.0

    @property
    def fps(self):
        return 8 if self._full and self.started and self.game.alive else None

    def on_swipe(self, direction):
        if not self.game.alive:
            return
        self.game.turn(direction)
        if not self.started:
            self.started, self._last_tick = True, self._now()

    def on_game_tap(self):
        if not self.game.alive:
            self.game.reset()
            self.started = False

    def tick(self):
        """Advance by the configured speed; call from draw. Paused unless fullscreen and started."""
        now = self._now()
        speed = max(0.05, float(self.cfg.get("speed", 0.2)))
        if not (self._full and self.started and self.game.alive):
            return
        if now - self._last_tick > 3 * speed:          # resumed after a pause: don't fast-forward
            self._last_tick = now
        elif now - self._last_tick >= speed:
            self._last_tick += speed
            self.game.step()
            self._record(self.game.score)

    def _paint(self, cv, box, compact):
        self.tick()
        g = self.game
        head = 30 if compact else 62
        x0, y0, x1, y1 = box
        cv.d.rounded_rectangle(box, radius=14, fill=THEME["card"])
        if compact:
            cv.text((x0 + 12, y0 + 6), f"snake {g.score}", size=18, bold=True, max_width=x1 - x0 - 24)
        else:
            self.header(cv, box, "snake", g.score)
        bx0, by0, bx1, by1 = _fit((x0 + 6, y0, x1 - 6, y1 - 6), head, self.COLS / self.ROWS)
        cell = (bx1 - bx0) / self.COLS
        cv.d.rounded_rectangle((bx0, by0, bx1, by1), radius=6, fill=THEME["bg"])

        def rect(p, pad=0.0):
            return _ordered(bx0 + p[0] * cell + pad, by0 + p[1] * cell + pad, bx0 + (p[0] + 1) * cell - pad - 1,
                            by0 + (p[1] + 1) * cell - pad - 1)
        if g.food:
            cv.d.ellipse(rect(g.food, cell * 0.12), fill=THEME["crit"])
        for i, p in enumerate(g.snake):
            cv.d.rectangle(rect(p, cell * 0.05), fill=THEME["accent"] if i == 0 else THEME["ok"])
        if compact:
            pass                                       # the small view is a paused preview
        elif not g.alive:
            self.banner(cv, (bx0 + (bx1 - bx0) // 6, by0 + (by1 - by0) // 3, bx1 - (bx1 - bx0) // 6, by1 - (by1 - by0) // 3),
                        [("game over", 30, THEME["crit"]), ("tap to restart", 18, THEME["dim"])])
        elif not self.started:
            self.banner(cv, (bx0 + (bx1 - bx0) // 5, by0 + (by1 - by0) // 3, bx1 - (bx1 - bx0) // 5, by1 - (by1 - by0) // 3),
                        [("swipe to start", 24, THEME["text"])])


def _mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def tile_color(value):
    """Fill color for a 2048 tile: from the card color towards the accent as the value grows, then warn."""
    if value == 0:
        return _mix(THEME["card"], THEME["bg"], 0.5)
    level = min(11, int(math.log2(value)))             # 2 -> 1 ... 2048 -> 11
    if level >= 11:
        return THEME["warn"]
    return _mix(THEME["card"], THEME["accent"], 0.15 + 0.85 * (level - 1) / 9)


def _text_on(fill):
    lum = 0.299 * fill[0] + 0.587 * fill[1] + 0.114 * fill[2]
    return THEME["bg"] if lum > 150 else THEME["text"]


@widget("game_2048")
class Game2048Widget(SwipeGame):
    """2048: swipe to slide the tiles, tap to restart after game over. Use ``on_tap: fullscreen`` to open it."""
    game_key = "2048"
    fields = []

    def __init__(self, cfg, ctx):
        super().__init__(cfg, ctx)
        self.game = Game2048(4)

    def on_swipe(self, direction):
        if not self.game.game_over and self.game.move(direction):
            self._record(self.game.score)

    def on_game_tap(self):
        if self.game.game_over:
            self.game.reset()

    def _paint(self, cv, box, compact):
        g = self.game
        x0, y0, x1, y1 = box
        cv.d.rounded_rectangle(box, radius=14, fill=THEME["card"])
        if compact:
            cv.text((x0 + 12, y0 + 6), f"2048  {g.score}", size=18, bold=True, max_width=x1 - x0 - 24)
        else:
            self.header(cv, box, "2048", g.score)
        bx0, by0, bx1, by1 = _fit((x0 + 6, y0, x1 - 6, y1 - 6), 30 if compact else 62, 1.0)
        n = g.size
        gap = max(2, (bx1 - bx0) // 60)
        cell = max(1.0, (bx1 - bx0 - gap * (n + 1)) / n)
        cv.d.rounded_rectangle((bx0, by0, bx1, by1), radius=8, fill=THEME["bg"])
        for r in range(n):
            for c in range(n):
                v = g.board[r][c]
                rect = (bx0 + gap + c * (cell + gap), by0 + gap + r * (cell + gap),
                        bx0 + gap + c * (cell + gap) + cell, by0 + gap + r * (cell + gap) + cell)
                fill = tile_color(v)
                cv.d.rounded_rectangle(rect, radius=6, fill=fill)
                if v:
                    size = max(10, int(cell * (0.5 if v < 100 else 0.42 if v < 1000 else 0.34)))
                    cv.text(((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2), v, size=size, bold=True,
                            color=_text_on(fill), anchor="mm", max_width=cell - 4)
        if g.game_over and not compact:
            self.banner(cv, (bx0 + (bx1 - bx0) // 8, by0 + (by1 - by0) // 3, bx1 - (bx1 - bx0) // 8, by1 - (by1 - by0) // 3),
                        [("game over", 30, THEME["crit"]), ("tap to restart", 18, THEME["dim"])])
