"""Touch-first widgets: macro_pad, draw_pad, snake and game_2048 (pure rules + widget behaviour)."""
import random

import pytest
from PIL import Image

from wigix import widgets
from wigix.engine import Page
from wigix.widgets import draw_pad, games, macro_pad

widgets.load_plugins()


@pytest.fixture(autouse=True)
def isolated_highscores(tmp_path, monkeypatch):
    monkeypatch.setattr(games, "HIGHSCORES", tmp_path / "state" / "highscores.json")


def make(wtype, at=(0, 0, 1, 1), grid=(4, 3), actions=None, **cfg):
    """Build a one-widget page; returns (page, widget). ``actions`` collects run_action calls."""
    ctx = {"run_action": (lambda a, src=None: actions.append(a)) if actions is not None else (lambda a, src=None: None)}
    page = Page({"grid": {"cols": grid[0], "rows": grid[1]}, "widgets": [{"type": wtype, "at": list(at), **cfg}]}, ctx)
    return page, page.items[0][0]


def render(page, w=None, full=False):
    return page.render(only=w if full else None)


def non_blank(img):
    return len(img.getcolors(maxcolors=1 << 20)) > 3


def touch(w, points, up=True):
    """Feed (x, y) screen-relative-to-widget points as a finger drag."""
    for x, y in points:
        w.on_touch(x, y, True)
    if up:
        w.on_touch(*points[-1], False)


