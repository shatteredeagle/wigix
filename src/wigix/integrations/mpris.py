# SPDX-License-Identifier: GPL-3.0-only
"""Media player status and control through ``playerctl`` (any MPRIS player on Linux: Spotify, browsers, mpv, ...).

    integrations:
      media:
        type: mpris
        # player: spotify          # optional: only this player (see `playerctl -l`); default: the active one

Needs the ``playerctl`` package. ``data``: ``{status, artist, title, album, art_url, position, length, player}``
with ``position``/``length`` in seconds and ``status`` one of playing/paused/stopped (stopped also when no
player is running). The cover art, once fetched, is the PIL image ``art_image`` (fetched on the poll thread, once
per track, so drawing never does I/O).
"""
import io
import pathlib
import shutil
import stat
import subprocess
import urllib.parse

import requests
from PIL import Image

from . import integration
from .net import fetch_limited
from .polling import PollingIntegration

SEP = "\x1f"
FIELDS = ("status", "artist", "title", "album", "mpris:artUrl", "position", "mpris:length", "playerName")
FORMAT = SEP.join("{{" + f + "}}" for f in FIELDS)
EMPTY = {"status": "stopped", "artist": "", "title": "", "album": "", "art_url": "", "position": 0.0, "length": 0.0, "player": ""}
MAX_ART_BYTES = 8 * 2**20


def _seconds(microseconds: str) -> float:
    try:
        return int(microseconds) / 1e6
    except ValueError:
        return 0.0


def _read_art_file(path: pathlib.Path) -> bytes:
    """Read a cover file, refusing anything that is not a regular file of at most MAX_ART_BYTES (/dev/zero, a FIFO ...)."""
    st = path.stat()
    if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_ART_BYTES:
        raise ValueError("cover art is not a regular file under the size limit")
    with path.open("rb") as f:
        raw = f.read(MAX_ART_BYTES + 1)                 # the file may have grown since stat()
    if len(raw) > MAX_ART_BYTES:
        raise ValueError("cover art too large")
    return raw


@integration("mpris")
class Mpris(PollingIntegration):
    interval = 1.0

    def __init__(self, name, cfg):
        super().__init__(name, cfg)
        self.art_image: Image.Image | None = None
        self._art_for: str | None = None

    def _playerctl(self, *args, check=True) -> subprocess.CompletedProcess:
        exe = shutil.which("playerctl")
        if exe is None:
            raise RuntimeError("playerctl not found: install the playerctl package")
        cmd = [exe] + (["-p", self.cfg["player"]] if self.cfg.get("player") else []) + list(args)
        return subprocess.run(cmd, capture_output=True, text=True, timeout=4, check=check)

    def poll(self):
        p = self._playerctl("metadata", "--format", FORMAT, check=False)
        if p.returncode != 0:
            err = (p.stderr or "").strip()
            if "No players found" in err or "No player could handle" in err or not err:
                self.art_image, self._art_for = None, None
                return dict(EMPTY)                        # nothing is running: a normal state, not an error
            raise RuntimeError(err.splitlines()[0])
        parts = (p.stdout.rstrip("\n").split(SEP) + [""] * len(FIELDS))[:len(FIELDS)]
        status, artist, title, album, art_url, position, length, player = parts
        data = {"status": status.lower() or "stopped", "artist": artist, "title": title, "album": album,
                "art_url": art_url, "position": _seconds(position), "length": _seconds(length), "player": player}
        self._update_art(art_url)
        return data

    def _update_art(self, url: str):
        """Load the cover once per distinct URL (a failed fetch is not retried until the track changes)."""
        if url == self._art_for:
            return
        self._art_for, self.art_image = url, None
        try:
            if url.startswith("file://"):
                raw = _read_art_file(pathlib.Path(urllib.parse.unquote(urllib.parse.urlparse(url).path)))
            elif url.startswith(("http://", "https://")):
                raw = fetch_limited(url, MAX_ART_BYTES, timeout=6)
            else:
                return
            im = Image.open(io.BytesIO(raw)).convert("RGB")
            im.thumbnail((256, 256))
            self.art_image = im
        except (OSError, requests.RequestException, ValueError, Image.DecompressionBombError):
            self.art_image = None

    # -- controls (called from the touch thread) -------------------------------------------------------
    def _control(self, command: str):
        self._playerctl(command, check=False)
        self.refresh()

    def play_pause(self):
        self._control("play-pause")

    def next(self):
        self._control("next")

    def previous(self):
        self._control("previous")
