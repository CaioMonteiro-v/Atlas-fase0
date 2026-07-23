"""Utilitários para extrair texto de uploads (txt/md/pdf) + OCR de PDF escaneado."""

from __future__ import annotations

import base64
import logging
from collections.abc import Awaitable, Callable
from io import BytesIO

log = logging.getLogger("atlas.knowledge.extract")

OcrFn = Callable[[bytes, str], Awaitable[str]]


def _pypdf_text(raw: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError("suporte a PDF indisponível (pypdf não instalado)") from exc
    reader = PdfReader(BytesIO(raw))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return "\n\n".join(p.strip() for p in pages if p and p.strip())


def _pymupdf_text(raw: bytes) -> str:
    try:
        import fitz  # pymupdf
    except ImportError:
        return ""
    try:
        doc = fitz.open(stream=raw, filetype="pdf")
        parts = [(page.get_text() or "").strip() for page in doc]
        doc.close()
        return "\n\n".join(p for p in parts if p)
    except Exception as exc:
        log.warning("pymupdf falhou na extração: %s", exc)
        return ""


def extract_text_from_bytes(filename: str, raw: bytes) -> tuple[str, str]:
    """Devolve (texto, formato). Levanta ValueError se não der para ler sem OCR."""
    name = (filename or "").lower()

    if name.endswith(".pdf") or raw[:4] == b"%PDF":
        text = _pypdf_text(raw)
        if not text.strip():
            text = _pymupdf_text(raw)
        if not text.strip():
            raise ValueError("PDF sem texto extraível (pode ser imagem escaneada)")
        return text, "pdf"

    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return raw.decode(encoding), "text"
        except UnicodeDecodeError:
            continue
    raise ValueError("encoding não reconhecido — envie .txt, .md ou .pdf")


async def extract_document(
    filename: str,
    raw: bytes,
    *,
    ocr: OcrFn | None = None,
) -> tuple[str, str]:
    """Como extract_text_from_bytes, mas com OCR (Gemini) se o PDF for escaneado."""
    name = (filename or "").lower()
    is_pdf = name.endswith(".pdf") or raw[:4] == b"%PDF"

    if not is_pdf:
        return extract_text_from_bytes(filename, raw)

    text = ""
    try:
        text = _pypdf_text(raw)
    except ValueError:
        text = ""
    if not text.strip():
        text = _pymupdf_text(raw)

    if text.strip():
        return text, "pdf"

    if ocr is None:
        raise ValueError(
            "PDF sem texto extraível (escaneado). "
            "Configure Gemini (ATLAS_LLM_PROVIDER=gemini) para OCR automático."
        )

    log.info("PDF escaneado — rodando OCR (%d bytes)", len(raw))
    ocr_text = (await ocr(raw, "application/pdf")).strip()
    if len(ocr_text) < 40:
        raise ValueError("OCR não extraiu conteúdo suficiente do PDF")
    return ocr_text, "pdf-ocr"


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
