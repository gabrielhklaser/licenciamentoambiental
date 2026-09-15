# -*- coding: utf-8 -*-
"""
FASE 2 - Agente Administrativo (auditoria de bloqueios e checklist)
====================================================================

Agente DETERMINÍSTICO baseado em regras. Recebe o JSON do parser (Fase 1) e a
lista de documentos efetivamente anexados ao processo (na prototipagem a
submissão é simulada com uma lista de nomes) e realiza uma auditoria rígida:

Hard Constraints (bloqueio imediato do processo):
    - CPF/CNPJ do empreendedor ausente;
    - Matrícula do imóvel ausente;
    - Anotação de Responsabilidade Técnica (ART) ausente.

Conferência de checklist:
    - Cruza a lista de documentos exigidos (já desduplicada para LIR/LOR na
      Fase 1) com os arquivos anexados, usando normalização + similaridade de
      strings para tolerar pequenas variações de nomenclatura;
    - Retorna, para cada documento exigido, o status "ok" ou "pendente".
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Optional

import logging

logger = logging.getLogger("licenciamento.agente_administrativo")


class AgenteAdministrativo:
    """Auditor administrativo determinístico (regras de bloqueio + checklist)."""

    LIMIAR_SIMILARIDADE = 0.82       # tolerância de nomenclatura entre exigido x anexado
    LIMIAR_PALAVRAS_CHAVE = 0.5      # % mínima de palavras-chave do exigido presentes no anexo

    # Hard constraints: campo no JSON -> rótulo humano para o relatório
    CAMPOS_CRITICOS: list[tuple[list[str], str]] = [
        (["empreendedor", "cpf_cnpj"], "CPF/CNPJ do empreendedor"),
        (["empreendimento", "matricula_imovel"], "Matrícula do imóvel"),
        (["responsavel_tecnico", "registro_art"],
         "Anotação de Responsabilidade Técnica (ART)"),
    ]

    # ------------------------------------------------------------------
    @staticmethod
    def _normalizar(texto: Optional[str]) -> str:
        """Minúsculas, sem acentos e sem pontuação (comparação tolerante)."""
        if not texto:
            return ""
        texto = unicodedata.normalize("NFKD", texto)
        texto = "".join(c for c in texto if not unicodedata.combining(c))
        texto = re.sub(r"[^\w\s]", " ", texto.lower())
        return re.sub(r"\s+", " ", texto).strip()

    @classmethod
    def _documento_estah_anexado(cls, documento_exigido: str, anexados: list[str]) -> Optional[str]:
        """Verifica se o documento exigido corresponde a algum arquivo anexado.

        Retorna o nome do arquivo correspondente ou None. Combina:
            1) contenção de sub-cadeia normalizada;
            2) similaridade de strings (SequenceMatcher >= LIMIAR_SIMILARIDADE);
            3) siglas do documento (PGRS, PCA, EIV...) presentes no nome do anexo;
            4) casamento por palavras-chave (>= LIMIAR_PALAVRAS_CHAVE).
        """
        exigido_n = cls._normalizar(documento_exigido)
        # palavras-chave que definem o documento (ignora artigos/preposições curtas)
        palavras = {p for p in exigido_n.split() if len(p) > 3}
        # siglas no texto original (ex.: 'Plano de Gerenciamento ... (PGRS)')
        siglas = {s.lower() for s in re.findall(r"\b[A-Z]{2,6}\b", documento_exigido or "")}

        for anexo in anexados:
            anexo_n = cls._normalizar(anexo)
            if not anexo_n:
                continue
            if exigido_n in anexo_n or anexo_n in exigido_n:
                return anexo
            ratio = SequenceMatcher(None, exigido_n, anexo_n).ratio()
            if ratio >= cls.LIMIAR_SIMILARIDADE:
                return anexo
            if siglas and any(s in anexo_n for s in siglas):
                return anexo
            # casamento por palavras-chave do documento exigido presentes no anexo
            if palavras:
                comuns = sum(1 for p in palavras if p in anexo_n)
                if comuns / len(palavras) >= cls.LIMIAR_PALAVRAS_CHAVE:
                    return anexo
        return None

    # ------------------------------------------------------------------
    def auditar(self, dados_processo: dict, documentos_anexados: Optional[list[str]] = None) -> dict[str, Any]:
        """Executa a auditoria administrativa completa.

        Args:
            dados_processo: JSON estruturado gerado pelo FormularioParser (Fase 1).
            documentos_anexados: lista simulada de arquivos submetidos ao processo.

        Returns:
            Dicionário com:
                status_geral: "APROVADO" | "PENDENTE" | "BLOQUEADO"
                bloqueios: hard constraints violadas (impossibilitam a análise)
                documentos_ok / documentos_pendentes: cruzamento do checklist
                avisos: documentos anexados que não corresponderam a exigências
        """
        documentos_anexados = list(documentos_anexados or [])
        bloqueios: list[str] = []
        avisos: list[str] = []

        # 1) Hard constraints --------------------------------------------
        for caminho, rotulo in self.CAMPOS_CRITICOS:
            valor: Any = dados_processo
            try:
                for chave in caminho:
                    valor = (valor or {}).get(chave)
            except AttributeError:
                valor = None
            if not valor or not str(valor).strip():
                bloqueios.append(f"Falta {rotulo} (dado crítico ausente no processo).")
                logger.warning("Hard constraint violada: %s", rotulo)

        # Status de triagem vindo do parser (ART ausente já marca o bloqueio)
        if dados_processo.get("status_triagem") == "bloqueado_sem_art":
            msg = "Processo sem ART (Anotação de Responsabilidade Técnica) válida."
            if not any("ART" in b for b in bloqueios):
                bloqueios.append(msg)

        # 2) Conferência de checklist ------------------------------------
        exigidos: list[str] = (dados_processo.get("documentos_exigidos", {}) or {}) \
            .get("lista_deduplicada", [])

        documentos_ok: list[str] = []
        documentos_pendentes: list[dict[str, str]] = []
        anexados_reconhecidos: set[str] = set()
        anexados_disponiveis = [a for a in documentos_anexados]

        # Casamento greedy 1-para-1: cada anexo atende apenas UMA exigência
        # (evita que um mesmo arquivo, ex. 'pca_...pdf', cubra exigências distintas
        # que apenas mencionam a mesma sigla).
        for exigido in exigidos:
            anexo_correspondente = None
            candidatos = [a for a in anexados_disponiveis
                          if a not in anexados_reconhecidos]
            anexo_correspondente = self._documento_estah_anexado(exigido, candidatos)
            if anexo_correspondente:
                documentos_ok.append(exigido)
                anexados_reconhecidos.add(anexo_correspondente)
            else:
                documentos_pendentes.append({
                    "documento": exigido,
                    "justificativa": "Documento exigido não localizado entre os anexos do processo.",
                })

        for anexo in documentos_anexados:
            if anexo not in anexados_reconhecidos:
                avisos.append(f"Anexo '{anexo}' não corresponde a nenhuma exigência do checklist.")

        # 3) Status consolidado -------------------------------------------
        if bloqueios:
            status_geral = "BLOQUEADO"
        elif documentos_pendentes:
            status_geral = "PENDENTE"
        else:
            status_geral = "APROVADO"

        resultado = {
            "agente": "AgenteAdministrativo",
            "status_geral": status_geral,
            "bloqueios": bloqueios,
            "documentos_ok": documentos_ok,
            "documentos_pendentes": documentos_pendentes,
            "avisos": avisos,
            "resumo": {
                "total_exigidos": len(exigidos),
                "total_ok": len(documentos_ok),
                "total_pendentes": len(documentos_pendentes),
                "total_anexados_informados": len(documentos_anexados),
            },
        }
        logger.info("Auditoria administrativa concluída: %s", status_geral)
        return resultado
