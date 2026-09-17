"""COMPILADOR DO PARECER TÉCNICO (texto editável + exportação .docx/.pdf).

Fluxo sugerido pelo licenciador: o parecer é compilado como TEXTO (prévia
editável no navegador, copiável/colável em qualquer editor) e, a partir do
texto final (editado ou não), exporta-se .docx (python-docx) e .pdf
(reportlab, conforme a skill 'pdf').
"""
from __future__ import annotations

import io
import re
from datetime import date
from typing import Optional


def compilar_texto_parecer(
        dados_processo: dict,
        quadro_documentos: Optional[list] = None,
        resultado_admin: Optional[dict] = None,
        resultados_tecnicos: Optional[list] = None,
        arquivos_recebidos: Optional[list] = None,
        resumo_quadro: Optional[dict] = None,
        comentarios_analista: Optional[str] = None,
        numero_parecer: str = "001/2026",
        prazo_dias: int = 30,
        data_referencia: Optional[date] = None) -> str:
    """Compila o parecer técnico como TEXTO estruturado (mesmo conteúdo do
    .docx oficial, pronto para prévia editável e copiar/colar)."""
    ref = data_referencia or date.today()
    dados_processo = dados_processo or {}
    quadro_documentos = quadro_documentos or []
    resultado_admin = resultado_admin or {}
    resultados_tecnicos = resultados_tecnicos or []
    resumo_quadro = resumo_quadro or {}

    emp = dados_processo.get("empreendimento", {}) or {}
    empreendedor = dados_processo.get("empreendedor", {}) or {}
    pleito = dados_processo.get("pleito", {}) or {}
    razao = (empreendedor.get("nome_razao_social")
             or empreendedor.get("razao_social") or "—")
    cnpj = (empreendedor.get("cpf_cnpj")
            or empreendedor.get("cnpj_cpf") or "—")

    linhas: list[str] = [
        "PREFEITURA MUNICIPAL DE CAMPO BOM/RS",
        "SECRETARIA DO MEIO AMBIENTE — SEMA",
        "Setor de Licenciamento Ambiental",
        "",
        f"PARECER TÉCNICO Nº {numero_parecer}",
        "",
        "1. IDENTIFICAÇÃO DO PROCESSO",
        f"Interessado: {razao} (CPF/CNPJ: {cnpj})",
        f"Atividade: {emp.get('nome_empreendimento') or '—'}",
        f"Ramo/CODRAM: {emp.get('ramo_atividade') or '—'} | "
        f"Porte: {emp.get('porte') or '—'} | "
        f"Potencial poluidor: {emp.get('potencial_poluidor') or '—'}",
        f"Licença pleiteada: {pleito.get('tipo_licenca') or '—'} "
        f"(fases: {', '.join(pleito.get('fases_componentes') or ['—'])})",
        f"Data da análise: {ref:%d/%m/%Y}",
        "",
        "2. DOCUMENTAÇÃO APRESENTADA",
    ]
    if arquivos_recebidos:
        for i, nome in enumerate(arquivos_recebidos, 1):
            linhas.append(f"{i}. {nome}")
    else:
        linhas.append("Nenhum documento apresentado.")

    linhas += ["", "3. ANÁLISE DA DOCUMENTAÇÃO EXIGIDA PARA A LICENÇA"]
    rotulo = {"CONFORME": "EM CONFORMIDADE", "PENDENTE": "PENDENTE",
              "NAO_APRESENTADO": "NÃO APRESENTADO"}
    if quadro_documentos:
        for linha in quadro_documentos:
            situacao = rotulo.get(linha.get("situacao"),
                                  linha.get("situacao"))
            marcador = {"EM CONFORMIDADE": "OK", "PENDENTE": "!",
                        "NÃO APRESENTADO": "X"}.get(situacao, "•")
            texto_linha = (f"[{marcador}] {linha.get('documento')} — "
                           f"{situacao}")
            if linha.get("arquivo"):
                texto_linha += f" (arquivo: {linha['arquivo']})"
            linhas.append(texto_linha)
            for pend in linha.get("pendencias") or []:
                linhas.append(f"    -> {pend}")
        if resumo_quadro:
            linhas.append(
                "Síntese: {conforme} em conformidade, {pendente} com "
                "pendência(s), {ausente} não apresentado(s).".format(
                    conforme=resumo_quadro.get("CONFORME", 0),
                    pendente=resumo_quadro.get("PENDENTE", 0),
                    ausente=resumo_quadro.get("NAO_APRESENTADO", 0)))
    else:
        linhas.append("Quadro de documentos não disponível.")

    linhas += ["", "4. PENDÊNCIAS ADMINISTRATIVAS"]
    pend_admin: list[str] = list(resultado_admin.get("bloqueios", []) or [])
    for doc_pendente in resultado_admin.get("documentos_pendentes", []) or []:
        if isinstance(doc_pendente, dict):
            pend_admin.append((f"{doc_pendente.get('documento')} — "
                               f"{doc_pendente.get('justificativa', '')}")
                              .strip(" —"))
        else:
            pend_admin.append(str(doc_pendente))
    if pend_admin:
        for item in pend_admin:
            linhas.append(f"• {item}")
    else:
        linhas.append("Sem pendências administrativas registradas.")

    linhas += ["", "5. ANÁLISE TÉCNICA — TERMOS DE REFERÊNCIA"]
    houve_tecnica = False
    for resultado in resultados_tecnicos:
        if getattr(resultado, "itens_reprovados", None):
            houve_tecnica = True
            status = getattr(getattr(resultado, "status", None),
                             "value", resultado.status)
            linhas.append(f"• Documento: {resultado.documento_analisado} — "
                          f"{resultado.norma_tr} — {status}")
            for item in resultado.itens_reprovados:
                linhas.append(f"    -> {item}")
            if getattr(resultado, "trecho_referencia", ""):
                linhas.append(f'    (trecho do laudo: '
                              f'"{resultado.trecho_referencia}")')
    if not houve_tecnica:
        linhas.append("Laudos analisados sem não conformidades com os Termos "
                      "de Referência aplicáveis.")

    linhas += ["", "6. CONCLUSÃO — PROVIDÊNCIAS PARA COMPLEMENTAÇÃO"]
    if comentarios_analista:
        linhas.append("Considerações do analista:")
        for paragrafo in comentarios_analista.split("\n"):
            if paragrafo.strip():
                linhas.append(paragrafo.strip())
    faltando: list[str] = []
    for linha in quadro_documentos:
        if linha.get("situacao") == "NAO_APRESENTADO":
            faltando.append(f"Apresentar: {linha.get('documento')}.")
        elif linha.get("situacao") == "PENDENTE":
            for pend in linha.get("pendencias") or []:
                faltando.append(
                    f"Regularizar '{linha.get('documento')}': {pend}")
    faltando.extend(pend_admin)
    for resultado in resultados_tecnicos:
        if getattr(resultado, "itens_reprovados", None) and hasattr(
                resultado, "resumo_para_oficio"):
            faltando.append(resultado.resumo_para_oficio())
    if faltando:
        linhas.append(
            f"Diante do exposto, o interessado deverá atender "
            f"{len(faltando)} providência(s) no prazo de {prazo_dias} dias "
            f"corridos contados do recebimento deste parecer, sob pena de "
            f"indeferimento do processo:")
        for i, item in enumerate(dict.fromkeys(faltando), 1):
            linhas.append(f"{i}. {item}")
    else:
        linhas.append("Análise concluída SEM PROVIDÊNCIAS pendentes: a "
                      "documentação está em conformidade com o exigido para "
                      "esta fase do licenciamento.")

    linhas += ["", "", "Campo Bom/RS, " + f"{ref:%d de %B de %Y}.", "",
               "_______________________________________",
               "Analista Ambiental — SEMA Campo Bom",
               f"Parecer emitido pelo Sistema de Verificação do Licenciamento "
               f"Ambiental (nº {numero_parecer})."]
    return "\n".join(linhas)


