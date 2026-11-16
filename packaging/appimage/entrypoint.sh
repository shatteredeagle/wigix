#! /bin/bash
# -I: ignore PYTHON* variables and the user's site-packages, so the host Python cannot leak into the bundle.
exec "{{ python-executable }}" -I "${APPDIR}/wigix-appimage/launcher.py" "$@"
