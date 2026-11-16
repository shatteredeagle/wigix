# Bambu Lab printer and audio visualizer

## Bambu Lab 3D printer (`bambu` integration, `printer` widget)

Talks to the printer's own MQTT broker on the local network, so no cloud account is involved. Works with P1S, P1P,
X1 and A1 printers in LAN mode.

```yaml
integrations:
  printer:
    type: bambu
    host: printer.lan                              # the printer's hostname or IP
    serial: 00M00A000000000
    access_code_file: ~/.config/wigix/bambu_code   # or `access_code: 12345678` / $BAMBU_ACCESS_CODE
    # port: 8883                                   # default

pages:
  - widgets:
      - {type: printer, at: [0, 0, 1, 1], name: P1S}
```

### Finding the serial and access code

- **Access code**: on the printer's screen open Settings, then WLAN (some models: Network). The 8-character
  "Access Code" is listed there. It changes if you regenerate it or reset the network settings.
- **Serial number**: Settings, then Device (or the label on the printer); it is also shown in Bambu Studio's device
  tab. It is 15 characters and is part of the MQTT topic, so it must be exact.
- **LAN mode**: the local broker only answers when LAN Mode is enabled (Settings, WLAN, "LAN Mode Only"). Newer
  firmware also offers "Developer Mode" alongside it; if the connection is refused, enable that too. The
  printer must be reachable from the dashboard host on TCP port 8883.

Keep the access code in a 0600 file (`chmod 600 ~/.config/wigix/bambu_code`) rather than inline in the YAML.

### What the widget shows

State (printing green, paused amber, failed red, idle dim, finished blue), the job name, progress percentage and bar,
time remaining (`1h 12m`), layer `x/y`, and nozzle and bed temperatures as `current/target` (amber while still
heating). A non-zero `print_error` appears as a code such as `error 0300_4001`. When the printer is unreachable the
card says "offline". Tapping with `on_tap: fullscreen` adds the chamber temperature on printers that report one.

The printer sends only changes after its first report, so the integration merges every message into one state and asks
for a full report on connect and every 60 seconds.

## Audio visualizer (`audio_visualizer` widget)

Shows a live spectrum of whatever the computer is playing (Linux, PipeWire or PulseAudio).

```yaml
- type: audio_visualizer
  at: [0, 0, 2, 1]
  bars: 32            # number of frequency bars (default 32)
  style: bars         # bars | wave (mirrored envelope) | line
  # device: alsa_output.pci-0000_00_1f.3.analog-stereo.monitor   # optional source override
  # name: Audio       # optional card title
```

### Which audio source is used

By default the **monitor of the default output** (what you hear, not the microphone): `parec` is started with
`--device=@DEFAULT_MONITOR@`. If `parec` (package `pulseaudio-utils` / `libpulse`) is not installed, `pw-record`
(PipeWire) is used with `stream.capture.sink=true`, which records the default sink's monitor. If neither exists the
card says "parec missing".

Set `device:` to capture something else: a PulseAudio source name (`pactl list short sources`, monitors end in
`.monitor`) for `parec`, or a node name or id (`pw-cli ls Node`) for `pw-record`.

Audio is read as mono 16-bit 44.1 kHz, run through a Hann-windowed FFT and folded into log-spaced bars from 40 Hz to
16 kHz with fast attack and slow decay. The widget redraws at 15 fps while visible. The capture process runs only
while the widget is being drawn: a few seconds after it leaves the screen the process is stopped, and the next draw
starts it again.
