# Deployment guide

Recommended production layout:

```text
GitHub repo
  ├─ GitHub Pages -> frontend/
  ├─ Render       -> backend/ FastAPI + WebSocket
  └─ Supabase     -> Postgres persistence
```

## 1. Supabase

1. Open your Supabase project:

```text
https://xiblqetehrnprycbkwyp.supabase.co
```

2. Run `backend/db/schema.sql` in SQL Editor.
3. Copy the Postgres connection string from Project Settings -> Database.
4. Put it into Render as `DATABASE_URL`.

Do not commit the database password.

## 2. Render backend

This repo includes `render.yaml`.

In Render:

1. New -> Blueprint.
2. Connect this GitHub repository.
3. Render reads `render.yaml`.
4. Set these secret environment variables:

```text
DATABASE_URL
GEMINI_API_KEY
GROQ_API_KEY
TRADINGVIEW_WEBHOOK_SECRET
BITKUB_API_KEY
BITKUB_API_SECRET
```

Keep `BITKUB_REAL_TRADING_ENABLED=false` until real trading is explicitly approved.

After deploy, verify:

```text
https://YOUR-RENDER-SERVICE.onrender.com/api/health
https://YOUR-RENDER-SERVICE.onrender.com/api/db/status
```

## 3. GitHub Pages frontend

This repo includes `.github/workflows/pages.yml`.

In GitHub:

1. Repo -> Settings -> Pages.
2. Source: GitHub Actions.
3. Repo -> Settings -> Secrets and variables -> Actions -> Variables.
4. Add repository variable:

```text
API_BASE_URL=https://YOUR-RENDER-SERVICE.onrender.com
```

On push to `main`, GitHub Actions writes `frontend/config.js` and deploys the static frontend.

The frontend can now call the hosted backend using `window.API_BASE_URL`.

## 4. Local development

Keep `frontend/config.js` as:

```js
window.API_BASE_URL = window.API_BASE_URL || "";
```

This makes the local frontend call the same local FastAPI host.