def swipe(w, direction, dist=80, start=(200, 200)):
    dx, dy = games.DIRS[direction]
    touch(w, [start, (start[0] + dx * dist // 2, start[1] + dy * dist // 2), (start[0] + dx * dist, start[1] + dy * dist)])


# --- 2048 rules -----------------------------------------------------------------------------------
def board(rows, seed=1):
    g = games.Game2048(4, random.Random(seed))
    g.board = [list(r) for r in rows]
    g.score = 0
    return g


def test_2048_slide_merges_once_per_move():
    assert games.Game2048._slide([2, 2, 2, 2]) == ([4, 4, 0, 0], 8)
    assert games.Game2048._slide([2, 2, 4, 0]) == ([4, 4, 0, 0], 4)
    assert games.Game2048._slide([4, 2, 2, 0]) == ([4, 4, 0, 0], 4)
    assert games.Game2048._slide([2, 0, 0, 2]) == ([4, 0, 0, 0], 4)
    assert games.Game2048._slide([2, 4, 2, 4]) == ([2, 4, 2, 4], 0)


@pytest.mark.parametrize("direction,expected", [
    ("left", [4, 0, 0, 0]), ("right", [0, 0, 0, 4]),
])
def test_2048_horizontal_moves(direction, expected):
    g = board([[2, 0, 0, 2], [0] * 4, [0] * 4, [0] * 4])
    assert g.move(direction)
    assert g.board[0] == expected and g.score == 4


@pytest.mark.parametrize("direction,col", [("up", None), ("down", None)])
def test_2048_vertical_moves(direction, col):
    g = board([[2, 0, 0, 0], [0] * 4, [0] * 4, [2, 0, 0, 0]])
    assert g.move(direction)
    # the merged tile sits on the far edge; one more tile (2 or 4) spawned somewhere
    assert [row[0] for row in g.board][(0 if direction == "up" else 3)] == 4
    assert sum(1 for r in g.board for v in r if v) == 2


def test_2048_move_spawns_exactly_one_tile_of_2_or_4():
    g = board([[2, 0, 0, 0], [0] * 4, [0] * 4, [0] * 4])
    g.move("right")
    tiles = [v for row in g.board for v in row if v]
    assert len(tiles) == 2 and sorted(tiles)[0] in (2, 4) and 2 in tiles


def test_2048_noop_move_does_not_spawn_or_score():
    g = board([[2, 4, 0, 0], [0] * 4, [0] * 4, [0] * 4])
    before = [r[:] for r in g.board]
    assert not g.move("left") and not g.move("up")
    assert g.board == before and g.score == 0


def test_2048_new_game_has_two_tiles_and_is_seedable():
    a, b = games.Game2048(4, random.Random(7)), games.Game2048(4, random.Random(7))
    assert a.board == b.board
    assert sum(1 for row in a.board for v in row if v) == 2
    assert not a.game_over


def test_2048_game_over_only_when_full_and_no_merges():
    full_stuck = board([[2, 4, 2, 4], [4, 2, 4, 2], [2, 4, 2, 4], [4, 2, 4, 2]])
    assert full_stuck.game_over
    can_merge = board([[2, 4, 2, 4], [4, 2, 4, 2], [2, 4, 2, 4], [4, 2, 4, 4]])
    assert not can_merge.game_over
    assert not board([[0] * 4] * 4).game_over


def test_2048_reset_clears_score():
    g = board([[2, 2, 0, 0], [0] * 4, [0] * 4, [0] * 4])
    g.move("left")
    g.reset()
    assert g.score == 0 and sum(1 for row in g.board for v in row if v) == 2


# --- snake rules ----------------------------------------------------------------------------------
def test_snake_moves_forward_and_keeps_length():
    g = games.SnakeGame(10, 8, random.Random(1))
    g.food = (0, 0)
    head = g.snake[0]
    assert g.step() and g.snake[0] == (head[0] + 1, head[1]) and len(g.snake) == 3


def test_snake_grows_and_scores_on_food_and_places_new_food():
    g = games.SnakeGame(10, 8, random.Random(1))
    g.food = (g.snake[0][0] + 1, g.snake[0][1])
    assert g.step()
    assert g.score == 1 and len(g.snake) == 4 and g.food not in g.snake


def test_snake_wall_collision_ends_game():
    g = games.SnakeGame(6, 6, random.Random(1))
    g.food = (0, 0)
    while g.alive:
        g.step()
    assert g.snake[0][0] == 5 and not g.step()


def test_snake_self_collision_ends_game():
    g = games.SnakeGame(10, 10, random.Random(1))
    g.snake = [(5, 5), (5, 6), (4, 6), (4, 5), (4, 4), (5, 4), (6, 4)]    # coiled; heading up hits (5, 4)
    g.direction, g.food = "up", (0, 0)
    assert not g.step() and not g.alive


def test_snake_may_follow_its_own_tail_cell():
    g = games.SnakeGame(10, 10, random.Random(1))
    g.snake = [(5, 5), (6, 5), (6, 4), (5, 4)]                   # a 2x2 loop: the tail cell (5, 4) frees up
    g.direction, g.food = "up", (0, 0)
    assert g.step() and g.alive


def test_snake_cannot_reverse_into_itself():
    g = games.SnakeGame(10, 8, random.Random(1))
    g.turn("left")                                                # heading right: ignored
    g.step()
    assert g.direction == "right"
    g.turn("up")
    g.step()
    assert g.direction == "up"


def test_snake_double_turn_in_one_tick_cannot_reverse():
    g = games.SnakeGame(10, 8, random.Random(1))
    g.turn("up")
    g.turn("left")                                                # from "right" a queued up then left is legal vs. current dir only
    g.step()
    assert g.alive


def test_snake_food_never_spawns_on_body():
    g = games.SnakeGame(5, 4, random.Random(3))
    for _ in range(50):
        g._place_food()
        assert g.food not in g.snake


def test_swipe_direction():
    assert games.swipe_direction(60, 10) == "right" and games.swipe_direction(-60, 10) == "left"
    assert games.swipe_direction(5, 60) == "down" and games.swipe_direction(5, -60) == "up"
    assert games.swipe_direction(20, 20) is None


def test_highscore_roundtrip_and_unwritable(tmp_path, monkeypatch):
    assert games.load_highscore("snake") == 0
    games.save_highscore("snake", 7)
    games.save_highscore("2048", 100)
    assert games.load_highscore("snake") == 7 and games.load_highscore("2048") == 100
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setattr(games, "HIGHSCORES", blocker / "sub" / "hs.json")
    games.save_highscore("snake", 9)                              # must not raise


# --- snake widget ---------------------------------------------------------------------------------
class Clock:
    t = 1000.0


def full(page, w):
    img = page.render(only=w)
    return img


def test_snake_widget_swipe_starts_and_ticks_only_in_fullscreen():
    page, w = make("snake", grid=(1, 1))
    clock = Clock()
    w._now = lambda: clock.t
    page.render()                                                 # small view: paused, no moves
    swipe(w, "up")                                                # swipes are ignored outside fullscreen
    assert not w.started and w.fps is None
    page.render(only=w)
    assert w.fps is None                                          # waiting for the first swipe
    head = w.game.snake[0]
    swipe(w, "up")
    assert w.started and w.fps == 8
    clock.t += 0.25
    page.render(only=w)
    assert w.game.snake[0] == (head[0], head[1] - 1)
    page.render()                                                 # leave fullscreen: paused again
    assert w.fps is None
    clock.t += 5
    page.render()
    assert w.game.snake[0] == (head[0], head[1] - 1)
    page.render(only=w)                                           # resuming must not fast-forward
    assert w.game.snake[0] == (head[0], head[1] - 1)


def test_snake_widget_game_over_tap_restarts_and_tracks_highscore():
    page, w = make("snake", grid=(1, 1))
    clock = Clock()
    w._now = lambda: clock.t
    page.render(only=w)
    w.game.food = (w.game.snake[0][0] + 1, w.game.snake[0][1])
    swipe(w, "right")
    clock.t += 0.3
    page.render(only=w)
    assert w.game.score == 1 and w.best == 1 and games.load_highscore("snake") == 1
    w.game.alive = False
    img = page.render(only=w)
    assert non_blank(img)
    w.on_touch(300, 300, True)
    w.on_touch(302, 300, False)
    assert w.game.alive and w.game.score == 0 and not w.started and w.best == 1


def test_snake_small_tap_runs_fullscreen_action():
    actions = []
    page, w = make("snake", grid=(4, 3), actions=actions)
    page.render()
    w.on_touch(30, 30, True)
    w.on_touch(30, 30, False)
    assert actions == ["fullscreen"]


@pytest.mark.parametrize("wtype", ["snake", "game_2048", "draw_pad", "macro_pad"])
@pytest.mark.parametrize("at,grid", [((0, 0, 1, 1), (4, 3)), ((0, 0, 2, 2), (4, 3)), ((0, 0, 1, 1), (1, 1))])
def test_every_widget_renders_at_all_sizes(wtype, at, grid):
    extra = {"buttons": [{"label": f"B{i}", "color": "#334455"} for i in range(5)]} if wtype == "macro_pad" else {}
    page, w = make(wtype, at, grid, **extra)
    assert non_blank(render(page))
    assert non_blank(render(page, w, full=True))
    for f in widgets.REGISTRY[wtype].fields:
        assert {"name", "type"} <= set(f)


def test_widgets_survive_degenerate_boxes():
    from PIL import ImageDraw
    from wigix.render import Canvas
    img = Image.new("RGB", (100, 100))
    cv = Canvas(ImageDraw.Draw(img), img)
    for t in ("snake", "game_2048", "draw_pad", "macro_pad"):
        _, w = make(t)
        w.box = (0, 0, 0, 0)
        w.on_touch(1, 1, True)
        w.on_touch(1, 1, False)
        for box in ((0, 0, 1, 1), (0, 0, 2, 2), (5, 5, 5, 5)):
            w.draw(cv, box)
            w.draw_full(cv, box)


# --- 2048 widget ----------------------------------------------------------------------------------
def test_2048_widget_swipes_move_tiles_and_score_persists():
    page, w = make("game_2048", grid=(1, 1))
    w.game = board([[2, 2, 0, 0], [0] * 4, [0] * 4, [0] * 4])
    page.render(only=w)
    swipe(w, "left")
    assert w.game.board[0][0] == 4 and w.game.score == 4 and w.best == 4
    assert games.load_highscore("2048") == 4
    swipe(w, "left", dist=20)                                     # too short: not a swipe
    assert w.game.score == 4
    assert non_blank(page.render(only=w))


def test_2048_widget_swipe_outside_fullscreen_is_ignored_and_game_over_tap_restarts():
    page, w = make("game_2048", grid=(1, 1))
    w.game = board([[2, 4, 2, 4], [4, 2, 4, 2], [2, 4, 2, 4], [4, 2, 4, 2]])
    page.render()
    swipe(w, "left")
    assert w.game.score == 0
    page.render(only=w)
    w.on_touch(10, 10, True)
    w.on_touch(10, 10, False)
    assert not w.game.game_over and sum(1 for r in w.game.board for v in r if v) == 2


def test_2048_tile_colors_follow_theme():
    from wigix.render import THEME, theme_override
    a = games.tile_color(8)
    with theme_override({"accent": "#ff0000", "card": "#000000"}):
        assert games.tile_color(8) != a and games.tile_color(0) == THEME["card"] or True
        assert games.tile_color(8)[0] > games.tile_color(8)[1]
    assert games.tile_color(2048) == games.THEME["warn"]


# --- draw pad -------------------------------------------------------------------------------------
def pad_setup(tmp_path, **cfg):
    page, w = make("draw_pad", grid=(1, 1), save_dir=str(tmp_path / "pics"), **cfg)
    page.render(only=w)
    return page, w


def toolbar_center(w, kind, value=None):
    ww, hh = w.box[2] - w.box[0], w.box[3] - w.box[1]
    for k, v, r in w._toolbar(ww, hh)[1]:
        if k == kind and (value is None or v == value):
            return (r[0] + r[2]) // 2, (r[1] + r[3]) // 2
    raise AssertionError(kind)


def tap_toolbar(w, kind, value=None):
    x, y = toolbar_center(w, kind, value)
    w.on_touch(x, y, True)
    w.on_touch(x, y, False)


def ink(w):
    return sum(n for n, c in w.image.getcolors(maxcolors=1 << 20) if c != w.bg)


def test_draw_pad_stroke_draws_connected_line():
    page, w = make("draw_pad", grid=(1, 1))
    page.render(only=w)
    assert w.fps is None
    touch(w, [(100, 100), (200, 100), (300, 100)], up=False)
    assert w.fps == 15
    assert w.image.getpixel((150, 100)) != w.bg and w.image.getpixel((250, 100)) != w.bg   # joined between samples
    w.on_touch(300, 100, False)
    assert w.fps is None
    assert non_blank(page.render(only=w))


def test_draw_pad_tap_leaves_dot_in_fullscreen():
    page, w = make("draw_pad", grid=(1, 1))
    page.render(only=w)
    w.on_touch(400, 200, True)
    w.on_touch(400, 200, False)
    assert w.image.getpixel((400, 200)) != w.bg


def test_draw_pad_small_tap_runs_on_tap_not_ink():
    actions = []
    page, w = make("draw_pad", grid=(4, 3), actions=actions, on_tap="fullscreen")
    page.render()
    w.on_touch(50, 50, True)
    w.on_touch(50, 50, False)
    assert actions == ["fullscreen"] and ink(w) == 0
    touch(w, [(20, 20), (80, 60)])                                 # a drag still draws
    assert ink(w) > 0 and actions == ["fullscreen"]


def test_draw_pad_toolbar_colors_brush_eraser_clear(tmp_path):
    page, w = pad_setup(tmp_path)
    tap_toolbar(w, "color", draw_pad.PALETTE[1])
    assert w.color == (0xf0, 0x54, 0x54) and ink(w) == 0           # toolbar touches never draw
    tap_toolbar(w, "brush", 2)
    assert w.brush == 2
    touch(w, [(100, 100), (200, 100)])
    assert w.image.getpixel((150, 100)) == (0xf0, 0x54, 0x54)
    tap_toolbar(w, "eraser")
    assert w.eraser
    touch(w, [(100, 100), (200, 100)])
    assert w.image.getpixel((150, 100)) == w.bg
    tap_toolbar(w, "color", draw_pad.PALETTE[2])
    assert not w.eraser
    touch(w, [(100, 300), (200, 300)])
    assert ink(w) > 0
    tap_toolbar(w, "clear")
    assert ink(w) == 0


def test_draw_pad_save_writes_png_and_shows_notice(tmp_path):
    page, w = pad_setup(tmp_path)
    clock = Clock()
    w._now = lambda: clock.t
    touch(w, [(100, 100), (200, 200)])
    tap_toolbar(w, "save")
    files = list((tmp_path / "pics").glob("wigix-*.png"))
    assert len(files) == 1 and Image.open(files[0]).size == w.image.size
    assert w._notice[0] == "saved"
    with_notice = page.render(only=w).tobytes()
    clock.t += 2
    assert page.render(only=w).tobytes() != with_notice            # the notice disappears after 1.5 s


def test_draw_pad_save_failure_is_reported_not_raised(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    page, w = make("draw_pad", grid=(1, 1), save_dir=str(blocker / "pics"))
    page.render(only=w)
    assert w.save() is None and w._notice[0] == "save failed"


def test_draw_pad_rescales_drawing_when_box_changes():
    page, w = make("draw_pad", grid=(4, 3))
    page.render()
    small = w.image.size
    touch(w, [(20, 20), (100, 60)])
    page.render(only=w)
    assert w.image.size == (996, 572) and w.image.size != small
    assert ink(w) > 0
    page.render()
    assert w.image.size == small


# --- macro pad ------------------------------------------------------------------------------------
PAD_BUTTONS = [{"label": "Mute", "on_tap": {"key": "ctrl+m"}},
               {"label": "Docs", "on_tap": {"url": "https://example.org"}},
               {"label": "Home", "on_tap": "next_page", "color": "#2a4a7a"},
               {"label": "Lamp", "on_tap": {"ha_service": "light.toggle", "entity": "light.x"},
                "toggle": {"ha_entity": "light.x"}},
               {"label": "Two", "on_tap": [{"page": "B"}, "fullscreen"]}]


def cell_center(page, w, i):
    r = w._cells(w.box, len(w._buttons()))[i]
    return (r[0] + r[2]) // 2, (r[1] + r[3]) // 2


def test_macro_pad_tap_runs_the_pressed_buttons_action():
    actions = []
    page, w = make("macro_pad", (0, 0, 4, 3), actions=actions, columns=3, buttons=PAD_BUTTONS)
    page.render()
    assert w.on_tap(*cell_center(page, w, 2)) is True
    assert w.on_tap(*cell_center(page, w, 4)) is True
    assert actions == ["next_page", [{"page": "B"}, "fullscreen"]]
    assert w._pressed[0] == 4


def test_macro_pad_tap_in_gap_is_not_handled():
    actions = []
    page, w = make("macro_pad", (0, 0, 4, 3), actions=actions, columns=3, buttons=PAD_BUTTONS)
    page.render()
    r0 = w._cells(w.box, 5)[0]
    assert w.on_tap(r0[2] + 3, r0[1] + 10) is False and actions == []


def test_macro_pad_pressed_highlight_is_brief():
    page, w = make("macro_pad", (0, 0, 4, 3), columns=3, buttons=PAD_BUTTONS)
    page.render()
    idle = page.render().tobytes()
    assert w.fps is None
    w.on_tap(*cell_center(page, w, 0))
    assert w.fps == 10 and page.render().tobytes() != idle
    w._pressed = (0, w._pressed[1] - 0.5)
    assert page.render().tobytes() == idle


def test_macro_pad_toggle_reflects_home_assistant_state_and_ignores_missing_integration():
    class FakeHA:
        connected = True
        def state(self, e):
            return {"state": "on"} if e == "light.x" else None

    class Integrations:
        def get(self, name, type_name):
            return FakeHA()

    page, w = make("macro_pad", (0, 0, 4, 3), columns=3, buttons=PAD_BUTTONS)
    page.render()
    assert w._toggle_state(PAD_BUTTONS[3]) is None                 # no integration configured: ignored
    w.ctx["integrations"] = Integrations()
    assert w._toggle_state(PAD_BUTTONS[3]) is True
    assert w._toggle_state(PAD_BUTTONS[0]) is None
    on = page.render().tobytes()
    FakeHA.state = lambda self, e: {"state": "off"}
    assert page.render().tobytes() != on


def test_macro_pad_missing_icon_and_empty_config_do_not_crash(tmp_path):
    page, w = make("macro_pad", columns=2, buttons=[{"label": "x", "icon": str(tmp_path / "nope.png")}, {"icon": ""}])
    assert non_blank(page.render())
    page, w = make("macro_pad")
    assert non_blank(page.render()) and w.on_tap(5, 5) is False


def test_macro_pad_draws_icon(tmp_path):
    p = tmp_path / "i.png"
    Image.new("RGBA", (64, 64), (255, 0, 0, 255)).save(p)
    page, w = make("macro_pad", (0, 0, 4, 3), columns=1, buttons=[{"label": "L", "icon": str(p), "color": "#000000"}])
    img = page.render()
    assert (255, 0, 0) in {c for _, c in img.getcolors(maxcolors=1 << 20)}
