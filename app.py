# -*- coding: utf-8 -*-
"""
FASE 4 - Dashboard de Licenciamento Ambiental (Streamlit)
=========================================================

Fluxo em DUAS ETAPAS (wizard):

    ETAPA 1 — UPLOAD: a página inicial pede apenas a subida dos documentos
    do processo (.htm/.html do formulário, .pdf, .docx, .xlsx, .txt).

    ETAPA 2 — ANÁLISE: após carregar os arquivos, exibe a avaliação com o
    QUADRO RESUMO da documentação (recebidos em conformidade / com
    pendências / não apresentados + quais são as pendências), as análises
    por documento (ex.: validade da matrícula - 90 dias da emissão) e, ao
    final, o botão de emissão do PARECER TÉCNICO (.docx) apontando o que
    falta para contemplar toda a documentação da licença.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from licenciamento.agente_administrativo import AgenteAdministrativo
from licenciamento.agente_financeiro import AgenteFinanceiro
from licenciamento.agente_gis import NORMA_GIS, AgenteGIS
from licenciamento.auditor_tecnico import AuditorTecnico
from licenciamento.calibracao import Calibracao
from licenciamento.esquemas_tecnicos import StatusValidacao
from licenciamento.gerador_oficios import GeradorOficios
from licenciamento.seguranca import (attr_html, md_seguro,
                                     nome_arquivo_seguro, sufixo_seguro)
import importlib
import licenciamento.compilador_parecer
importlib.reload(licenciamento.compilador_parecer)
from licenciamento.compilador_parecer import (compilar_texto_parecer,
                                              exportar_docx, exportar_pdf)
from licenciamento.parser_formulario import FormularioParser
from licenciamento.validador_documentos import (EXTENSOES_GIS,
                                                EXTENSOES_IMAGEM,
                                                EXTENSOES_TEXTO,
                                                ValidadorDocumentos)

RAIZ = Path(__file__).resolve().parent

# ==============================================================================
# SISTEMA VISUAL (skill frontend-design: FRAME -> SYSTEM -> COMPOSE -> MOTION)
# Ideia-assinatura: "DOSSIÊ DE PROCESSO" - trilha de etapas no topo + fichas
# com régua de status. TODA cor é token (variável CSS); 2 temas = 2 conjuntos.
# ==============================================================================
CSS_TOKENS = """
<style>
:root {
  --c-bg: #f6f7f4;            /* fundo da página (neutro quente) */
  --c-surface: #ffffff;       /* superfícies (fichas, cards) */
  --c-surface-2: #eef1ea;     /* superfície secundária */
  --c-border: #d9ded4;
  --c-text: #1c2419;
  --c-muted: #5c6657;
  --c-brand: #2e7d32;         /* verde institucional SEMA */
  --c-brand-dark: #1b5e20;
  --c-brand-soft: #e4efe2;
  --c-ok: #2e7d32; --c-warn: #b26a00; --c-bad: #b3261e; --c-neutral: #6b7280;
  --r-lg: 14px; --r-sm: 8px;  /* só 2 raios */
  --s-1: 8px; --s-2: 16px; --s-3: 24px;
  --dur: 0.16s;               /* movimento único e curto */
}
/* Cabeçalho institucional (marca) */
.sema-marca {
  border-left: 6px solid var(--c-brand);
  background: var(--c-surface);
  border-radius: var(--r-sm);
  padding: var(--s-2) var(--s-2);
  margin-bottom: var(--s-2);
}
.sema-marca .titulo { font-size: 1.45rem; font-weight: 700;
  color: var(--c-text); line-height: 1.25; }
.sema-marca .subtitulo { font-size: .88rem; color: var(--c-muted);
  margin-top: 2px; }
/* TRILHA DE ETAPAS (assinatura visual) */
.sema-trilha { display: flex; gap: 0; margin: var(--s-1) 0 var(--s-3); }
.sema-trilha .etapa { flex: 1; display: flex; align-items: center; gap: 10px;
  padding: 10px 14px; background: var(--c-surface);
  border: 1px solid var(--c-border); border-left: none; }
.sema-trilha .etapa:first-child { border-left: 1px solid var(--c-border);
  border-radius: var(--r-sm) 0 0 var(--r-sm); }
.sema-trilha .etapa:last-child { border-radius: 0 var(--r-sm) var(--r-sm) 0; }
.sema-trilha .bola { width: 28px; height: 28px; min-width: 28px;
  border-radius: 50%; display: flex; align-items: center; justify-content:
  center; font-weight: 700; font-size: .9rem;
  background: var(--c-surface-2); color: var(--c-muted);
  border: 2px solid var(--c-border); transition: all var(--dur) ease; }
