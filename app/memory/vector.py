"""
Vetores: serialização e busca por similaridade.

Estratégia deliberada para a Fase 0: força bruta em numpy.
Com até ~50k nós, um scan completo leva poucos milissegundos e não exige
nenhuma extensão nativa nem serviço externo. Quando isso deixar de valer,
troca-se APENAS este arquivo por sqlite-vec (local) ou pgvector (Postgres) —
a interface `search()` não muda. É o Cap. 119 (independência tecnológica)
aplicado na prática, e não na retórica.
"""

from __future__ import annotations

import numpy as np

DTYPE = np.float32


def to_blob(vector: list[float]) -> bytes:
    """Normaliza em L2 e serializa. Normalizar na escrita faz a busca virar
    um produto escalar puro — sem divisão por norma a cada consulta."""
    arr = np.asarray(vector, dtype=DTYPE)
    norm = float(np.linalg.norm(arr))
    if norm > 0:
        arr = arr / norm
    return arr.astype(DTYPE).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=DTYPE)


def rank(query: list[float], candidates: list[tuple[str, bytes]], top_k: int = 8) -> list[tuple[str, float]]:
    """Devolve [(owner_id, score)] ordenado por similaridade de cosseno."""
    if not candidates:
        return []

    q = np.asarray(query, dtype=DTYPE)
    q_norm = float(np.linalg.norm(q))
    if q_norm == 0:
        return []
    q = q / q_norm

    ids = [c[0] for c in candidates]
    matrix = np.vstack([from_blob(c[1]) for c in candidates])

    if matrix.shape[1] != q.shape[0]:
        # Dimensões diferentes = embeddings de modelos diferentes misturados.
        # Falhar alto é melhor do que devolver resultados sem sentido.
        raise ValueError(
            f"Dimensão incompatível: query={q.shape[0]}, índice={matrix.shape[1]}. "
            "Reindexe os embeddings após trocar de modelo."
        )

    scores = matrix @ q  # já normalizados => produto escalar = cosseno
    order = np.argsort(-scores)[:top_k]
    return [(ids[i], float(scores[i])) for i in order]
