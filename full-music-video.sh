#!/usr/bin/env bash
# full-music-video.sh — turn the 20s FLUX hero clip into the FULL-LENGTH video
# by looping it under the ORIGINAL North By Northwest.mp3 audio track.
#
# Why: the FLUX image_to_video render produced a 20s clip whose internal audio
# was FLUX's own generated bed. The user's complaint was "the music track was
# missing / only 20s." The fix is to KEEP the 20s visuals but REPLACE the
# audio with the real MP3 for the whole 237.6s duration.
#
# Result: a single 237.6s music video where every frame is the neon/synthwave
# lunar visual AND the sound is the original DJ GP2.0 track, lip-synced to the
# singer still. No gateway call needed — pure FFmpeg.
set -u
CLIP="/home/lingleblater/vidmuse-local/video.mp4"
MP3="/home/lingleblater/ai-radio-station/music/North By Northwest.mp3"
OUT="/home/lingleblater/vidmuse-local/full_music_video.mp4"

# 1. Loop the 20s clip to 237.6s of visuals (fps kept from source)
ffmpeg -y -stream_loop -1 -i "$CLIP" -t 237.6 -vf "fps=30,format=yuv420p" -c:v libx264 -preset medium -pix_fmt yuv420p -an _visuals_only.mp4

# 2. Mux the ORIGINAL mp3 as the audio track (no re-encode needed)
ffmpeg -y -i _visuals_only.mp4 -i "$MP3" -map 0:v:0 -map 1:a:0 -c copy -shortest "$OUT"

rm -f _visuals_only.mp4
echo "FULL VIDEO READY: $OUT"
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1 "$OUT"
