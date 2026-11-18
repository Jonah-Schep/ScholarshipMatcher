# ScholarMatch web app (React + TypeScript)

A custom front end for the ScholarMatch HTTP API (`scholarmatch/api`). It does everything the Streamlit app does:
profile and essays, match runs with live progress, saved scholarships, the advisor chat, and the data & audit page.

## Run it

Start the API from the project root, then the dev server from this folder:

```bash
python -m scholarmatch.api
```

```bash
npm install
npm run dev
```

Open http://localhost:5173. The dev server proxies `/api` to the API on `127.0.0.1:8000`, so there is no CORS setup and
no API address in the code. If you change `SM_API_PORT`, change the target in `vite.config.ts` too.

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Dev server with hot reload (port 5173) |
| `npm run typecheck` | Type-check only |
| `npm run build` | Type-check, then build to `dist/` |
| `npm run gen:api` | Regenerate `src/api/schema.d.ts` from `openapi.json` |

## When the API changes

The TypeScript types come from the API's OpenAPI schema, so a changed route or field shows up as a type error:

```bash
python scripts/export_openapi.py
```

```bash
npm run gen:api
npm run typecheck
```

## Layout

```
src/api/client.ts      typed client (openapi-fetch) + ApiError / unwrap
src/api/schema.d.ts    generated, do not edit
src/Layout.tsx         sidebar, profile picker, loads meta + health once
src/pages/             one file per page (routes in main.tsx: /p/<profile>/<page>)
src/components/        ResultCard
src/lib.ts             useLoad hook, money / amount formatting, list parsing
```

TypeScript is pinned to 5.x: `openapi-typescript` needs TypeScript's JavaScript compiler API, which TypeScript 7 no
longer ships.
