# Touch widgets

Widgets built for the touch panel: a launcher pad, a sketch pad and two swipe games. They are part of the normal
widget set, so they work in the web editor and in any grid cell.

## Fullscreen

`draw_pad`, `snake` and `game_2048` read raw touch (so strokes and swipes are not mistaken for page swipes). Put
`on_tap: fullscreen` on them: a short tap on the small widget opens it fullscreen, where the game runs and the whole
panel is the playing field. A close button appears in the top-right corner (the games and the pad ignore that
corner). Games are paused while they are not fullscreen. `macro_pad` uses the ordinary tap handling, so its buttons
can open fullscreen widgets themselves.

## macro_pad

A Stream-Deck-style grid of launcher buttons in one widget; a whole page can be a pad.

```yaml
- type: macro_pad
  at: [0, 0, 4, 3]
  columns: 4
  buttons:
    - {label: Mute, color: "#7a2a2a", on_tap: {key: "ctrl+shift+m"}}
    - {label: Docs, icon: ~/icons/docs.png, on_tap: {url: "https://example.org"}}
    - {label: Notes, on_tap: {command: "kate ~/notes.md"}}
    - label: Lamp
      on_tap: {ha_service: light.toggle, entity: light.desk}
      toggle: {ha_entity: light.desk}        # outline and dot show the on/off state from Home Assistant
    - {label: Draw, on_tap: [{page: Sketch}, fullscreen]}
```

Each `on_tap` is any action (`next_page`, `fullscreen`, `{page: ...}`, `{command: ...}`, `{key: ...}`, `{type: ...}`,
`{url: ...}`, `{ha_service: ...}`) or a list of them. `command`, `key` and `type` actions are ignored unless the
config has `security: {allow_commands: true}`. `toggle` is optional and ignored when no Home Assistant integration
is connected. A pressed button lights up for a quarter of a second.

## draw_pad

```yaml
- {type: draw_pad, at: [0, 0, 2, 2], on_tap: fullscreen, save_dir: ~/Pictures/wigix}
```

Drag to draw. The toolbar at the bottom has eight colors, three brush sizes, an eraser, clear and save. Save writes
`wigix-YYYYmmdd-HHMMSS.png` into `save_dir` (default `~/Pictures/wigix`) and shows "saved" for 1.5 s. The drawing is
kept when the widget goes fullscreen (it is rescaled to the new size).

## snake

```yaml
- {type: snake, at: [2, 0, 2, 2], on_tap: fullscreen, speed: 0.2}
```

Open it fullscreen, swipe (at least 40 px) to start and steer; hitting a wall or yourself ends the game and a tap
restarts. `speed` is seconds per move.

## game_2048

```yaml
- {type: game_2048, at: [2, 2, 1, 1], on_tap: fullscreen}
```

Swipe to slide the tiles; equal tiles merge once per move and a new 2 or 4 appears after every move that changes
the board. Tile colors are derived from the theme (card to accent, gold at 2048). Tap to restart after game over.

High scores are kept in memory and saved to `~/.local/state/wigix/highscores.json` when that is writable.
