# AppImage

A single file that runs on most Linux distributions without installing Python or libusb. It behaves like the
installed `wigix` command: every subcommand (`run`, `preview`, `editor`, `doctor`, `init`, `devices`, `url`,
`autostart`, `tray`) works the same. The libusb library and the tray's PyQt6 are bundled.

## Download and run

Grab `wigix-<version>-x86_64.AppImage` from the [releases page](https://github.com/shatteredeagle/wigix/releases),
then:

```sh
mkdir -p ~/Apps && mv ~/Downloads/wigix-*-x86_64.AppImage ~/Apps/wigix.AppImage
chmod +x ~/Apps/wigix.AppImage
~/Apps/wigix.AppImage --version
~/Apps/wigix.AppImage init
~/Apps/wigix.AppImage doctor
```

The file is about 136 MB (Python, Pillow, numpy and PyQt6 included). It needs a 64-bit Intel/AMD Linux with a glibc
as old as Ubuntu 22.04's (2.35) or newer.

**Verify the download.** Each release also carries `wigix-<version>-x86_64.AppImage.sha256`; put it next to the
AppImage and run `sha256sum --check wigix-<version>-x86_64.AppImage.sha256`. It should print `OK`.

## FUSE

AppImages mount themselves with FUSE 2. Ubuntu 22.04 and newer no longer ship it by default: `sudo apt install
libfuse2` (Ubuntu 24.04: `libfuse2t64`). Without FUSE, run any command with `--appimage-extract-and-run`, which
unpacks to a temporary directory first (slower start-up):

```sh
~/Apps/wigix.AppImage --appimage-extract-and-run doctor
```

## The udev rule (one-time root step)

Your user needs permission to open the panel, and an AppImage cannot install system files. Run it once and the rule
stays for every future version:

```sh
~/Apps/wigix.AppImage init --udev       # prints the rule and the exact commands; run them with root rights
```

`wigix doctor` inside the AppImage repeats the same commands in its "AppImage" line.

## Start at login

```sh
~/Apps/wigix.AppImage autostart enable --boot
```

The systemd unit and the tray's autostart entry written by this command point at the `.AppImage` file itself (read
from the `APPIMAGE` variable the runtime sets), not at its temporary mount, so they keep working after a reboot.
Do not move the file afterwards; if you do, run `autostart enable` again from the new place.

## Updating

Download the new release and replace the file in place under the same name (`mv new.AppImage ~/Apps/wigix.AppImage`),
then restart the daemon: `systemctl --user restart wigix`. The autostart entries keep working because the path did
not change.

## The tray

`wigix tray` works from the AppImage: PyQt6 is bundled. Qt itself still needs a few libraries from your system that
any desktop has: OpenGL (`libGL`), `libxkbcommon`, `fontconfig`, and on X11 `libxcb-cursor0` (Ubuntu/Debian
`sudo apt install libxcb-cursor0`). On GNOME the tray icon needs the AppIndicator extension, as for any tray app.

## Building it yourself

```sh
packaging/appimage/build.sh        # writes dist-appimage/wigix-<version>-x86_64.AppImage and a .sha256 file
```

It needs Python 3.10+, network access (python-appimage downloads its Python 3.12 base image) and the libusb-1.0
library on the build machine. Build on an old distribution (the release workflow uses Ubuntu 22.04) so the result
runs on old ones too.
