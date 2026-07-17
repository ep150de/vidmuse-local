import { useState, useEffect, useRef } from "react";
import { Check, ChevronRight, Film, Zap, RefreshCw, AlertTriangle } from "lucide-react";
import { api } from "../api";

interface Props {
  onSelect: (index: number) => void;
  onError?: (message: string) => void;
}

interface Concept {
  title: string;
  style: string;
  palette: string[];
  narrative: string;
  key_moments: Record<string, string>;
  complexity: string;
}

export function ConceptSelector({ onSelect, onError }: Props) {
  const [concepts, setConcepts] = useState<Concept[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const startedRef = useRef(false);

  const loadConcepts = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.generateConcepts();
      const list = res.concepts || [];
      if (list.length === 0) {
        throw new Error("Director returned no concepts — try chatting again with more direction.");
      }
      setConcepts(list);
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
      <div className="max-w-2xl mx-auto text-center py-20 panel animate-rise">
        <div className="animate-spin w-14 h-14 border-2 border-cyan-400 border-t-transparent rounded-full mx-auto mb-6" />
        <h2 className="font-display text-xl mb-2">Generating Concepts</h2>
        <p className="text-sm text-[var(--muted)]">
          The director is drafting three visual directions for your track…
        </p>
      </div>
    );
  }

  if (error && concepts.length === 0) {
    return (
      <div className="max-w-xl mx-auto text-center py-16 panel animate-rise">
        <AlertTriangle className="w-12 h-12 text-amber-400 mx-auto mb-4" />
        <h2 className="font-display text-xl mb-2">Concept Generation Failed</h2>
        <p className="text-sm text-[var(--muted)] mb-6">{error}</p>
        <button
          onClick={loadConcepts}
          className="inline-flex items-center gap-2 py-3 px-6 bg-cyan-500/20 border border-cyan-400/40 text-cyan-200 font-display text-sm tracking-wider hover:bg-cyan-500/30 transition-all"
        >
          <RefreshCw className="w-4 h-4" />
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto animate-rise">
      <h2 className="font-display text-2xl tracking-wide mb-2">Choose Your Concept</h2>
      <p className="text-sm text-[var(--muted)] mb-8">
        The AI director crafted {concepts.length} video concepts from your song and vision.
      </p>

      {error && (
        <div className="mb-6 panel border-amber-400/40 px-4 py-3 text-sm text-amber-100">
          {error}
        </div>
      )}

      <div className="grid gap-6 md:grid-cols-3">
        {concepts.map((concept, i) => (
          <button
            key={i}
            onClick={() => setSelected(i)}
            className={`
              relative text-left panel p-6 border-2 transition-all
              ${selected === i
                ? "border-cyan-400 bg-cyan-500/10 shadow-lg shadow-cyan-500/10"
                : "border-cyan-500/20 hover:border-cyan-400/50"
              }
            `}
          >
            {selected === i && (
              <div className="absolute top-4 right-4 w-6 h-6 bg-cyan-400 flex items-center justify-center">
                <Check className="w-4 h-4 text-navy-900" />
              </div>
            )}

            <div className="flex items-center gap-2 mb-3">
              <Film className="w-5 h-5 text-cyan-400" />
              <h3 className="font-display text-lg">{concept.title}</h3>
            </div>

            <p className="text-sm text-[var(--muted)] mb-4 line-clamp-3">{concept.style}</p>

            <div className="flex gap-2 mb-4">
              {concept.palette?.map((color, j) => (
                <div
                  key={j}
                  className="w-6 h-6 rounded-full border border-cyan-500/30"
                  style={{ backgroundColor: color }}
                  title={color}
                />
              ))}
            </div>

            <div className="space-y-2 mb-4">
              {Object.entries(concept.key_moments || {}).slice(0, 2).map(([key, value]) => (
                <div key={key} className="text-xs">
                  <span className="text-cyan-400 capitalize">{key}:</span>{" "}
                  <span className="text-[var(--muted)]">{value}</span>
                </div>
              ))}
            </div>

            <div className="flex items-center gap-2 text-xs text-[var(--muted)]">
              <Zap className="w-3 h-3" />
              <span className="capitalize">{concept.complexity}</span>
            </div>
          </button>
        ))}
      </div>

      {selected !== null && (
        <div className="mt-8 text-center">
          <button
            onClick={() => onSelect(selected)}
            className="bg-gradient-to-r from-cyan-500 to-blue-600 hover:from-cyan-400 hover:to-blue-500 text-white font-display text-sm tracking-wider py-4 px-8 inline-flex items-center gap-3 transition-all shadow-lg shadow-cyan-500/25"
          >
            Create Storyboard for &ldquo;{concepts[selected]?.title}&rdquo;
            <ChevronRight className="w-5 h-5" />
          </button>
        </div>
      )}
    </div>
  );
}
