#!/usr/bin/env bash
# drive_inscribed.sh — full sequential renderer + assembler for Inscribed in the Block
# Honors BFL 1-concurrent limit naturally (renders scenes one at a time).
# Polls each scene until the clip exists; slow-mo flagged scenes; concat+mux+trim+QC at end.
set -u
cd /home/lingleblater/vidmuse-local
export NOUS_TOOLS_ENABLED=1
export MANAGED_NOUS_TOOLS=1
LOG=/home/lingleblater/vidmuse-local/inscribed_drive.log
: > "$LOG"
echo "[$(date)] driver start" >> "$LOG"

render_one() {  # $1 = index
  local n=$1
  for attempt in $(seq 1 400); do
    if [ -f "ins_$(printf '%02d' $n).mp4" ]; then
      echo "[$(date)] scene $n already present" >> "$LOG"
      return 0
    fi
    out=$(bash render_inscribed_scene.sh "$n" 2>&1)
    if echo "$out" | grep -q "RESULT:"; then
      echo "[$(date)] scene $n SAVED" >> "$LOG"
      return 0
    fi
    # not ready yet; wait before next poll
    sleep 45
  done
  echo "[$(date)] scene $n FAILED after attempts" >> "$LOG"
  return 1
}

# render all 16 scenes (0..15) in order
for n in $(seq 0 15); do
  render_one "$n" || echo "[$(date)] WARN scene $n could not render" >> "$LOG"
done

echo "[$(date)] all scenes rendered; building slow-mo" >> "$LOG"
# slow-mo scenes 5 (index 5) and 11 (index 11)
ffmpeg -y -i ins_05.mp4 -vf "setpts=2*PTS" -af "asetpts=2*PTS" ins_slow_05.mp4 >/dev/null 2>&1
ffmpeg -y -i ins_11.mp4 -vf "setpts=2*PTS" -af "asetpts=2*PTS" ins_slow_11.mp4 >/dev/null 2>&1

# concat list
rm -f ins_assemble_list.txt
for n in $(seq 0 15); do
  if [ "$n" = "5" ]; then f="ins_slow_05.mp4"; elif [ "$n" = "11" ]; then f="ins_slow_11.mp4"; else f="ins_$(printf '%02d' $n).mp4"; fi
  printf "file '%s/%s'\n" "$PWD" "$f" >> ins_assemble_list.txt
done

ffmpeg -y -f concat -safe 0 -i ins_assemble_list.txt -i inscribed.mp3 -map 0:v:0 -map 1:a:0 -c:v copy -c:a copy final_inscribed.mp4 >/dev/null 2>&1
# trim to song length
ffmpeg -y -i final_inscribed.mp4 -t 161.3 -c copy final_inscribed_trim.mp4 >/dev/null 2>&1
mv -f final_inscribed_trim.mp4 final_inscribed.mp4
echo "[$(date)] assembled final_inscribed.mp4 ($(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1 final_inscribed.mp4) s)" >> "$LOG"

# QC
frez=$(ffmpeg -i final_inscribed.mp4 -vf "blackdetect=d=0.08:pix_th=0.02,freezedetect=n=0.003" -an -f null - 2>&1 | grep -iE "blackdetect|freeze" | head -5 || true)
fr=$(ffprobe -v error -count_frames -select_streams v:0 -show_entries stream=nb_read_frames -of default=noprint_wrappers=1 final_inscribed.mp4 | tail -1)
echo "[$(date)] QC frames=$fr blackfreeze=[$frez]" >> "$LOG"
echo "[$(date)] DONE" >> "$LOG"
