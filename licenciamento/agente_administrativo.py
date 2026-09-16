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

from licenciamento.identificador_documentos import IdentificadorDocumentos

logger = logging.getLogger("licenciamento.agente_administrativo")


class AgenteAdministrativo:
    """Auditor administrativo determinístico (regras de bloqueio + checklist)."""

    LIMIAR_SIMILARIDADE = 0.82       # tolerância de nomenclatura entre exigido x anexado
    LIMIAR_PALAVRAS_CHAVE = 0.5      # % mínima de palavras-chave do exigido presentes no anexo

    # Valor extraído do CONTEÚDO de cada tipo de documento para completar
    # campos críticos ausentes no formulário (o anexo é a fonte da verdade)
    VALOR_NO_DOCUMENTO: dict[str, tuple[list[str], re.Pattern, str]] = {
        "MATRICULA_IMOVEL": (
            ["empreendimento", "matricula_imovel"],
            re.compile(r"MATR[IÍ]CULA\s*N[ºo°.]?\s*([\d.\-]{4,})", re.I),
            "Matrícula do imóvel"),
        "CNPJ": (
            ["empreendedor", "cpf_cnpj"],
            re.compile(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}"),
            "CPF/CNPJ do empreendedor"),
        "ART": (
            ["responsavel_tecnico", "registro_art"],
            re.compile(r"ART\s*N?[ºo°.]?\s*([A-Z0-9/\-]{6,})", re.I),
            "Anotação de Responsabilidade Técnica (ART)"),
    }

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

    def _completar_campos_criticos(self, dados_processo: dict,
                                   anexados: list[str],
                                   textos: dict[str, str],
                                   identificador: IdentificadorDocumentos) -> list[str]:
        """Recupera campos críticos ausentes no formulário a partir dos anexos:
        reconhece o documento (nome -> aprendido -> conteúdo) e extrai o valor
        do próprio documento. Devolve os avisos de completamento."""
        completados: list[str] = []
        for tipo, (caminho, padrao, rotulo) in self.VALOR_NO_DOCUMENTO.items():
            valor: Any = dados_processo
            try:
                for chave in caminho:
                    valor = (valor or {}).get(chave)
            except AttributeError:
                valor = None
            if valor and str(valor).strip():
                continue  # o formulário já trouxe o dado
            for anexo in anexados:
                idf = identificador.identificar(anexo, textos.get(anexo))
                if idf.get("tipo") != tipo:
                    continue
                m = padrao.search(textos.get(anexo) or "")
                if not m:
                    continue
                extraido = m.group(0) if tipo == "CNPJ" else m.group(1)
                destino = dados_processo
                for chave in caminho[:-1]:
                    destino = destino.setdefault(chave, {})
                destino[caminho[-1]] = extraido
                completados.append(
                    f"{rotulo} não lido no formulário - COMPLETADO a partir do "
                    f"anexo '{anexo}' (documento reconhecido pelo {idf.get('via')}).")
                if tipo == "ART" and \
                        dados_processo.get("status_triagem") == "bloqueado_sem_art":
                    dados_processo["status_triagem"] = "liberado_triagem"
                break
        return completados

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
    def auditar(self, dados_processo: dict,
                documentos_anexados: Optional[list[str]] = None,
                textos_anexados: Optional[dict[str, str]] = None,
                identificador: Optional[IdentificadorDocumentos] = None) -> dict[str, Any]:
        """Executa a auditoria administrativa completa.

        Args:
            dados_processo: JSON estruturado gerado pelo FormularioParser (Fase 1).
            documentos_anexados: lista dos arquivos submetidos ao processo.
            textos_anexados: {nome_arquivo: texto_extraído} para o
                reconhecimento pelo CONTEÚDO quando o nome não basta.
            identificador: IdentificadorDocumentos (default: instância própria
                com o aprendizado persistente em config/).

        Returns:
            Dicionário com:
                status_geral: "APROVADO" | "PENDENTE" | "BLOQUEADO"
                bloqueios: hard constraints violadas (impossibilitam a análise)
                documentos_ok / documentos_pendentes: cruzamento do checklist
                origem_ok: como cada documento foi reconhecido
                    (nome/conteúdo/aprendido) - transparência ao licenciador
                avisos: anexos não correspondidos + campos críticos completados
                    a partir de documentos anexados
        """
        documentos_anexados = list(documentos_anexados or [])
        textos = textos_anexados or {}
        identificador = identificador or IdentificadorDocumentos()
        bloqueios: list[str] = []
        avisos: list[str] = []

        # 0) Campos críticos ausentes no FORMULÁRIO são recuperados dos
        #    ANEXOS (nome -> aprendido -> conteúdo) - ex.: matrícula cujo
        #    rótulo no formulário não foi lido, mas o documento está anexado
        avisos.extend(self._completar_campos_criticos(
            dados_processo, documentos_anexados, textos, identificador))

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
        origem_ok: dict[str, dict[str, str]] = {}
        for exigido in exigidos:
            anexo_correspondente = None
            via_reconhecimento = "nome"
            candidatos = [a for a in anexados_disponiveis
                          if a not in anexados_reconhecidos]
            anexo_correspondente = self._documento_estah_anexado(exigido, candidatos)
            if not anexo_correspondente:
                # FALLBACK: o nome não casou - reconhece pelo CONTEÚDO ou pelo
                # APRENDIZADO (nomes alterados, ex.: '°Cópia da matrícula...')
                tipo_exigido = IdentificadorDocumentos.tipo_da_exigencia(exigido)
                if tipo_exigido:
                    for anexo in candidatos:
                        idf = identificador.identificar(anexo, textos.get(anexo))
                        if idf.get("tipo") == tipo_exigido:
                            anexo_correspondente = anexo
                            via_reconhecimento = idf.get("via") or "conteudo"
                            break
            if anexo_correspondente:
                documentos_ok.append(exigido)
                anexados_reconhecidos.add(anexo_correspondente)
                if via_reconhecimento != "nome":
                    origem_ok[exigido] = {"anexo": anexo_correspondente,
                                          "via": via_reconhecimento}
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
            "origem_ok": origem_ok,
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
