# -*- coding: utf-8 -*-
"""
FASE 4 - Dashboard de Triagem de Licenciamento Ambiental (Streamlit)
=====================================================================

Interface web que consolida as saídas das Fases 1, 2 e 3:

    - Upload múltiplo (.html/.htm do formulário + .pdf/.txt dos laudos);
    - Painel de semáforo com expanders por etapa de auditoria:
        ① Triagem Administrativa (verde OK / vermelho bloqueado-pendências);
        ② Análise Financeira (cálculo final em URMs);
        ③ Auditoria Técnica (regras dos Termos de Referência validadas);
    - Aprovação humana: comentários editáveis do analista antes do ofício;
    - Geração da Minuta de Ofício (.docx) com as pendências consolidadas.

Execução:  streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session

from licenciamento.agente_administrativo import AgenteAdministrativo
from licenciamento.agente_financeiro import AgenteFinanceiro
from licenciamento.auditor_tecnico import AuditorTecnico
from licenciamento.banco import obter_engine, salvar_processo, semear_tabela_urm
from licenciamento.calibracao import Calibracao
from licenciamento.esquemas_tecnicos import StatusValidacao
from licenciamento.gerador_oficios import GeradorOficios
from licenciamento.parser_formulario import FormularioParser

RAIZ = Path(__file__).parent

st.set_page_config(page_title="Licenciamento Ambiental · Campo Bom",
                   page_icon="🌿", layout="wide")

# ============================================================================
# Estado da sessão
# ============================================================================
if "processo" not in st.session_state:
    st.session_state.processo = None
if "anexados_texto" not in st.session_state:
    st.session_state.anexados_texto = ""
if "comentarios" not in st.session_state:
    st.session_state.comentarios = ""


# ============================================================================
# Funções auxiliares do pipeline
# ============================================================================
def executar_analise(arquivos: list, anexados: list[str]) -> None:
    """Roda o pipeline completo (Fases 1 -> 2 -> 3) e guarda em session_state."""
    formulario = next((a for a in arquivos if a.name.lower().endswith((".htm", ".html"))), None)
    laudos = [a for a in arquivos if a.name.lower().endswith((".pdf", ".txt"))]

    if formulario is None:
        st.error("Envie o formulário do processo (.htm ou .html).")
        return

    with st.spinner("⏳ Processando o formulário e os laudos..."):
        # FASE 1 - Parser
        parser = FormularioParser(caminho_arquivo=None, conteudo_html=formulario.getvalue().decode("utf-8", errors="replace"))
        dados = parser.gerar_json()

        # FASE 2 - Agentes determinísticos
        resultado_admin = AgenteAdministrativo().auditar(dados, anexados)
        resultado_financeiro = AgenteFinanceiro().calcular_do_parser(dados)

        # FASE 3 - Auditor técnico (TRs)
        auditor = AuditorTecnico()
        textos_laudos = {a.name: AuditorTecnico.extrair_texto(a.name, a.getvalue())
                         for a in laudos}
        resultados_tecnicos = auditor.auditar_lote(textos_laudos)

        gerador = GeradorOficios()
        pendencias = gerador._coletar_pendencias(dados, resultado_admin, resultados_tecnicos)

    st.session_state.processo = {
        "dados": dados,
        "admin": resultado_admin,
        "financeiro": resultado_financeiro,
        "tecnicos": resultados_tecnicos,
        "anexados": anexados,
        "gabarito": {"fonte": auditor.fonte_gabarito, "revisado": auditor.gabarito_revisado},
    }
    st.session_state.comentarios = gerador._comentario_sugerido(pendencias)
    st.toast("Análise concluída!", icon="✅")


def carregar_exemplo_ficticio() -> None:
    """Carrega o processo LOR fictício empacotado em /exemplos (mock data)."""
    arquivos: list = []

    class ArquivoFake:  # simula o UploadedFile do Streamlit
        def __init__(self, caminho: Path):
            self.name = caminho.name
            self._bytes = caminho.read_bytes()

        def getvalue(self) -> bytes:
            return self._bytes

    formulario = RAIZ / "exemplos" / "formulario_LOR_medio_alto.htm"
    arquivos.append(ArquivoFake(formulario))
    for laudo in sorted((RAIZ / "exemplos" / "laudos").glob("*.pdf")):
        arquivos.append(ArquivoFake(laudo))

    # Simulação: TODOS os documentos exigidos foram anexados (as 2 pendências
    # do exemplo vêm da AUDITORIA TÉCNICA: RFO + PCA)
    anexados = [
        "formulario_enquadramento_assinado.pdf", "copia_cpf_cnpj.pdf",
        "matricula_imovel_atualizada.pdf", "planta_localizacao.pdf",
        "eiv_estudo_impacto_vizinhanca.pdf", "art_responsavel_tecnico.pdf",
        "copia_licenca_previa.pdf", "pca_plano_controle_ambiental.pdf",
        "projeto_executivo_sistema_tratamento_efluentes.pdf", "pgrs.pdf",
        "licenca_supressao_vegetacao.pdf", "rca_relatorio_controle_ambiental.pdf",
        "laudo_sistema_tratamento_efluentes.pdf", "certificado_conclusao_pca.pdf",
        "alvara_bombeiros.pdf",
    ]
    st.session_state.anexados_texto = "\n".join(anexados)
    executar_analise(arquivos, anexados)


def _fmt_urm(valor) -> str:
    """Formata um valor de URM no padrão brasileiro (ex.: 1.252,80)."""
    if valor is None:
        return "—"
    return f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def emoji_status_admin(status: str) -> str:
    return {"APROVADO": "🟢", "PENDENTE": "🟡", "BLOQUEADO": "🔴"}.get(status, "⚪")


def emoji_status_tecnico(status: StatusValidacao) -> str:
    return {StatusValidacao.CONFORME: "🟢",
            StatusValidacao.PENDENTE: "🔴",
            StatusValidacao.REVISAO_MANUAL: "🟡"}.get(status, "⚪")


# ============================================================================
# BARRA LATERAL - Upload e roteamento
# ============================================================================
with st.sidebar:
    st.title("🌿 Licenciamento Ambiental")
    st.caption("Prefeitura de Campo Bom · Secretaria Municipal do Meio Ambiente")
    with st.expander("⚙️ Calibração (documentos oficiais)"):
        resumo_cal = Calibracao().resumo()
        if resumo_cal["ativa"]:
            for chave, info in resumo_cal["itens"].items():
                if info["ativa"]:
                    selo = "✅" if info["revisado"] else "🟡 (rascunho)"
                    st.markdown(f"**{chave}** {selo}")
                    st.caption(f"Fonte: {info['fonte']}")
            st.caption("Rascunhos (🟡) precisam de revisão: "
                       "`python ferramentas/ingestar_pdfs.py --promover`")
        else:
            st.caption("Usando padrões internos. Rode `ferramentas/ingestar_pdfs.py` "
                       "com os PDFs oficiais para calibrar.")
    st.divider()

    st.subheader("1 · Submissão do processo")
    arquivos = st.file_uploader(
        "Formulário (.htm/.html) e documentos (.pdf/.txt)",
        type=["htm", "html", "pdf", "txt"], accept_multiple_files=True)

    st.subheader("2 · Arquivos anexados (simulação)")
    st.caption("Na prototipagem, a submissão é simulada por esta lista "
               "(um nome de arquivo por linha). Edite conforme o processo.")
    anexados_padrao = "\n".join(a.name for a in arquivos if a) or \
        st.session_state.anexados_texto
    anexados_texto = st.text_area(
        "Lista de anexos", value=anexados_padrao, height=170,
        key="caixa_anexados",
        help="Os nomes são casados com o checklist exigido por normalização e similaridade.")
    anexados = [linha.strip() for linha in anexados_texto.splitlines() if linha.strip()]
    st.session_state.anexados_texto = anexados_texto

    st.divider()
    if st.button("🔍 **Executar análise prévia**", type="primary",
                 disabled=not arquivos, width="stretch"):
        executar_analise(arquivos, anexados)

    if st.button("🧪 Carregar processo fictício de exemplo (LOR)",
                 width="stretch",
                 help="Simulação exigida na especificação: LOR porte Médio/potencial Alto "
                      "com duas pendências técnicas (RFO e PCA)."):
        carregar_exemplo_ficticio()

# ============================================================================
# ÁREA PRINCIPAL
# ============================================================================
st.header("Painel de Triagem de Licenciamento Ambiental")
st.caption("Fase 1 (Parser) → Fase 2 (Agentes Administrativo/Financeiro) → "
           "Fase 3 (Auditor Técnico) → Fase 4 (Ofício)")

processo = st.session_state.processo
if processo is None:
    st.info("⬅️ Envie o formulário .htm/.html do processo e os laudos (.pdf) na barra "
            "lateral e clique em **Executar análise prévia** — ou carregue o "
            "**processo fictício de exemplo** para ver o sistema em ação.")
    st.stop()

dados = processo["dados"]
admin = processo["admin"]
fin = processo["financeiro"]
tecnicos = processo["tecnicos"]
empreendimento = dados.get("empreendimento", {})
pleito = dados.get("pleito", {})
pendencias_totais = len(admin.get("documentos_pendentes", [])) + len(admin.get("bloqueios", [])) \
    + sum(1 for t in tecnicos if t.itens_reprovados)

# ------------------------- Faixa superior de status -------------------------
c1, c2, c3, c4 = st.columns([1.2, 1.2, 1, 1])
c1.metric("Empreendimento", (empreendimento.get("nome_empreendimento") or "—")[:34],
          f"{pleito.get('tipo_licenca', '—')} · Porte {empreendimento.get('porte', '—')} · "
          f"Potencial {empreendimento.get('potencial_poluidor', '—')}")
c2.metric("Triagem do Parser", dados.get("status_triagem", "—").replace("_", " ").upper(),
          f"ART: {dados.get('responsavel_tecnico', {}).get('registro_art') or 'AUSENTE'}")
c3.metric("Administrativo", f"{emoji_status_admin(admin['status_geral'])} {admin['status_geral']}",
          f"{admin['resumo']['total_ok']}/{admin['resumo']['total_exigidos']} docs OK")
c4.metric("Taxa (URMs)", _fmt_urm(fin.get("total_urm")),
          f"Pendências: {pendencias_totais}")

st.divider()

# ------------------------- ① Triagem Administrativa -------------------------
with st.expander(f"① Triagem Administrativa — {emoji_status_admin(admin['status_geral'])} "
                 f"{admin['status_geral']}", expanded=admin["status_geral"] != "APROVADO"):
    if admin["bloqueios"]:
        st.error("**🚫 Bloqueios administrativos (hard constraints):**\n\n"
                 + "\n".join(f"- {b}" for b in admin["bloqueios"]))
    if admin["documentos_pendentes"]:
        st.warning("**Documentos pendentes:**\n\n"
                   + "\n".join(f"- {p['documento']}" for p in admin["documentos_pendentes"]))
    if admin["documentos_ok"]:
        st.success(f"**Documentos OK ({len(admin['documentos_ok'])}):**\n\n"
                   + ", ".join(admin["documentos_ok"]))
    for aviso in admin.get("avisos", []):
        st.caption(f"ℹ️ {aviso}")

# ------------------------- ② Análise Financeira -----------------------------
with st.expander(f"② Análise Financeira — {_fmt_urm(fin.get('total_urm'))} URMs"):
    if fin.get("erro"):
        st.error(f"Falha no cálculo: {fin['erro']}")
    else:
        df_fases = pd.DataFrame(
            [{"Fase": fase, "Valor (URMs)": _fmt_urm(valor)}
             for fase, valor in fin.get("composicao_fases", {}).items()])
        e1, e2 = st.columns(2)
        with e1:
            st.markdown(f"**Grupo de atividade:** {fin.get('grupo_atividade')}")
            st.markdown(f"**Porte/Faixa:** {fin.get('porte_ou_faixa')}")
            if fin.get("regra_aplicada"):
                st.markdown(f"**Regra:** {fin['regra_aplicada']}")
        with e2:
            st.dataframe(df_fases, width="stretch", hide_index=True)
            st.markdown(f"### Total: {_fmt_urm(fin.get('total_urm'))} URMs")
            selo = "" if fin.get("tabela_revisada") else " (🟡 rascunho - conferir Manual)"
            st.caption(f"📚 Fonte da tabela: {fin.get('fonte_tabela')}{selo}")

# ------------------------- ③ Auditoria Técnica ------------------------------
with st.expander("③ Auditoria Técnica — Termos de Referência validados",
                 expanded=any(t.itens_reprovados for t in tecnicos)):
    gab = (processo.get("gabarito") or {})
    if gab:
        selo = "" if gab.get("revisado") else " (🟡 rascunho - conferir TRs)"
        st.caption(f"📐 Gabarito dos TRs: {gab.get('fonte')}{selo}")
    if not tecnicos:
        st.info("Nenhum laudo (.pdf/.txt) submetido para auditoria técnica.")
    for resultado in tecnicos:
        emoji = emoji_status_tecnico(resultado.status)
        cab = f"{emoji} {resultado.documento_analisado} · {resultado.norma_tr} · **{resultado.status.value}**"
        with st.container(border=True):
            st.markdown(cab)
            st.caption(f"Motor da análise: {resultado.origem.value}")
            if resultado.itens_reprovados:
                st.error("\n".join(f"**✗** {item}" for item in resultado.itens_reprovados))
            if resultado.trecho_referencia:
                st.markdown(f"> 📄 *Trecho de referência do laudo:* \"{resultado.trecho_referencia}\"")
            with st.expander("Ver métricas extraídas"):
                st.json(resultado.metricas)

# ------------------------- ④ Dados estruturados (JSON) ----------------------
with st.expander("④ Dados extraídos do formulário (JSON estruturado — Fase 1)"):
    st.json(dados)

st.divider()

# ------------------------- Aprovação humana + Ofício ------------------------
st.subheader("✍️ Aprovação humana e geração do ofício")
col_com, col_doc = st.columns([2, 1.4])

with col_com:
    comentarios = st.text_area(
        "Comentários do analista (editáveis — entram no ofício)",
        value=st.session_state.comentarios, height=200, key="caixa_comentarios")
    st.session_state.comentarios = comentarios
    revisado = st.checkbox("Confirmo que revisei as pendências e os comentários acima")

with col_doc:
    st.markdown("**Minuta de Ofício de Complementação**")
    st.caption("Consolida todas as pendências (administrativas + técnicas) em um "
               "documento Word editável, com justificativas e trechos de referência.")
    numero_oficio = st.text_input("Nº do ofício", value="042/2026")
    prazo_dias = st.number_input("Prazo (dias)", min_value=5, max_value=180, value=30)

    if revisado:
        docx_bytes = GeradorOficios().gerar_oficio_complementacao(
            dados_processo=dados,
            resultado_admin=admin,
            resultados_tecnicos=tecnicos,
            comentarios_analista=comentarios or None,
            numero_oficio=numero_oficio,
            prazo_dias=int(prazo_dias),
        )
        st.download_button(
            label="📄 Baixar Minuta de Ofício (.docx)",
            data=docx_bytes,
            file_name=f"oficio_complementacao_{numero_oficio.replace('/', '-')}.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            type="primary", width="stretch")
    else:
        st.button("📄 Baixar Minuta de Ofício (.docx)", disabled=True,
                  width="stretch",
                  help="Marque a confirmação de revisão do analista para liberar o download.")

# ------------------------- Persistência (protótipo) -------------------------
with st.expander("💾 Persistir processo no banco de dados (Fase 2)"):
    st.caption("Protótipo grava em SQLite local. Em produção, defina DATABASE_URL "
               "(PostgreSQL + PostGIS) — a coluna de geometria usa SIRGAS 2000 (SRID 4674).")
    numero_proc = st.text_input("Número do processo", value="2026/001234", key="num_proc_db")
    if st.button("Salvar processo no banco"):
        try:
            engine = obter_engine()
            with Session(engine) as sessao:
                semear_tabela_urm(sessao, AgenteFinanceiro.MATRIZ_URM)
            id_proc = salvar_processo(dados, admin, fin, processo["anexados"],
                                      numero_processo=numero_proc, engine=engine)
            st.success(f"Processo salvo com id {id_proc}.")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Falha ao salvar: {exc}")
