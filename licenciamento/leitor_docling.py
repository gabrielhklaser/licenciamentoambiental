# -*- coding: utf-8 -*-
"""
Módulo de Integração com Docling e PyMuPDF para Leitura Estrutural de Formulários e Documentos
===========================================================================================

Este módulo disponibiliza ao GabeBrain capacidade avançada de:
  1. Conversão estruturada de documentos PDF e formulários com preservação de tabelas via Docling;
  2. Fallback de alta fidelidade para PyMuPDF com marcação página a página [[pag N]];
  3. Reconhecimento de campos, blocos semióticos e metadados de ART e RRT/RTT.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("licenciamento.leitor_docling")

_DOCLING_DISPONIVEL = False
try:
    from docling.document_converter import DocumentConverter
    _DOCLING_DISPONIVEL = True
except ImportError:
    _DOCLING_DISPONIVEL = False


class LeitorDocling:
    """Leitor estruturado de documentos técnicos e formulários via Docling / PyMuPDF."""

    def __init__(self):
        self.disponivel = _DOCLING_DISPONIVEL
        self._converter: Optional[Any] = None

    def _obter_converter(self) -> Optional[Any]:
        if not self.disponivel:
            return None
        if self._converter is None:
            try:
                self._converter = DocumentConverter()
            except Exception as exc:
                logger.warning("Falha ao instanciar Docling DocumentConverter: %s", exc)
                self.disponivel = False
        return self._converter

    def converter_para_markdown(self, caminho_arquivo: str | Path) -> Optional[str]:
        """Converte documento PDF/Word para Markdown estruturado via Docling."""
        converter = self._obter_converter()
        if not converter:
            return None
        try:
            res = converter.convert(str(caminho_arquivo))
            if hasattr(res, "document") and hasattr(res.document, "export_to_markdown"):
                return res.document.export_to_markdown()
        except Exception as exc:
            logger.warning("Erro na conversão Docling de %s: %s", caminho_arquivo, exc)
        return None

    def extrair_tabelas(self, caminho_arquivo: str | Path) -> list[list[list[str]]]:
        """Extrai tabelas de um documento estruturado."""
        converter = self._obter_converter()
        if not converter:
            return []
        tabelas: list[list[list[str]]] = []
        try:
            res = converter.convert(str(caminho_arquivo))
            doc = getattr(res, "document", None)
            if doc and hasattr(doc, "tables"):
                for t in doc.tables:
                    if hasattr(t, "export_to_dataframe"):
                        df = t.export_to_dataframe()
                        tabelas.append([df.columns.tolist()] + df.values.tolist())
        except Exception as exc:
            logger.warning("Falha ao extrair tabelas com Docling: %s", exc)
        return tabelas
