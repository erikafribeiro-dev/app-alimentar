# Changelog

## v2.2 — 2026-10-30
### Novidades
- **Esqueci a senha** (pesquisador): link por e-mail → tela "Nova senha" → entra no painel
- **Esqueci o código / não acho meu diário** (participante): orientação na tela de entrada
- **Cadastro de pesquisador** com código validado no servidor (trocável pelo app)
- **Perfil do participante** no primeiro acesso (nome, idade, responsável, diagnóstico, restrições, contato)
- **Áudio** como descrição da refeição (compatível com iPhone) + comentário em texto
- Painel do pesquisador com perfil, comentário, player de áudio e CSV completo
- Service worker próprio (rede primeiro) — substitui o da Mesada que servia versão antiga
- Regras de acesso no banco: participante lê só o próprio; só pesquisador autorizado lê tudo
- Templates de e-mail no visual do app

### Correções
- Sair/entrar com o mesmo nome restaura a sessão (perfil e histórico não somem)
- Sessão expirada do pesquisador volta ao login em vez de "Erro ao carregar"
- Mensagens claras para erros do Supabase + botão "Verificar instalação"

### Qualidade
123 testes do app (Playwright) + 14 testes do banco (PostgreSQL emulando Supabase).

## v2.0 — 2026-10-12
- Migração completa para Supabase (fotos, áudios, comentários); Google Drive removido
- Área do pesquisador com login por e-mail/senha; Config dentro da área protegida

## v1.0 — 2026-09-04
- Primeira versão: fotos por refeição, arco do dia, painel básico, GitHub Pages
