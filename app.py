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

st.set_page_config(page_title="Licenciamento Ambiental — SEMA Campo Bom",
                   page_icon="🌿", layout="wide")

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
    for arq in documentos:
        texto = validador.extrair_texto(arq.name, arq.getvalue())
        textos_anexos[arq.name] = texto
        registro = {"nome": arq.name, "texto": texto,
                    "tipo": validador.identificar_tipo(arq.name, texto)}
        arquivos_analise.append(registro)
        analises[arq.name] = validador.analisar_documento(arq.name, texto)
        if len(texto.strip()) >= 40:
            resultados_tecnicos.extend(auditor.auditar_documento(arq.name, texto))

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
    st.title("🌿 Sistema de Verificação do Licenciamento Ambiental")
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

    st.title("📋 Etapa 2 — Avaliação da documentação")
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
    c1, c2, c3, c4 = st.columns([1.6, 1.0, 1.2, 0.9])
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
    c3.metric("Triagem", (dados.get("status_triagem") or "—").replace("_", " ").upper())
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
        st.dataframe(pd.DataFrame(linhas_df), width="stretch", hide_index=True)
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
            docx_bytes = GeradorOficios().gerar_parecer_tecnico(
                dados_processo=dados,
                quadro_documentos=quadro,
                resultado_admin=admin,
                resultados_tecnicos=tecnicos,
                arquivos_recebidos=processo.get("arquivos") or [],
                resumo_quadro=resumo,
                comentarios_analista=comentarios or None,
                numero_parecer=numero,
                prazo_dias=int(prazo))
            st.download_button(
                label="📄 Baixar Parecer Técnico (.docx)",
                data=docx_bytes,
                file_name=f"parecer_tecnico_{numero.replace('/', '-')}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                type="primary", width="stretch")
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
