# Contributing

Thanks for helping. The easiest wins:

- **A widget or integration** for something you run. Read [docs/writing-plugins.md](docs/writing-plugins.md): one Python
  file plus tests. `wigix preview` renders without hardware, and `tests/fake_http.py` fakes services.
- **Protocol findings** (partial updates, other firmware versions, the update mode): add to [docs/protocol.md](docs/protocol.md)
  with how you observed it. Please keep to what you saw on the wire; don't paste vendor code or firmware.
- **Bug reports**: include `wigix -v run` output, your firmware (`wigix.WigiDash().info()`), and whether a
  replug fixes it.

## Development

```sh
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
```

Tests must not touch real services or hardware. New widgets should render without exceptions when their integration is
missing or offline (see existing tests for the pattern).

## Ground rules

- Clean-room: no decompiled vendor code, no vendor binaries or firmware in the repo or in PRs.
- No secrets or personal hostnames in code, docs, tests or screenshots. Use `example.com` / `.lan` placeholders.
- Keep widgets' `draw()` fast and free of I/O; do network work in an integration's `poll()`.