.sema-trilha .rotulo { font-size: .86rem; color: var(--c-muted); }
.sema-trilha .rotulo b { display: block; font-size: .95rem; }
.sema-trilha .atual .bola { background: var(--c-brand);
  border-color: var(--c-brand-dark); color: #fff; }
.sema-trilha .atual { border-top: 3px solid var(--c-brand); }
.sema-trilha .atual .rotulo { color: var(--c-text); }
.sema-trilha .concluida .bola { background: var(--c-brand-soft);
  border-color: var(--c-brand); color: var(--c-brand-dark); }
/* Fichas de métrica com régua de status */
[data-testid="stMetric"] { background: var(--c-surface);
  border: 1px solid var(--c-border); border-top: 3px solid var(--c-brand);
  border-radius: var(--r-sm); padding: 10px 14px;
  transition: box-shadow var(--dur) ease; }
[data-testid="stMetric"]:hover { box-shadow: 0 2px 10px rgba(28,36,25,.08); }
[data-testid="stMetricLabel"] p { font-size: .80rem !important;
  color: var(--c-muted) !important; }
[data-testid="stMetricValue"] { font-size: 1.02rem !important;
  color: var(--c-text) !important; }
/* Superfícies gerais */
[data-testid="stExpander"], details { background: var(--c-surface);
  border: 1px solid var(--c-border) !important; border-radius: var(--r-sm); }
[data-testid="stCaptionContainer"] p { color: var(--c-muted) !important; }
[data-testid="stFileUploaderDropzone"] { background: var(--c-surface) !important;
  border: 1.5px dashed var(--c-border) !important;
  transition: border-color var(--dur) ease; }
[data-testid="stFileUploaderDropzone"]:hover { border-color: var(--c-brand) !important; }
hr { border-color: var(--c-border); }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>
"""
CSS_TEMA_ESCURO = """
<style>
/* TEMA ESCURO = apenas troca de tokens (mesma estrutura visual) */
:root {
  --c-bg: #0f1216; --c-surface: #171c23; --c-surface-2: #1d242e;
  --c-border: #2a313c; --c-text: #e8eaed; --c-muted: #aab2bd;
  --c-brand: #5cb860; --c-brand-dark: #79cf7d; --c-brand-soft: #1f2b20;
  --c-ok: #5cb860; --c-warn: #e0a340; --c-bad: #e57368; --c-neutral: #97a1ad;
}
[data-testid="stApp"], [data-testid="stAppViewContainer"],
[data-testid="stAppViewContainer"] > .main { background: var(--c-bg);
  color: var(--c-text); }
[data-testid="stHeader"] { background: rgba(15,18,22,0.2); }
[data-testid="stSidebar"] { background: #141920;
  border-right: 1px solid var(--c-border); }
[data-testid="stSidebar"] * { color: var(--c-text); }
h1, h2, h3, h4, h5, h6, p, li, strong, b, label, summary { color: var(--c-text); }
[data-testid="stAlert"] { background-color: var(--c-surface-2) !important;
  color: var(--c-text) !important; border: 1px solid var(--c-border) !important; }
[data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea {
  background: var(--c-surface) !important; color: var(--c-text) !important;
  border: 1px solid var(--c-border) !important; }
[data-testid="stJson"] { background: var(--c-surface) !important; }
[data-testid="stMarkdownContainer"] a { color: #7fc4ff; }
</style>
"""


def aplicar_tema(escuro: bool) -> None:
    """Injeta o SISTEMA de tokens (sempre) + o override do tema escolhido."""
    st.markdown(CSS_TOKENS, unsafe_allow_html=True)
    if escuro:
        st.markdown(CSS_TEMA_ESCURO, unsafe_allow_html=True)


def link_download(bytes_conteudo: bytes, nome_arquivo: str,
                  mime: str, rotulo: str) -> str:
    """LINK DE DOWNLOAD data-URI (à prova de proxy): o conteúdo viaja dentro
    da própria página, então SEMPRE baixa no navegador. BLINDADO (skill
    security-audit): nome passado por slug seguro e TODOS os atributos
    escapados — anti quebra de atributo/injeção de evento."""
    import base64 as _b64
    payload = _b64.b64encode(bytes_conteudo).decode("ascii")
    return ('<a download="' + nome_arquivo_seguro(nome_arquivo)
            + '" href="data:' + attr_html(mime)
            + ";base64," + payload + '" style="display:inline-block;'
            'padding:6px 10px;border:1px solid var(--c-border, #ccc);'
            'border-radius:8px;text-decoration:none;font-size:.9rem">'
            + attr_html(rotulo) + "</a>")


def cabecalho_institucional(subtitulo: str) -> None:
    """Marca da aplicação (SYSTEM/COMPOSE): faixa verde institucional."""
    st.markdown(
        '<div class="sema-marca"><div class="titulo">'
        '🌿 Sistema de Verificação do Licenciamento Ambiental</div>'
        f'<div class="subtitulo">{subtitulo}</div></div>',
        unsafe_allow_html=True)


def trilha_etapas(atual: int) -> None:
    """TRILHA DE ETAPAS (elemento-assinatura da interface): mostra onde o
    licenciador está no fluxo e o que vem a seguir."""
    etapas = [("1", "Declarar e enviar", "Licença + documentos do processo"),
              ("2", "Avaliar o dossiê", "Checklist, taxas e parecer")]
    pedacos = []
    for numero, titulo, detalhe in etapas:
        n = int(numero)
        classe = ("atual" if n == atual else
                  "concluida" if n < atual else "")
        pedacos.append(
            f'<div class="etapa {classe}"><div class="bola">'
            f'{"✓" if n < atual else numero}</div>'
            f'<div class="rotulo"><b>{titulo}</b>{detalhe}</div></div>')
    st.markdown('<div class="sema-trilha">' + "".join(pedacos) + "</div>",
                unsafe_allow_html=True)


st.set_page_config(page_title="Licenciamento Ambiental — SEMA Campo Bom",
                   page_icon="🌿", layout="wide")

# ---- Preferências: tema claro (padrão) ou escuro, na barra lateral ----
with st.sidebar:
    st.markdown(
        '<div class="sema-marca"><div class="titulo" style="font-size:1.05rem">'
        'SEMA · Campo Bom</div><div class="subtitulo">Secretaria do Meio '
        'Ambiente — licenciamento ambiental</div></div>',
        unsafe_allow_html=True)
    st.header("⚙️ Preferências")
    st.toggle("🌙 Tema escuro", key="tema_escuro",
              help="Alterna entre o tema claro (padrão) e o tema escuro.")
aplicar_tema(bool(st.session_state.get("tema_escuro")))

# ---- AGENTE AUDITOR DO SISTEMA (independente): bateria de verificações ->
# DUPLA CHECAGEM de cada achado -> correção automática segura -> relatório.
# Botão na barra lateral; resultado no painel logo abaixo. ----
with st.sidebar:
    if st.button("🧭 Auditar sistema", type="secondary",
                 help="Verifica configs, parser (fixtures), pleitos/taxas, "
                      "ambiente e integração; cada erro é DUPLA-CHECADO e os "
                      "de correção segura são corrigidos na hora. Bateria "
                      "completa (com testes): python -m "
                      "licenciamento.auditor_sistema"):
        with st.spinner("Agente auditor: detectando e dupla-checando..."):
            from licenciamento.auditor_sistema import AuditorSistema
            _aud = AuditorSistema(com_testes=False,
                                  raiz=Path(__file__).resolve().parent)
            _aud.auditar()
            _aud.corrigir()
            st.session_state["auditoria"] = _aud.relatorio()

if st.session_state.get("auditoria"):
    with st.expander("🧭 Auditoria do sistema — agente independente",
                     expanded=True):
        _rel = st.session_state["auditoria"]
        _r = _rel["resumo"]
        _c1, _c2, _c3, _c4 = st.columns(4)
        _c1.metric("Achados", _r["total"])
        _c2.metric("Confirmados", _r["por_status"].get("CONFIRMADO", 0))
        _c3.metric("Corrigidos", _r["por_status"].get("CORRIGIDO", 0))
        _c4.metric("Pendentes", _r["por_status"].get("PENDENTE", 0))
        if _rel["erros"]:
            st.dataframe(pd.DataFrame(
                [{"ID": e["id"], "Severidade": e["severidade"],
                  "Status": e["status"], "Componente": e["componente"],
                  "Descrição": e["descricao"], "Correção": e["correcao"]}
                 for e in _rel["erros"]],
                width="stretch", hide_index=True, height=280))
        else:
            st.success("✅ Nenhum erro confirmado — sistema íntegro.")
        st.caption("Achados instáveis (não repetidos na 2ª passada) são "
                   "descartados como falso positivo · relatórios em auditoria/")


# Formatos aceitos no upload (etapa 1) - inclui IMAGENS, pois documentações
# às vezes são enviadas como fotos/escaneamentos (png, jpg, etc.)
FORMATOS_UPLOAD = ["htm", "html", "pdf", "docx", "doc", "xlsx", "xls",
                   "txt", "csv", "rtf",
                   "png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff", "gif",
                   # camadas GIS exigidas pelo checklist oficial
                   # ("Arquivo KMZ/KML ... curvas de nível e mapa de APPs")
                   "kml", "kmz", "geojson", "gpx"]


def icone_arquivo(nome: str) -> str:
    """Ícone do arquivo na listagem do upload (imagem x GIS x documento)."""
    if Path(nome).suffix.lower() in EXTENSOES_IMAGEM:
        return "🖼️"
    if Path(nome).suffix.lower() in EXTENSOES_GIS:
        return "🗺️"
    if Path(nome).suffix.lower() in (".htm", ".html"):
        return "🧾"
    return "📄"


# ======================================================================
# Helpers de apresentação
# ======================================================================
def _fmt_urm(valor) -> str:
    """Formata um valor de URM no padrão brasileiro (ex.: 5.001,40)."""
    if valor is None:
        return "—"
    return f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def emoji_situacao(situacao: str) -> str:
    """Semáforo do quadro resumo de documentos."""
    return {"CONFORME": "✅", "PENDENTE": "🟡",
            "NAO_APRESENTADO": "❌"}.get(situacao, "⚪")


def rotulo_situacao(situacao: str) -> str:
    return {"CONFORME": "Em conformidade", "PENDENTE": "Com pendência(s)",
            "NAO_APRESENTADO": "Não apresentado"}.get(situacao, situacao)


def emoji_status_tecnico(status: StatusValidacao) -> str:
    return {"CONFORME": "🟢", "PENDENTE": "🟡",
            "REVISAO_MANUAL": "🔵"}.get(getattr(status, "value", str(status)), "⚪")


def rodape_calibracao() -> None:
    """Rodapé: fontes oficiais ativas (config/)."""
    cal = Calibracao()
    fontes: list[str] = []
    if cal.taxas_urm:
        selo = "" if cal.taxas_urm.get("revisado") else " (🟡 rascunho)"
        fontes.append(f"💰 Taxas: {cal.taxas_urm.get('fonte')}{selo}")
    if cal.gabarito_trs:
        selo = "" if cal.gabarito_trs.get("revisado") else " (🟡 rascunho)"
        fontes.append(f"📐 TRs: {cal.gabarito_trs.get('fonte')}{selo}")
    if cal.checklists_oficiais:
        selo = "" if cal.checklists_oficiais.get("revisado") else " (🟡 rascunho)"
        fontes.append(f"📋 Formulários: {cal.checklists_oficiais.get('fonte')}{selo}")
    if cal.regras_documentos:
        fontes.append(f"🗂️ Regras documentais: {cal.regras_documentos.get('fonte')}")
    if fontes:
        st.caption("Calibração ativa — " + "  |  ".join(fontes))


# ======================================================================
# ETAPA 2 — Motor da análise (parser + agentes + quadro + parecer)
# ======================================================================
def _ponto_do_formulario(dados: dict) -> Optional[tuple[float, float]]:
    """Ponto do empreendimento em (longitude, latitude) graus decimais.

    Os formulários do RS declaram UTM (E/N/Fuso 22S); as camadas GIS vêm em
    geográficas - a conversão é obrigatória para o confronto geométrico
    (skill gis-multicamadas)."""
    from licenciamento.leitor_gis import utm_para_lonlat
    coord = (dados.get("empreendimento") or {}).get("coordenadas") or {}
    if not isinstance(coord, dict):
        return None
    lat, lon = coord.get("latitude"), coord.get("longitude")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        return (float(lon), float(lat))
    leste, norte = coord.get("easting_m"), coord.get("northing_m")
    if isinstance(leste, (int, float)) and isinstance(norte, (int, float)):
        fuso = coord.get("fuso") or 22
        hemisferio = (coord.get("hemisferio") or "S")
        ponto = utm_para_lonlat(float(leste), float(norte),
                                int(fuso), str(hemisferio))
        if ponto:
            return ponto
    return None


def executar_analise(arquivos: list, tipo_selecionado: str,
                     natureza_selecionada: str = "Primeira licença") -> None:
    """Processa os arquivos carregados e monta o estado do processo.

    A licença pleiteada é a DECLARADA pelo licenciador na Etapa 1 (fonte da
    verdade do tipo): ela dirige a listagem de documentos exigidos e o cálculo
    da taxa. O formulário HTML é lido para COMPLETAR os demais dados (CODRAM,
    Área total de intervenção/útil, Porte/Potencial Poluidor, coordenadas).
    Se a marcação do item 3 divergir da seleção, a divergência é sinalizada.

    Args:
        arquivos: lista de objetos com `.name` e `.getvalue()` (UploadedFile
            do Streamlit).
        tipo_selecionado: sigla do pleito (LP, LI, LO, LIR, LOR).
        natureza_selecionada: 'Primeira licença' ou 'Renovação'.
    """
    validador = ValidadorDocumentos()

    formularios = [a for a in arquivos if Path(a.name).suffix.lower() in (".htm", ".html")]
    documentos = [a for a in arquivos
                  if Path(a.name).suffix.lower() not in (".htm", ".html")]

    # ---- Fase 1: parser do formulário -------------------------------
    dados: dict = {}
    if formularios:
        salvar_entrada_real(formularios, {})
        try:
            parser = FormularioParser(conteudo_html=formularios[0].getvalue().decode(
                "utf-8", errors="replace"))
            dados = parser.parse()
            # o pleito DECLARADO na Etapa 1 prevalece e re-monta a listagem
            # de documentos das fases correspondentes
            dados = parser.aplicar_pleito_manual(
                tipo_selecionado, natureza_selecionada)
            salvar_entrada_real([], dados)  # JSON da leitura (caixa-preta)
        except Exception as exc:  # noqa: BLE001
            st.session_state.erro_formulario = str(exc)
            salvar_entrada_real([], {"erro": str(exc)})

    pleito = dados.get("pleito", {})
    tipo_licenca = pleito.get("tipo_licenca")

    # checklist da licença: deduplicado do formulário (ou oficial por fase)
    exigencias: list[str] = list(
        dados.get("documentos_exigidos", {}).get("lista_deduplicada") or [])
    if not exigencias and tipo_licenca:
        cal = Calibracao()
        por_fase = (cal.checklists_oficiais or {}).get("documentos_por_fase", {})
        fases = {"LIR": ["LP", "LI"], "LOR": ["LP", "LI", "LO"]}.get(
            tipo_licenca, [tipo_licenca])
        for fase in fases:
            exigencias.extend(por_fase.get(fase, []))

    # ---- Extração de texto e análise por documento -------------------
    arquivos_analise: list[dict] = []
    textos_anexos: dict[str, str] = {}
    analises: dict[str, object] = {}
    resultados_tecnicos: list = []
    auditor = AuditorTecnico()
    # ARTs/RTTs DECLARADAS no formulário HTML (RT principal + seção 4.3):
    # base de conferência para os documentos que são uma ART/RTT
    arts_formulario: list[dict] = []
    rt_principal = dados.get("responsavel_tecnico") or {}
    if rt_principal.get("registro_art"):
        arts_formulario.append({"numero": rt_principal.get("registro_art"),
                                "nome": rt_principal.get("nome"),
                                "secao": "8",
                                "registro": rt_principal.get("registro_crea")})
    # Seção 4.3: PODEM EXISTIR VÁRIAS RTTs/ARTs (Projeto Urbanístico,
    # Execução de Obras...). Cada uma entra com o conselho do profissional:
    # RTT/RRT é do CAU/BR; ART é do CREA/CRBio.
    for prof in dados.get("responsaveis_etapas") or []:
        if prof.get("art_rtt"):
            arts_formulario.append({"numero": prof.get("art_rtt"),
                                    "nome": prof.get("nome"),
                                    "secao": "4.3",
                                    "registro": prof.get("registro"),
                                    "etapa": prof.get("etapa")})
    # áreas declaradas no formulário (conferência de projetos urbanísticos)
    _emp = dados.get("empreendimento") or {}
    areas_formulario = {"area_total_ha": _emp.get("area_total_ha"),
                        "area_util_ha": _emp.get("area_util_ha")}
    for arq in documentos:
        texto = validador.extrair_texto(arq.name, arq.getvalue())
        textos_anexos[arq.name] = texto
        registro = {"nome": arq.name, "texto": texto,
                    "tipo": validador.identificar_tipo(arq.name, texto)}
        arquivos_analise.append(registro)
        analises[arq.name] = validador.analisar_documento(arq.name, texto)
        if len(texto.strip()) >= 40:
            # DUPLA CHECAGEM sempre: 2ª execução + conferência título x TR
            resultados_tecnicos.extend(auditor.auditar_com_dupla_checagem(
                arq.name, texto,
                arts_formulario=arts_formulario or None,
                areas_formulario=areas_formulario or None,
                tipo_documento=registro["tipo"]))

    # ---- Camadas GIS: leitura + conferência geométrica ---------------
    # (skill gis-multicamadas: camadas têm confronto GEOMÉTRICO e NUNCA
    #  recebem TR de conteúdo - o AuditorTecnico já as exclui do roteamento)
    camadas_gis: list[dict] = []
    coordenada_form = _ponto_do_formulario(dados)
    agente_gis = AgenteGIS()
    for arq in documentos:
        if Path(arq.name).suffix.lower() not in EXTENSOES_GIS:
            continue
        pacote = agente_gis.ler(arq.name, arq.getvalue())
        for res in agente_gis.conferir(
                arq.name, pacote, areas_formulario=areas_formulario or None,
                ponto_empreendimento=coordenada_form):
            camadas_gis.append({
                "arquivo": res.documento_analisado,
                "camada": (res.metricas or {}).get("camada_area") or arq.name,
                "tema": (res.metricas or {}).get("tema") or "",
                "status": res.status.value,
                "norma_tr": res.norma_tr,
                "erros": list(res.itens_reprovados or []),
                "avisos": [],
                "metricas": res.metricas or {},
            })

    # o formulário também participa do casamento do quadro
    for form in formularios:
        arquivos_analise.append({"nome": form.name,
                                 "texto": validador.extrair_texto(
                                     form.name, form.getvalue()),
                                 "tipo": "FORMULARIO"})

    # bytes das imagens anexadas (preview para conferência manual no painel)
    imagens = {a.name: a.getvalue() for a in documentos
               if Path(a.name).suffix.lower() in EXTENSOES_IMAGEM}

    # ---- Fase 2: agentes administrativo e financeiro -----------------
    nomes_anexos = [a.name for a in arquivos]
    admin = AgenteAdministrativo().auditar(
        dados, nomes_anexos, textos_anexados=textos_anexos) if dados else {
        "status_geral": "BLOQUEADO",
        "bloqueios": ["Formulário .htm/.html do requerimento não apresentado."],
        "resumo": {"total_ok": 0, "total_pendentes": 0},
        "documentos_pendentes": [],
        "avisos": []}
    financeiro = AgenteFinanceiro().calcular_do_parser(dados) if dados else {}

    # ---- Quadro resumo (checklist x arquivos x análises) -------------
    quadro, extras = validador.montar_quadro(exigencias, arquivos_analise, analises)
    resumo_quadro = validador.resumo_quadro(quadro)

    st.session_state.processo = {
        "dados": dados,
        "admin": admin,
        "financeiro": financeiro,
        "tecnicos": resultados_tecnicos,
        "camadas_gis": camadas_gis,
        "analises": {k: v for k, v in analises.items()},
        "quadro": quadro,
        "extras": [e.get("nome") for e in extras],
        "resumo_quadro": resumo_quadro,
        "arquivos": nomes_anexos,
        "textos_anexos": textos_anexos,
        "arquivos_analise": arquivos_analise,
        "imagens": imagens,
        "exigencias": exigencias,
        "regras": {"fonte": validador.fonte_regras,
                   "revisado": validador.regras_revisadas,
                   "validade_matricula_dias": validador.matricula_validade_dias},
    }
    st.session_state.etapa = "analise"


def salvar_entrada_real(formularios: list, dados: dict) -> None:
    """CAIXA-PRETA: guarda o formulário REAL enviado pelo licenciador e o JSON
    produzido pelo parser em entradas_reais/ (fora do git). É o insumo para
    calibrar o leitor contra os layouts verdadeiros (Word->HTML)."""
    try:
        pasta = RAIZ / "entradas_reais"
        pasta.mkdir(exist_ok=True)
        carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")
        for i, form in enumerate(formularios[:3]):
            sufixo = sufixo_seguro(Path(form.name).suffix)  # anti traversal
            (pasta / f"{carimbo}_{i}_formulario{sufixo}").write_bytes(
                form.getvalue())
        if dados:
            (pasta / f"{carimbo}_leitura_parser.json").write_text(
                json.dumps(dados, ensure_ascii=False, indent=2),
                encoding="utf-8")
    except Exception:  # noqa: BLE001 — a caixa-preta nunca derruba a análise
        pass


# ======================================================================
# ETAPA 1 — Página inicial (só a inserção dos documentos)
# ======================================================================
def pagina_upload() -> None:
    cabecalho_institucional(
        "Prefeitura Municipal de Campo Bom/RS · leitura do requerimento, "
        "conferência de documentos e cálculo de taxa")
    trilha_etapas(atual=1)
    st.subheader("Secretaria Municipal do Meio Ambiente — Campo Bom/RS")

    st.markdown(
        "### 1️⃣ Declare a licença pleiteada\n"
        "A licença informada aqui **dirige a análise**: define a listagem de "
        "documentos exigidos e o cálculo da taxa (Manual de Taxas SEMA Campo "
        "Bom). Em seguida o sistema lê o formulário HTML e completa os demais "
        "dados (CODRAM, Área total de intervenção/útil, Porte/Potencial "
        "Poluidor, coordenadas, responsável técnico).")

    ROTULOS_TIPO = {"LP": "Licença Prévia (LP)",
                    "LI": "Licença de Instalação (LI)",
                    "LO": "Licença de Operação (LO)",
                    "LIR": "Licença de Instalação e Regularização (LIR)",
                    "LOR": "Licença de Operação e Regularização (LOR)",
                    "AUTORIZACAO": "Autorização Geral",
                    "PRAD": "PRAD - Plano de Recuperação de Área Degradada"}

    def _limitar_tipos_na_renovacao() -> None:
        """Renovação aplica-se apenas a LP, LI e LO (regularizações,
        autorizações e planos não se renovam)."""
        if (st.session_state.get("natureza_pleito") == "Renovação"
                and st.session_state.get("tipo_selecionado")
                not in ("LP", "LI", "LO")):
            st.session_state.tipo_selecionado = "LP"

    col_tipo, col_natureza = st.columns([1.5, 1.0])
    with col_natureza:
        natureza = st.radio("Natureza do pleito",
                            ["Primeira licença", "Renovação"],
                            key="natureza_pleito",
                            on_change=_limitar_tipos_na_renovacao)
    with col_tipo:
        tipo = st.radio(
            "Licença pleiteada",
            (["LP", "LI", "LO"] if natureza == "Renovação"
             else ["LP", "LI", "LO", "LIR", "LOR", "AUTORIZACAO", "PRAD"]),
            key="tipo_selecionado",
            format_func=lambda k: ROTULOS_TIPO[k])

    st.markdown(
        "### 2️⃣ Envie a documentação do processo\n"
        "**Formatos aceitos:** formulário `.htm`/`.html`, `.pdf`, "
        "Word (`.docx`), Excel (`.xlsx`), `.txt`/`.csv`, imagens e "
        "**camadas GIS (`.kml`/`.kmz`/`.geojson`/`.gpx`)** exigidas pelo "
        "checklist (projeto urbanístico, curvas de nível e mapa de APPs).")

    arquivos = st.file_uploader(
        "Documentos do processo (formulário + anexos)",
        type=FORMATOS_UPLOAD, accept_multiple_files=True,
        help="Inclua o formulário do requerimento (.htm/.html) e todos os "
             "documentos exigidos para a licença selecionada (matrícula, "
             "ART/RTT, laudos, PGRS, alvarás, camadas GIS KMZ/KML etc.).")

    if arquivos:
        st.markdown(f"**{len(arquivos)} arquivo(s) carregado(s):**")
        for arq in arquivos:
            st.markdown(f"- {icone_arquivo(arq.name)} `{arq.name}` — "
                        f"{len(arq.getvalue()) / 1024:,.0f} KB")
        qtde_img = sum(1 for a in arquivos if Path(a.name).suffix.lower() in EXTENSOES_IMAGEM)
        if qtde_img:
            st.caption(f"🖼️ {qtde_img} arquivo(s) de imagem: o conteúdo é conferido "
                       f"por OCR quando disponível; sem OCR, entram para conferência "
                       f"manual com preview na etapa de análise.")

    if st.button("🔍 Analisar documentação", type="primary",
                 disabled=(not arquivos or not tipo),
                 help="Executa a triagem, a conferência do checklist pela "
                      "licença selecionada, a auditoria técnica pelos TRs e "
                      "monta o quadro resumo."):
        with st.spinner("Analisando a documentação (Fases 1 a 3)..."):
            executar_analise(arquivos, tipo, natureza)
        st.rerun()

    if st.session_state.get("erro_formulario"):
        st.error(f"Falha ao interpretar o formulário: "
                 f"{st.session_state.erro_formulario}")

    rodape_calibracao()


def pagina_analise() -> None:
    processo = st.session_state.get("processo") or {}
    dados = processo.get("dados", {})
    admin = processo.get("admin", {})
    financeiro = processo.get("financeiro", {})
    tecnicos = processo.get("tecnicos", [])
    quadro = processo.get("quadro", [])
    resumo = processo.get("resumo_quadro", {})
    analises = processo.get("analises", {})

    cabecalho_institucional(
        "Dossiê do processo · triagem, taxas, auditoria técnica e parecer")
    trilha_etapas(atual=2)
    if st.button("⬅️ Enviar outros documentos"):
        st.session_state.etapa = "upload"
        st.session_state.processo = None
        st.rerun()

    emp = dados.get("empreendimento", {})
    empreendedor = dados.get("empreendedor", {})
    pleito = dados.get("pleito", {})
    # Nome de exibição: NOME FANTASIA do formulário; sem fantasia, o EMPREENDEDOR
    # (razão social); último caso, a denominação da atividade
    nome_exibicao = (empreendedor.get("nome_fantasia")
                     or empreendedor.get("nome_razao_social")
                     or emp.get("nome_empreendimento") or "—")
    # rótulos e valores das métricas com fonte menor (cabeçalho compacto)
    st.markdown(
        "<style>"
        '[data-testid="stMetricLabel"] p {font-size: 0.80rem !important;}'
        '[data-testid="stMetricValue"] {font-size: 1.02rem !important;}'
        "</style>", unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns([1.6, 1.0, 1.6, 0.9])
    c1.metric("Empreendimento", str(nome_exibicao)[:36])
    # Licença pleiteada = tipo MARCADO no formulário (seção MOTIVO DO
    # ENCAMINHAMENTO À SEMA), com a origem da leitura indicada
    c2.metric("Licença pleiteada", pleito.get("tipo_licenca") or "—")
    metodo_leitura = pleito.get("metodo_deteccao") or ""
    if metodo_leitura:
        origem_leitura = ("seleção sua na Etapa 1" if metodo_leitura
                          == "selecionado pelo licenciador na Etapa 1"
                          else f"via {metodo_leitura}")
        if pleito.get("natureza"):
            origem_leitura += f" • {pleito['natureza']}"
        c2.caption(origem_leitura)
    # Tipo de empreendimento = CODRAM/ramo da atividade licenciado (lido do
    # formulário); ex.: 'PARCELAMENTO DO SOLO...' com 'CODRAM 3414,40' na legenda
    ramo_ativ = (emp.get("ramo_atividade") or "").strip()
    codram = (emp.get("codram") or "").strip()
    if ramo_ativ:
        # SOMENTE a primeira linha do campo, mas com o texto COMPLETO
        descricao_tipo = re.sub(r"^[\d.,]+\s*[-–—]\s*", "",
                                ramo_ativ.splitlines()[0]).strip()
        c3.metric("Tipo de empreendimento", descricao_tipo)
    else:
        m_cod = re.match(r"\s*(\d{1,4}(?:[.,\-]\d{1,3})+|\d{3,4})", codram)
        c3.metric("Tipo de empreendimento",
                  (m_cod.group(1) if m_cod else codram) or "—")
    if codram:
        # legenda curta: somente o código (campo pode conter run-on da tabela)
        m_cod = re.match(r"\s*(\d{1,4}(?:[.,\-]\d{1,3})+|\d{3,4})", codram)
        codigo_curto = (m_cod.group(1) if m_cod
                        else codram.splitlines()[0][:20])
        c3.caption("CODRAM " + codigo_curto)
    # Diagnóstico do item 3: mostrado quando a marcação não foi lida OU
    # divergiu da seleção do licenciador (transparência, sem travar o fluxo)
    if not pleito.get("tipo_licenca") or pleito.get("divergencia_selecao"):
        brutas = pleito.get("leitura_bruta_secao") or []
        with st.expander("🔍 Leitura da marcação do item 3 "
                         "(MOTIVO DO ENCAMINHAMENTO) no formulário",
                         expanded=bool(pleito.get("divergencia_selecao"))):
            if pleito.get("divergencia_selecao"):
                st.warning("⚖️ " + pleito["divergencia_selecao"])
            if brutas:
                st.caption("Linhas lidas na seção (os símbolos de marcação "
                           "podem não ter sobrevivido à conversão p/ HTML):")
                st.code("\n".join(brutas) or "(seção vazia)", language=None)
            for av in (dados.get("avisos_parser") or []):
                if "MARCADA" in av or "DIVERGÊNCIA" in av:
                    st.caption("• " + av)
    c4.metric("Taxa (URMs)", _fmt_urm(financeiro.get("total_urm")))
    if financeiro.get("erro"):
        c4.caption(f"⚠️ {str(financeiro['erro'])[:60]}")
    elif financeiro.get("total_urm") is None:
        c4.caption("Formulário não lido - taxa indisponível")

    st.divider()

    # --------------------------------------------------------------
    # CONSOLIDAÇÃO DAS ETAPAS DO PROCESSO (Dupla checagem integrada)
    # --------------------------------------------------------------
    st.subheader("📊 Consolidação das Etapas do Processo")
    col_e1, col_e2, col_e3, col_e4 = st.columns(4)
    with col_e1:
        st_adm = admin.get("status_geral", "—")
        cor_adm = "normal" if st_adm == "LIBERADO" else "off"
        rotulo_adm = "✅ Regular" if st_adm == "LIBERADO" else ("🚫 Bloqueado" if st_adm == "BLOQUEADO" else "🟡 Pendências")
        st.metric("1. Etapa Administrativa", rotulo_adm, help="Conferência de formulário, CNPJ/CPF e checklist de documentos obrigatórios.")
    with col_e2:
        tot_urm = financeiro.get("total_urm")
        rotulo_fin = f"✅ {_fmt_urm(tot_urm)} URM" if tot_urm is not None else "⚠️ Indisponível"
        st.metric("2. Etapa Financeira", rotulo_fin, help="Enquadramento do porte, potencial poluidor e cálculo de taxas municipais.")
    with col_e3:
        qtd_pend = resumo.get("PENDENTE", 0) + len([t for t in tecnicos if t.status.value in ("PENDENTE", "REVISAO_MANUAL")])
        rotulo_tec = "✅ Conforme" if qtd_pend == 0 else f"🟡 {qtd_pend} pendência(s)"
        st.metric("3. Etapa Técnica", rotulo_tec, help="Auditoria de Termos de Referência, laudos, ARTs e projetos.")
    with col_e4:
        rotulo_par = "✅ Deferimento" if qtd_pend == 0 and st_adm == "LIBERADO" else "🟡 Com Pendências"
        st.metric("4. Parecer Técnico", rotulo_par, help="Parecer técnico compilado e consolidado para emissão.")

    st.divider()

    # --------------------------------------------------------------
    # QUADRO RESUMO DA DOCUMENTAÇÃO
    # --------------------------------------------------------------
    st.subheader("🗂️ Quadro resumo da documentação")
    total = len(quadro)
    st.markdown(
        f"**✅ {resumo.get('CONFORME', 0)} em conformidade** · "
        f"**🟡 {resumo.get('PENDENTE', 0)} com pendência(s)** · "
        f"**❌ {resumo.get('NAO_APRESENTADO', 0)} não apresentado(s)** "
        f"— de {total} exigência(s) para a licença "
        f"**{md_seguro(pleito.get('tipo_licenca') or '—')}**")

    if quadro:
        linhas_df = [{
            "Exigência da licença": q.get("documento"),
            "Situação": f"{emoji_situacao(q['situacao'])} {rotulo_situacao(q['situacao'])}",
            "Arquivo apresentado": q.get("arquivo") or "—",
            "Pendências": "  |  ".join(q.get("pendencias") or []) or "—",
        } for q in quadro]
        st.caption("Legenda: ✅ Em conformidade · 🟡 Com pendência(s) · "
                   "❌ Não apresentado · ⚪ Sem análise — o detalhe de cada "
                   "linha está na coluna Pendências e na triagem "
                   "administrativa abaixo.")
        st.dataframe(pd.DataFrame(linhas_df), width="stretch",
                     hide_index=True, height=660)
    else:
        st.info("Sem checklist de exigências para esta licença "
                "(formulário não identificado).")

    extras = processo.get("extras") or []
    if extras:
        with st.expander(f"📎 Documentos recebidos sem exigência correspondente "
                         f"({len(extras)})"):
            for nome in extras:
                st.markdown(f"- `{nome}`")

    regras = processo.get("regras") or {}
    if regras:
        selo = "" if regras.get("revisado") else " (🟡 rascunho)"
        st.caption(f"🗂️ Regras documentais: {regras.get('fonte')} — validade da "
                   f"matrícula: {regras.get('validade_matricula_dias')} dias corridos da "
                   f"emissão{selo}")

    # --------------------------------------------------------------
    # ANÁLISE DOS DOCUMENTOS RECEBIDOS (CONSOLIDADA E SEM REPETIÇÃO)
    # --------------------------------------------------------------
    st.subheader("🔎 Análise dos documentos recebidos")

    # Consolida os resultados por documento (evita duplicar análise geral x técnica)
    docs_map: dict[str, dict] = {}
    for nome, a in analises.items():
        docs_map[nome] = {
            "nome": nome,
            "status": a.status,
            "norma_tr": a.norma_tr or "Documento",
            "itens_reprovados": list(a.itens_reprovados or []),
            "trecho_referencia": a.trecho_referencia,
            "metricas": a.metricas or {},
        }

    for t in tecnicos:
        nome = t.documento_analisado
        if nome not in docs_map:
            docs_map[nome] = {
                "nome": nome,
                "status": t.status,
                "norma_tr": t.norma_tr,
                "itens_reprovados": list(t.itens_reprovados or []),
                "trecho_referencia": t.trecho_referencia,
                "metricas": t.metricas or {},
            }
        else:
            doc = docs_map[nome]
            doc["norma_tr"] = t.norma_tr
            for item in (t.itens_reprovados or []):
                if item and item not in doc["itens_reprovados"]:
                    doc["itens_reprovados"].append(item)
            ordem_status = {"REVISAO_MANUAL": 0, "PENDENTE": 1, "CONFORME": 2}
            if ordem_status.get(t.status.value, 9) < ordem_status.get(doc["status"].value, 9):
                doc["status"] = t.status
            if t.trecho_referencia:
                doc["trecho_referencia"] = t.trecho_referencia
            if t.metricas:
                doc["metricas"] = {**doc["metricas"], **t.metricas}

    def _limpar_erros_display(itens: list[str]) -> list[str]:
        out = []
        for it in itens:
            txt = it.strip()
            if any(ign in txt.lower() for ign in ["dupla checagem", "1ª execução", "2ª execução", "divergentes entre"]):
                continue
            if txt and txt not in out:
                out.append(txt)
        return out

    pendentes = []
    conformes = []
    for nome, doc in docs_map.items():
        doc["itens_reprovados"] = _limpar_erros_display(doc["itens_reprovados"])
        if doc["status"].value in ("PENDENTE", "REVISAO_MANUAL") or doc["itens_reprovados"]:
            pendentes.append(doc)
        else:
            conformes.append(doc)

    # Exibe primeiro os documentos que precisam de atenção (pendências)
    if pendentes:
        st.markdown(f"**Documentos com pendências ou para revisão ({len(pendentes)}):**")
        for doc in pendentes:
            emoji = emoji_status_tecnico(doc["status"])
            with st.container(border=True):
                st.markdown(f"**{emoji} {md_seguro(doc['nome'])}** · `{md_seguro(doc['norma_tr'])}` · **{doc['status'].value}**")
                for err in doc["itens_reprovados"]:
                    st.markdown(f"- ⚠️ {md_seguro(err)}")

                with st.expander("🔍 Ver detalhes / trecho do laudo", expanded=False):
                    if doc.get("trecho_referencia"):
                        st.markdown(f"> 📄 *Trecho identificado:* \"{md_seguro(doc['trecho_referencia'])}\"")
                    metricas = doc.get("metricas") or {}
                    if metricas.get("data_emissao"):
                        st.markdown(
                            f"**Matrícula:** emitida em {metricas['data_emissao'][8:10]}/"
                            f"{metricas['data_emissao'][5:7]}/{metricas['data_emissao'][:4]} · "
                            f"{metricas.get('dias_desde_emissao', '?')} dias desde a emissão · "
                            f"prazo de {metricas.get('prazo_validade_dias')} dias")
                    metricas_exibir = {k: v for k, v in metricas.items() if k not in ("dupla_checagem", "tr_confirmado_pelo_titulo") and v is not None}
                    if metricas_exibir:
                        st.json(metricas_exibir)
                    if doc["nome"] in (processo.get("imagens") or {}):
                        st.image(processo["imagens"][doc["nome"]], width="stretch")
    else:
        st.success("✅ Todos os documentos analisados estão em conformidade!")

    # Documentos em conformidade agrupados de forma limpa e compacta
    if conformes:
        with st.expander(f"✅ Documentos em conformidade ({len(conformes)})", expanded=False):
            for doc in conformes:
                metricas = doc.get("metricas") or {}
                extra_info = ""
                if metricas.get("data_emissao"):
                    extra_info = f" (emitida em {metricas['data_emissao'][8:10]}/{metricas['data_emissao'][5:7]}/{metricas['data_emissao'][:4]} · válida)"
                st.markdown(f"- ✅ **{md_seguro(doc['nome'])}** — *{md_seguro(doc['norma_tr'])}*{extra_info}")

    # --------------------------------------------------------------
    # CAMADAS GIS (skill gis-multicamadas): confronto GEOMÉTRICO
    # Cada camada tem tema próprio (geologia, drenagem, APP, curvas de nível,
    # projeto urbanístico...) e NUNCA recebe TR de conteúdo - por isso estas
    # camadas ficam fora do bloco de laudos acima.
    # --------------------------------------------------------------
    camadas_gis = processo.get("camadas_gis") or []
    if camadas_gis:
        with st.expander(f"🗺️ Camadas GIS — confronto geométrico "
                         f"({len(camadas_gis)})",
                         expanded=any(c["status"] != "CONFORME"
                                      for c in camadas_gis)):
            st.caption(NORMA_GIS)
            for cam in camadas_gis:
                emoji = emoji_status_tecnico(
                    StatusValidacao(cam["status"])) if cam.get("status") \
                    else "🟡"
                with st.container(border=True):
                    st.markdown(
                        f"**{emoji} {md_seguro(str(cam.get('camada')))}** "
                        f"· `{md_seguro(str(cam.get('arquivo')))}` · "
                        f"tema **{md_seguro(str(cam.get('tema') or '—'))}** · "
                        f"**{cam.get('status')}**")
                    for erro in cam.get("erros") or []:
                        st.markdown(f"- ⚠️ {md_seguro(erro)}")
                    for aviso in cam.get("avisos") or []:
                        st.markdown(f"- 🔎 {md_seguro(aviso)}")
                    met = cam.get("metricas") or {}
                    if met:
                        st.json(met)

    # --------------------------------------------------------------
    # ADMINISTRATIVO + TAXA (compactos)
    # --------------------------------------------------------------
    with st.expander(f"🏛️ Triagem administrativa — {admin.get('status_geral', '—')}",
                     expanded=admin.get("status_geral") == "BLOQUEADO"):
        resumo_admin = admin.get("resumo", {})
        st.markdown(f"Checklist: **{resumo_admin.get('total_ok', 0)}/"
                    f"{resumo_admin.get('total_ok', 0) + resumo_admin.get('total_pendentes', 0)}** "
                    f"documentos OK · bloqueios: "
                    f"**{len(admin.get('bloqueios') or [])}**")
        for bloqueio in admin.get("bloqueios") or []:
            st.error("🚫 " + md_seguro(bloqueio))
        for pend in admin.get("documentos_pendentes") or []:
            if isinstance(pend, dict):
                st.warning(f"🟡 {md_seguro(pend.get('documento'))} — "
                           f"{md_seguro(pend.get('justificativa', ''))}")
            else:
                st.warning("🟡 " + md_seguro(pend))
        # Reconhecimento inteligente: documento identificado pelo CONTEÚDO ou
        # pelo APRENDIZADO (o nome do arquivo não casou direto)
        for exigido, origem in (admin.get("origem_ok") or {}).items():
            estrela = "🧠 aprendido" if origem.get("via") == "aprendido" else "📄 conteúdo"
            st.info(f"{estrela}: '{origem.get('anexo')}' reconhecido como "
                    f"**{exigido}** (o nome do arquivo não casou direto).")
        for aviso in admin.get("avisos") or []:
            if "COMPLETADO" in aviso:
                st.info("🔗 " + aviso)
        # Responsáveis técnicos das etapas (seção 4.3): ART/RTT conferida
        # nos documentos apresentados (nº + nome/registro)
        for conf in admin.get("conferencia_responsaveis") or []:
            etapa_txt = conf.get("etapa") or "não informada"
            if conf.get("encontrado"):
                st.success("✅ " + str(conf.get("profissional")) + " — "
                           + str(conf.get("art_rtt")) + " (" + etapa_txt + "): "
                           + str(conf.get("nivel")) + " em `"
                           + str(conf.get("anexo")) + "`.")
            else:
                st.warning("🟡 " + str(conf.get("profissional")) + " — ART/RTT "
                           + str(conf.get("art_rtt"))
                           + " NÃO confirmada nos anexos (etapa: "
                           + etapa_txt + ").")

        # Registros ANEXADOS que não foram declarados no formulário
        # (podem existir VÁRIAS RTTs: Projeto Urbanístico, Execução de Obras...)
        for reg in admin.get("conferencia_rtts_anexadas") or []:
            st.warning(
                "🟡 " + str(reg.get("tipo")) + " nº " + str(reg.get("numero"))
                + " (" + str(reg.get("nome") or "profissional não identificado")
                + ", " + str(reg.get("conselho") or "conselho não identificado")
                + ") está anexada em `" + str(reg.get("anexo"))
                + "` mas NÃO foi declarada no formulário (4.3/item 14) — "
                "atividade: " + str(reg.get("atividade") or "não informada")
                + ".")

        # MATRÍCULA e CONTRATO SOCIAL x dados declarados (confronto bilateral)
        for rotulo, chave in (("Matrícula", "conferencia_matricula"),
                              ("Contrato Social", "conferencia_contrato_social")):
            conf = admin.get(chave)
            if not conf:
                continue
            status_conf = conf.get("status")
            if status_conf == "CONFERE":
                st.success(f"✅ {rotulo} `{conf.get('anexo')}`: "
                           + md_seguro(str(conf.get("detalhe") or "confere")))
            elif status_conf == "DIVERGENTE":
                for item in conf.get("itens_reprovados") or []:
                    st.warning(f"🟡 {rotulo} `{conf.get('anexo')}`: "
                               + md_seguro(str(item)))
            elif status_conf in ("NAO_ENCONTRADO", "ANEXO_NAO_LEGIVEL"):
                st.warning(f"🟡 {rotulo} `{conf.get('anexo')}`: "
                           + md_seguro(str(conf.get("detalhe"))))

        # Conferência do CNPJ: formulário HTML x número de inscrição na
        # matrícula anexada (PDF escaneado lido via OCR)
        conf_cnpj = admin.get("conferencia_cnpj")
        if conf_cnpj:
            status_cnpj = conf_cnpj.get("status")
            cnpj_fmt = conf_cnpj.get("cnpj_formulario") or ""
            if len(cnpj_fmt) == 14:
                cnpj_fmt = (cnpj_fmt[:2] + "." + cnpj_fmt[2:5] + "."
                            + cnpj_fmt[5:8] + "/" + cnpj_fmt[8:12] + "-"
                            + cnpj_fmt[12:])
            if status_cnpj == "CONFERE":
                st.success("✅ CNPJ do formulário (" + cnpj_fmt
                           + ") conferido no número de inscrição da matrícula `"
                           + str(conf_cnpj.get("anexo")) + "` ("
                           + str(conf_cnpj.get("detalhe")) + ").")
            elif status_cnpj == "DIVERGENTE":
                st.warning("🟡 CNPJ do formulário (" + cnpj_fmt
                           + ") DIFERE do número de inscrição na matrícula `"
                           + str(conf_cnpj.get("anexo")) + "` (encontrado: "
                           + str(conf_cnpj.get("cnpj_encontrado"))
                           + ") - conferir manualmente.")
            elif status_cnpj == "NAO_ENCONTRADO":
                st.warning("🟡 CNPJ (" + cnpj_fmt
                           + ") não localizado no texto da matrícula `"
                           + str(conf_cnpj.get("anexo"))
                           + "` - conferir manualmente.")
            elif status_cnpj == "ANEXO_NAO_LEGIVEL":
                st.warning("🟡 Matrícula `" + str(conf_cnpj.get("anexo"))
                           + "` sem texto legível (escaneada e OCR "
                           "indisponível) - conferir o CNPJ manualmente.")

    if financeiro:
        with st.expander("💰 Taxa de licenciamento (URMs)"):
            st.markdown(f"**Total: {_fmt_urm(financeiro.get('total_urm'))} URMs** "
                        f"({financeiro.get('tipo_licenca')} · grupo "
                        f"{financeiro.get('grupo_atividade')})")
            st.caption(f"📚 Fonte: {financeiro.get('fonte_tabela')}")
            if financeiro.get("regra_aplicada"):
                st.caption(f"Regra: {financeiro['regra_aplicada']}")
            for aviso in financeiro.get("avisos") or []:
                st.warning(f"⚠️ {aviso}")

    with st.expander("④ Dados extraídos do formulário (JSON estruturado — Fase 1)"):
        st.json(dados)

    st.divider()

    # --------------------------------------------------------------
    # PARECER TÉCNICO (botão final)
    # --------------------------------------------------------------
    banner_path = RAIZ / "assets" / "banner_campo_bom.jpg"
    if banner_path.exists():
        c_b1, c_b2 = st.columns([1, 1])
        with c_b1:
            st.image(str(banner_path), use_container_width=True)

    st.subheader("📄 Emissão do Parecer Técnico — Padrão SEMA Campo Bom")
    st.markdown("O parecer consolida **o que falta para contemplar todos os "
                "documentos referentes a esta licença** (exigências não "
                "apresentadas, pendências por documento e não conformidades com "
                "os TRs), formatado no **padrão oficial SEMA Campo Bom**.")

    with st.expander("🏛️ Cabeçalho, Processo e Assinatura do Parecer (Padrão SEMA)", expanded=True):
        c_h1, c_h2, c_h3 = st.columns(3)
        with c_h1:
            numero = st.text_input("Nº do parecer", value="", placeholder="Ex: 303/2026", key="parecer_num")
        with c_h2:
            numero_proc = st.text_input("Nº do processo", value="", placeholder="Ex: 1157/2026", key="parecer_proc")
        with c_h3:
            prazo = st.number_input("Prazo para complementação (dias)", min_value=5,
                                    max_value=180, value=30, key="parecer_prazo")

        c_s1, c_s2, c_s3 = st.columns(3)
        with c_s1:
            nome_tec = st.text_input("Técnico / Analista responsável",
                                     value="GABRIEL HENNEMANN KLASER", key="parecer_nome_tec")
        with c_s2:
            cargo_tec = st.text_input("Cargo / Função",
                                      value="ASSESSOR SUPERIOR SETORIAL DE LICENCIAMENTO AMBIENTAL",
                                      key="parecer_cargo_tec")
        with c_s3:
            reg_tec = st.text_input("Registro profissional",
                                    value="CREA RS230362", key="parecer_reg_tec")

        objeto_custom = st.text_area(
            "Objeto do parecer (opcional - deixe em branco para preenchimento automático)",
            value="",
            help="Descreva o objeto específico do parecer ou deixe em branco para utilizar a formulação padrão da SEMA.",
            key="parecer_obj_custom")

    col_com, col_btn = st.columns([2, 1.2])
    with col_com:
        comentarios = st.text_area(
            "Comentários do analista (entram no parecer)", height=120,
            key="comentarios_parecer")
        revisado = st.checkbox("Confirmo a conferência da análise acima", key="parecer_conferido")
    with col_btn:
        st.write("")
        st.write("")
        if revisado:
            gerar = st.button("📝 Gerar parecer (texto editável)",
                              type="primary", width="stretch",
                              help="Compila o parecer para prévia "
                                   "editável. Edite no campo abaixo, "
                                   "copie/cole ou exporte .docx/.pdf.")
        else:
            gerar = False
            st.button("📄 Baixar Parecer Técnico (.docx / .pdf)", disabled=True,
                      width="stretch",
                      help="Marque a confirmação da conferência para liberar a emissão.")

    if revisado:
        if gerar:
            try:
                st.session_state["parecer_texto"] = \
                    compilar_texto_parecer(
                        dados_processo=dados,
                        quadro_documentos=quadro,
                        resultado_admin=admin,
                        resultados_tecnicos=tecnicos,
                        arquivos_recebidos=processo.get("arquivos") or [],
                        resumo_quadro=resumo,
                        comentarios_analista=comentarios or None,
                        numero_parecer=numero,
                        prazo_dias=int(prazo),
                        numero_processo=numero_proc,
                        objeto_parecer=objeto_custom or None,
                        nome_tecnico=nome_tec or None,
                        cargo_tecnico=cargo_tec or None,
                        registro_tecnico=reg_tec or None)
            except Exception as exc:  # noqa: BLE001
                st.session_state["parecer_texto"] = None
                st.error(f"❌ Falha ao COMPILAR o parecer: {exc}")
                st.stop()

        bytes_docx = bytes_pdf = None
        if st.session_state.get("parecer_texto"):
            texto_final = st.session_state.get("parecer_texto") or ""
            with st.expander("👁️ Prévia do parecer — EDITÁVEL antes de exportar",
                             expanded=True):
                st.caption("Edite livremente abaixo. Para levar a outro "
                           "documento: selecione tudo (Ctrl+A) e copie "
                           "(Ctrl+C). Ou exporte .docx/.pdf pelos botões.")
                texto_final = st.text_area(
                    "Texto do parecer (editável)", value=texto_final,
                    height=520, key="parecer_texto_area",
                    label_visibility="collapsed")

            # 2) EXPORTAR .docx / .pdf a partir do texto (editado ou não)
            num_clean = numero.strip().replace('/', '-') if numero.strip() else "minuta"
            nome_base = f"parecer_tecnico_{num_clean}"
            try:
                bytes_docx = exportar_docx(texto_final, numero or "________")
                bytes_pdf = exportar_pdf(texto_final, numero or "________")
            except Exception as exc:  # noqa: BLE001
                st.error(f"❌ Falha ao EXPORTAR o parecer: {exc}")
                bytes_docx = bytes_pdf = None
        else:
            st.info("ℹ️ Preencha os campos de identificação acima (Nº do parecer, Nº do processo e assinatura) e clique em **'📝 Gerar parecer (texto editável)'** para compilar a minuta oficial.")
        if bytes_docx and bytes_pdf:
            c_docx, c_pdf = st.columns(2)
            with c_docx:
                st.download_button(
                    label="⬇️ Baixar .docx (Padrão SEMA com Banner)", data=bytes_docx,
                    file_name=nome_base + ".docx",
                    mime="application/vnd.openxmlformats-officedocument."
                         "wordprocessingml.document",
                    type="primary", width="stretch", key="dl_parecer_docx")
                st.markdown(link_download(
                    bytes_docx, nome_base + ".docx",
                    "vnd.openxmlformats-officedocument."
                    "wordprocessingml.document",
                    "⬇️ .docx (link direto)"),
                    unsafe_allow_html=True)
            with c_pdf:
                st.download_button(
                    label="⬇️ Baixar .pdf (Padrão SEMA com Banner)", data=bytes_pdf,
                    file_name=nome_base + ".pdf", mime="application/pdf",
                    width="stretch", key="dl_parecer_pdf")
                st.markdown(link_download(
                    bytes_pdf, nome_base + ".pdf", "application/pdf",
                    "⬇️ .pdf (link direto)"), unsafe_allow_html=True)
            st.caption(f"Prévia: {len(texto_final.splitlines())} linhas · "
                       f"docx {len(bytes_docx) // 1024} KB · pdf "
                       f"{len(bytes_pdf) // 1024} KB · cópias salvas em saidas/")
            _pasta = Path("saidas")
            _pasta.mkdir(exist_ok=True)
            (_pasta / (nome_base + ".docx")).write_bytes(bytes_docx)
            (_pasta / (nome_base + ".pdf")).write_bytes(bytes_pdf)

    rodape_calibracao()


# ======================================================================
# Roteador de etapas
# ======================================================================
if "etapa" not in st.session_state:
    st.session_state.etapa = "upload"

if st.session_state.etapa == "upload":
    pagina_upload()
else:
    pagina_analise()
