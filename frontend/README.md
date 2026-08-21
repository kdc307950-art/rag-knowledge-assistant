# React Frontend

> Document version: `0.1`
> Applies to application version: `0.1.0`
> Last reviewed: `2026-08-21`

React + TypeScript + Vite client for the FastAPI API at `http://127.0.0.1:8000`.

```powershell
npm install
npm run dev
```

The Vite development server proxies `/api` to the local backend. In multi-user mode the browser uses the backend's `HttpOnly` session cookie; it does not persist or inject API keys or Bearer tokens. The current frontend includes username/password login with `/auth/me` session restoration and `/auth/logout`, role-aware diagnostics, SSE chat with stop, source tracing, explicit chat error states, G2 general answers, knowledge-grounded drafting, debug evidence, batch document upload, task polling, document deletion, and knowledge-base clearing.

```powershell
npm run test
npm run build
npm run lint
```

Keep the backend at one process and one worker. Upload task state and the vector-store write gate are process-local.
