"""
Adaptador Gemini via REST puro (httpx). Sem SDK de propósito: o SDK do Google
puxa dezenas de dependências e amarra o formato das mensagens ao fornecedor.
Trocar por Claude/OpenAI/Ollama = escrever outro arquivo deste tamanho.

Free tier: os limites são por minuto e por dia. O retry com backoff abaixo
não é luxo — é o que evita que a ingestão de um documento morra no meio.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from app.llm.base import LLM, Message

log = logging.getLogger("atlas.llm")
T = TypeVar("T", bound=BaseModel)

BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiLLM(LLM):
    name = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.0-flash",
        embed_model: str = "text-embedding-004",
        timeout: float = 60.0,
    ) -> None:
        self._key = api_key
        self._model = model
        self._embed_model = embed_model
        # A chave vai no HEADER, não em `?key=` na URL.
        #
        # Dois motivos. (1) Segurança: query string vaza em log de proxy, em
        # histórico de navegador e em Referer. (2) As chaves novas do Google
        # ("Auth keys", prefixo AQ.) são sensíveis a receber credencial por mais
        # de um caminho ao mesmo tempo — mandar por um canal só elimina o erro
        # "Multiple authentication credentials received".
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"x-goog-api-key": api_key},
        )

    @property
    def embedding_model(self) -> str:
        return self._embed_model

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _to_contents(messages: list[Message]) -> list[dict[str, Any]]:
        role_map = {"user": "user", "assistant": "model"}
        return [
            {"role": role_map.get(m.role, "user"), "parts": [{"text": m.content}]}
            for m in messages
        ]

    async def _post(self, path: str, payload: dict[str, Any], attempts: int = 4) -> dict[str, Any]:
        url = f"{BASE}/{path}"
        delay = 1.0
        for i in range(attempts):
            r = await self._client.post(url, json=payload)
            if r.status_code == 429 or r.status_code >= 500:
                if i == attempts - 1:
                    r.raise_for_status()
                log.warning("Gemini %s — retry em %.1fs (tentativa %d)", r.status_code, delay, i + 1)
                await asyncio.sleep(delay)
                delay *= 2
                continue
            if r.status_code >= 400:
                raise RuntimeError(_explain(r.status_code, r.text, self._model))
            return r.json()
        raise RuntimeError("inalcançável")

    # ---------------------------------------------------------------- geração
    async def stream(self, system: str, messages: list[Message]) -> AsyncIterator[str]:
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": self._to_contents(messages),
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 2048},
        }
        url = f"{BASE}/models/{self._model}:streamGenerateContent"

        # O streaming também precisa de retry: no free tier o 429 por RPM é
        # rotineiro e passa em segundos. Falhar de cara joga na cara do usuário
        # um erro que teria sumido sozinho.
        delay = 2.0
        for attempt in range(3):
            async with self._client.stream(
                "POST", url, json=payload, params={"alt": "sse"}
            ) as r:
                if r.status_code == 429 and attempt < 2:
                    await r.aread()
                    log.warning("Gemini 429 (cota/RPM) — nova tentativa em %.0fs", delay)
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                if r.status_code >= 400:
                    body = (await r.aread()).decode(errors="replace")
                    raise RuntimeError(_explain(r.status_code, body, self._model))

                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    for cand in chunk.get("candidates", []):
                        for part in cand.get("content", {}).get("parts", []):
                            if text := part.get("text"):
                                yield text
                return

    async def complete(self, system: str, messages: list[Message]) -> str:
        data = await self._post(
            f"models/{self._model}:generateContent",
            {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": self._to_contents(messages),
                "generationConfig": {"temperature": 0.4},
            },
        )
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)

    # -------------------------------------------------------------- embeddings
    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        # batchEmbedContents: N textos, 1 requisição. Economiza o rate limit.
        data = await self._post(
            f"models/{self._embed_model}:batchEmbedContents",
            {
                "requests": [
                    {
                        "model": f"models/{self._embed_model}",
                        "content": {"parts": [{"text": t[:8000]}]},
                    }
                    for t in texts
                ]
            },
        )
        return [e["values"] for e in data["embeddings"]]

    # ---------------------------------------------------------- saída estruturada
    async def extract(self, system: str, text: str, schema: type[T]) -> T:
        """Usa responseSchema nativo do Gemini: o modelo é forçado a devolver
        JSON válido no formato pedido. Muito mais confiável do que implorar
        'responda só JSON' no prompt e torcer."""
        data = await self._post(
            f"models/{self._model}:generateContent",
            {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": text}]}],
                "generationConfig": {
                    "temperature": 0.1,
                    "responseMimeType": "application/json",
                    "responseSchema": _to_gemini_schema(schema),
                },
            },
        )
        raw = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        return schema.model_validate_json(raw)

    async def ocr(self, data: bytes, mime: str = "application/pdf") -> str:
        """Lê PDF/imagem escaneada via multimodal Gemini."""
        import base64

        if len(data) > 12_000_000:
            raise ValueError("arquivo grande demais para OCR (máx. ~12 MB)")
        payload = {
            "contents": [{
                "role": "user",
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": mime,
                            "data": base64.b64encode(data).decode("ascii"),
                        }
                    },
                    {
                        "text": (
                            "Extraia TODO o texto legível deste documento. "
                            "Preserve a ordem de leitura. "
                            "Não resuma — transcreva. "
                            "Se houver fórmulas, descreva-as em texto. "
                            "Responda só com o texto extraído."
                        )
                    },
                ],
            }],
            "generationConfig": {"temperature": 0.0, "maxOutputTokens": 8192},
        }
        result = await self._post(f"models/{self._model}:generateContent", payload)
        parts = result["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)


def _to_gemini_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """Converte o JSON Schema do Pydantic para o subconjunto aceito pelo Gemini
    (sem $defs, sem $ref, sem additionalProperties)."""
    src = schema.model_json_schema()
    defs = src.get("$defs", {})

    def walk(node: dict[str, Any]) -> dict[str, Any]:
        if "$ref" in node:
            ref = node["$ref"].rsplit("/", 1)[-1]
            return walk(defs[ref])
        out: dict[str, Any] = {}
        if "enum" in node:
            return {"type": "STRING", "enum": node["enum"]}
        t = node.get("type")
        if t == "object":
            out["type"] = "OBJECT"
            out["properties"] = {k: walk(v) for k, v in node.get("properties", {}).items()}
            if req := node.get("required"):
                out["required"] = req
        elif t == "array":
            out["type"] = "ARRAY"
            out["items"] = walk(node.get("items", {"type": "string"}))
        elif t == "integer":
            out["type"] = "INTEGER"
        elif t == "number":
            out["type"] = "NUMBER"
        elif t == "boolean":
            out["type"] = "BOOLEAN"
        else:
            out["type"] = "STRING"
        return out

    return walk(src)


def _explain(status: int, body: str, model: str) -> str:
    """Traduz o erro do Google para algo acionável.

    O corpo bruto do Google é útil, mas o 429 em particular engana: ele quase
    nunca significa "você gastou sua cota". Costuma significar que o modelo
    apontado não tem cota gratuita — ou nem existe mais.
    """
    dica = ""
    if status == 429:
        dica = (
            f"\n\nO 429 aqui raramente é cota gastada. Verifique, nesta ordem:\n"
            f"  1. O modelo '{model}' ainda existe e tem free tier?\n"
            f"     (gemini-2.0-flash foi DESLIGADO em 03/03/2026)\n"
            f"     Free tier hoje: gemini-2.5-flash (10/min) ou gemini-2.5-flash-lite (15/min).\n"
            f"  2. Cota real em https://ai.dev/usage?tab=rate-limit\n"
            f"  3. Se for RPM, espere um minuto — o retry automático já tentou 3x."
        )
    elif status in (401, 403):
        dica = "\n\nProblema de credencial: confira ATLAS_GEMINI_API_KEY no .env."
    return f"Gemini {status}: {body[:400]}{dica}"
