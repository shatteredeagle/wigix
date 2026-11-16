# Media widgets: image, video, camera

Three widgets for pictures and moving pictures. Videos and streams need `ffmpeg` on the machine running wigix
(`apt install ffmpeg`, `pacman -S ffmpeg`, ...); without it the card says "ffmpeg missing" and everything else keeps working.
All three round their corners like cards (`rounded: false` turns that off) and take `fit`:

| fit | result |
|---|---|
| `cover` (default) | fills the widget, cropping the overflow |
| `contain` | whole picture visible, bars in the card color |
| `stretch` | fills the widget, ignoring the aspect ratio |

## `image`

A static image or an animated GIF from a file or an http(s) URL.

```yaml
- {type: image, at: [0, 0, 2, 2], source: ~/Pictures/family.jpg, fit: cover}
- {type: image, at: [2, 0, 1, 1], source: https://example.com/status.png, refresh: 60, name: Radar}
- {type: image, at: [3, 0, 1, 1], source: ~/gifs/party.gif, fit: contain}
```

- `source`: path or URL. Files are reloaded when they change; URLs every `refresh` seconds (default 300).
- Downloads and decoding happen on a worker thread, so a slow server never freezes the dashboard. A failing URL keeps
  showing the last good picture.
- Animated GIFs advance by their own frame timing; the dashboard redraws at 10 fps while one is on screen.
- `name`: optional label drawn over the picture.

## `video`

A video file or live stream, decoded by ffmpeg while its page is visible (no sound).

```yaml
- {type: video, at: [0, 0, 2, 2], source: ~/Videos/loop.mp4, loop: true}
- {type: video, at: [2, 0, 2, 1], source: "rtsp://user:pass@cam.lan/stream1", fps: 10, fit: contain}
```

- `fps`: frames per second to render, default 12, at most 15 (the USB link tops out around 17).
- `loop`: restart a file when it ends (default true). A non-looping file stops on its last frame.
- Network sources (`rtsp://`, `http://` MJPEG or HLS, ...) are treated as live streams: if the stream drops, ffmpeg is
  restarted with a growing delay (1 s up to 30 s) and a "reconnecting" badge shows meanwhile.
- ffmpeg is killed a few seconds after the page is hidden and restarted when it comes back. Tapping to fullscreen
  (`on_tap: fullscreen`) re-scales the stream to the full screen.
- The source is a local path or a URL starting with `http://`, `https://`, `rtsp://`, `rtmp://`, `rtp://`, `udp://`
  or `tcp://`. Anything else (`concat:`, `subfile:`, `data:`, a name starting with `-`) shows "source not allowed",
  and ffmpeg runs with a protocol whitelist (no `pipe`).
- Keep credentials out of the config if you share it: put the URL in a file you control and use a placeholder here.

## `camera`

A security or pet camera. Configure exactly one source:

| option | source |
|---|---|
| `snapshot_url` | a JPEG still, polled every `refresh` seconds (default 2) |
| `stream_url` | an RTSP or MJPEG stream via ffmpeg (about 10 fps, 15 in fullscreen) |
| `ha_camera` | a Home Assistant `camera.*` entity |
| `frigate` + `camera` | a [Frigate](https://frigate.video) NVR camera |

The camera name and a green `LIVE` badge are drawn over the picture; the badge turns into a yellow `stale` when the
newest frame is older than 3x `refresh` (3 seconds for streams). A tap opens the camera fullscreen unless the widget has
its own `on_tap`. Snapshot polling pauses while the page has not been shown for a while.

```yaml
# Plain snapshot URL, with an optional token read from a 0600 file
- {type: camera, at: [0, 0, 2, 1], name: Garage, snapshot_url: "http://cam.lan/snapshot.jpg",
   token_file: ~/.config/wigix/cam_token, refresh: 2}

# RTSP stream
- {type: camera, at: [2, 0, 2, 1], name: Porch, stream_url: "rtsp://cam.lan:554/stream1"}
```

### Frigate

```yaml
- type: camera
  at: [0, 1, 2, 2]
  frigate: true                       # or: frigate: http://frigate.lan:5000  (then `url` is not needed)
  url: http://frigate.lan:5000
  camera: front_door                  # the camera name in Frigate's config
  token_file: ~/.config/wigix/frigate_token   # only if you put Frigate behind an authenticating proxy
```

Frames come from `<url>/api/<camera>/latest.jpg`. The widget also reads `<url>/api/events?camera=<camera>&limit=1`; while
the newest event is less than 60 seconds old its label (for example `person`) shows as a red tag.

### Home Assistant

```yaml
integrations:
  ha: {type: home_assistant, url: http://homeassistant.lan:8123, token_file: ~/.config/wigix/ha_token}

# in a page:
- {type: camera, at: [0, 0, 2, 2], ha_camera: camera.front_door, name: Front door, refresh: 3}
```

The picture is fetched with the integration's `camera_image()` (HA's `/api/camera_proxy/<entity>`), so the same token
and TLS settings apply. Add `integration: ha` if you have more than one Home Assistant.

Other options: `headers` (extra HTTP headers for snapshots), `verify` (`false` or a CA file path for self-signed TLS), `token` / `token_file`
(sent as `Authorization: Bearer ...`).
