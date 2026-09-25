# -*- coding: utf-8 -*-
"""COMPILADOR DO PARECER TÉCNICO (padrão oficial SEMA Campo Bom).
=================================================================
Segue o modelo oficial de Parecer Técnico da SEMA Campo Bom:
- Cabeçalho padronizado: PARECER TÉCNICO: {numero} – SEMA/CB, Empreendedor,
  CNPJ, Nº do processo, 'Prezados,', OBJETO DO PARECER;
- Seções técnicas concisas: IDENTIFICAÇÃO, DOCUMENTAÇÃO, PENDÊNCIAS,
  CONSTATAÇÕES TÉCNICAS (Termos de Referência) e CONCLUSÃO E EXIGÊNCIAS;
- Bloco de assinatura para o usuário completar (Nome, Cargo e Conselho/Registro);
- Banner oficial de Campo Bom inserido no topo das exportações .pdf e .docx.
"""
from __future__ import annotations

import io
import logging
import re
from datetime import date
from pathlib import Path
from typing import Optional

logger = logging.getLogger("licenciamento.compilador_parecer")

MESES_PT = [
    "", "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"
]


def compilar_texto_parecer(
        dados_processo: dict,
        quadro_documentos: Optional[list] = None,
        resultado_admin: Optional[dict] = None,
        resultados_tecnicos: Optional[list] = None,
        arquivos_recebidos: Optional[list] = None,
        resumo_quadro: Optional[dict] = None,
        comentarios_analista: Optional[str] = None,
        numero_parecer: str = "",
        numero_processo: Optional[str] = None,
        objeto_parecer: Optional[str] = None,
        nome_tecnico: Optional[str] = None,
        cargo_tecnico: Optional[str] = None,
        registro_tecnico: Optional[str] = None,
        prazo_dias: int = 30,
        data_referencia: Optional[date] = None,
        **kwargs) -> str:
    """Compila o parecer técnico estruturado conforme o padrão oficial da SEMA Campo Bom."""
    ref = data_referencia or date.today()
    dados_processo = dados_processo or {}
    quadro_documentos = quadro_documentos or []
    resultado_admin = resultado_admin or {}
    resultados_tecnicos = resultados_tecnicos or []
    resumo_quadro = resumo_quadro or {}

    # Resiliência para parâmetros passados via kwargs
    numero_processo = numero_processo or kwargs.get("numero_processo")
    objeto_parecer = objeto_parecer or kwargs.get("objeto_parecer")
    nome_tecnico = nome_tecnico or kwargs.get("nome_tecnico")
    cargo_tecnico = cargo_tecnico or kwargs.get("cargo_tecnico")
    registro_tecnico = registro_tecnico or kwargs.get("registro_tecnico")

    emp = dados_processo.get("empreendimento", {}) or {}
    empreendedor = dados_processo.get("empreendedor", {}) or {}
    pleito = dados_processo.get("pleito", {}) or {}
    razao = (empreendedor.get("nome_razao_social")
             or empreendedor.get("razao_social")
             or emp.get("nome_empreendimento") or "—")
    cnpj = (empreendedor.get("cpf_cnpj")
            or empreendedor.get("cnpj_cpf") or "—")
    
    # Campo limpo/preenchível se não fornecido
    proc_num = (str(numero_processo).strip() if (numero_processo and str(numero_processo).strip())
                else (dados_processo.get("numero_processo") or dados_processo.get("processo") or "________"))
    par_num = str(numero_parecer).strip() if (numero_parecer and str(numero_parecer).strip()) else "________"
    atividade = emp.get("nome_empreendimento") or emp.get("ramo_atividade") or "Atividade sob Licenciamento"
    tipo_lic = pleito.get("tipo_licenca") or "Licença Ambiental"
    fases = ", ".join(pleito.get("fases_componentes") or ["—"])

    # Cabeçalho estritamente no padrão oficial do parecer de Campo Bom
    linhas: list[str] = [
        f"PARECER TÉCNICO: {par_num} – SEMA/CB",
        f"PARECER TÉCNICO Nº {par_num}",
        f"Empreendedor: {razao}",
        f"CNPJ: {cnpj}",
        f"Nº do processo: {proc_num}",
        "",
        "Prezados,",
        "",
        "OBJETO DO PARECER",
    ]

    if objeto_parecer and objeto_parecer.strip():
        linhas.append(objeto_parecer.strip())
    else:
        linhas.append(
            f"Avaliação técnica da documentação ambiental e laudos apresentados para fins de "
            f"requerimento de {tipo_lic} referente ao empreendimento '{razao}', bem como a "
            f"verificação do cumprimento das condicionantes operacionais e dos Termos de Referência "
            f"oficiais da SEMA Campo Bom."
        )

    linhas += [
        "",
        "1. IDENTIFICAÇÃO DO PROCESSO",
        f"Interessado: {razao} (CPF/CNPJ: {cnpj})",
        f"Atividade: {atividade}",
        f"Ramo/CODRAM: {emp.get('ramo_atividade') or '—'} | "
        f"Porte: {emp.get('porte') or '—'} | "
        f"Potencial poluidor: {emp.get('potencial_poluidor') or '—'}",
        f"Licença pleiteada: {tipo_lic} (fases: {fases})",
        f"Data da análise: {ref:%d/%m/%Y}",
        "",
        "2. DOCUMENTAÇÃO APRESENTADA",
    ]
    if arquivos_recebidos:
        linhas.append(f"Foram apresentados {len(arquivos_recebidos)} documento(s) para instrução do processo.")
    else:
        linhas.append("Nenhum documento apresentado.")

    linhas += ["", "3. ANÁLISE DA DOCUMENTAÇÃO EXIGIDA PARA A LICENÇA"]
    faltantes_checklist = [l for l in quadro_documentos if l.get("situacao") == "NAO_APRESENTADO"]
    pendentes_checklist = [l for l in quadro_documentos if l.get("situacao") == "PENDENTE"]

    if faltantes_checklist or pendentes_checklist:
        if faltantes_checklist:
            linhas.append("Documentos exigidos pelo checklist e NÃO apresentados:")
            for l in faltantes_checklist:
                linhas.append(f"• Apresentar: {l.get('documento')}")
        if pendentes_checklist:
            if faltantes_checklist:
                linhas.append("")
            linhas.append("Documentos apresentados com pendência documental:")
            for l in pendentes_checklist:
                arq_info = f" (arquivo: {l.get('arquivo')})" if l.get('arquivo') else ""
                linhas.append(f"• {l.get('documento')}{arq_info}:")
                for pend in l.get("pendencias") or []:
                    linhas.append(f"    - {pend}")
    else:
        if quadro_documentos:
            linhas.append("Todos os documentos exigidos pelo checklist da licença foram apresentados.")
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

    linhas += [
        "",
        "CONSTATAÇÕES TÉCNICAS",
        "5. ANÁLISE TÉCNICA — TERMOS DE REFERÊNCIA"
    ]
    houve_tecnica = False

    def _limpar_erro_tecnico(item: str) -> Optional[str]:
        txt = item.strip()
        txt_l = txt.lower()
        if any(ign in txt_l for ign in [
            "dupla checagem", "1ª execução", "2ª execução", "divergentes entre a 1ª",
            "motor da análise", "origem da análise", "heurístico", "determinístico"
        ]):
            return None
        return txt

    for resultado in resultados_tecnicos:
        reprovados_brutos = getattr(resultado, "itens_reprovados", None) or []
        itens_filtrados = []
        for it in reprovados_brutos:
            it_limpo = _limpar_erro_tecnico(it)
            if it_limpo and it_limpo not in itens_filtrados:
                itens_filtrados.append(it_limpo)

        if itens_filtrados:
            houve_tecnica = True
            norma_nome = getattr(resultado, "norma_tr", "TR Aplicável")
            doc_nome = getattr(resultado, "documento_analisado", "Documento")
            linhas.append(f"• {doc_nome} ({norma_nome}):")
            for item in itens_filtrados:
                linhas.append(f"    - {item}")
    if not houve_tecnica:
        linhas.append("Laudos técnicos analisados em conformidade com os Termos de Referência aplicáveis.")

    linhas += [
        "",
        "CONCLUSÃO E EXIGÊNCIAS",
        "6. CONCLUSÃO — PROVIDÊNCIAS PARA COMPLEMENTAÇÃO"
    ]
    if comentarios_analista:
        linhas.append("Considerações do analista:")
        for paragrafo in comentarios_analista.split("\n"):
            if paragrafo.strip():
                linhas.append(paragrafo.strip())
        linhas.append("")

    faltando: list[str] = []
    # 1) Faltantes no checklist
    for linha in quadro_documentos:
        if linha.get("situacao") == "NAO_APRESENTADO":
            faltando.append(f"Apresentar: {linha.get('documento')}.")
        elif linha.get("situacao") == "PENDENTE":
            for pend in linha.get("pendencias") or []:
                faltando.append(f"Regularizar '{linha.get('documento')}': {pend}")

    # 2) Pendências administrativas
    for p in pend_admin:
        if p not in faltando:
            faltando.append(p)

    # 3) Pendências técnicas nos laudos
    for resultado in resultados_tecnicos:
        reprovados_brutos = getattr(resultado, "itens_reprovados", None) or []
        for it in reprovados_brutos:
            it_limpo = _limpar_erro_tecnico(it)
            if it_limpo:
                item_oficio = f"No documento '{resultado.documento_analisado}': {it_limpo}"
                if item_oficio not in faltando:
                    faltando.append(item_oficio)

    faltando_dedup = list(dict.fromkeys(faltando))
    if faltando_dedup:
        linhas.append(
            f"Diante das constatações, o empreendedor fica notificado a atender, "
            f"no prazo de {prazo_dias} dias corridos (improrrogável) contados do recebimento deste parecer, "
            f"às seguintes exigências sob pena de indeferimento do processo e sanções administrativas cabíveis:")
        for i, item in enumerate(faltando_dedup, 1):
            linhas.append(f"{i}. {item}")
    else:
        linhas.append("Análise concluída SEM EXIGÊNCIAS pendentes: a "
                      "documentação apresentada e os laudos técnicos atendem "
                      "integralmente aos requisitos exigidos para esta fase do licenciamento.")

    # Data por extenso no padrão formal de Campo Bom
    dia_str = "1º" if ref.day == 1 else str(ref.day)
    mes_str = MESES_PT[ref.month] if 1 <= ref.month <= 12 else ""
    data_extenso = f"{dia_str} de {mes_str} de {ref.year}"

    nome_tec = (nome_tecnico or "GABRIEL HENNEMANN KLASER").strip()
    cargo_tec = (cargo_tecnico or "ASSESSOR SUPERIOR SETORIAL DE LICENCIAMENTO AMBIENTAL").strip()
    reg_tec = (registro_tecnico or "CREA RS230362").strip()

    linhas += [
        "",
        "",
        f"Campo Bom, {data_extenso}.",
        "Atenciosamente,",
        "",
        nome_tec,
        cargo_tec,
        reg_tec,
    ]
    return "\n".join(linhas)


