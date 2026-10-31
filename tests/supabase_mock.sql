-- Emula o que o Supabase fornece de fábrica, para testar setup.sql num Postgres comum.
do $$ begin
  if not exists (select 1 from pg_roles where rolname='anon') then create role anon nologin; end if;
  if not exists (select 1 from pg_roles where rolname='authenticated') then create role authenticated nologin; end if;
  if not exists (select 1 from pg_roles where rolname='service_role') then create role service_role nologin bypassrls; end if;
  if not exists (select 1 from pg_roles where rolname='supabase_auth_admin') then create role supabase_auth_admin nologin; end if;  -- GoTrue grava em auth.users com este papel
end $$;

create schema if not exists auth;
create schema if not exists storage;
grant usage on schema public, auth, storage to anon, authenticated, supabase_auth_admin;
-- No Supabase, funções criadas em public são executáveis por anon/authenticated por padrão
alter default privileges for role postgres in schema public grant execute on functions to anon, authenticated;
alter default privileges for role postgres in schema public grant all on tables to anon, authenticated;

create table auth.users (
  id uuid primary key default gen_random_uuid(),
  email text,
  raw_user_meta_data jsonb default '{}'::jsonb,
  is_anonymous boolean default false,
  created_at timestamptz default now()
);
grant all on auth.users to supabase_auth_admin;
-- GoTrue tem privilégio de criar triggers? Não; o trigger é criado pelo postgres no SQL Editor.

create table storage.buckets (id text primary key, name text, public boolean default false);
create table storage.objects (id uuid default gen_random_uuid() primary key, bucket_id text, name text, owner uuid);
alter table storage.objects enable row level security;
grant all on storage.buckets, storage.objects to anon, authenticated;

-- auth.uid() / auth.jwt() leem as claims da sessão, como no Supabase (via request.jwt.claims)
create or replace function auth.jwt() returns jsonb language sql stable as $$
  select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb
$$;
create or replace function auth.uid() returns uuid language sql stable as $$
  select nullif(auth.jwt() ->> 'sub', '')::uuid
$$;
grant execute on function auth.jwt(), auth.uid() to anon, authenticated, supabase_auth_admin;

-- Helper de teste: "logar" como um usuário (define as claims da sessão)
create or replace function test_login(p_uid uuid, p_anon boolean) returns void language plpgsql as $$
begin
  perform set_config('request.jwt.claims',
    json_build_object('sub', p_uid, 'role', 'authenticated', 'is_anonymous', p_anon)::text, false);
  perform set_config('role', 'authenticated', false);
end $$;
create or replace function test_logout() returns void language plpgsql as $$
begin
  perform set_config('request.jwt.claims', '', false);
  perform set_config('role', 'postgres', false);
end $$;
