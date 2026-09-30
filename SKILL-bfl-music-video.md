name: bfl-music-video
description: >
  Generate an AI MTV music video from a single mp3 + a text direction +
  a reference image of the singer, using the BFL Flux 3 image-to-video
  tool. The mp3 becomes the audio layer (so the ORIGINAL track plays under
  the visuals, lip-synced to the singer image) instead of FLUX generating
  its own throwaway audio. One command, no manual job-id tracking: the
  script auto-captures the submitted job id and polls until Ready.
usage: >
  bash /home/lingleblater/vidmuse-local/run-bfl-video.sh "<PROMPT>" <MP3> <SINGER_IMG>
trigger: >
  Use when the user wants an AI music video (mtv-style) from an mp3 + image,
  especially the North By Northwest (DJ GP2.0) track. Replaces the bare
  bfl_flux3_text_to_video call that produced a 20s silent-audio clip with no
  original music.
