"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import type { ChatEvent } from "@/lib/chat-events";

type Message = { id: string; role: "user" | "assistant" | "error"; text: string; activity?: string[] };
type Approval = Extract<ChatEvent, { type: "approval" }>;

const newId = () => crypto.randomUUID();

const formatArgs = (args: Record<string, unknown>) =>
  Object.entries(args)
    .map(([k, v]) => `${k}: ${JSON.stringify(v)}`)
    .join(", ");

const ChatPage = () => {
  const [sessionId, setSessionId] = useState<string>("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [approval, setApproval] = useState<Approval | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const abort = useRef<AbortController | null>(null);

  // Effects must return nothing or a cleanup function, so these use block bodies
  // (scrollIntoView returns a Promise in newer browsers).
  useEffect(() => {
    setSessionId(newId());
    return () => abort.current?.abort();
  }, []);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy, approval]);

  /** Apply one streamed event to the in-progress assistant message (`turnId`). */
  const apply = (turnId: string, event: ChatEvent) => {
    const update = (fn: (m: Message) => Message) =>
      setMessages((all) => all.map((m) => (m.id === turnId ? fn(m) : m)));

    switch (event.type) {
      case "delta":
        update((m) => ({ ...m, text: m.text + event.text }));
        break;
      case "message":
        // The complete answer replaces whatever deltas streamed for it.
        update((m) => ({ ...m, text: event.text }));
        break;
      case "tool_call":
        update((m) => ({ ...m, activity: [...(m.activity ?? []), `${event.name}(${formatArgs(event.args)})`] }));
        break;
      case "approval":
        setApproval(event);
        break;
      case "error":
        setMessages((all) => [...all, { id: newId(), role: "error", text: event.message }]);
        break;
      default:
        break;
    }
  };

  const send = async (payload: { message?: string; decision?: "approve" | "reject" }) => {
    const turnId = newId();
    const controller = new AbortController();
    abort.current = controller;
    setBusy(true);
    setApproval(null);
    setMessages((all) => [...all, { id: turnId, role: "assistant", text: "" }]);

    try {
      const res = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sessionId, ...payload }),
        signal: controller.signal,
      });
      if (!res.ok || !res.body) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error ?? `Request failed (${res.status})`);
      }

      // Read newline-delimited JSON events as they arrive.
      const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += value;
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        lines.filter(Boolean).forEach((line) => apply(turnId, JSON.parse(line) as ChatEvent));
      }
      if (buffer.trim()) apply(turnId, JSON.parse(buffer) as ChatEvent);
    } catch (err) {
      if (!controller.signal.aborted) {
        setMessages((all) => [
          ...all,
          { id: newId(), role: "error", text: err instanceof Error ? err.message : String(err) },
        ]);
      }
    } finally {
      // Drop the assistant bubble if the turn produced nothing to show (e.g. it only paused for approval).
      setMessages((all) => all.filter((m) => m.id !== turnId || m.text || m.activity?.length));
      setBusy(false);
      if (abort.current === controller) abort.current = null;
    }
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    const text = input.trim();
    if (!text || busy) return;
    setMessages((all) => [...all, { id: newId(), role: "user", text }]);
    setInput("");
    void send({ message: text });
  };

  const decide = (decision: "approve" | "reject") => {
    setMessages((all) => [
      ...all,
      { id: newId(), role: "user", text: decision === "approve" ? "Approved" : "Rejected" },
    ]);
    void send({ decision });
  };

  const stop = () => abort.current?.abort();

  const reset = () => {
    stop();
    setSessionId(newId());
    setMessages([]);
    setApproval(null);
  };

  return (
    <main className="chat">
      <header>
        <h1>HomeFix Assistant</h1>
        <button className="ghost" onClick={reset}>
          New chat
        </button>
      </header>

      <section className="messages">
        {messages.length === 0 && (
          <p className="empty">Ask about an order (try “Where is HF-1002?”) or plan a project.</p>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`bubble ${m.role}`}>
            {m.activity && m.activity.length > 0 && (
              <div className="tools">
                {m.activity.map((a, i) => (
                  <div key={i}>⚙ {a}</div>
                ))}
              </div>
            )}
            {m.text || (m.role === "assistant" && busy ? <span className="typing">Thinking…</span> : null)}
          </div>
        ))}
        {approval && (
          <div className="bubble approval">
            <strong>Approval needed</strong>
            {approval.actions.map((a, i) => (
              <div key={i} className="action">
                {a.name}({formatArgs(a.args)})
              </div>
            ))}
            <div className="actions">
              <button onClick={() => decide("approve")}>Approve</button>
              <button className="ghost" onClick={() => decide("reject")}>
                Reject
              </button>
            </div>
          </div>
        )}
        <div ref={bottom} />
      </section>

      <form onSubmit={onSubmit}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={approval ? "Approve or reject the action above" : "Type a message"}
          disabled={busy || !!approval || !sessionId}
          autoFocus
        />
        {busy ? (
          <button type="button" className="ghost" onClick={stop}>
            Stop
          </button>
        ) : (
          <button type="submit" disabled={!!approval || !input.trim()}>
            Send
          </button>
        )}
      </form>
    </main>
  );
};

export default ChatPage;
