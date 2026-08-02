# Atlas / Ayra — Fase 1

Plataforma de inteligência: a **Ayra** é o ponto único de entrada; o **Atlas**
conecta memória, jornadas, conhecimento e o Domínio Financeiro.

## Rodar em 3 minutos

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # preencha ATLAS_GEMINI_API_KEY (ou use ATLAS_LLM_PROVIDER=fake)
uvicorn app.main:app --reload
```

| Endereço | O que é |
|---|---|
| http://localhost:8000 | **plataforma Atlas** (Ayra, jornadas, memória, biblioteca, finanças) |
| http://localhost:8000/docs | API inteira, testável no navegador |
| http://localhost:8000/health | checagem rápida |

Testes (não precisam de rede nem de chave): `python -m pytest tests/ -v`

O `.env.example` já vem com `ATLAS_LLM_PROVIDER=fake`: o sistema inteiro roda sem chave e
sem internet. Use isso para desenvolver memória, grafo e API sem queimar cota do free tier.
Para usar o Gemini de verdade, troque para `gemini` e cole a chave do AI Studio — se ela
estiver faltando ou com cara de placeholder, **o servidor se recusa a subir**, em vez de
quebrar só quando você já estiver esperando uma resposta na tela.

## Estrutura

```
app/
  core/       infraestrutura: config, banco, schema.sql, auth
  domain/     modelos (Pydantic) — memória, jornadas, finanças
  memory/     as 4 camadas + busca híbrida + fachada MemoryService
  finance/    Domínio Financeiro (contas, lançamentos, metas, saúde)
  llm/        contrato + adaptador Gemini + adaptador fake
  knowledge/  ingestão de documentos -> grafo
  ayra/       orquestração: contexto, prompt, consolidação, planejamento
  api/        HTTP: chat (SSE), memória, jornadas, conhecimento, finanças
web/          plataforma Atlas (Ayra no centro)
tests/        integração do núcleo + finanças
```

Regra de dependência: **cada camada só importa a de baixo**. `memory/` não sabe que existe HTTP.
É isso que permite plugar depois um CLI, um bot de WhatsApp ou um app mobile no mesmo núcleo.

## Endpoints

| Método | Rota | Para quê |
|---|---|---|
| POST | `/chat` | conversa com streaming SSE |
| GET | `/chat/{sid}/history` | histórico da sessão |
| DELETE | `/chat/{sid}` | descartar a conversa (Cap. 30) |
| GET/POST/PATCH/DELETE | `/memory/personal` | ver, criar, editar, remover memória (Cap. 128) |
| GET | `/memory/audit` | o que a Ayra leu e por quê (Cap. 127) |
| GET | `/memory/export` | levar tudo embora, em JSON (Cap. 31) |
| POST | `/memory/wipe` | apagar tudo, de verdade |
| POST | `/journeys` | criar jornada |
| POST | `/journeys/{id}/diagnose` | descobrir o objetivo real (Cap. 20) |
| POST | `/journeys/{id}/plan` | Ayra planeja diagnóstico + passos (Cap. 20) |
| POST | `/journeys/{id}/steps` | passos da jornada |
| POST | `/knowledge/ingest` | ingerir documento (roda em background) |
| GET | `/knowledge/search` | busca híbrida (léxica + semântica) |
| GET | `/knowledge/{id}/neighbors` | percorrer o grafo |
| GET | `/finance/health` | indicadores de saúde financeira (Cap. 92) |
| GET/POST/DELETE | `/finance/accounts` | contas |
| GET/POST | `/finance/transactions` | lançamentos |
| GET/POST/PATCH/DELETE | `/finance/goals` | metas financeiras |

## O que a Fase 1 entregou

- **Plataforma web** com Início, Ayra, Jornadas, Estudos, Finanças, Gabinete, Memória e Biblioteca
- **Onboarding em 60s** (`POST /chat/onboard`) — objetivo → jornada planejada → sessão
- **Chat amarrado à jornada** (`journey_id` no `/chat`)
- **Domínio Educação** — estudo GERAL (matemática, direito, medicina, programação, concursos, idiomas…)
  - Capítulos + quiz + caderno + competências
  - Revisão espaçada (SM-2), plano semanal, progresso visual, simulado multi-capítulo
  - Material da trilha com OCR de PDF escaneado (Gemini)
- **Domínio Financeiro**: contas, lançamentos, metas e saúde (Cap. 82–92)
  - Relatório mensal por categoria, transferência real entre contas
  - Consultoria com Ayra (`POST /finance/start-with-ayra`)
- **Domínio Gabinete**: cidadãos, demandas, linha do tempo e agenda (Cap. 97–105)
  - Dossiê do cidadão, agenda com status, atrasadas, assessoria Ayra
- **Biblioteca com PDF** (`.txt`, `.md`, `.pdf` via pypdf + OCR)
  - Lista de nós, vizinhos, apagar, OCR na ingestão
- **Início acionável** — **Ayra do dia seguinte** (1 foco/dia) + alertas
- **Jornadas** — pausar/concluir, definir objetivo real
- **Dashboard, auditoria, export e wipe** na UI (Caps. 31 / 127)
- Planejamento de jornada pela Ayra (`POST /journeys/{id}/plan`)

## Próximos passos

1. Usar de verdade e ajustar o que a Ayra erra no dia a dia.
2. Notificação matinal (e-mail/push) do briefing do dia.
3. Auth multi-usuário (JWT) quando sair do uso single-user.
4. Postgres + pgvector quando o SQLite apertar.
5. Orçamentos recorrentes / envelopes (finanças) se precisar.

## Decisões que valem defender

- **SQLite antes de Postgres.** Zero infraestrutura, um arquivo, backup é `cp`.
- **REST puro no lugar do SDK do Gemini.** Trocar de modelo = um arquivo.
- **Busca vetorial em numpy, força bruta.** Até ~50 mil nós, milissegundos.
- **`user_id` sempre vem do token.** Nenhuma rota aceita `user_id` no corpo.
- **Domínios independentes** (Cap. 116): finanças evolui sem reescrever memória.
