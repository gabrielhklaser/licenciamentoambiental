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
from licenciamento.auditor_tecnico import AuditorTecnico
from licenciamento.calibracao import Calibracao
from licenciamento.esquemas_tecnicos import StatusValidacao
from licenciamento.gerador_oficios import GeradorOficios
from licenciamento.parser_formulario import FormularioParser
from licenciamento.validador_documentos import (EXTENSOES_IMAGEM,
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
                   "png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff", "gif"]


def icone_arquivo(nome: str) -> str:
    """Ícone do arquivo na listagem do upload (imagem x documento)."""
    if Path(nome).suffix.lower() in EXTENSOES_IMAGEM:
        return "🖼️"
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
                                "secao": "8"})
    for prof in dados.get("responsaveis_etapas") or []:
        if prof.get("art_rtt"):
            arts_formulario.append({"numero": prof.get("art_rtt"),
                                    "nome": prof.get("nome"),
                                    "secao": "4.3"})
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
                areas_formulario=areas_formulario or None))

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
            sufixo = Path(form.name).suffix.lower() or ".html"
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
        "Word (`.docx`), Excel (`.xlsx`), `.txt`/`.csv`.")

    arquivos = st.file_uploader(
        "Documentos do processo (formulário + anexos)",
        type=FORMATOS_UPLOAD, accept_multiple_files=True,
        help="Inclua o formulário do requerimento (.htm/.html) e todos os "
             "documentos exigidos para a licença selecionada (matrícula, ART, "
             "laudos, PGRS, alvarás, etc.).")

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
    # QUADRO RESUMO DA DOCUMENTAÇÃO
    # --------------------------------------------------------------
    st.subheader("🗂️ Quadro resumo da documentação")
    total = len(quadro)
    st.markdown(
        f"**✅ {resumo.get('CONFORME', 0)} em conformidade** · "
        f"**🟡 {resumo.get('PENDENTE', 0)} com pendência(s)** · "
        f"**❌ {resumo.get('NAO_APRESENTADO', 0)} não apresentado(s)** "
        f"— de {total} exigência(s) para a licença "
        f"**{pleito.get('tipo_licenca') or '—'}**")

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
    # ANÁLISE POR DOCUMENTO RECEBIDO
    # --------------------------------------------------------------
    st.subheader("🔎 Análise dos documentos recebidos")
    ordem = {"CONFORME": 0, "PENDENTE": 1, "REVISAO_MANUAL": 2}
    por_nome = sorted(analises.items(),
                      key=lambda kv: (ordem.get(getattr(kv[1], "status").value, 3),
                                      kv[0]))
    for nome, analise in por_nome:
        emoji = emoji_status_tecnico(analise.status)
        cab = (f"{emoji} {nome} · {analise.norma_tr or 'Documento'} · "
               f"**{analise.status.value}**")
        with st.container(border=True):
            st.markdown(cab)
            if analise.itens_reprovados:
                st.error("\n".join(f"**✗** {item}" for item in analise.itens_reprovados))
            metricas = analise.metricas or {}
            if metricas.get("data_emissao"):
                st.markdown(
                    f"**Matrícula:** emitida em {metricas['data_emissao'][8:10]}/"
                    f"{metricas['data_emissao'][5:7]}/{metricas['data_emissao'][:4]} · "
                    f"{metricas.get('dias_desde_emissao', '?')} dias desde a emissão · "
                    f"{'**dentro**' if analise.status.value == 'CONFORME' else '**fora**'} "
                    f"do prazo de {metricas.get('prazo_validade_dias')} dias")
            if analise.trecho_referencia:
                st.markdown(f"> 📄 *Trecho do final do documento:* "
                            f"\"{analise.trecho_referencia}\"")
            if nome in (processo.get("imagens") or {}):
                with st.expander("🖼️ Ver imagem anexada (conferência manual)"):
                    st.image(processo["imagens"][nome], width="stretch")

    # --------------------------------------------------------------
    # AUDITORIA TÉCNICA (TRs)
    # --------------------------------------------------------------
    with st.expander("📐 Auditoria técnica — Termos de Referência validados",
                     expanded=any(t.itens_reprovados for t in tecnicos)):
        gab = st.session_state.get("processo", {}).get("gabarito") or {}
        if not tecnicos:
            st.info("Nenhum laudo (.pdf/.txt) submetido para auditoria técnica.")
        for resultado in tecnicos:
            emoji = emoji_status_tecnico(resultado.status)
            cab = (f"{emoji} {resultado.documento_analisado} · {resultado.norma_tr} · "
                   f"**{resultado.status.value}**")
            with st.container(border=True):
                st.markdown(cab)
                st.caption(f"Motor da análise: {resultado.origem.value}")
            _dc = (resultado.metricas or {}).get("dupla_checagem")
            if _dc:
                _tt = (resultado.metricas or {}).get(
                    "tr_confirmado_pelo_titulo")
                st.caption("🔎 Dupla checagem: " + str(_dc)
                           + (" · TR confirmado pelo título do documento"
                              if _tt else ""))
                if resultado.itens_reprovados:
                    st.error("\n".join(f"**✗** {item}" for item in resultado.itens_reprovados))
                if resultado.trecho_referencia:
                    st.markdown(f"> 📄 *Trecho de referência do laudo:* "
                                f"\"{resultado.trecho_referencia}\"")
                with st.expander("Ver métricas extraídas"):
                    st.json(resultado.metricas)

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
            st.error(f"🚫 {bloqueio}")
        for pend in admin.get("documentos_pendentes") or []:
            if isinstance(pend, dict):
                st.warning(f"🟡 {pend.get('documento')} — {pend.get('justificativa', '')}")
            else:
                st.warning(f"🟡 {pend}")
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
    st.subheader("📄 Emissão do Parecer Técnico")
    st.markdown("O parecer consolida **o que falta para contemplar todos os "
                "documentos referentes a esta licença** (exigências não "
                "apresentadas, pendências por documento e não conformidades com "
                "os TRs).")

    col_com, col_doc = st.columns([2, 1.4])
    with col_com:
        comentarios = st.text_area(
            "Comentários do analista (entram no parecer)", height=150,
            key="comentarios_parecer")
        revisado = st.checkbox("Confirmo a conferência da análise acima")
    with col_doc:
        numero = st.text_input("Nº do parecer", value="001/2026")
        prazo = st.number_input("Prazo para complementação (dias)", min_value=5,
                                max_value=180, value=30)

        if revisado:
            # gera UMA vez por (número, prazo, comentários) e guarda na sessão:
            # o clique do download re-renderiza a página sem regenerar o docx
            chave_parecer = (numero, int(prazo), comentarios or "")
            if (st.session_state.get("parecer_docx") is None
                    or st.session_state.get("parecer_chave") != chave_parecer):
                try:
                    st.session_state["parecer_docx"] = \
                        GeradorOficios().gerar_parecer_tecnico(
                            dados_processo=dados,
                            quadro_documentos=quadro,
                            resultado_admin=admin,
                            resultados_tecnicos=tecnicos,
                            arquivos_recebidos=processo.get("arquivos") or [],
                            resumo_quadro=resumo,
                            comentarios_analista=comentarios or None,
                            numero_parecer=numero,
                            prazo_dias=int(prazo))
                    st.session_state["parecer_chave"] = chave_parecer
                    # cópia auditável em disco (saidas/ é gitignored)
                    _pasta = Path("saidas")
                    _pasta.mkdir(exist_ok=True)
                    (_pasta / f"parecer_tecnico_{numero.replace('/', '-')}"
                     f".docx").write_bytes(
                        st.session_state["parecer_docx"])
                except Exception as exc:  # noqa: BLE001
                    st.session_state["parecer_docx"] = None
                    st.error(f"❌ Falha ao GERAR o parecer: {exc}")
            docx_bytes = st.session_state.get("parecer_docx")
            if docx_bytes:
                st.download_button(
                    label="📄 Baixar Parecer Técnico (.docx)",
                    data=docx_bytes,
                    file_name=f"parecer_tecnico_{numero.replace('/', '-')}.docx",
                    mime="application/vnd.openxmlformats-officedocument."
                         "wordprocessingml.document",
                    type="primary", width="stretch", key="dl_parecer")
                # FALLBACK à prova de proxy: o download do Streamlit usa a rota
                # /media/ que pode não atravessar o proxy do preview; o link
                # data-URI viaja no próprio conteúdo da página e SEMPRE baixa
                import base64 as _b64
                _link = _b64.b64encode(docx_bytes).decode("ascii")
                _nome = f"parecer_tecnico_{numero.replace('/', '-')}.docx"
                st.markdown(
                    '<a download="' + _nome + '" href="data:application/'
                    'vnd.openxmlformats-officedocument.wordprocessingml.'
                    'document;base64,' + _link + '">'
                    "⬇️ Se o botão acima não iniciar o download, "
                    "CLIQUE AQUI</a>", unsafe_allow_html=True)
                st.caption(f"Documento gerado com {len(docx_bytes) / 1024:.0f} "
                           "KB · cópia salva em saidas/")
        else:
            st.button("📄 Baixar Parecer Técnico (.docx)", disabled=True,
                      width="stretch",
                      help="Marque a confirmação da conferência para liberar a emissão.")

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
