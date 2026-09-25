# -*- coding: utf-8 -*-
"""
Leitor de imagens para OCR de documentos escaneados e fotos de anexos.
======================================================================
Implementa as diretrizes da skill `document-image-analysis`:
1. Pré-processamento via Pillow (correção de rotação EXIF, conversão RGB, contraste);
2. OCR via RapidOCR/ONNX em CPU (rápido, sem necessidade de tesseract no SO);
3. Fallback gracioso para pytesseract (se RapidOCR não estiver disponível);
4. Resiliência: falhas nunca derrubam o fluxo (devolve string vazia com metadados).
"""

from __future__ import annotations

import io
import logging
from typing import Any, Optional

logger = logging.getLogger("licenciamento.leitor_imagem")


class LeitorImagem:
    """Extração de texto de arquivos de imagem (.png, .jpg, .jpeg, .webp, .tiff, .bmp)."""

    _ocr_instancia = None

    @classmethod
    def _obter_ocr(cls):
        """Singleton lazy do RapidOCR."""
        if cls._ocr_instancia is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
                cls._ocr_instancia = RapidOCR()
            except Exception as exc:  # noqa: BLE001
                logger.info("RapidOCR indisponível para imagens: %s", exc)
                cls._ocr_instancia = False
        return cls._ocr_instancia or None

    @classmethod
    def preprocessar_imagem(cls, conteudo: bytes) -> bytes:
        """Ajusta orientação EXIF, normaliza modo de cor para RGB e ajusta contraste."""
        try:
            from PIL import Image, ImageEnhance, ImageOps
            img = Image.open(io.BytesIO(conteudo))
            img = ImageOps.exif_transpose(img)
            if img.mode != "RGB":
                img = img.convert("RGB")

            # Aumento suave de contraste para realçar textos digitalizados
            enhancer = ImageEnhance.Contrast(img)
            img = enhancer.enhance(1.2)

            saida = io.BytesIO()
            img.save(saida, format="PNG")
            return saida.getvalue()
        except Exception as exc:
            logger.debug("Pré-processamento da imagem falhou, usando original: %s", exc)
            return conteudo

    @classmethod
    def extrair_texto(cls, conteudo: bytes) -> tuple[str, dict[str, Any]]:
        """Extrai texto de bytes de imagem.
        
        Retorna (texto, info_metadados).
        """
        info: dict[str, Any] = {
            "metodo": "nenhum",
            "sucesso": False,
            "caracteres": 0,
            "aviso": None
        }

        if not conteudo:
            return "", info

        # 1. Pré-processamento
        bytes_otimizados = cls.preprocessar_imagem(conteudo)

        # 2. Tenta RapidOCR (motor principal)
        ocr = cls._obter_ocr()
        if ocr is not None:
            try:
                resultado, _ = ocr(bytes_otimizados)
                if resultado:
                    linhas = [linha[1] for linha in resultado if len(linha) > 1 and linha[1]]
                    texto = "\n".join(linhas).strip()
                    info["metodo"] = "rapidocr"
                    info["sucesso"] = bool(texto)
                    info["caracteres"] = len(texto)
                    return texto, info
            except Exception as exc:
                logger.warning("Falha ao executar RapidOCR em imagem: %s", exc)

        # 3. Fallback para pytesseract se instalado
        try:
            import pytesseract
            from PIL import Image
            img = Image.open(io.BytesIO(bytes_otimizados))
            texto = ""
            try:
                texto = pytesseract.image_to_string(img, lang="por") or ""
            except Exception:
                texto = pytesseract.image_to_string(img) or ""
            texto = texto.strip()
            if texto:
                info["metodo"] = "pytesseract"
                info["sucesso"] = True
                info["caracteres"] = len(texto)
                return texto, info
        except Exception:
            pass

        info["aviso"] = "OCR indisponível ou imagem sem texto legível"
        return "", info
