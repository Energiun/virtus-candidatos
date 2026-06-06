# Virtus Exec — Busca de Candidatos LinkedIn

Ferramenta que busca candidatos no LinkedIn via Google e envia a lista por e-mail.

## Como configurar

### 1. Variáveis de ambiente (no Render.com)

Configure estas 3 variáveis no painel do Render:

| Variável | Valor |
|---|---|
| `EMAIL_REMETENTE` | Seu e-mail Gmail (ex: seunome@gmail.com) |
| `EMAIL_SENHA` | Senha de App do Gmail (não a senha normal) |
| `SERPAPI_KEY` | Sua chave da SerpAPI (serpapi.com) |

### 2. Como gerar a Senha de App do Gmail

1. Acesse myaccount.google.com
2. Segurança → Verificação em duas etapas (ative se não tiver)
3. Segurança → Senhas de app
4. Gere uma senha para "Aplicativo: Outro" → nomeie "Virtus"
5. Copie os 16 caracteres gerados

### 3. Como obter a chave SerpAPI

1. Acesse serpapi.com
2. Crie conta gratuita (100 buscas/mês grátis)
3. Copie sua API Key no dashboard

## Deploy no Render.com

1. Suba esta pasta para um repositório GitHub
2. No Render: New → Web Service → conecte o repositório
3. Configure as variáveis de ambiente acima
4. Deploy automático!
