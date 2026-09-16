"""Leitura de PDFs, incluindo ESCANEADOS (imagem) via OCR.

Matrículas, ARTs e laudos chegam como PDF de imagens (digitalização). O
pipeline é em camadas:

1. TEXTO NATIVO (pypdf) - resolve PDFs "de verdade";
2. Se o texto nativo for insuficiente (pouquíssimos caracteres por página =
   documento escaneado), renderiza as páginas (PyMuPDF) e roda OCR
   (RapidOCR/ONNX, roda em CPU sem binário de sistema);
3. OCR indisponível NÃO quebra o fluxo: devolve o texto nativo com aviso
   (o usuário é alertado de que o anexo precisa de conferência manual).

Observação: o primeiro uso de OCR carrega os modelos (~segundos); a instância
é reaproveitada (singleton) para não recarregar a cada anexo.
"""
from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)


class LeitorPDF:
    """Extração de texto de PDF com fallback de OCR para digitalizados."""

    # abaixo disso por página considera-se "sem camada de texto" (escaneado)
    MINIMO_CHARS_POR_PAGINA = 60
    # matrículas/laudos são curtos; OCR limitado às primeiras páginas
    MAX_PAGINAS_OCR = 15
    # zoom de renderização (4x = ~288 dpi em PDF de 72 dpi)
    ZOOM_OCR = 4.0

    _ocr_instancia = None  # singleton lazy (modelos pesados)

    @classmethod
    def ocr_disponivel(cls) -> bool:
        """True se a pilha de OCR (rapidocr/onnx) pôde ser carregada."""
        return cls._obter_ocr() is not None

    @classmethod
    def _obter_ocr(cls):
        if cls._ocr_instancia is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
                cls._ocr_instancia = RapidOCR()
            except Exception as exc:  # noqa: BLE001
                logger.info("OCR indisponível (rapidocr/onnxruntime): %s", exc)
                cls._ocr_instancia = False
        return cls._ocr_instancia or None

    @classmethod
    def extrair(cls, conteudo: bytes) -> tuple[str, dict]:
        """Extrai texto do PDF. Retorna (texto, info).

        info: {metodo: texto_nativo|ocr, paginas: int, aviso: str|None}
        """
        info: dict = {"metodo": "texto_nativo", "paginas": 0, "aviso": None}
        nativo, paginas = cls._texto_nativo(conteudo)
        info["paginas"] = paginas
        limite = cls.MINIMO_CHARS_POR_PAGINA * max(
            min(paginas, cls.MAX_PAGINAS_OCR), 1)
        if paginas and len(nativo.strip()) < limite:
            texto_ocr, aviso = cls._ocr_paginas(conteudo)
            if texto_ocr.strip():
                info["metodo"] = "ocr"
                logger.info("PDF escaneado lido via OCR (%s páginas)",
                            min(paginas, cls.MAX_PAGINAS_OCR))
                return texto_ocr, info
            if aviso:
                info["aviso"] = aviso
        return nativo, info

    @staticmethod
    def _texto_nativo(conteudo: bytes) -> tuple[str, int]:
        try:
            from pypdf import PdfReader
            leitor = PdfReader(io.BytesIO(conteudo))
            partes = [(p.extract_text() or "") for p in leitor.pages]
            return "\n".join(partes), len(partes)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Falha no texto nativo do PDF: %s", exc)
            return "", 0

    @classmethod
    def _ocr_paginas(cls, conteudo: bytes) -> tuple[str, str | None]:
        ocr = cls._obter_ocr()
        if ocr is None:
            return "", ("PDF sem camada de texto e OCR indisponível - "
                        "instale 'rapidocr-onnxruntime' (pip) para ler "
                        "documentos escaneados")
        try:
            try:
                import pymupdf
            except ImportError:  # compatibilidade versões antigas
                import fitz as pymupdf  # type: ignore
            doc = pymupdf.open(stream=conteudo, filetype="pdf")
            partes: list[str] = []
            for pagina in doc.pages(0, cls.MAX_PAGINAS_OCR):
                pix = pagina.get_pixmap(
                    matrix=pymupdf.Matrix(cls.ZOOM_OCR, cls.ZOOM_OCR))
                resultado, _ = ocr(pix.tobytes("png"))
                if resultado:
                    partes.append(" ".join(linha[1] for linha in resultado))
            return "\n".join(partes), None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Falha no OCR do PDF: %s", exc)
            return "", f"Falha no OCR do PDF: {exc}"
