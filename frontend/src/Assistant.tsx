import { useEffect, useRef, useState } from "react";

const BASE = "http://localhost:8000";

interface Msg { role: "user" | "assistant"; content: string; }

// Browser speech APIs (Chrome/Edge). Voice input via SpeechRecognition,
// spoken replies via speechSynthesis — no external service needed.
const SR: any = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;

const GREETING: Msg = {
  role: "assistant",
  content: "Hi! Ask me anything about your tenders, products or prices — e.g. \"show me the specs of the metro tender\", \"do we have 11 kV aluminium cable?\", \"any open escalations?\" — or tell me to approve a bid (I'll always confirm first).",
};

function historyKey() {
  return `bidpilot_chat_${(localStorage.getItem("bidpilot_actor") || "default").toLowerCase()}`;
}

function loadHistory(): Msg[] {
  try {
    const raw = localStorage.getItem(historyKey());
    const parsed = raw ? (JSON.parse(raw) as Msg[]) : [];
    return parsed.length ? parsed : [GREETING];
  } catch {
    return [GREETING];
  }
}

export default function Assistant() {
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>(loadHistory);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [listening, setListening] = useState(false);
  const [speak, setSpeak] = useState(false);
  const recRef = useRef<any>(null);
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight, behavior: "smooth" });
    try { localStorage.setItem(historyKey(), JSON.stringify(msgs.slice(-60))); } catch { /* full */ }
  }, [msgs, open]);

  const say = (text: string) => {
    if (!speak || !window.speechSynthesis) return;
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text.slice(0, 600));
    u.lang = "en-IN";
    window.speechSynthesis.speak(u);
  };

  const send = async (text: string) => {
    const content = text.trim();
    if (!content || busy) return;
    const next: Msg[] = [...msgs, { role: "user", content }];
    setMsgs(next);
    setInput("");
    setBusy(true);
    try {
      const resp = await fetch(`${BASE}/assistant/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          actor: localStorage.getItem("bidpilot_actor") ?? "",
          messages: next.map((m) => ({ role: m.role, content: m.content })),
        }),
      });
      const data = await resp.json();
      const reply = resp.ok ? (data.reply ?? "…") : `Error: ${JSON.stringify(data).slice(0, 200)}`;
      setMsgs((m) => [...m, { role: "assistant", content: reply }]);
      say(reply);
    } catch (e) {
      setMsgs((m) => [...m, { role: "assistant", content: `Connection error: ${e}` }]);
    } finally {
      setBusy(false);
    }
  };

  const toggleMic = () => {
    if (!SR) {
      setMsgs((m) => [...m, { role: "assistant", content: "Voice input needs Chrome or Edge (Web Speech API not available in this browser)." }]);
      return;
    }
    if (listening) {
      recRef.current?.stop();
      return;
    }
    const rec = new SR();
    rec.lang = "en-IN";
    rec.interimResults = false;
    rec.onresult = (ev: any) => {
      const text = ev.results[0][0].transcript;
      setListening(false);
      send(text);
    };
    rec.onend = () => setListening(false);
    rec.onerror = () => setListening(false);
    recRef.current = rec;
    setListening(true);
    rec.start();
  };

  if (!open) {
    return (
      <button className="assistant-fab" onClick={() => setOpen(true)} title="BidPilot assistant — chat or voice">
        💬
      </button>
    );
  }

  return (
    <div className="assistant-panel">
      <div className="assistant-head">
        <b>BidPilot assistant</b>
        <button className={`mini ${speak ? "on" : ""}`} title="Speak replies aloud"
                onClick={() => { if (speak) window.speechSynthesis?.cancel(); setSpeak(!speak); }}>
          {speak ? "🔊" : "🔇"}
        </button>
        <button className="mini" title="Clear this conversation"
                onClick={() => { localStorage.removeItem(historyKey()); setMsgs([GREETING]); }}>
          🗑
        </button>
        <button className="mini" onClick={() => setOpen(false)}>✕</button>
      </div>
      <div className="assistant-body" ref={bodyRef}>
        {msgs.map((m, i) => (
          <div key={i} className={`bubble ${m.role}`}>{m.content}</div>
        ))}
        {busy && <div className="bubble assistant muted">thinking…</div>}
      </div>
      <div className="assistant-input">
        <button className={`mic ${listening ? "listening" : ""}`} onClick={toggleMic}
                title={listening ? "Listening — tap to stop" : "Speak your question"}>
          {listening ? "⏺" : "🎤"}
        </button>
        <input
          type="text" value={input} placeholder={listening ? "listening…" : "ask or command…"}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") send(input); }}
          disabled={busy}
        />
        <button onClick={() => send(input)} disabled={busy || !input.trim()}>➤</button>
      </div>
    </div>
  );
}