# ============================================================================
# EXPORTAÇÕES a partir do TEXTO (editado ou não)
# ============================================================================
_LINHA_TITULO = re.compile(r"^(PARECER TÉCNICO|PREFEITURA|SECRETARIA|"
                           r"Setor de Licenciamento)")


def exportar_docx(texto: str, numero_parecer: str = "001/2026") -> bytes:
    """Converte o texto do parecer em .docx (títulos em negrito,
    bullets e numeração preservados)."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    doc = Document()
    for secao in doc.sections:
        secao.top_margin = Pt(46)
        secao.bottom_margin = Pt(46)
        secao.left_margin = Pt(56)
        secao.right_margin = Pt(56)

    def par(conteudo: str, negrito: bool = False, tamanho: int = 11,
            centro: bool = False, italico: bool = False) -> None:
        p = doc.add_paragraph()
        run = p.add_run(conteudo)
        run.font.size = Pt(tamanho)
        run.bold = negrito
        run.italic = italico
        if centro:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    for linha_bruta in (texto or "").splitlines():
        linha = linha_bruta.rstrip()
        if not linha.strip():
            par("", tamanho=6)
            continue
        if _LINHA_TITULO.match(linha):
            par(linha, negrito=True, tamanho=12, centro=True)
        elif linha.startswith("    -> ") or linha.startswith("    ("):
            par(linha.strip(), tamanho=10, italico=True)
        elif linha.startswith("• "):
            par(linha, tamanho=10)
        elif re.match(r"^\d+\.\s+[A-ZÀ-Ú]", linha):
            par(linha, negrito=True, tamanho=11)
        elif linha.startswith("_______________________________________"):
            par(linha, tamanho=10, centro=False)
        else:
            par(linha, tamanho=10 if len(linha) > 90 else 11)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def exportar_pdf(texto: str, numero_parecer: str = "001/2026") -> bytes:
    """Converte o texto do parecer em .pdf (reportlab, conforme a skill pdf:
    geração em memória e validação com pypdf antes de devolver)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.enums import TA_CENTER
    from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            topMargin=46, bottomMargin=46,
                            leftMargin=56, rightMargin=56,
                            title=f"Parecer Técnico nº {numero_parecer}")
    base = getSampleStyleSheet()
    estilo_titulo = ParagraphStyle("titulo_central", parent=base["Title"],
                                   fontSize=12, alignment=TA_CENTER,
                                   spaceAfter=4)
    estilo_corpo = ParagraphStyle("corpo", parent=base["BodyText"],
                                  fontSize=10.5, leading=14)
    estilo_negrito = ParagraphStyle("negrito", parent=estilo_corpo,
                                    fontName="Helvetica-Bold")
    estilo_italico = ParagraphStyle("italico", parent=estilo_corpo,
                                    fontName="Helvetica-Oblique",
                                    fontSize=9.5)

    def esc(t: str) -> str:
        return (t.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;"))

    fluxo: list = []
    for linha in (texto or "").splitlines():
        bruta = linha.rstrip()
        if not bruta.strip():
            fluxo.append(Spacer(1, 5))
            continue
        if _LINHA_TITULO.match(bruta):
            fluxo.append(Paragraph(esc(bruta), estilo_titulo))
        elif bruta.startswith("    -> ") or bruta.startswith("    ("):
            fluxo.append(Paragraph(esc(bruta.strip()), estilo_italico))
        elif bruta.startswith("• ") or re.match(r"^\d+\.\s", bruta):
            fluxo.append(Paragraph(esc(bruta), estilo_negrito))
        else:
            fluxo.append(Paragraph(esc(bruta), estilo_corpo))
    doc.build(fluxo)
    pdf_bytes = buffer.getvalue()

    # VALIDAÇÃO (skill pdf): abre com pypdf e contém o marcador-chave
    from pypdf import PdfReader
    leitor = PdfReader(io.BytesIO(pdf_bytes))
    conteudo = "\n".join((p.extract_text() or "") for p in leitor.pages)
    if "PARECER TÉCNICO" not in conteudo:
        raise RuntimeError("PDF do parecer gerado sem o conteúdo esperado")
    return pdf_bytes
