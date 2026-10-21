# Sonar Reply

Responde comentários do Instagram e envia uma DM apresentando o grupo.
Fluxo: **comentário → resposta pública (IA) → DM com pergunta → pessoa responde → link do grupo**.

## Segurança por padrão

- `DRY_RUN=true`: não chama a Meta, só simula e registra. Use para testar tudo.
- `APPROVAL_MODE=true`: nada é publicado sem você aprovar no painel.
- Limite de respostas por hora, resposta pública sem links, nunca responde à própria conta.
- Painel protegido por senha (`ADMIN_PASSWORD`). **Troque a senha e nunca suba o `.env` para o GitHub.**

## Rodar localmente

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # edite o .env (pelo menos ADMIN_PASSWORD)
python -m pytest              # 20 testes, sem usar a Meta nem a IA
uvicorn app.main:app --reload   # o .env é lido automaticamente
```

Abra http://localhost:8000 (usuário `admin`, senha = `ADMIN_PASSWORD`).

Para simular um comentário sem a Meta (modo teste):

```bash
curl -X POST localhost:8000/webhook -H 'Content-Type: application/json' -d '{"entry":[{"id":"1","changes":[{"field":"comments","value":{"id":"c1","text":"Que fofo!","from":{"id":"111","username":"ana"},"media":{"id":"m1"}}}]}]}'
```

(sem `META_APP_SECRET`, o webhook só aceita chamadas sem assinatura enquanto `DRY_RUN=true`.)

## Como personalizar

- **Regras, textos da DM e prompt da IA:** `rules.yaml` ou a página "Regras" do painel.
- **IA:** `AI_BASE_URL`, `AI_API_KEY` e `AI_MODEL` no `.env`. Qualquer API no formato
  OpenAI `/chat/completions` funciona (Groq, OpenRouter, Gemini no modo compatível...).
  Se a IA falhar ou estiver sem chave, o sistema usa os textos de reserva (`public_replies`).
- **Link do grupo:** `GROUP_LINK`.

## Ligar na Meta (passo a passo resumido)

1. Conta Instagram **profissional** da sonar_pet, ligada a uma Página do Facebook.
2. Em [Meta for Developers](https://developers.facebook.com), crie um app e adicione o produto
   Instagram. Em modo de desenvolvimento, com a sua própria conta, não precisa de revisão do app.
3. Permissões: comentários e mensagens (ex.: `instagram_business_basic`,
   `instagram_business_manage_comments`, `instagram_business_manage_messages`).
4. Gere o token de acesso e preencha `IG_ACCOUNT_ID`, `IG_ACCESS_TOKEN` e `META_APP_SECRET` no `.env`.
5. Publique o servidor (veja abaixo) e cadastre o webhook em `https://SEU-ENDERECO/webhook`
   com o `META_VERIFY_TOKEN`; assine os campos **comments** e **messages**.
6. Teste com `DRY_RUN=true` e depois `DRY_RUN=false` mantendo `APPROVAL_MODE=true`.
   Só desligue a aprovação quando confiar no tom das respostas.

> **Confirme na documentação oficial da Meta** os nomes das permissões, a versão da API
> (`GRAPH_VERSION`) e os endpoints em `app/meta.py`: a Meta muda isso com frequência.
> Regras da API: uma única DM por comentário (até 7 dias); novas mensagens só depois que a
> pessoa responde, em até 24 horas.

## Publicar

Precisa de uma URL pública com HTTPS. O `Dockerfile` serve para Render, Railway, Fly.io etc.
Monte um volume persistente em `/srv/data` (senão o banco apaga a cada deploy) e use um plano
que não "durma" por inatividade, ou a Meta perde eventos.

## Estrutura

```
app/main.py      webhook (assinatura HMAC) e painel
app/service.py   fluxo comentário -> DM -> link
app/rules.py     regras, sanitização, opt-out
app/ai.py        chamada à IA (trocável por .env)
app/meta.py      chamadas à API do Instagram
app/db.py        SQLite
tests/           testes automatizados
```

## Limitações conhecidas

- Curtir comentários não consta na documentação da API e não está implementado.
- Funciona para uma conta; várias contas e a revisão de app da Meta ficam para uma próxima fase.
