# Diário Alimentar

PWA para coleta de fotos, áudios e comentários de refeições em pesquisa sobre alimentação de pessoas autistas.

**App:** https://erikafribeiro-dev.github.io/app-alimentar/

## Estrutura
| Caminho | O que é |
|---|---|
| `index.html` | O app inteiro (arquivo único, publicado pelo GitHub Pages) |
| `sw.js` | Service worker (rede primeiro; limpa caches antigos) |
| `supabase/setup.sql` | Banco: tabelas, regras de acesso, gatilho de cadastro de pesquisador (idempotente) |
| `supabase/email-templates/` | Assunto e corpo dos e-mails de redefinição de senha e confirmação |
| `tests/test_diario_alimentar.py` | 123 testes do app (Playwright + pytest, Supabase simulado) |
| `tests/test_setup_sql.sql` | 14 testes do banco num PostgreSQL local emulando o Supabase |

## Acessos
| Quem | Credencial | Onde trocar |
|---|---|---|
| Participantes | código `DIARIO2026` | `DEFAULT_ACCESS_CODE` no `index.html` |
| Cadastro de pesquisador | código `Pesquisa@2026` | App → Pesquisador → Configuração → "Trocar código" (fica só no banco) |
| Pesquisadores | e-mail + senha (Supabase Auth); "Esqueci a senha" no app | Supabase → Authentication → Users |

## Supabase (configuração única)
1. SQL Editor → cole `supabase/setup.sql` → Run
2. Authentication → Sign In / Providers: **Anonymous** ativado; **Email** com *Confirm email* desmarcado
3. Authentication → URL Configuration: Site URL e Redirect URLs = `https://erikafribeiro-dev.github.io/app-alimentar/`
4. Authentication → Email Templates: assunto e corpo de `supabase/email-templates/`
5. (Opcional) Project Settings → Auth → SMTP: remetente "Diário Alimentar" — ver `supabase/email-templates/COMO-CONFIGURAR.md`

## Publicar uma atualização
Suba o novo `index.html` na raiz do repositório (branch `main`). O GitHub Pages atualiza em 1–2 min.

## Rodar os testes
```bash
pip install pytest playwright && playwright install chromium
pytest tests/test_diario_alimentar.py -v
# banco (precisa de psql apontando para um Postgres local):
tests/rodar_testes_sql.sh
```
