#!/bin/bash
# usage: cam.sh out.jpg  - grab a frame from the C920 after letting auto-exposure settle
ffmpeg -loglevel error -y -f v4l2 -video_size 1280x720 -i /dev/video0 -frames:v 15 -update 1 "$1"
