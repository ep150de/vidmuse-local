import { useState, useEffect } from "react";
import { Check, ChevronRight, Palette, Film, Zap } from "lucide-react";
import { api } from "../api";

interface Props {
  onSelect: (index: number) => void;
}

interface Concept {
  title: string;
  style: string;
  palette: string[];
  narrative: string;
  key_moments: Record<string, string>;
  complexity: string;
}

export function ConceptSelector({ onSelect }: Props) {
  const [concepts, setConcepts] = useState<Concept[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.generateConcepts().then((res) => {
      setConcepts(res.concepts || []);
      setLoading(false);
    });
  }, []);

  if (loading) {
    return (
      <div className="text-center py-20">
        <div className="animate-spin w-12 h-12 border-4 border-cyan-400 border-t-transparent rounded-full mx-auto mb-4" />
        <p className="text-gray-400">Generating concepts...</p>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto">
      <h2 className="text-2xl font-bold mb-2">Choose Your Concept</h2>
      <p className="text-gray-400 mb-8">The AI director has crafted 3 video concepts based on your song and vision.</p>

      <div className="grid gap-6 md:grid-cols-3">
        {concepts.map((concept, i) => (
          <button
            key={i}
            onClick={() => setSelected(i)}
            className={`
              relative text-left rounded-xl p-6 border-2 transition-all
              ${selected === i
                ? "border-cyan-400 bg-cyan-500/10 shadow-lg shadow-cyan-500/10"
                : "border-gray-700 bg-[#1a1f3d] hover:border-gray-500"
              }
            `}
          >
            {selected === i && (
              <div className="absolute top-4 right-4 w-6 h-6 bg-cyan-400 rounded-full flex items-center justify-center">
                <Check className="w-4 h-4 text-black" />
              </div>
            )}

            <div className="flex items-center gap-2 mb-3">
              <Film className="w-5 h-5 text-cyan-400" />
              <h3 className="font-bold text-lg">{concept.title}</h3>
            </div>

            <p className="text-sm text-gray-400 mb-4 line-clamp-3">{concept.style}</p>

            <div className="flex gap-2 mb-4">
              {concept.palette.map((color, j) => (
                <div
                  key={j}
                  className="w-6 h-6 rounded-full border border-gray-600"
                  style={{ backgroundColor: color }}
                  title={color}
                />
              ))}
            </div>

            <div className="space-y-2 mb-4">
              {Object.entries(concept.key_moments).slice(0, 2).map(([key, value]) => (
                <div key={key} className="text-xs">
                  <span className="text-cyan-400 capitalize">{key}:</span>{" "}
                  <span className="text-gray-400">{value}</span>
                </div>
              ))}
            </div>

            <div className="flex items-center gap-2 text-xs text-gray-500">
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
            className="bg-gradient-to-r from-cyan-500 to-blue-600 hover:from-cyan-400 hover:to-blue-500 text-white font-bold py-4 px-8 rounded-xl flex items-center gap-3 mx-auto transition-all shadow-lg shadow-cyan-500/25"
          >
            Create Storyboard for "{concepts[selected]?.title}"
            <ChevronRight className="w-5 h-5" />
          </button>
        </div>
      )}
    </div>
  );
}
