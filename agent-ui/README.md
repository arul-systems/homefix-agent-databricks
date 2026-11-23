# agent-ui

A minimal Next.js chat app for the HomeFix agent deployed on Databricks (`agent-bricks-list`).

- `app/page.tsx`: chat window (client). It keeps one `sessionId` per conversation; "New chat" starts a new one.
- `app/chat/route.ts`: `POST /chat` backend. It takes `{sessionId, message}` or `{sessionId, decision: "approve" | "reject"}`
  and returns `{status, reply, tools, approval}`.
- `lib/databricks.ts`: gets an OAuth token for a service principal (client ID and secret,
  `client_credentials` grant against `<host>/oidc/v1/token`), caches it until shortly before expiry, and calls the agent's
  `POST /api/invocations`.

The credentials stay on the server; the browser only talks to `/chat`.

## Setup

1. Create (or reuse) a service principal and an OAuth secret for it (workspace **Settings → Identity and access →
   Service principals → Secrets**).
2. Give it `CAN_USE` on the agent app:

   ```bash
   databricks apps update-permissions agent-bricks-list --profile agent-dev \
     --json '{"access_control_list":[{"service_principal_name":"<client-id>","permission_level":"CAN_USE"}]}'
   ```

3. Configure and run:

   ```bash
   cp .env.example .env.local   # fill in DATABRICKS_CLIENT_ID and DATABRICKS_CLIENT_SECRET
   npm install
   npm run dev                  # http://localhost:3000
   ```

## Approvals

`cancel_order` pauses for human approval. The backend returns `approval` with the pending action, and the UI shows
Approve and Reject buttons, which resume the same session with `{"resume": {"decisions": [...]}}`.
