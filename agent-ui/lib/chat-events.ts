/** Events the /chat backend streams to the browser, one JSON object per line (NDJSON). */
export type ChatEvent =
  | { type: "delta"; id: string; text: string } // a token chunk of the answer being written
  | { type: "message"; id: string; text: string } // a complete answer (replaces its deltas)
  | { type: "tool_call"; name: string; args: Record<string, unknown> }
  | { type: "tool_result"; name: string }
  | { type: "approval"; actions: { name: string; args: Record<string, unknown>; description?: string }[] }
  | { type: "error"; message: string }
  | { type: "done" };
