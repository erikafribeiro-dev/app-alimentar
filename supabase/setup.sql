-- ============================================================
-- DIÁRIO ALIMENTAR — Supabase (seguro para rodar mais de uma vez)
-- ============================================================
-- Modelo de acesso:
--   • Participantes entram com login ANÔNIMO + código de acesso do app.
--     Gravam registros e o próprio perfil; leem apenas o que é seu.
--   • Pesquisadores criam conta no app (e-mail + senha + CÓDIGO DE CADASTRO).
--     O código é validado AQUI no banco, por gatilho — nunca no app.
--     Só quem está na tabela `pesquisadores` lê tudo.
-- ============================================================

-- 0) Configuração do app (código de cadastro de pesquisadores)
create table if not exists public.config_app (
  chave text primary key,
  valor text not null
);
alter table public.config_app enable row level security;   -- sem policies: ninguém lê via API
insert into public.config_app (chave, valor)
values ('codigo_cadastro_pesquisador', 'Pesquisa@2026')
on conflict (chave) do nothing;                              -- não sobrescreve se você já trocou

-- 1) Pesquisadores autorizados
create table if not exists public.pesquisadores (
  user_id   uuid primary key references auth.users(id) on delete cascade,
  email     text,
  nome      text,
  criado_em timestamptz not null default now()
);
alter table public.pesquisadores enable row level security;

-- 2) Função: a sessão atual é de um pesquisador autorizado?
create or replace function public.is_pesquisador()
returns boolean language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.pesquisadores where user_id = auth.uid())
$$;

drop policy if exists "pesquisador_ve_lista" on public.pesquisadores;
create policy "pesquisador_ve_lista" on public.pesquisadores
  for select to authenticated using (public.is_pesquisador());

-- 3) Gatilho: ao criar usuário com código de cadastro válido → vira pesquisador.
--    Código errado → o cadastro é recusado.
create or replace function public.tg_novo_usuario()
returns trigger language plpgsql security definer set search_path = public as $$
declare
  codigo   text := new.raw_user_meta_data ->> 'codigo_cadastro';
  esperado text;
begin
  if codigo is null then return new; end if;                -- login anônimo ou usuário criado no painel
  select valor into esperado from public.config_app where chave = 'codigo_cadastro_pesquisador';
  if esperado is null or codigo <> esperado then
    raise exception 'codigo_cadastro invalido';
  end if;
  insert into public.pesquisadores (user_id, email, nome)
  values (new.id, new.email, new.raw_user_meta_data ->> 'nome')
  on conflict (user_id) do nothing;
  return new;
end $$;
drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users for each row execute function public.tg_novo_usuario();
-- O serviço de autenticação (papel supabase_auth_admin) precisa poder executar o gatilho
grant usage on schema public to supabase_auth_admin;
grant execute on function public.tg_novo_usuario() to supabase_auth_admin;
grant select on public.config_app to supabase_auth_admin;
grant insert on public.pesquisadores to supabase_auth_admin;

-- 3b) Pré-validação do código pelo app (mensagem clara antes de criar a conta).
--     Não substitui o gatilho: o gatilho continua sendo a barreira real.
create or replace function public.validar_codigo_cadastro(codigo text)
returns boolean language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.config_app
                 where chave = 'codigo_cadastro_pesquisador' and valor = codigo)
$$;
revoke all on function public.validar_codigo_cadastro(text) from public;
grant execute on function public.validar_codigo_cadastro(text) to anon, authenticated;

-- 4) Pesquisador pode trocar o código de cadastro pelo app
create or replace function public.definir_codigo_cadastro(novo text)
returns void language plpgsql security definer set search_path = public as $$
begin
  if not public.is_pesquisador() then raise exception 'nao autorizado'; end if;
  if length(coalesce(novo,'')) < 6 then raise exception 'codigo muito curto'; end if;
  insert into public.config_app (chave, valor) values ('codigo_cadastro_pesquisador', novo)
  on conflict (chave) do update set valor = excluded.valor;
