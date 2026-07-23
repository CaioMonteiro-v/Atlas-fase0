# Atlas / Ayra — Fase 0

Núcleo de memória portátil, do usuário, independente de modelo.

## Rodar em 3 minutos

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # preencha ATLAS_GEMINI_API_KEY (ou use ATLAS_LLM_PROVIDER=fake)
uvicorn app.main:app --reload
```

| Endereço | O que é |
|---|---|
| http://localhost:8000 | **console da Ayra** (é aqui que você conversa) |
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
  domain/     modelos (Pydantic) — a única definição de "o que é uma memória"
  memory/     as 4 camadas + busca híbrida + fachada MemoryService
  llm/        contrato + adaptador Gemini + adaptador fake
  knowledge/  ingestão de documentos -> grafo
  ayra/       orquestração: contexto, prompt, consolidação
  api/        HTTP: chat (SSE), memória, jornadas, conhecimento
web/          console mínimo para testar o streaming
tests/        teste de integração do núcleo
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
| POST | `/journeys/{id}/steps` | passos da jornada |
| POST | `/knowledge/ingest` | ingerir documento (roda em background) |
| GET | `/knowledge/search` | busca híbrida (léxica + semântica) |
| GET | `/knowledge/{id}/neighbors` | percorrer o grafo |

## De onde veio cada coisa

| Código antigo | Agora | Motivo |
|---|---|---|
| `memory/models.py` (to_dict/from_dict manuais) | `domain/models.py` (Pydantic) | o `from_dict` estourava `TypeError` em toda subclasse |
| `memory/implementations.py` (InMemory*) | `memory/store.py` (SQLite) | tudo era perdido a cada restart |
| `memory/memory_manager.py` | `memory/service.py` | ganhou `build_context()`, que é o que faz o produto existir |
| `llm/openai_adapter.py` (mock embutido) | `llm/gemini.py` + `llm/fake.py` | mock nunca mora dentro do adaptador de produção |
| `knowledge/document_processor.py` | `knowledge/ingest.py` | não importava `Optional` (NameError), chamava embedding 1 a 1, sem transação |
| — | `app/api/*` | não existia camada web nenhuma |
| — | jornadas | eram "a unidade central do Atlas" no PDF e não existiam no código |

## Próximos passos, em ordem

1. **Ligar a chave do Gemini e conversar de verdade.** Ver a resposta em streaming
   com as fontes ao lado é o teste de fumaça do produto inteiro.
2. **Ingerir 5 documentos seus** e conferir o grafo em `/knowledge/search`.
   Se a busca híbrida devolve o que faz sentido, o núcleo funciona.
3. **Frontend real** (React/Vite ou htmx). O `web/index.html` é só um console de teste.
4. **Módulo Financeiro** como primeiro domínio — ele exercita jornada + memória
   pessoal + conhecimento ao mesmo tempo, e é o que valida se a arquitetura escala
   para o segundo domínio sem reescrita.
5. **Postgres + pgvector** quando o SQLite apertar. O `store.py` é a única coisa que muda.

## Decisões que valem defender

- **SQLite antes de Postgres.** Zero infraestrutura, um arquivo, backup é `cp`. O schema
  foi escrito para migrar sem reescrita.
- **REST puro no lugar do SDK do Gemini.** Trocar de modelo = escrever um arquivo de 150
  linhas. Com SDK, é refatorar o projeto.
- **Busca vetorial em numpy, força bruta.** Até ~50 mil nós, um scan leva milissegundos.
  Trocar por `sqlite-vec` depois muda só `memory/vector.py`.
- **`user_id` sempre vem do token.** Nenhuma rota aceita `user_id` no corpo. Se aceitasse,
  qualquer pessoa leria a memória de qualquer outra.
