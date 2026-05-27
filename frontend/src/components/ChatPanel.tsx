import { useEffect, useRef, useState } from "react";
import type { ChatItem } from "../types";
import UnderstandCard from "./UnderstandCard";

function Composer({
  onSend,
  busy,
  placeholder,
}: {
  onSend: (text: string) => void;
  busy: boolean;
  placeholder: string;
}) {
  const [text, setText] = useState("");
  const submit = () => {
    const value = text.trim();
    if (value && !busy) {
      onSend(value);
      setText("");
    }
  };
  return (
    <div className="composer">
      <textarea
        value={text}
        placeholder={placeholder}
        rows={2}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
      />
      <button className="icon-btn mic" title="语音（规划中）" disabled>
        🎙
      </button>
      <button className="icon-btn send" title="发送" onClick={submit} disabled={busy}>
        ↑
      </button>
    </div>
  );
}

function ClarifyCard({
  text,
  options,
  busy,
  onSend,
}: {
  text: string;
  options: Extract<ChatItem, { kind: "clarify" }>["options"];
  busy: boolean;
  onSend: (text: string) => void;
}) {
  return (
    <div className="clarify">
      <div className="clarify-title">{text}</div>
      <div className="clarify-options">
        {options.map((opt) => (
          <button
            className="choice"
            key={opt.id}
            disabled={busy}
            onClick={() => onSend(opt.message)}
            title={opt.description}
          >
            <span>{opt.label}</span>
            {opt.description && <small>{opt.description}</small>}
          </button>
        ))}
      </div>
    </div>
  );
}

function hintFor(items: ChatItem[]): string {
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i];
    if (it.kind === "understanding") {
      return it.data.kind === "mood" ? "模糊需求 → 推断你想要什么" : "明确需求 → 确认你的安排";
    }
  }
  return "说出你的出行需求";
}

export default function ChatPanel({
  items,
  thinking,
  streamingId,
  busy,
  onSend,
}: {
  items: ChatItem[];
  thinking: string | null;
  streamingId: string | null;
  busy: boolean;
  onSend: (text: string) => void;
}) {
  const streamRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = streamRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [items, thinking]);

  return (
    <div className="chat">
      <div className="panel-title">
        <span className="dot accent" />
        对话
        <span className="hint">{hintFor(items)}</span>
      </div>
      <div className="chat-card">
        <div className="stream" ref={streamRef}>
          {items.map((item) => {
            if (item.kind === "understanding") {
              return <UnderstandCard u={item.data} key={item.id} />;
            }
            if (item.kind === "clarify") {
              return (
                <ClarifyCard
                  key={item.id}
                  text={item.text}
                  options={item.options}
                  busy={busy}
                  onSend={onSend}
                />
              );
            }
            if (item.kind === "status") {
              return (
                <div className="msg bot" key={item.id}>
                  <div className="bubble thinking">
                    <span className="dot-b" />
                    {item.text}
                  </div>
                </div>
              );
            }
            const isUser = item.role === "user";
            return (
              <div className={`msg ${isUser ? "user" : "bot"}`} key={item.id}>
                <div className="who">{isUser ? "我" : "RoamMind"}</div>
                <div className="bubble">
                  {item.content}
                  {streamingId === item.id && <span className="caret" />}
                </div>
              </div>
            );
          })}
          {busy && thinking && (
            <div className="msg bot">
              <div className="bubble thinking">
                <span className="dot-b" />
                {thinking}
              </div>
            </div>
          )}
        </div>
        <Composer
          onSend={onSend}
          busy={busy}
          placeholder="说出你的出行需求，例如「今天有点累，想找个安静的地方待着」…"
        />
      </div>
    </div>
  );
}
