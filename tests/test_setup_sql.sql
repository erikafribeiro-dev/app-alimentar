-- Testes do banco. Cada bloco falha com EXCEPTION se a expectativa não for atendida.
\set ON_ERROR_STOP on
\set QUIET on

-- ------------------------------------------------------------
-- T1. Cadastro de pesquisador com código CERTO (como o GoTrue faz)
-- ------------------------------------------------------------
set role supabase_auth_admin;
insert into auth.users (id, email, raw_user_meta_data)
values ('11111111-1111-1111-1111-111111111111', 'erika@pesquisa.br',
        '{"nome":"Érika","codigo_cadastro":"Pesquisa@2026"}');
reset role;
do $$ begin
  if not exists (select 1 from public.pesquisadores where user_id='11111111-1111-1111-1111-111111111111' and email='erika@pesquisa.br' and nome='Érika')
  then raise exception 'T1 FALHOU: pesquisador não foi registrado pelo gatilho'; end if;
  raise notice 'T1 OK  código certo → pesquisador criado';
end $$;

-- ------------------------------------------------------------
-- T2. Código ERRADO → cadastro recusado, nada gravado
-- ------------------------------------------------------------
do $$ begin
  begin
    set role supabase_auth_admin;
    insert into auth.users (id, email, raw_user_meta_data)
    values ('22222222-2222-2222-2222-222222222222', 'intruso@x.com', '{"codigo_cadastro":"errado"}');
    reset role;
    raise exception 'T2 FALHOU: código errado foi aceito';
  exception when others then
    reset role;
    if sqlerrm not like '%codigo_cadastro invalido%' then raise; end if;
  end;
  if exists (select 1 from auth.users where email='intruso@x.com') then raise exception 'T2 FALHOU: usuário gravado apesar do erro'; end if;
  if exists (select 1 from public.pesquisadores where email='intruso@x.com') then raise exception 'T2 FALHOU: pesquisador gravado'; end if;
  raise notice 'T2 OK  código errado → recusado';
end $$;

-- ------------------------------------------------------------
-- T3. Login anônimo (sem código) → usuário criado, NÃO é pesquisador
-- ------------------------------------------------------------
set role supabase_auth_admin;
insert into auth.users (id, is_anonymous) values ('33333333-3333-3333-3333-333333333333', true);
insert into auth.users (id, is_anonymous) values ('44444444-4444-4444-4444-444444444444', true);
reset role;
do $$ begin
  if exists (select 1 from public.pesquisadores where user_id in ('33333333-3333-3333-3333-333333333333','44444444-4444-4444-4444-444444444444'))
  then raise exception 'T3 FALHOU: anônimo virou pesquisador'; end if;
  raise notice 'T3 OK  anônimo não vira pesquisador';
end $$;

-- ------------------------------------------------------------
-- T4. Usuário criado no painel (sem metadado) → não é pesquisador (precisa do insert manual)
-- ------------------------------------------------------------
set role supabase_auth_admin;
insert into auth.users (id, email) values ('55555555-5555-5555-5555-555555555555', 'painel@x.com');
reset role;
do $$ begin
  if exists (select 1 from public.pesquisadores where user_id='55555555-5555-5555-5555-555555555555') then raise exception 'T4 FALHOU'; end if;
  raise notice 'T4 OK  usuário do painel não é pesquisador automaticamente';
end $$;

-- ------------------------------------------------------------
-- T5. Participante anônimo grava perfil e registro; auth_uid preenchido
-- ------------------------------------------------------------
select test_login('33333333-3333-3333-3333-333333333333', true);
insert into public.participantes (participante_id, nome_responsavel, nome_pessoa, idade, responsavel)
values ('p-ana', 'Ana', 'Leo', 7, 'Mãe');
insert into public.registros (participante_id, participante_nome, refeicao, foto_path)
values ('p-ana', 'Ana', 'Almoço', 'p-ana/2026-09-07/a.jpg');
select test_logout();
do $$ begin
  if (select auth_uid from public.participantes where participante_id='p-ana') <> '33333333-3333-3333-3333-333333333333' then raise exception 'T5 FALHOU: auth_uid perfil'; end if;
  if (select auth_uid from public.registros where participante_id='p-ana') <> '33333333-3333-3333-3333-333333333333' then raise exception 'T5 FALHOU: auth_uid registro'; end if;
  raise notice 'T5 OK  participante grava perfil e registro';
