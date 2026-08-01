import { useState, useEffect, useRef } from "react";
import { Check, ChevronRight, Film, Zap, RefreshCw, AlertTriangle, Quote } from "lucide-react";
import { api, mediaUrl } from "../api";

interface Props {
  onSelect: (index: number) => void;
  onError?: (message: string) => void;
}

interface Concept {
  title: string;
  style: string;
  palette: string[];
  narrative: string;
  lyric_hooks?: string[];
  key_moments: Record<string, string>;
  complexity: string;
}

export function ConceptSelector({ onSelect, onError }: Props) {
  const [concepts, setConcepts] = useState<Concept[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [meta, setMeta] = useState<{
    has_lyrics?: boolean;
    has_references?: boolean;
    lyric_brief?: any;
    reference_urls?: string[];
  }>({});
  const startedRef = useRef(false);

  const loadConcepts = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.generateConcepts();
      const list = res.concepts || [];
      if (list.length === 0) {
        throw new Error("Director returned no concepts — add lyrics or more direction and retry.");
      }
      setConcepts(list);
      setMeta({
        has_lyrics: res.has_lyrics,
        has_references: res.has_references,
        lyric_brief: res.lyric_brief,
        reference_urls: res.reference_urls || [],
      });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      onError?.(msg);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    loadConcepts();
  }, []);

  if (loading) {
    return (
      <div className="max-w-2xl mx-auto text-center py-20 panel rounded-xl animate-rise">
        <div className="animate-spin w-14 h-14 border-2 border-[var(--accent)] border-t-transparent rounded-full mx-auto mb-6" />
        <h2 className="font-display text-xl mb-2 text-[var(--ink)]">Building lyric-grounded concepts</h2>
        <p className="text-sm text-[var(--text-secondary)]">
          Extracting themes from lyrics, then drafting three visual treatments…
        </p>
      </div>
    );
  }

  if (error && concepts.length === 0) {
    return (
      <div className="max-w-xl mx-auto text-center py-16 panel rounded-xl animate-rise">
        <AlertTriangle className="w-12 h-12 text-amber-400 mx-auto mb-4" />
        <h2 className="font-display text-xl mb-2 text-[var(--ink)]">Concept Generation Failed</h2>
        <p className="text-sm text-[var(--text-secondary)] mb-6">{error}</p>
        <button
          onClick={loadConcepts}
          className="inline-flex items-center gap-2 py-3 px-6 rounded-lg bg-[var(--accent-soft)] border border-[var(--accent)]/40 text-[var(--ink)] text-sm font-semibold hover:bg-[var(--accent)]/20 transition-all"
        >
          <RefreshCw className="w-4 h-4" />
          Retry
        </button>
      </div>
    );
  }

  const themes = meta.lyric_brief?.themes || [];
  const hooks = meta.lyric_brief?.key_phrases || [];
  const refs = meta.reference_urls || [];

  return (
    <div className="max-w-5xl mx-auto animate-rise">
      <div className="mb-8">
        <h2 className="font-display text-2xl tracking-tight text-[var(--ink)] mb-2">
          Choose your concept
        </h2>
        <p className="text-sm text-[var(--text-secondary)] max-w-2xl mb-3">
          {concepts.length} treatments of the same lyric themes
          {meta.has_references ? " · identity locked to your reference photo" : ""}.
        </p>
        <div className="flex flex-wrap items-center gap-2 mb-3">
          <span
            className={`text-xs px-2.5 py-1 rounded-full border ${
              meta.has_lyrics
                ? "border-emerald-400/40 text-emerald-200 bg-emerald-500/10"
                : "border-amber-400/40 text-amber-100 bg-amber-500/10"
            }`}
          >
            {meta.has_lyrics ? "Lyrics grounded" : "No lyrics — themes may drift"}
          </span>
          <span
            className={`text-xs px-2.5 py-1 rounded-full border ${
              meta.has_references
                ? "border-sky-400/40 text-sky-100 bg-sky-500/10"
                : "border-[var(--border)] text-[var(--text-secondary)]"
            }`}
          >
            {meta.has_references ? "Reference photo locked" : "No reference photo"}
          </span>
          {themes.slice(0, 4).map((t: string) => (
            <span
              key={t}
              className="text-xs px-2.5 py-1 rounded-full border border-[var(--border)] text-[var(--ink)] bg-[var(--surface-2)]"
            >
              {t}
            </span>
          ))}
        </div>
        {refs.length > 0 && (
          <div className="flex items-center gap-3 mb-2">
            <p className="text-xs text-[var(--text-secondary)]">Identity lock:</p>
            <div className="flex gap-2">
              {refs.slice(0, 3).map((url) => (
                <div
                  key={url}
                  className="w-12 h-12 rounded-md overflow-hidden border border-sky-400/40"
                >
                  <img
                    src={mediaUrl(url)}
                    alt="Reference"
                    className="w-full h-full object-cover"
                  />
                </div>
              ))}
            </div>
          </div>
        )}
        {!!hooks.length && (
          <p className="text-xs text-[var(--text-secondary)] line-clamp-2">
            Lyric anchors: {hooks.slice(0, 3).map((h: string) => `“${h}”`).join(" · ")}
          </p>
        )}
      </div>

      {error && (
        <div className="mb-6 panel rounded-xl border-amber-400/40 px-4 py-3 text-sm text-amber-100">
          {error}
        </div>
      )}

      <div className="grid gap-5 md:grid-cols-3">
        {concepts.map((concept, i) => {
          const active = selected === i;
          return (
            <button
              key={i}
              type="button"
              onClick={() => setSelected(i)}
              className={`
                relative text-left rounded-xl border p-5 transition-all
                ${active
                  ? "border-[var(--accent)] bg-[var(--accent-soft)] shadow-[0_12px_40px_rgba(0,0,0,0.35)]"
                  : "border-[var(--border)] bg-[var(--panel)] hover:border-[var(--accent)]/45"
                }
              `}
            >
              {active && (
                <div className="absolute top-4 right-4 w-7 h-7 rounded-full bg-[var(--accent)] flex items-center justify-center">
                  <Check className="w-4 h-4 text-[var(--navy)]" />
                </div>
              )}

              <div className="flex items-center gap-2 mb-3 pr-8">
                <Film className="w-5 h-5 text-[var(--accent)] shrink-0" />
                <h3 className="text-base font-semibold text-[var(--ink)] leading-snug">
                  {concept.title}
                </h3>
              </div>

              <p className="text-sm text-[var(--text-secondary)] mb-3 leading-relaxed line-clamp-2">
                {concept.style}
              </p>

              {concept.narrative && (
                <p className="text-sm text-[var(--ink)]/90 mb-3 leading-relaxed line-clamp-4">
                  {concept.narrative}
                </p>
              )}

              {!!concept.lyric_hooks?.length && (
                <div className="mb-4 space-y-1.5">
                  {concept.lyric_hooks.slice(0, 3).map((hook) => (
                    <p key={hook} className="text-xs text-sky-100/90 flex gap-1.5 leading-snug">
                      <Quote className="w-3 h-3 shrink-0 mt-0.5 text-[var(--accent)]" />
                      <span>{hook}</span>
                    </p>
                  ))}
                </div>
              )}

              <div className="flex gap-2 mb-4">
                {concept.palette?.map((color, j) => (
                  <div
                    key={j}
                    className="w-6 h-6 rounded-full border border-white/20"
                    style={{ backgroundColor: color }}
                    title={color}
                  />
                ))}
              </div>

              <div className="space-y-2.5 mb-4">
                {Object.entries(concept.key_moments || {})
                  .slice(0, 3)
                  .map(([key, value]) => (
                    <div key={key} className="text-sm leading-snug">
                      <span className="font-medium text-[var(--accent)] capitalize">{key}</span>
                      <span className="text-[var(--ink)]"> — {value}</span>
                    </div>
                  ))}
              </div>

              <div className="flex items-center gap-2 text-xs font-medium text-[var(--text-secondary)]">
                <Zap className="w-3.5 h-3.5 text-[var(--accent)]" />
                <span className="capitalize">{concept.complexity} complexity</span>
              </div>
            </button>
          );
        })}
      </div>

      {selected !== null && (
        <div className="mt-8 text-center">
          <button
            type="button"
            onClick={() => onSelect(selected)}
            className="cta-primary inline-flex items-center gap-3 py-3.5 px-7 rounded-xl text-sm"
          >
            Create storyboard for &ldquo;{concepts[selected]?.title}&rdquo;
            <ChevronRight className="w-5 h-5" />
          </button>
        </div>
      )}
    </div>
  );
}