# ============================================================================
# EXPORTAÇÕES (.docx e .pdf) com BANNER OFICIAL DE CAMPO BOM
# ============================================================================
_LINHA_TITULO = re.compile(r"^(PARECER TÉCNICO|PREFEITURA|SECRETARIA|"
                           r"Setor de Licenciamento)", re.I)

_LINHA_SECAO = re.compile(r"^(OBJETO DO PARECER|CONSTATAÇÕES TÉCNICAS|"
                          r"CONCLUSÃO E EXIGÊNCIAS|\d+\.\s+[A-ZÀ-Ú])", re.I)

_LINHA_CABECALHO = re.compile(r"^(Empreendedor:|CNPJ:|Nº do processo:|Prezados,)", re.I)


def _obter_caminho_banner() -> Optional[Path]:
    """Localiza o arquivo de imagem do banner de Campo Bom."""
    caminhos = [
        Path(__file__).resolve().parent.parent / "assets" / "banner_campo_bom.jpg",
        Path("assets/banner_campo_bom.jpg"),
        Path("parecer_img_p1_0.jpeg"),
    ]
    for c in caminhos:
        if c.exists():
            return c
    return None


def exportar_docx(texto: str, numero_parecer: str = "303/2026") -> bytes:
    """Converte o texto do parecer em .docx com cabeçalho oficial e banner."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches, Pt

    doc = Document()
    for secao in doc.sections:
        secao.top_margin = Pt(36)
        secao.bottom_margin = Pt(36)
        secao.left_margin = Pt(50)
        secao.right_margin = Pt(50)

    # 1. Inserção do Banner oficial de Campo Bom no topo do DOCX
    banner = _obter_caminho_banner()
    if banner:
        try:
            doc.add_picture(str(banner), width=Inches(6.2))
            p_sep = doc.add_paragraph()
            p_sep.paragraph_format.space_after = Pt(8)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Não foi possível carregar o banner no docx: %s", exc)

    def par(conteudo: str, negrito: bool = False, tamanho: int = 11,
            centro: bool = False, italico: bool = False, space_after: int = 2) -> None:
        p = doc.add_paragraph()
        run = p.add_run(conteudo)
        run.font.size = Pt(tamanho)
        run.bold = negrito
        run.italic = italico
        p.paragraph_format.space_after = Pt(space_after)
        if centro:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    for linha_bruta in (texto or "").splitlines():
        linha = linha_bruta.rstrip()
        if not linha.strip():
            par("", tamanho=4, space_after=2)
            continue
        if _LINHA_TITULO.match(linha):
            par(linha, negrito=True, tamanho=12, space_after=4)
        elif _LINHA_SECAO.match(linha):
            par(linha, negrito=True, tamanho=11, space_after=4)
        elif _LINHA_CABECALHO.match(linha):
            par(linha, negrito=True, tamanho=10.5, space_after=2)
        elif linha.startswith("• "):
            par(linha, tamanho=10, space_after=3)
        elif linha.startswith("    - "):
            par(linha, tamanho=9.5, italico=True, space_after=2)
        elif linha.startswith("Atenciosamente,"):
            par(linha, tamanho=11, space_after=12)
        elif re.match(r"^[A-ZÀ-Ú\s]{4,}$", linha) and len(linha) < 60:
            # Nome do técnico em destaque
            par(linha, negrito=True, tamanho=11, space_after=2)
        else:
            par(linha, tamanho=10 if len(linha) > 90 else 10.5, space_after=3)

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def exportar_pdf(texto: str, numero_parecer: str = "303/2026") -> bytes:
    """Converte o texto do parecer em .pdf com layout oficial e banner de Campo Bom."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.platypus import Image as RLImage, Paragraph, SimpleDocTemplate, Spacer

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            topMargin=36, bottomMargin=36,
                            leftMargin=48, rightMargin=48,
                            title=f"Parecer Técnico nº {numero_parecer}")
    base = getSampleStyleSheet()

    estilo_titulo = ParagraphStyle("titulo_parecer", parent=base["Title"],
                                   fontSize=11.5, alignment=TA_LEFT,
                                   fontName="Helvetica-Bold", spaceAfter=2)
    estilo_secao = ParagraphStyle("secao_destaque", parent=base["Heading2"],
                                  fontSize=10.5, fontName="Helvetica-Bold",
                                  spaceBefore=6, spaceAfter=3)
    estilo_cabecalho = ParagraphStyle("cabecalho_destaque", parent=base["BodyText"],
                                      fontSize=10, fontName="Helvetica-Bold",
                                      spaceAfter=2)
    estilo_corpo = ParagraphStyle("corpo", parent=base["BodyText"],
                                  fontSize=9.5, leading=13)
    estilo_negrito = ParagraphStyle("negrito", parent=estilo_corpo,
                                    fontName="Helvetica-Bold")
    estilo_italico = ParagraphStyle("italico", parent=estilo_corpo,
                                    fontName="Helvetica-Oblique",
                                    fontSize=8.8)

    def esc(t: str) -> str:
        return (t.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;"))

    fluxo: list = []

    # 1. Inserção do Banner oficial de Campo Bom no topo do PDF
    banner = _obter_caminho_banner()
    if banner:
        try:
            w = 499  # largura imprimível A4 (595 - 48 - 48)
            h = w / (1600 / 362)
            fluxo.append(RLImage(str(banner), width=w, height=h))
            fluxo.append(Spacer(1, 10))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Não foi possível inserir banner no PDF: %s", exc)

    for linha in (texto or "").splitlines():
        bruta = linha.rstrip()
        if not bruta.strip():
            fluxo.append(Spacer(1, 4))
            continue
        if _LINHA_TITULO.match(bruta):
            fluxo.append(Paragraph(esc(bruta), estilo_titulo))
        elif _LINHA_SECAO.match(bruta):
            fluxo.append(Paragraph(esc(bruta), estilo_secao))
        elif _LINHA_CABECALHO.match(bruta):
            fluxo.append(Paragraph(esc(bruta), estilo_cabecalho))
        elif bruta.startswith("    - "):
            fluxo.append(Paragraph(esc(bruta.strip()), estilo_italico))
        elif bruta.startswith("• "):
            fluxo.append(Paragraph(esc(bruta), estilo_negrito))
        elif bruta.startswith("Atenciosamente,"):
            fluxo.append(Spacer(1, 6))
            fluxo.append(Paragraph(esc(bruta), estilo_corpo))
            fluxo.append(Spacer(1, 6))
        elif re.match(r"^[A-ZÀ-Ú\s]{4,}$", bruta) and len(bruta) < 60:
            fluxo.append(Paragraph(esc(bruta), estilo_negrito))
        else:
            fluxo.append(Paragraph(esc(bruta), estilo_corpo))

    doc.build(fluxo)
    pdf_bytes = buffer.getvalue()

    # Validação (skill pdf): abre com pypdf e confere conteúdo
    from pypdf import PdfReader
    leitor = PdfReader(io.BytesIO(pdf_bytes))
    conteudo = "\n".join((p.extract_text() or "") for p in leitor.pages)
    if "PARECER TÉCNICO" not in conteudo:
        raise RuntimeError("PDF do parecer gerado sem o conteúdo esperado")
    return pdf_bytes
