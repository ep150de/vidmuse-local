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
      content: "Hi! I'm your AI video director. I've analyzed your song — let me know what kind of video you're envisioning, and I'll help you create it. Want me to propose some concepts?",
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

      // Auto-transition if concepts are mentioned
      if (res.response.toLowerCase().includes("concept") && res.response.includes("1.")) {
        setTimeout(onConceptsReady, 2000);
      }
    } catch (e) {
      setMessages((prev) => [...prev, { role: "agent", content: "Sorry, I had trouble connecting to the server. Please try again." }]);
    } finally {
      setIsTyping(false);
    }
  };

  return (
    <div className="max-w-3xl mx-auto h-[70vh] flex flex-col">
      <div className="flex items-center gap-3 mb-4">
        <Bot className="w-6 h-6 text-cyan-400" />
        <h2 className="text-xl font-bold">AI Video Director</h2>
        <span className="text-xs bg-cyan-500/20 text-cyan-400 px-2 py-1 rounded-full">Phase: Ideation</span>
      </div>

      <div className="flex-1 overflow-y-auto space-y-4 mb-4 pr-2">
        {messages.map((msg, i) => (
          <div
            key={i}
            className={`flex gap-3 ${msg.role === "user" ? "flex-row-reverse" : ""}`}
          >
            <div className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 ${
              msg.role === "agent" ? "bg-cyan-500/20" : "bg-gold-500/20"
            }`}>
              {msg.role === "agent" ? <Bot className="w-4 h-4 text-cyan-400" /> : <User className="w-4 h-4 text-gold-400" />}
            </div>
            <div className={`max-w-[80%] rounded-2xl px-4 py-3 ${
              msg.role === "agent"
                ? "bg-[#1a1f3d] text-gray-200"
                : "bg-cyan-500/20 text-cyan-100"
            }`}>
              <p className="whitespace-pre-wrap text-sm leading-relaxed">{msg.content}</p>
            </div>
          </div>
        ))}
        {isTyping && (
          <div className="flex gap-3">
            <div className="w-8 h-8 rounded-full bg-cyan-500/20 flex items-center justify-center">
              <Bot className="w-4 h-4 text-cyan-400" />
            </div>
            <div className="bg-[#1a1f3d] rounded-2xl px-4 py-3">
              <div className="flex gap-1">
                <div className="w-2 h-2 bg-cyan-400 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                <div className="w-2 h-2 bg-cyan-400 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                <div className="w-2 h-2 bg-cyan-400 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
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
          placeholder="Describe your vision or ask for concepts..."
          className="flex-1 bg-[#1a1f3d] border border-gray-600 rounded-xl px-4 py-3 text-white placeholder-gray-500 focus:border-cyan-400 focus:outline-none"
        />
        <button
          onClick={sendMessage}
          disabled={isTyping || !input.trim()}
          className="bg-cyan-500 hover:bg-cyan-400 text-black rounded-xl px-6 py-3 font-medium disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          <Send className="w-5 h-5" />
        </button>
      </div>

      <button
        onClick={onConceptsReady}
        className="mt-4 flex items-center justify-center gap-2 text-sm text-cyan-400 hover:text-cyan-300 transition-colors"
      >
        <Sparkles className="w-4 h-4" />
        Skip to concept selection
      </button>
    </div>
  );
}