end $$;

-- ------------------------------------------------------------
-- T6. Outro participante anônimo NÃO lê nem edita dados do primeiro
-- ------------------------------------------------------------
select test_login('44444444-4444-4444-4444-444444444444', true);
do $$ declare n int; begin
  select count(*) into n from public.registros; if n <> 0 then raise exception 'T6 FALHOU: leu % registros alheios', n; end if;
  select count(*) into n from public.participantes; if n <> 0 then raise exception 'T6 FALHOU: leu % perfis alheios', n; end if;
  update public.participantes set idade = 99 where participante_id = 'p-ana';
  get diagnostics n = row_count; if n <> 0 then raise exception 'T6 FALHOU: editou perfil alheio'; end if;
  raise notice 'T6 OK  participante não vê nem edita dados de outro';
end $$;
select test_logout();

-- ------------------------------------------------------------
-- T7. Participante lê e edita o PRÓPRIO perfil (upsert do app)
-- ------------------------------------------------------------
select test_login('33333333-3333-3333-3333-333333333333', true);
insert into public.participantes (participante_id, nome_responsavel, nome_pessoa, idade, responsavel)
values ('p-ana', 'Ana', 'Leo', 8, 'Mãe')
on conflict (participante_id) do update set idade = excluded.idade, nome_pessoa = excluded.nome_pessoa;
do $$ declare n int; v int; begin
  select count(*) into n from public.registros; if n <> 1 then raise exception 'T7 FALHOU: deveria ler 1 registro próprio, leu %', n; end if;
  select idade into v from public.participantes where participante_id='p-ana'; if v <> 8 then raise exception 'T7 FALHOU: upsert não atualizou'; end if;
  raise notice 'T7 OK  participante lê/edita o próprio perfil';
end $$;
select test_logout();

-- ------------------------------------------------------------
-- T8. Pesquisador lê tudo; usuário do painel (não autorizado) não lê nada
-- ------------------------------------------------------------
select test_login('11111111-1111-1111-1111-111111111111', false);
do $$ declare n int; begin
  if not public.is_pesquisador() then raise exception 'T8 FALHOU: is_pesquisador() falso'; end if;
  select count(*) into n from public.registros; if n <> 1 then raise exception 'T8 FALHOU: pesquisador leu % registros', n; end if;
  select count(*) into n from public.participantes; if n <> 1 then raise exception 'T8 FALHOU: pesquisador leu % perfis', n; end if;
  raise notice 'T8a OK pesquisador lê tudo';
end $$;
select test_logout();
select test_login('55555555-5555-5555-5555-555555555555', false);
do $$ declare n int; begin
  if public.is_pesquisador() then raise exception 'T8 FALHOU: usuário do painel virou pesquisador'; end if;
  select count(*) into n from public.registros; if n <> 0 then raise exception 'T8 FALHOU: não autorizado leu %', n; end if;
  raise notice 'T8b OK usuário não autorizado não lê nada';
end $$;
select test_logout();

-- ------------------------------------------------------------
-- T9. config_app nunca é legível pela API (anon nem authenticated)
-- ------------------------------------------------------------
select test_login('11111111-1111-1111-1111-111111111111', false);
do $$ declare n int; begin
  select count(*) into n from public.config_app;
  if n <> 0 then raise exception 'T9 FALHOU: código de cadastro legível pela API'; end if;
  raise notice 'T9 OK  config_app invisível pela API';
end $$;
select test_logout();

-- ------------------------------------------------------------
-- T10. RPC definir_codigo_cadastro: só pesquisador; mínimo 6; passa a valer no gatilho
-- ------------------------------------------------------------
select test_login('33333333-3333-3333-3333-333333333333', true);
do $$ begin
  begin perform public.definir_codigo_cadastro('NovoCodigo2027'); raise exception 'T10 FALHOU: anônimo trocou o código';
  exception when others then if sqlerrm not like '%nao autorizado%' then raise; end if; end;
end $$;
select test_logout();
select test_login('11111111-1111-1111-1111-111111111111', false);
do $$ begin
  begin perform public.definir_codigo_cadastro('abc'); raise exception 'T10 FALHOU: aceitou código curto';
  exception when others then if sqlerrm not like '%codigo muito curto%' then raise; end if; end;
  perform public.definir_codigo_cadastro('NovoCodigo2027');
