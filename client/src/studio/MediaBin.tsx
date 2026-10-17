import { useCallback, useId } from "react";
import { useDropzone } from "react-dropzone";
import { Image, Music, Trash2 } from "lucide-react";
import { mediaUrl } from "../api";
import type { StudioScene } from "./types";

interface Props {
  audioName: string | null;
  stillName: string | null;
  stillPreviewUrl: string | null;
  scenes: StudioScene[];
  selectedIndex: number | null;
  onAudio: (file: File) => void;
  onStill: (file: File | null) => void;
  onSelectScene: (index: number) => void;
}

export function MediaBin({
  audioName,
  stillName,
  stillPreviewUrl,
  scenes,
  selectedIndex,
  onAudio,
  onStill,
  onSelectScene,
}: Props) {
  const audioLabelId = useId();
  const stillLabelId = useId();
  const clipsLabelId = useId();

  const onAudioDrop = useCallback(
    (files: File[]) => {
      if (files[0]) onAudio(files[0]);
    },
    [onAudio]
  );
  const onStillDrop = useCallback(
    (files: File[]) => {
      if (files[0]) onStill(files[0]);
    },
    [onStill]
  );

  const audioDz = useDropzone({
    onDrop: onAudioDrop,
    accept: { "audio/*": [".mp3", ".wav", ".m4a", ".ogg"] },
    multiple: false,
    noKeyboard: false,
  });
  const stillDz = useDropzone({
    onDrop: onStillDrop,
    accept: { "image/*": [".png", ".jpg", ".jpeg", ".webp"] },
    multiple: false,
    noKeyboard: false,
  });

  return (
    <aside
      className="w-[15.5rem] shrink-0 border-r flex flex-col min-h-0"
      style={{
        borderColor: "var(--border)",
        background: "var(--bg-elevated)",
      }}
      aria-label="Media bin"
    >
      <div className="px-4 py-3 border-b" style={{ borderColor: "var(--border)" }}>
        <h2 className="studio-label !mb-0">Media</h2>
      </div>

      <div className="p-3 space-y-3 overflow-y-auto flex-1">
        <div>
          <p id={audioLabelId} className="studio-label">
            Song
          </p>
          <div
            {...audioDz.getRootProps()}
            aria-labelledby={audioLabelId}
            className="rounded-[var(--radius)] border border-dashed p-3 cursor-pointer transition-colors"
            style={{
              borderColor: audioDz.isDragActive
                ? "var(--accent)"
                : "var(--border-strong)",
              background: audioDz.isDragActive
                ? "var(--accent-soft)"
                : "var(--surface)",
            }}
          >
            <input {...audioDz.getInputProps()} aria-labelledby={audioLabelId} />
            <Music
              className="w-4 h-4 mb-2"
              style={{ color: "var(--accent)" }}
              aria-hidden="true"
            />
            <p className="text-sm font-medium">Import MP3</p>
            <p className="text-xs text-[var(--muted)] mt-1 truncate">
              {audioName || "Drop a track or press Enter"}
            </p>
          </div>
        </div>

        <div>
          <p id={stillLabelId} className="studio-label">
            Singer still
          </p>
          <div
            {...stillDz.getRootProps()}
            aria-labelledby={stillLabelId}
            className="rounded-[var(--radius)] border border-dashed p-3 cursor-pointer transition-colors"
            style={{
              borderColor: stillDz.isDragActive
                ? "var(--accent)"
                : "var(--border-strong)",
              background: stillDz.isDragActive
                ? "var(--accent-soft)"
                : "var(--surface)",
            }}
          >
            <input {...stillDz.getInputProps()} aria-labelledby={stillLabelId} />
            <Image
              className="w-4 h-4 mb-2 text-[var(--success)]"
              aria-hidden="true"
            />
            <p className="text-sm font-medium">Optional photo</p>
            <p className="text-xs text-[var(--muted)] mt-1 truncate">
              {stillName || "Enables identity-locked lip sync"}
            </p>
            {stillPreviewUrl && (
              <img
                src={stillPreviewUrl}
                alt="Uploaded singer reference"
                className="mt-2 w-full rounded-md object-cover aspect-video"
              />
            )}
          </div>
          {stillName && (
            <button
              type="button"
              onClick={() => onStill(null)}
              className="mt-2 inline-flex items-center gap-1 text-xs text-[var(--muted)] hover:text-[var(--danger)]"
            >
              <Trash2 className="w-3 h-3" aria-hidden="true" />
              Remove still
            </button>
          )}
        </div>

        <div>
          <p id={clipsLabelId} className="studio-label">
            Clips
          </p>
          <ul className="space-y-1.5" aria-labelledby={clipsLabelId}>
            {scenes.length === 0 && (
              <li className="text-xs text-[var(--muted)] px-0.5">
                Rendered H3 takes appear here.
              </li>
            )}
            {scenes.map((scene) => {
              const selected = selectedIndex === scene.index;
              return (
                <li key={scene.index}>
                  <button
                    type="button"
                    onClick={() => onSelectScene(scene.index)}
                    aria-current={selected ? "true" : undefined}
                    className="w-full text-left rounded-lg border px-2.5 py-2 transition-colors"
                    style={{
                      borderColor: selected ? "var(--accent)" : "transparent",
                      background: selected
                        ? "var(--accent-soft)"
                        : "var(--surface)",
                    }}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-semibold truncate">
                        Scene {scene.index + 1}
                        <span className="text-[var(--muted)] font-medium">
                          {" "}
                          · {scene.type}
                        </span>
                      </span>
                      <span
                        className={`status-dot ${scene.status}`}
                        title={scene.status}
                        aria-label={scene.status}
                      />
                    </div>
                    {scene.video_url && (
                      <video
                        src={mediaUrl(scene.video_url)}
                        muted
                        playsInline
                        preload="metadata"
                        className="mt-2 w-full rounded-md aspect-video object-cover bg-black"
                        aria-hidden="true"
                      />
                    )}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      </div>
    </aside>
  );
}
