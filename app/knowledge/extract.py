"""Utilitários para extrair texto de uploads (txt/md/pdf)."""

from __future__ import annotations

from io import BytesIO


def extract_text_from_bytes(filename: str, raw: bytes) -> tuple[str, str]:
    """Devolve (texto, formato). Levanta ValueError se não der para ler."""
    name = (filename or "").lower()

    if name.endswith(".pdf") or raw[:4] == b"%PDF":
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
        text = "\n\n".join(p.strip() for p in pages if p and p.strip())
        if not text.strip():
            raise ValueError("PDF sem texto extraível (pode ser imagem escaneada)")
        return text, "pdf"

    # texto / markdown
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return raw.decode(encoding), "text"
        except UnicodeDecodeError:
            continue
    raise ValueError("encoding não reconhecido — envie .txt, .md ou .pdf")
