name: music-video
description: >
  Generate a PROPER AI music video (not a 20s loop) from an mp3 + a text
  direction + a reference image of the singer, using the BFL Flux 3
  image-to-video tool. Each lyric segment in the song becomes its OWN
  short clip (distinct visual, lip-synced to the singer still, scored to
  that moment of the track), then all clips concat in timeline order to
  the full-length video with the ORIGINAL mp3 as the audio layer.
trigger: >
  Use when the user wants a real music video (mtv-style) from an mp3 + image,
  especially the North By Northwest (DJ GP2.0) track — where every scene
  matches the lyric being sung at that timestamp, not a duplicated 20s loop.
usage: >
  bash /home/lingleblater/vidmuse-local/run-music-video.sh "<DIRECTION>" <MP3> <SINGER_IMG>

  The script reads analysis/timeline.json (58 lyric-shots with start/end
  times), and for each shot submits one bfl_flux3_image_to_video job with:
    - input_image = per-scene still (singer ref recolored/tinted per mood)
    - input_audio = the mp3 segment for that timestamp
    - prompt      = the lyric line + direction (so the motion matches the words)
  then FFmpeg-concats the clips into full_music_video.mp4 (237.6s, original audio).