end $$;
revoke all on function public.definir_codigo_cadastro(text) from public;
grant execute on function public.definir_codigo_cadastro(text) to authenticated;

-- 5) Perfil dos participantes
create table if not exists public.participantes (
  participante_id  text primary key,
  auth_uid         uuid default auth.uid(),
  nome_responsavel text,
  nome_pessoa      text,
  idade            int,
  responsavel      text,
  diagnostico      text,
  restricoes       text,
  contato          text,
  criado_em        timestamptz not null default now(),
  atualizado_em    timestamptz not null default now()
);
alter table public.participantes enable row level security;
drop policy if exists "participante_grava_perfil"   on public.participantes;
drop policy if exists "participante_edita_perfil"   on public.participantes;
drop policy if exists "participante_le_perfil"      on public.participantes;
drop policy if exists "pesquisador_le_perfis"       on public.participantes;
create policy "participante_grava_perfil" on public.participantes
  for insert to authenticated with check (auth_uid = auth.uid());
create policy "participante_edita_perfil" on public.participantes
  for update to authenticated using (auth_uid = auth.uid()) with check (auth_uid = auth.uid());
create policy "participante_le_perfil" on public.participantes
  for select to authenticated using (auth_uid = auth.uid());
create policy "pesquisador_le_perfis" on public.participantes
  for select to authenticated using (public.is_pesquisador());

create or replace function public.tg_touch_atualizado()
returns trigger language plpgsql as $$ begin new.atualizado_em = now(); return new; end $$;
drop trigger if exists participantes_touch on public.participantes;
create trigger participantes_touch before update on public.participantes
  for each row execute function public.tg_touch_atualizado();

-- 6) Registros (fotos, áudios, comentários)
create table if not exists public.registros (
  id                bigint generated always as identity primary key,
  participante_id   text not null,
  participante_nome text not null,
  refeicao          text not null,
  foto_path         text not null,
  audio_path        text,
  comentario        text,
  auth_uid          uuid default auth.uid(),
  criado_em         timestamptz not null default now()
);
alter table public.registros add column if not exists audio_path text;
alter table public.registros add column if not exists comentario text;
alter table public.registros add column if not exists auth_uid uuid default auth.uid();

create index if not exists idx_registros_participante on public.registros (participante_id);
create index if not exists idx_registros_criado_em    on public.registros (criado_em desc);
create index if not exists idx_registros_auth_uid     on public.registros (auth_uid);

alter table public.registros enable row level security;
drop policy if exists "inserir_registros"         on public.registros;
drop policy if exists "ler_registros"             on public.registros;
drop policy if exists "ler_proprios_registros"    on public.registros;
drop policy if exists "ler_registros_pesquisador" on public.registros;
create policy "inserir_registros" on public.registros
  for insert to authenticated with check (auth_uid = auth.uid());
create policy "ler_proprios_registros" on public.registros
  for select to authenticated using (auth_uid = auth.uid());
create policy "ler_registros_pesquisador" on public.registros
  for select to authenticated using (public.is_pesquisador());

-- 7) Bucket "refeicoes" — público para servir fotos/áudios por URL direta (IDs aleatórios);
--    listar o conteúdo só é possível para pesquisador.
insert into storage.buckets (id, name, public)
values ('refeicoes', 'refeicoes', true)
on conflict (id) do update set public = true;
drop policy if exists "upload_fotos" on storage.objects;
drop policy if exists "ler_fotos"    on storage.objects;
create policy "upload_fotos" on storage.objects
  for insert to authenticated with check (bucket_id = 'refeicoes');
create policy "ler_fotos" on storage.objects
  for select to authenticated using (bucket_id = 'refeicoes' and public.is_pesquisador());

-- ============================================================
-- OPCIONAL — se você já criou um usuário manualmente no painel
-- (Authentication → Users), autorize-o como pesquisador assim:
-- insert into public.pesquisadores (user_id, email)
--   select id, email from auth.users where email = 'seu@email.com'
--   on conflict do nothing;
-- ============================================================
