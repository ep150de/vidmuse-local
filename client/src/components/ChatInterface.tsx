import { useState, useRef, useEffect } from "react";
import { Send, Bot, User, Sparkles } from "lucide-react";
import { api } from "../api";

interface Props {
  onConceptsReady: () => void;
}

interface Message {
  role: "user" | "agent";
  content: string;
}

export function ChatInterface({ onConceptsReady }: Props) {
  const [messages, setMessages] = useState<Message[]>([
    {
      role: "agent",
      content:
        "Signal locked. I've analyzed your track — tell me the mood, or ask me to spit three video concepts.",
    },
  ]);
  const [input, setInput] = useState("");
  const [isTyping, setIsTyping] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const sendMessage = async () => {
    if (!input.trim()) return;

    const userMsg = input.trim();
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content: userMsg }]);
    setIsTyping(true);

    try {
      const res = await api.chat(userMsg);
      setMessages((prev) => [...prev, { role: "agent", content: res.response }]);

      if (
        res.response.toLowerCase().includes("concept") &&
        res.response.includes("1.")
      ) {
        setTimeout(onConceptsReady, 2000);
      }
    } catch {
      setMessages((prev) => [
        ...prev,
        {
          role: "agent",
          content: "Comms glitch — couldn't reach the director. Try again.",
        },
      ]);
    } finally {
      setIsTyping(false);
    }
  };

  return (
    <div className="max-w-3xl mx-auto h-[70vh] flex flex-col animate-rise">
      <div className="flex items-center gap-3 mb-4">
        <Bot className="w-6 h-6 text-cyan-400" />
        <h2 className="font-display text-lg tracking-wide">AI Video Director</h2>
        <span className="text-[10px] uppercase tracking-widest border border-cyan-500/40 text-cyan-300 px-2 py-1">
          Ideation
        </span>
      </div>

      <div className="panel flex-1 overflow-y-auto space-y-4 mb-4 p-4">
        {messages.map((msg, i) => (
          <div
            key={i}
            className={`flex gap-3 ${msg.role === "user" ? "flex-row-reverse" : ""}`}
          >
            <div
              className={`w-8 h-8 flex items-center justify-center shrink-0 ${
                msg.role === "agent" ? "bg-cyan-500/20" : "bg-gold-500/20"
              }`}
            >
              {msg.role === "agent" ? (
                <Bot className="w-4 h-4 text-cyan-400" />
              ) : (
                <User className="w-4 h-4 text-gold-400" />
              )}
            </div>
            <div
              className={`max-w-[80%] px-4 py-3 ${
                msg.role === "agent"
                  ? "bg-navy-900/80 border border-cyan-500/20 text-slate-200"
                  : "bg-cyan-500/15 border border-cyan-400/25 text-cyan-50"
              }`}
            >
              <p className="whitespace-pre-wrap text-sm leading-relaxed">
                {msg.content}
              </p>
            </div>
          </div>
        ))}
        {isTyping && (
          <div className="flex gap-3">
            <div className="w-8 h-8 bg-cyan-500/20 flex items-center justify-center">
              <Bot className="w-4 h-4 text-cyan-400" />
            </div>
            <div className="bg-navy-900/80 border border-cyan-500/20 px-4 py-3">
              <div className="flex gap-1">
                <div
                  className="w-2 h-2 bg-cyan-400 rounded-full animate-bounce"
                  style={{ animationDelay: "0ms" }}
                />
                <div
                  className="w-2 h-2 bg-cyan-400 rounded-full animate-bounce"
                  style={{ animationDelay: "150ms" }}
                />
                <div
                  className="w-2 h-2 bg-cyan-400 rounded-full animate-bounce"
                  style={{ animationDelay: "300ms" }}
                />
              </div>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="flex gap-3">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && sendMessage()}
          placeholder="Describe the vibe or ask for concepts…"
          className="flex-1 bg-navy-900/80 border border-cyan-500/25 px-4 py-3 text-white placeholder:text-[var(--muted)] focus:border-cyan-400 focus:outline-none"
        />
        <button
          type="button"
          onClick={sendMessage}
          disabled={isTyping || !input.trim()}
          className="cta-primary px-5 py-3 disabled:opacity-40"
        >
          <Send className="w-5 h-5" />
        </button>
      </div>

      <button
        type="button"
        onClick={onConceptsReady}
        className="mt-4 flex items-center justify-center gap-2 text-sm text-gold-400 hover:text-gold-300 transition-colors"
      >
        <Sparkles className="w-4 h-4" />
        Skip to concept selection
      </button>
    </div>
  );
}
