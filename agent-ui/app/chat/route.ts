import { NextResponse } from "next/server";
import type { ChatEvent } from "@/lib/chat-events";
import { AgentEvent, AgentInput, streamAgent } from "@/lib/databricks";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type ChatRequest = {
  sessionId: string;
  message?: string;
  decision?: "approve" | "reject";
};

type ActionRequest = { name: string; args?: Record<string, unknown>; description?: string };

const text = (content: unknown): string => {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((part) => (typeof part === "string" ? part : part?.type === "text" ? (part.text ?? "") : ""))
      .join("");
  }
  return "";
};

/** Translate one agent SSE event into zero or more browser events. */
const translate = (event: AgentEvent): ChatEvent[] => {
  switch (event.type) {
    case "delta": {
      const chunk = text(event.content);
      return chunk ? [{ type: "delta", id: event.id ?? "answer", text: chunk }] : [];
    }
    case "message": {
      const m = event.message;
      if (m.type === "tool") return [{ type: "tool_result", name: m.name ?? "tool" }];
      if (m.type !== "ai") return [];
      if (m.tool_calls?.length) {
        return m.tool_calls.map((c) => ({ type: "tool_call", name: c.name, args: c.args ?? {} }));
      }
      const answer = text(m.content);
      return answer ? [{ type: "message", id: m.id ?? "answer", text: answer }] : [];
    }
    case "interrupt": {
      const requests = (event.value as { action_requests?: ActionRequest[] })?.action_requests ?? [];
      return [
        {
          type: "approval",
          actions: requests.map((a) => ({ name: a.name, args: a.args ?? {}, description: a.description })),
        },
      ];
    }
    case "run.failed":
      return [{ type: "error", message: event.error?.message ?? "The agent run failed." }];
    default:
      return [];
  }
};

/** POST /chat: send a user message (or an approval decision) to the agent and stream its progress back. */
export const POST = async (req: Request) => {
  const body = (await req.json()) as ChatRequest;
  if (!body.sessionId) return NextResponse.json({ error: "sessionId is required" }, { status: 400 });

  let input: AgentInput;
  if (body.decision) {
    // Resuming a paused turn: one decision per pending action (cancel_order is the only gated tool).
    input = { resume: { decisions: [{ type: body.decision }] } };
  } else if (body.message?.trim()) {
    input = { messages: [{ role: "user", content: body.message.trim() }] };
  } else {
    return NextResponse.json({ error: "message or decision is required" }, { status: 400 });
  }

  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start: async (controller) => {
      const send = (event: ChatEvent) => controller.enqueue(encoder.encode(`${JSON.stringify(event)}\n`));
      try {
        await streamAgent(body.sessionId, input, (event) => translate(event).forEach(send), req.signal);
        send({ type: "done" });
      } catch (err) {
        if (!req.signal.aborted) {
          console.error(err);
          send({ type: "error", message: err instanceof Error ? err.message : String(err) });
        }
      } finally {
        controller.close();
      }
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "application/x-ndjson; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      "X-Accel-Buffering": "no",
    },
  });
};
