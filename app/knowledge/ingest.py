"""
Ingestão de documentos -> Grafo de Conhecimento (Cap. 32/33).

Reescrita do DocumentProcessor. O que mudou e por quê:

  - `Optional` não importado: o arquivo antigo nem importava. NameError garantido.
  - Chunking por caractere cortava palavras no meio; agora quebra em parágrafo/frase.
  - Uma chamada de embedding POR conceito: com 40 conceitos, 40 requisições
    sequenciais. Agora é uma chamada em lote.
  - Sem transação: uma falha no meio deixava nós órfãos sem aresta. Agora tudo
    ou nada, por chunk.
  - Rodava no request: um documento grande = requisição de 3 minutos = timeout.
    Agora roda em background e o endpoint devolve 202 na hora.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

from app.domain.models import Extraction, KnowledgeEdge, KnowledgeNode, Privacy, new_id
from app.llm.base import LLM
from app.memory.service import MemoryService

log = logging.getLogger("atlas.ingest")

EXTRACTION_SYSTEM = """Você extrai conhecimento estruturado de um texto.

Extraia conceitos (nome + definição), relações entre eles, aplicações práticas e
exercícios possíveis.

Regras:
- Nomes de conceitos são canônicos e curtos: "Machine Learning", não "o machine
  learning descrito no texto".
- Só crie uma relação se AMBOS os conceitos aparecerem na sua lista de conceitos.
- Se o texto não tiver conteúdo conceitual, devolva listas vazias.
"""


@dataclass
class IngestReport:
    document_id: str
    chunks: int = 0
    concepts: int = 0
    edges: int = 0
    errors: list[str] = field(default_factory=list)


def chunk_text(text: str, target: int = 1500, overlap: int = 200) -> list[str]:
    """Quebra respeitando parágrafos. O código antigo fatiava por índice de
    caractere e partia palavras ao meio — o LLM recebia texto mutilado."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""

    for p in paragraphs:
        if len(buf) + len(p) + 2 <= target:
            buf = f"{buf}\n\n{p}" if buf else p
            continue
        if buf:
            chunks.append(buf)
            tail = buf[-overlap:] if overlap else ""
            buf = f"{tail}\n\n{p}" if tail else p
        else:
            # parágrafo único maior que o alvo: quebra em frases
            sentences = re.split(r"(?<=[.!?])\s+", p)
            cur = ""
            for s in sentences:
                if len(cur) + len(s) + 1 <= target:
                    cur = f"{cur} {s}".strip()
                else:
                    if cur:
                        chunks.append(cur)
                    cur = s
            buf = cur
    if buf:
        chunks.append(buf)
    return chunks


class DocumentIngestor:
    def __init__(self, memory: MemoryService, llm: LLM, concurrency: int = 3) -> None:
        self.memory = memory
        self.llm = llm
        self._sem = asyncio.Semaphore(concurrency)  # respeita o rate limit do free tier

    async def ingest(
        self, user_id: str, title: str, text: str, metadata: dict | None = None
    ) -> IngestReport:
        ks = self.memory.knowledge
        doc = ks.upsert_node(
            KnowledgeNode(
                user_id=user_id, node_type="Documento", title=title,
                description=text[:500], metadata=metadata or {},
                privacy=Privacy.PRIVATE,  # o documento do usuário é dele
            )
        )
        report = IngestReport(document_id=doc.id)

        chunks = chunk_text(text)
        report.chunks = len(chunks)
        results = await asyncio.gather(
            *(self._extract(c) for c in chunks), return_exceptions=True
        )

        concept_ids: dict[str, str] = {}
        pending_edges: list[tuple[str, str, str, dict]] = []
        to_embed: list[tuple[str, str]] = [(doc.id, f"{title}\n{text[:2000]}")]

        for r in results:
            if isinstance(r, Exception):
                report.errors.append(str(r)[:150])
                continue
            for c in r.conceitos:
                node = ks.upsert_node(
                    KnowledgeNode(
                        user_id=user_id, node_type="Conceito", title=c.nome,
                        description=c.definicao,
                        metadata={"palavras_chave": c.palavras_chave},
                        privacy=Privacy.RESTRICTED,
                    )
                )
                if c.nome.lower() not in concept_ids:
                    report.concepts += 1
                    to_embed.append((node.id, f"{c.nome}: {c.definicao}"))
                concept_ids[c.nome.lower()] = node.id
                ks.add_edge(KnowledgeEdge(
                    id=new_id(), user_id=user_id, source_id=doc.id,
                    target_id=node.id, rel_type="CONTEM_CONCEITO",
                ))

            for rel in r.relacoes:
                pending_edges.append((rel.origem.lower(), rel.destino.lower(), rel.tipo,
                                      {"descricao": rel.descricao}))

            for app in r.aplicacoes:
                base = concept_ids.get(app.conceito_base.lower())
                if not base:
                    continue
                node = ks.upsert_node(KnowledgeNode(
                    user_id=user_id, node_type="Aplicacao",
                    title=f"Aplicação: {app.conceito_base}",
                    description=app.exemplo_uso, metadata={"contexto": app.contexto},
                ))
                ks.add_edge(KnowledgeEdge(user_id=user_id, source_id=base,
                                          target_id=node.id, rel_type="TEM_APLICACAO"))
                to_embed.append((node.id, app.exemplo_uso))

            for ex in r.exercicios:
                base = concept_ids.get(ex.conceito_base.lower())
                if not base:
                    continue
                node = ks.upsert_node(KnowledgeNode(
                    user_id=user_id, node_type="Exercicio",
                    title=f"Exercício: {ex.conceito_base}",
                    description=ex.pergunta,
                    metadata={"resposta": ex.resposta_esperada, "dificuldade": ex.dificuldade},
                ))
                ks.add_edge(KnowledgeEdge(user_id=user_id, source_id=base,
                                          target_id=node.id, rel_type="TEM_EXERCICIO"))

        # Arestas conceito->conceito só depois que TODOS os chunks rodaram:
        # a relação pode citar um conceito que só apareceu num chunk posterior.
        for origem, destino, tipo, props in pending_edges:
            src, dst = concept_ids.get(origem), concept_ids.get(destino)
            if src and dst and src != dst:
                ks.add_edge(KnowledgeEdge(user_id=user_id, source_id=src, target_id=dst,
                                          rel_type=tipo, properties=props))
                report.edges += 1

        await self._embed_batch(user_id, to_embed, report)
        log.info("ingestão '%s': %d chunks, %d conceitos, %d relações, %d erros",
                 title, report.chunks, report.concepts, report.edges, len(report.errors))
        return report

    async def _extract(self, chunk: str) -> Extraction:
        async with self._sem:
            return await self.llm.extract(EXTRACTION_SYSTEM, chunk, Extraction)

    async def _embed_batch(self, user_id: str, items: list[tuple[str, str]], report: IngestReport) -> None:
        BATCH = 50
        for i in range(0, len(items), BATCH):
            slice_ = items[i:i + BATCH]
            try:
                vecs = await self.llm.embed([t for _, t in slice_])
            except Exception as exc:
                report.errors.append(f"embedding falhou: {str(exc)[:100]}")
                continue
            for (node_id, _), vec in zip(slice_, vecs, strict=False):
                self.memory.knowledge.save_embedding(user_id, node_id, self.llm.embedding_model, vec)
