# -*- coding: utf-8 -*-
"""
FASE 4 - Motor de Geração de Documentos (Minutas de Ofício em .docx)
=====================================================================

Compila TODOS os apontamentos com status "PENDENTE"/"REPROVADO"/"BLOQUEADO"
das Fases 2 (administrativa/financeira) e 3 (auditoria técnica) em uma minuta
de ofício estruturada, editável no Word (python-docx).

Cada apontamento incorpora a justificativa extraída pela IA/engine (ex.:
"Furo de sondagem insuficiente conforme exigência técnica mínima") e o trecho
do documento que embasou a decisão, além dos comentários manuais do analista
(aprovação humana).
"""

from __future__ import annotations

import io
import logging
from datetime import date
from pathlib import Path
from typing import Any, Optional

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

logger = logging.getLogger("licenciamento.gerador_oficios")


class GeradorOficios:
    """Gera minutas de ofício de complementação de documentos (.docx)."""

    PREFEITURA = "PREFEITURA MUNICIPAL DE CAMPO BOM"
    SECRETARIA = "SECRETARIA MUNICIPAL DO MEIO AMBIENTE"
    SETOR = "Divisão de Licenciamento Ambiental"

    # Base legal citada no ofício - AJUSTAR conforme a legislação municipal vigente
    BASE_LEGAL = [
        "Lei Municipal nº ____/____ - Código Municipal do Meio Ambiente de Campo Bom/RS "
        "[AJUSTAR conforme manual de legislação];",
        "Termos de Referência vigentes da Secretaria Municipal do Meio Ambiente;",
        "Legislação estadual e federal aplicável (Código Florestal - Lei nº 12.651/2012, "
        "Resoluções CONSEMA/CETESB aplicáveis, entre outras).",
    ]

    # ------------------------------------------------------------------
    @staticmethod
    def _paragrafo(doc: Document, texto: str, negrito: bool = False,
                   italico: bool = False, tamanho: int = 11,
                   alinhamento: Optional[int] = None, cor: Optional[RGBColor] = None,
                   espaco_antes: int = 0):
        """Helper para criar parágrafos formatados no documento."""
        p = doc.add_paragraph()
        run = p.add_run(texto)
        run.bold = negrito
        run.italic = italico
        run.font.size = Pt(tamanho)
        if cor is not None:
            run.font.color.rgb = cor
        if alinhamento is not None:
            p.alignment = alinhamento
        p.paragraph_format.space_before = Pt(espaco_antes)
        return p

    # ------------------------------------------------------------------
    def _coletar_pendencias(self, dados_processo: dict,
                            resultado_admin: Optional[dict],
                            resultados_tecnicos: list) -> dict[str, list[str]]:
        """Consolida itens PENDENTES/BLOQUEADOS das fases 2 e 3."""
        pendencias: dict[str, list[str]] = {"ADMINISTRATIVAS": [], "TECNICAS": []}

        # Fase 2 - bloqueios administrativos (hard constraints)
        if resultado_admin:
            pendencias["ADMINISTRATIVAS"].extend(resultado_admin.get("bloqueios", []))
            for doc_pendente in resultado_admin.get("documentos_pendentes", []):
                if isinstance(doc_pendente, dict):
                    pendencias["ADMINISTRATIVAS"].append(
                        f"Documento pendente: {doc_pendente.get('documento')} - "
                        f"{doc_pendente.get('justificativa', '')}".strip(" -"))
                else:  # compatibilidade com listas simples
                    pendencias["ADMINISTRATIVAS"].append(f"Documento pendente: {doc_pendente}")

        # Fase 3 - auditoria técnica (itens reprovados com justificativa do TR)
        for resultado in resultados_tecnicos or []:
            if str(getattr(resultado, "status", "")) in ("StatusValidacao.PENDENTE", "PENDENTE") \
                    or getattr(resultado, "itens_reprovados", None):
                for item in resultado.itens_reprovados:
                    linha = f"[{resultado.norma_tr}] {item}"
                    if resultado.trecho_referencia:
                        linha += f" (base do laudo: \"{resultado.trecho_referencia}\")"
                    pendencias["TECNICAS"].append(linha)
        return pendencias

    def _comentario_sugerido(self, pendencias: dict[str, list[str]]) -> str:
        """Gera um texto inicial editável de comentários do analista (apoio humano)."""
        total = sum(len(v) for v in pendencias.values())
        if total == 0:
            return ("Análise concluída sem pendências. Processo apto para o prosseguimento "
                    "da análise técnica.")
        linhas = [f"Constatadas {total} pendência(s). Síntese do analista (editar se necessário):", ""]
        if pendencias["ADMINISTRATIVAS"]:
            linhas.append("- Há pendências/bloqueios administrativos que impedem o andamento "
                          "até a regularização.")
        if pendencias["TECNICAS"]:
            linhas.append("- Os laudos técnicos apresentam não conformidades com os Termos de "
                          "Referência aplicáveis, conforme detalhado acima.")
        linhas.append("")
        linhas.append("Observações adicionais do analista: ________________________________________")
        return "\n".join(linhas)

    # ------------------------------------------------------------------
    def gerar_oficio_complementacao(self,
                                    dados_processo: dict,
                                    resultado_admin: Optional[dict] = None,
                                    resultados_tecnicos: Optional[list] = None,
                                    comentarios_analista: Optional[str] = None,
                                    numero_oficio: str = "001/2026",
                                    prazo_dias: int = 30,
                                    caminho_saida: Optional[str] = None) -> bytes:
        """Gera a minuta do ofício de complementação e devolve os bytes do .docx.

        Args:
            dados_processo: JSON do parser (identificação do processo/empreendedor).
            resultado_admin: saída do AgenteAdministrativo (Fase 2).
            resultados_tecnicos: lista de ResultadoValidacao do AuditorTecnico (Fase 3).
            comentarios_analista: texto de aprovação humana revisado pelo licenciador.
            numero_oficio: numeração do ofício.
            prazo_dias: prazo de atendimento (dias corridos).
            caminho_saida: se informado, grava também o arquivo .docx em disco.

        Returns:
            Conteúdo binário do documento Word (para download em Streamlit).
        """
        try:
            empreendedor = (dados_processo.get("empreendedor") or {}).get("nome_razao_social") \
                or "___ (empreendedor) ___"
            emp = dados_processo.get("empreendimento") or {}
            nome_empreendimento = emp.get("nome_empreendimento") or "___ (empreendimento) ___"
            pleito = dados_processo.get("pleito") or {}
            tipo_licenca = pleito.get("tipo_licenca") or "___"
            descricoes = {"LP": "Licença Prévia", "LI": "Licença de Instalação",
                          "LO": "Licença de Operação", "LIR": "Licença de Instalação e "
                          "Regularização", "LOR": "Licença de Operação e Regularização",
                          "AUTORIZACAO": "Autorização Ambiental", "DECLARACAO": "Declaração Ambiental"}
            licenca_texto = descricoes.get(tipo_licenca, tipo_licenca)

            pendencias = self._coletar_pendencias(dados_processo, resultado_admin,
                                                  resultados_tecnicos)
            if comentarios_analista is None:
                comentarios_analista = self._comentario_sugerido(pendencias)

            # ---------------- Documento ----------------
            doc = Document()
            estilo = doc.styles["Normal"]
            estilo.font.name = "Arial"
            estilo.font.size = Pt(11)

            self._paragrafo(doc, self.PREFEITURA, negrito=True, tamanho=13,
                            alinhamento=WD_ALIGN_PARAGRAPH.CENTER)
            self._paragrafo(doc, self.SECRETARIA, negrito=True, tamanho=12,
                            alinhamento=WD_ALIGN_PARAGRAPH.CENTER)
            self._paragrafo(doc, self.SETOR, tamanho=10,
                            alinhamento=WD_ALIGN_PARAGRAPH.CENTER, italico=True)
            doc.add_paragraph()

            self._paragrafo(doc, f"OFÍCIO Nº {numero_oficio} - EXIGÊNCIA DE COMPLEMENTAÇÃO "
                                 f"DE DOCUMENTOS / ESCLARECIMENTOS", negrito=True, tamanho=12,
                            alinhamento=WD_ALIGN_PARAGRAPH.CENTER)
            doc.add_paragraph()

            self._paragrafo(doc, f"Ao(À) {empreendedor}")
            self._paragrafo(doc, f"Ref.: Processo de Licenciamento Ambiental - {licenca_texto} "
                                 f"({tipo_licenca})")
            documento_identificacao = (dados_processo.get("empreendedor") or {}).get("cpf_cnpj") \
                or "___"
            self._paragrafo(doc, f"Empreendimento: {nome_empreendimento} "
                                 f"- CNPJ/CPF nº {documento_identificacao}")
            doc.add_paragraph()

            self._paragrafo(
                doc,
                f"Prezado(a) Senhor(a),\n\nNo exame do processo em referência, após análise "
                f"documental, financeira e técnica realizada pelo Sistema de Verificação de "
                f"Licenciamento Ambiental desta Secretaria, verificaram-se as pendências "
                f"abaixo relacionadas, cuja complementação se faz necessária para o "
                f"prosseguimento da análise:", tamanho=11)
            doc.add_paragraph()

            # ---------------- Pendências administrativas ----------------
            if pendencias["ADMINISTRATIVAS"]:
                self._paragrafo(doc, "1. PENDÊNCIAS ADMINISTRATIVAS", negrito=True,
                                tamanho=12, espaco_antes=6)
                for i, item in enumerate(pendencias["ADMINISTRATIVAS"], start=1):
                    self._paragrafo(doc, f"1.{i} {item}", tamanho=11)
                doc.add_paragraph()

            # ---------------- Pendências técnicas ------------------------
            if pendencias["TECNICAS"]:
                self._paragrafo(doc, "2. NÃO CONFORMIDADES TÉCNICAS (TERMOS DE REFERÊNCIA)",
                                negrito=True, tamanho=12, espaco_antes=6)
                for i, item in enumerate(pendencias["TECNICAS"], start=1):
                    self._paragrafo(doc, f"2.{i} {item}", tamanho=11)
                doc.add_paragraph()

            if not any(pendencias.values()):
                self._paragrafo(doc, "Não foram constatadas pendências no presente processo.",
                                tamanho=11)
                doc.add_paragraph()

            # ---------------- Comentários do analista (aprovação humana) --
            self._paragrafo(doc, "3. COMENTÁRIOS DO ANALISTA RESPONSÁVEL", negrito=True,
                            tamanho=12, espaco_antes=6)
            for linha in comentarios_analista.splitlines():
                self._paragrafo(doc, linha, tamanho=11)
            doc.add_paragraph()

            # ---------------- Base legal ----------------------------------
            self._paragrafo(doc, "4. FUNDAMENTAÇÃO LEGAL", negrito=True, tamanho=12,
                            espaco_antes=6)
            for item in self.BASE_LEGAL:
                self._paragrafo(doc, f"- {item}", tamanho=10)
            doc.add_paragraph()

            # ---------------- Prazo e fechamento --------------------------
            self._paragrafo(
                doc,
                f"Requer-se o atendimento às pendências acima no prazo de {prazo_dias} "
                f"({prazo_dias}) dias corridos, contados do recebimento deste ofício, sob pena "
                f"de arquivamento do processo, nos termos da legislação aplicável.",
                tamanho=11)
            doc.add_paragraph()
            self._paragrafo(doc, f"Campo Bom/RS, {date.today().strftime('%d de %B de %Y')}.",
                            alinhamento=WD_ALIGN_PARAGRAPH.RIGHT)
            doc.add_paragraph()
            doc.add_paragraph()
            self._paragrafo(doc, "_________________________________________",
                            alinhamento=WD_ALIGN_PARAGRAPH.CENTER)
            self._paragrafo(doc, "Analista Ambiental / Licenciador",
                            alinhamento=WD_ALIGN_PARAGRAPH.CENTER)
            self._paragrafo(doc, self.SECRETARIA, alinhamento=WD_ALIGN_PARAGRAPH.CENTER,
                            tamanho=10)

            # ---------------- Serialização ---------------------------------
            buffer = io.BytesIO()
            doc.save(buffer)
            conteudo = buffer.getvalue()
            if caminho_saida:
                Path(caminho_saida).write_bytes(conteudo)
                logger.info("Ofício gravado em %s", caminho_saida)
            logger.info("Minuta de ofício gerada (%d pendências).",
                        sum(len(v) for v in pendencias.values()))
            return conteudo

        except Exception:  # noqa: BLE001
            logger.exception("Falha ao gerar a minuta de ofício")
            raise
