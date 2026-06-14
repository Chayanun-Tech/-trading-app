-- Supabase/Postgres schema for AI Trade Assistant.
-- Run this in Supabase SQL Editor before setting DATABASE_URL in backend/.env.

create extension if not exists pgcrypto;

create table if not exists bot_runs (
  id uuid primary key,
  exchange text not null,
  mode text not null check (mode in ('paper', 'real')),
  symbol text not null,
  timeframe text not null,
  config jsonb not null default '{}'::jsonb,
  status text not null default 'running',
  started_at timestamptz not null,
  stopped_at timestamptz,
  realized_pnl_thb numeric not null default 0,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists bot_events (
  id bigserial primary key,
  run_id uuid references bot_runs(id) on delete set null,
  ts timestamptz not null,
  level text not null,
  message text not null,
  data jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists orders (
  id uuid primary key,
  run_id uuid references bot_runs(id) on delete set null,
  ts timestamptz not null,
  mode text not null,
  exchange text not null,
  symbol text not null,
  side text not null,
  order_type text,
  price numeric,
  qty numeric,
  notional_thb numeric,
  status text,
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists positions (
  id uuid primary key,
  run_id uuid references bot_runs(id) on delete set null,
  status text not null,
  mode text not null,
  symbol text not null,
  side text not null,
  entry numeric,
  qty numeric,
  stop_loss numeric,
  take_profit numeric,
  opened_at timestamptz not null,
  closed_at timestamptz,
  last_price numeric,
  unrealized_pnl_thb numeric,
  realized_pnl_thb numeric,
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists strategies (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  symbol text,
  timeframe text,
  config jsonb not null default '{}'::jsonb,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists backtests (
  id uuid primary key default gen_random_uuid(),
  strategy_id uuid references strategies(id) on delete set null,
  symbol text not null,
  timeframe text not null,
  params jsonb not null default '{}'::jsonb,
  stats jsonb not null default '{}'::jsonb,
  result jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists watchlists (
  id uuid primary key default gen_random_uuid(),
  name text not null default 'default',
  symbol text not null,
  market text,
  note text,
  created_at timestamptz not null default now(),
  unique (name, symbol)
);

create table if not exists alerts (
  id uuid primary key default gen_random_uuid(),
  symbol text not null,
  kind text not null,
  value numeric not null,
  is_active boolean not null default true,
  last_triggered_at timestamptz,
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists app_memory (
  key text primary key,
  category text not null default 'general',
  value jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_bot_events_run_ts on bot_events(run_id, ts desc);
create index if not exists idx_orders_symbol_ts on orders(symbol, ts desc);
create index if not exists idx_positions_symbol_status on positions(symbol, status);
create index if not exists idx_backtests_symbol_tf on backtests(symbol, timeframe, created_at desc);
create index if not exists idx_app_memory_category on app_memory(category, updated_at desc);
