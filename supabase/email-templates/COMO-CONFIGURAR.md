# E-mails do Diário Alimentar — remetente e templates

## Por que precisa de SMTP próprio
O remetente padrão do Supabase (noreply@mail.app.supabase.io) NÃO pode ser alterado.
Além disso, o serviço padrão só entrega para membros do projeto e tem limite de
poucas mensagens por hora. Para o remetente aparecer como "Diário Alimentar" e
chegar a qualquer pesquisador, é preciso um servidor SMTP próprio. Abaixo, a opção
gratuita mais simples (Resend: 3.000 e-mails/mês grátis).

## Passo 1 — Criar conta no Resend (5 min)
1. https://resend.com → Sign up (pode usar o Google)
2. Menu **API Keys** → **Create API Key** → nome "supabase" → permissão "Sending access" → Create
   → copie a chave (começa com `re_`). Ela só aparece uma vez.

## Passo 2 — Escolher o remetente
Opção A (sem domínio próprio, funciona na hora):
   From: **onboarding@resend.dev**   Sender name: **Diário Alimentar**
   → o e-mail chega como "Diário Alimentar <onboarding@resend.dev>".
   Limitação: só entrega para o e-mail da sua conta Resend. Bom para testar.

Opção B (recomendada para a pesquisa — qualquer destinatário):
   Resend → **Domains** → **Add Domain** → um domínio seu (ex.: um que você já tenha,
   ou um subdomínio como mail.seudominio.com.br) → adicione os registros DNS que o
   Resend mostrar → **Verify**.
   From: **noreply@seudominio.com.br**   Sender name: **Diário Alimentar**
   (Não existe "noreply@diario-alimentar" sem registrar um domínio — e-mails de um
   domínio que não é seu são rejeitados como spam.)

## Passo 3 — Ligar no Supabase
https://supabase.com/dashboard/project/mxjamzrsosraqxvgybwu/settings/auth
   → seção **SMTP Settings** → **Enable Custom SMTP**
   Sender email:   (o From escolhido acima)
   Sender name:    Diário Alimentar
   Host:           smtp.resend.com
   Port:           465
   Username:       resend
   Password:       (a API key re_...)
   → Save

## Passo 4 — Templates (assunto e corpo com a cara do app)
https://supabase.com/dashboard/project/mxjamzrsosraqxvgybwu/auth/templates
   **Reset Password**
      Subject: Diário Alimentar — redefinir sua senha
      Body:    cole o conteúdo de redefinir-senha.html
   **Confirm signup**
      Subject: Diário Alimentar — confirme seu e-mail
      Body:    cole o conteúdo de confirmar-cadastro.html
   → Save em cada um

## Passo 5 — Testar
App → Pesquisador → Esqueci a senha → seu e-mail.
O e-mail deve chegar como "Diário Alimentar", com o botão dourado "Criar nova senha",
e o link deve abrir o app já na tela "Nova senha".
(Pré-requisito já combinado: Authentication → URL Configuration com
 https://erikafribeiro-dev.github.io/app-alimentar/ em Site URL e Redirect URLs.)