end $$;
select test_logout();
set role supabase_auth_admin;
insert into auth.users (id, email, raw_user_meta_data)
values ('66666666-6666-6666-6666-666666666666', 'novo@pesquisa.br', '{"nome":"Novo","codigo_cadastro":"NovoCodigo2027"}');
reset role;
do $$ begin
  begin
    set role supabase_auth_admin;
    insert into auth.users (id, email, raw_user_meta_data) values ('77777777-7777-7777-7777-777777777777', 'velho@x.com', '{"codigo_cadastro":"Pesquisa@2026"}');
    reset role; raise exception 'T10 FALHOU: código antigo ainda aceito';
  exception when others then reset role; if sqlerrm not like '%codigo_cadastro invalido%' then raise; end if; end;
  if not exists (select 1 from public.pesquisadores where email='novo@pesquisa.br') then raise exception 'T10 FALHOU: novo código não aceito'; end if;
  raise notice 'T10 OK troca de código funciona e vale no gatilho';
end $$;

-- ------------------------------------------------------------
-- T11. Storage: participante faz upload; só pesquisador lista
-- ------------------------------------------------------------
select test_login('33333333-3333-3333-3333-333333333333', true);
insert into storage.objects (bucket_id, name) values ('refeicoes', 'p-ana/2026-09-07/a.jpg');
do $$ declare n int; begin
  begin insert into storage.objects (bucket_id, name) values ('outro', 'x.jpg'); raise exception 'T11 FALHOU: upload em bucket errado aceito';
  exception when others then if sqlerrm not like '%row-level security%' then raise; end if; end;
  select count(*) into n from storage.objects; if n <> 0 then raise exception 'T11 FALHOU: participante listou % objetos', n; end if;
end $$;
select test_logout();
select test_login('11111111-1111-1111-1111-111111111111', false);
do $$ declare n int; begin
  select count(*) into n from storage.objects where bucket_id='refeicoes'; if n <> 1 then raise exception 'T11 FALHOU: pesquisador listou %', n; end if;
  raise notice 'T11 OK storage: upload só em refeicoes; listagem só pesquisador';
end $$;
select test_logout();

-- ------------------------------------------------------------
-- T12. Excluir usuário no painel remove da lista de pesquisadores (on delete cascade)
-- ------------------------------------------------------------
set role supabase_auth_admin;
delete from auth.users where id='66666666-6666-6666-6666-666666666666';
reset role;
do $$ begin
  if exists (select 1 from public.pesquisadores where email='novo@pesquisa.br') then raise exception 'T12 FALHOU'; end if;
  raise notice 'T12 OK excluir usuário remove acesso de pesquisador';
end $$;

\echo


-- ------------------------------------------------------------
-- T13. validar_codigo_cadastro: anon consulta sem expor o código
-- ------------------------------------------------------------
select test_logout();
set role anon;
do $$ begin
  if not public.validar_codigo_cadastro('NovoCodigo2027') then raise exception 'T13 FALHOU: código atual não validado'; end if;
  if public.validar_codigo_cadastro('Pesquisa@2026') then raise exception 'T13 FALHOU: código antigo validado'; end if;
  if public.validar_codigo_cadastro('') then raise exception 'T13 FALHOU: vazio validado'; end if;
  if (select count(*) from public.config_app) <> 0 then raise exception 'T13 FALHOU: anon leu config_app'; end if;
  raise notice 'T13 OK validar_codigo_cadastro responde sem expor o código';
end $$;
reset role;

-- ------------------------------------------------------------
-- T14. supabase_auth_admin tem os privilégios que o gatilho exige
-- ------------------------------------------------------------
do $$ begin
  if not has_function_privilege('supabase_auth_admin','public.tg_novo_usuario()','execute') then raise exception 'T14 FALHOU: sem execute no gatilho'; end if;
  if not has_schema_privilege('supabase_auth_admin','public','usage') then raise exception 'T14 FALHOU: sem usage em public'; end if;
  raise notice 'T14 OK privilégios do serviço de autenticação';
end $$;

\echo '===== TODOS OS TESTES DE BANCO PASSARAM (T1–T14) ====='
