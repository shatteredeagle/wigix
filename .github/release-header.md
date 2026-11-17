## Download and run

**Easiest, no Python needed: the AppImage** (64-bit Linux, about 136 MB)

```sh
curl -L -o ~/wigix.AppImage https://github.com/shatteredeagle/wigix/releases/download/vVERSION/wigix-VERSION-x86_64.AppImage
chmod +x ~/wigix.AppImage
~/wigix.AppImage init --udev      # one-time: prints the udev rule and the commands that let your user open the panel
~/wigix.AppImage init             # writes a starter config
~/wigix.AppImage run              # then `~/wigix.AppImage url` prints the editor link
```

The newest release is always at `https://github.com/shatteredeagle/wigix/releases/latest/download/wigix-x86_64.AppImage`.
If it won't start, install FUSE 2 (`libfuse2`) or add `--appimage-extract-and-run`. Details: [docs/appimage.md](https://github.com/shatteredeagle/wigix/blob/vVERSION/docs/appimage.md).

**With Python:** `pipx install https://github.com/shatteredeagle/wigix/releases/download/vVERSION/wigix-VERSION-py3-none-any.whl`,
then `wigix init --udev`, `wigix init`, `wigix run`. Arch, Docker and systemd autostart: [docs/install.md](https://github.com/shatteredeagle/wigix/blob/vVERSION/docs/install.md).

**Check your download:** `sha256sum --check --ignore-missing SHA256SUMS` (it should print `OK` for each file you have).

---

