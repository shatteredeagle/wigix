#!/usr/bin/env bash
# Build wigix-<version>-x86_64.AppImage into dist-appimage/ (run from anywhere).
#
# The image includes the `tray` extra (PyQt6), so `wigix tray` works out of the box (about 136 MB).
#
# Needs: python3 (3.10+) with venv and pip, curl access to github.com (python-appimage downloads its base image),
# and the libusb-1.0 shared library on this machine (copied into the AppImage so users need not install it).
# For old-glibc compatibility build on an old distro (CI uses Ubuntu 22.04). The icon (wigix.png) is the 64 px tray
# icon scaled 4x with nearest neighbour: Image.open("src/wigix/tray_icons/wigix-connected-64.png").resize((256, 256), 0).
set -euo pipefail

PYTHON_VERSION=3.12
LINUX_TAG=manylinux2014_x86_64
PYTHON_APPIMAGE_VERSION=${PYTHON_APPIMAGE_VERSION:-}   # empty: latest

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$here/../.." && pwd)
out="$root/dist-appimage"
name=wigix

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# Build tooling lives in a throwaway venv (BUILD_VENV=<dir> reuses one) so nothing touches the project environment.
venv=${BUILD_VENV:-$work/venv}
if [[ ! -x "$venv/bin/python-appimage" ]]; then
    python3 -m venv "$venv"
    "$venv/bin/pip" install -q "python-appimage${PYTHON_APPIMAGE_VERSION:+==$PYTHON_APPIMAGE_VERSION}" build
fi

# The wheel carries the dependency list from pyproject.toml, so the bundled versions follow it.
# (built from a copy so setuptools leaves no build/ or egg-info directories in the checkout)
mkdir "$work/src"
cp -r "$root/pyproject.toml" "$root/README.md" "$root/LICENSE" "$root/src" "$work/src/"
"$venv/bin/python" -m build --wheel --outdir "$work/wheel" "$work/src" >/dev/null 2>&1
wheel=$(ls "$work/wheel"/wigix-*.whl)
version=$(basename "$wheel" | cut -d- -f2)

libusb=$(ldconfig -p | awk '/libusb-1\.0\.so\.0 / && /x86-64/ {print $NF}' | sed -n 1p)
[[ -n "$libusb" ]] || { echo "libusb-1.0.so.0 not found: install libusb (apt install libusb-1.0-0)" >&2; exit 1; }

recipe="$work/recipe"
payload="$work/wigix-appimage"
mkdir -p "$recipe" "$payload/lib"
cp "$here/entrypoint.sh" "$here/wigix.desktop" "$here/wigix.png" "$recipe/"
cp "$here/launcher.py" "$payload/"
cp -L "$libusb" "$payload/lib/libusb-1.0.so.0"
echo "$wheel[tray]" > "$recipe/requirements.txt"

mkdir -p "$out"
(cd "$work" && "$venv/bin/python-appimage" build app -p "$PYTHON_VERSION" -l "$LINUX_TAG" "$recipe" -x "$payload")
mv "$work/wigix-x86_64.AppImage" "$out/$name-$version-x86_64.AppImage"
(cd "$out" && sha256sum "$name-$version-x86_64.AppImage" > "$name-$version-x86_64.AppImage.sha256")
echo "built $out/$name-$version-x86_64.AppImage"
