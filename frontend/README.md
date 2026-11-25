# HaqqAI frontend

Next.js (App Router) UI for the working prototype. It calls the FastAPI service in
`../backend/api`, which wraps the C4 pipeline (NPCE + CoVe).

## Run it

1. Start Postgres: `docker start financial_rag_postgres`
2. Start the API, from `backend/`:
   ```
   pip install -e ".[api]"
   python -m uvicorn api.server:app --host 127.0.0.1 --port 8000
   ```
   It takes about 40 seconds to load the models and index. `OPENAI_API_KEY` must be set.
3. Start the UI, from `frontend/`:
   ```
   npm install
   npm run dev
   ```
   Open the URL it prints. If port 3000 is busy it uses 3001.

The UI reaches the API through `src/app/api/*` route handlers. Point it somewhere else with the
`BACKEND_URL` environment variable (default `http://127.0.0.1:8000`).

## Notes

- The npm scripts call `node` directly on purpose. This repo lives in a folder with an `&` in its
  name (`R&D`), which breaks bare commands like `next dev` under `npm run` on Windows.
- Answers can take one to two minutes: the pipeline re-embeds every candidate fact at query time.
  `HAQQAI_TORCH_THREADS` (default 8) controls the embedding thread pool.
- Checks: `npm run lint`, `npm run typecheck`, `npm run build`.
