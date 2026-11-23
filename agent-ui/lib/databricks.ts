// Server-only helpers: OAuth machine-to-machine token for a service principal, and streaming agent invocation.

type CachedToken = { value: string; expiresAt: number };
let cached: CachedToken | null = null;

const env = (name: string): string => {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is not set`);
  return value.replace(/\/$/, "");
};

/** Get (and cache) a workspace OAuth token using the service principal's client ID and secret. */
const accessToken = async (): Promise<string> => {
  if (cached && cached.expiresAt > Date.now() + 60_000) return cached.value;

  const basic = Buffer.from(`${env("DATABRICKS_CLIENT_ID")}:${env("DATABRICKS_CLIENT_SECRET")}`).toString(
    "base64",
  );
  const res = await fetch(`${env("DATABRICKS_HOST")}/oidc/v1/token`, {
    method: "POST",
    headers: {
      Authorization: `Basic ${basic}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: new URLSearchParams({ grant_type: "client_credentials", scope: "all-apis" }),
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`OAuth token request failed (${res.status}): ${await res.text()}`);
  const data = (await res.json()) as { access_token: string; expires_in: number };
  cached = { value: data.access_token, expiresAt: Date.now() + data.expires_in * 1000 };
  return data.access_token;
};

export type AgentInput =
  | { messages: { role: "user"; content: string }[] }
  | { resume: { decisions: { type: "approve" | "reject"; message?: string }[] } };

export type AgentMessage = {
  id?: string;
  type: string; // "ai" | "human" | "tool"
  name?: string;
  content?: unknown;
  tool_calls?: { name: string; args: Record<string, unknown> }[];
};

/** Events the agent's /api/invocations streams as SSE (see homefix-agent/runtime/adapter.py). */
export type AgentEvent =
  | { type: "run.started" | "run.completed" }
  | { type: "run.failed"; error?: { message?: string; code?: string } }
  | { type: "delta"; id?: string; content: unknown }
  | { type: "message"; message: AgentMessage }
  | { type: "interrupt"; id: string; value: unknown };

/** Parse one SSE record ("event: ...\ndata: ...") into its JSON data, if any. */
const parseRecord = (record: string): AgentEvent | null => {
  const data = record
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n");
  return data ? (JSON.parse(data) as AgentEvent) : null;
};

/**
 * Invoke the deployed agent with streaming and call `onEvent` for each event as it arrives.
 * Resolves when the stream ends; aborting `signal` cancels the upstream request.
 */
export const streamAgent = async (
  sessionId: string,
  input: AgentInput,
  onEvent: (event: AgentEvent) => void | Promise<void>,
  signal?: AbortSignal,
): Promise<void> => {
  const res = await fetch(`${env("AGENT_APP_URL")}/api/invocations`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${await accessToken()}`,
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      "X-Routing-Key": sessionId, // keep a conversation on one app replica
    },
    body: JSON.stringify({ id: crypto.randomUUID(), session_id: sessionId, stream: true, input }),
    cache: "no-store",
    signal,
  });
  if (res.status === 401) {
    // Databricks Apps answers 401 with an empty body when the principal lacks CAN_USE on the app.
    throw new Error(
      `Agent request failed (401). Check that service principal ${process.env.DATABRICKS_CLIENT_ID} has CAN_USE ` +
        "on the agent app and that DATABRICKS_HOST is the workspace that hosts it.",
    );
  }
  if (!res.ok || !res.body) {
    throw new Error(`Agent request failed (${res.status}): ${(await res.text()).slice(0, 500)}`);
  }

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value.replace(/\r\n/g, "\n");
    // SSE records are separated by a blank line; keep any partial record in the buffer.
    const records = buffer.split("\n\n");
    buffer = records.pop() ?? "";
    for (const record of records) {
      const event = parseRecord(record);
      if (event) await onEvent(event);
    }
  }
  const last = parseRecord(buffer.trim());
  if (last) await onEvent(last);
};
