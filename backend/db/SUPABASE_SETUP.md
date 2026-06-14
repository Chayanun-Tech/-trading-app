# Supabase setup

Project URL:

```text
https://xiblqetehrnprycbkwyp.supabase.co
```

## 1. Create the tables

Open Supabase Dashboard -> SQL Editor, then run:

```sql
-- paste backend/db/schema.sql here
```

The schema creates durable tables for:

- `bot_runs`
- `bot_events`
- `orders`
- `positions`
- `strategies`
- `backtests`
- `watchlists`
- `alerts`
- `app_memory`

## 2. Add the database URL

In Supabase Dashboard:

1. Go to Project Settings.
2. Open Database.
3. Copy the Postgres connection string.
4. Put it in `backend/.env`:

```env
SUPABASE_URL=https://xiblqetehrnprycbkwyp.supabase.co
DATABASE_URL=postgresql://postgres.xiblqetehrnprycbkwyp:YOUR_PASSWORD@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres
```

Do not commit `backend/.env`.

## 3. Install dependency

```powershell
cd backend
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 4. Optional: run schema from your local machine

After `DATABASE_URL` is set, you can run the schema without using SQL Editor:

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\setup_db.py
```

## 5. Restart backend

After restarting, check:

```text
GET /api/db/status
GET /api/health
```

Expected:

```json
{
  "enabled": true,
  "configured": true,
  "supabase_url": "https://xiblqetehrnprycbkwyp.supabase.co"
}
```

## 6. Test app memory

```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/memory" `
  -ContentType "application/json" `
  -Body '{"key":"deploy.note","category":"setup","value":{"message":"Supabase connected"}}'
```

Then:

```text
GET /api/memory?category=setup
```

## Notes

- Without `DATABASE_URL`, the app still works but uses in-memory bot state.
- With `DATABASE_URL`, Auto Trade runs, events, orders, and positions are persisted.
- Keep real trading API keys only in backend environment variables.
