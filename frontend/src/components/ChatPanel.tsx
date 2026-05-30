import { useEffect, useRef, useState } from "react";
import type { ChatItem, FileContext } from "../types";
import UnderstandCard from "./UnderstandCard";

export interface AttachedFile {
  id: string;
  name: string;
  status: "parsing" | "ready" | "error";
  context?: FileContext;
  error?: string;
}

function Composer({
  onSend,
  onAttachFile,
  onRemoveAttachment,
  attachments,
  busy,
  placeholder,
}: {
  onSend: (text: string) => void;
  onAttachFile: (file: File) => void;
  onRemoveAttachment: (id: string) => void;
  attachments: AttachedFile[];
  busy: boolean;
  placeholder: string;
}) {
  const [text, setText] = useState("");
  const fileRef = useRef<HTMLInputElement | null>(null);
  const submit = () => {
    const value = text.trim();
    const hasReadyFile = attachments.some((f) => f.status === "ready");
    if ((value || hasReadyFile) && !busy) {
      onSend(value);
      setText("");
    }
  };
  return (
    <>
      {attachments.length > 0 && (
        <div className="attachments">
          {attachments.map((file) => (
            <div className={`attach-chip ${file.status}`} key={file.id} title={file.context?.summary || file.error}>
              <span className="attach-name">{file.name}</span>
              <small>
                {file.status === "parsing" ? "解析中" : file.status === "error" ? file.error || "解析失败" : file.context?.summary || "已解析"}
              </small>
              <button type="button" onClick={() => onRemoveAttachment(file.id)} disabled={busy}>
                ×
              </button>
            </div>
          ))}
        </div>
      )}
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
        <input
          ref={fileRef}
          type="file"
          className="file-input"
          accept=".txt,.md,.csv,.xlsx,.docx,.pdf"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) onAttachFile(file);
            e.currentTarget.value = "";
          }}
        />
        <button
          className="icon-btn attach"
          title="附加行程表"
          type="button"
          disabled={busy}
          onClick={() => fileRef.current?.click()}
        >
          ＋
        </button>
        <button className="icon-btn send" title="发送" onClick={submit} disabled={busy}>
          ↑
        </button>
      </div>
    </>
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
  onAttachFile,
  onRemoveAttachment,
  attachments,
}: {
  items: ChatItem[];
  thinking: string | null;
  streamingId: string | null;
  busy: boolean;
  onSend: (text: string) => void;
  onAttachFile: (file: File) => void;
  onRemoveAttachment: (id: string) => void;
  attachments: AttachedFile[];
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
          onAttachFile={onAttachFile}
          onRemoveAttachment={onRemoveAttachment}
          attachments={attachments}
          busy={busy}
          placeholder="说出你的出行需求，例如「今天有点累，想找个安静的地方待着」…"
        />
      </div>
    </div>
  );
}
