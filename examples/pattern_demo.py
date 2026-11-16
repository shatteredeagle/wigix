# SPDX-License-Identifier: GPL-3.0-only
import subprocess, time
from PIL import Image, ImageDraw
import struct
from wigix import W, H, WigiDash, rgb565
im = Image.new("RGB", (W, H), "black"); dr = ImageDraw.Draw(im)
for i, c in enumerate(["red", "green", "blue", "white", "yellow"]):
    dr.rectangle([i*W//5, 0, (i+1)*W//5, 150], fill=c)
dr.rectangle([0, 150, 60, H], fill="white")      # left edge marker
dr.text((120, 300), "WIGIDASH ON LINUX", fill="white", font_size=90) if False else None
try:
    from PIL import ImageFont
    f = ImageFont.truetype("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", 90)
except Exception: f = None
dr.text((120, 300), "HELLO ORION", fill="cyan", font=f)
dr.polygon([(W-20, H-20), (W-220, H-20), (W-20, H-220)], fill="magenta")  # bottom-right marker
d = WigiDash(); d.show(rgb565(im)); time.sleep(2)
subprocess.run(["tools/cam.sh", "/tmp/wigicap/cam11.jpg"])
