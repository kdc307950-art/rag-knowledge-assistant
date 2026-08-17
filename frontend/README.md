# React Frontend

React + TypeScript + Vite client for the FastAPI API at `http://127.0.0.1:8000`.

```powershell
npm install
npm run dev
```

The Vite development server proxies `/api` to the local backend. The current M3 surface includes API-key login, diagnostics, SSE chat with stop, batch document upload, task polling, document deletion, and knowledge-base clearing.

```powershell
npm run test
npm run build
npm run lint
```

Keep the backend at one process and one worker. Upload task state and the vector-store write gate are process-local.
