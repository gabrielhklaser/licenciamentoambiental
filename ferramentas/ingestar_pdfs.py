# -*- coding: utf-8 -*-
"""
Ingestão dos documentos oficiais (Fase de CALIBRAÇÃO)
=======================================================

Lê os PDFs oficiais da SEMA Campo Bom e produz:

    1. `docs_extraidos/<documento>/pagina_NNNN.txt` - texto integral por página
       (memória de consulta para calibração e auditoria da extração);
    2. Rascunhos de configuração em `config/*.draft.json`:
        - taxas_urm.draft.json            (Manual de Legislação e Taxas)
        - gabarito_trs.draft.json         (Termos de Referência)
        - checklists_oficiais.draft.json  (formulários .htm oficiais → checklist)
    3. Relatório de revisão: o que foi detectado, o que falta e os próximos passos.

IMPORTANTE (transparência): todo rascunho nasce com "revisado": false. Os valores
só entram em produção quando o licenciador confere o rascunho, corrige o que for
necessário e o promove para o arquivo final (sem o sufixo .draft). Enquanto isso,
os agentes usam os padrões internos do código.

Uso:
    python ferramentas/ingestar_pdfs.py arquivo1.pdf arquivo2.pdf ...
    python ferramentas/ingestar_pdfs.py --pasta uploads/          (todos os PDFs)
    python ferramentas/ingestar_pdfs.py --promover                (promove drafts revisados)
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import unicodedata
from typing import Optional
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from pypdf import PdfReader  # noqa: E402


def extrair_texto_docx(caminho: Path) -> list[str]:
    """Extrai texto de .docx (parágrafos + tabelas) como 'páginas' de texto."""
    from docx import Document  # python-docx
    doc = Document(str(caminho))
    linhas = [par.text for par in doc.paragraphs if par.text and par.text.strip()]
    for tabela in doc.tables:
        for linha in tabela.rows:
            celulas = [c.text.strip() for c in linha.cells if c.text and c.text.strip()]
            if celulas:
                linhas.append(" | ".join(celulas))
    # heurística: quebra em 'páginas' de ~40 linhas para manter o formato do pipeline
    paginas = ["\n".join(linhas[i:i + 40]) for i in range(0, len(linhas), 40)]
    return paginas or [""]

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger("ingestar")

PASTA_CONFIG_PADRAO = RAIZ / "config"
PASTA_TEXTOS_PADRAO = RAIZ / "docs_extraidos"


# ==============================================================================
# Utilidades
# ==============================================================================
def normalizar(texto: str) -> str:
    """Minúsculas sem acentos, espaços colapsados (comparação robusta)."""
    texto = unicodedata.normalize("NFKD", texto or "")
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto).lower().strip()


def num_br(bracelet: str) -> float | None:
    """Converte número no padrão brasileiro ('1.234,56') para float."""
    try:
        limpo = bracelet.strip().replace(".", "").replace(",", ".")
        return float(limpo)
    except (ValueError, AttributeError):
        return None


def slug(nome: str) -> str:
    slugify = re.sub(r"[^a-zA-Z0-9]+", "_", nome).strip("_").lower()
    return slugify[:60]


# ==============================================================================
# Extração de texto por página (com detecção de PDF digitalizado)
# ==============================================================================
def extrair_paginas(caminho_pdf: Path) -> list[str]:
    """Devolve o texto de cada página; lista vazia/pouco texto => PDF escaneado."""
    leitor = PdfReader(str(caminho_pdf))
    paginas = [(p.extract_text() or "") for p in leitor.pages]
    return paginas


def salvar_textos(nome_doc: str, paginas: list[str], pasta_textos: Path) -> Path:
    destino = pasta_textos / slug(nome_doc)
    destino.mkdir(parents=True, exist_ok=True)
    for i, texto in enumerate(paginas, start=1):
        (destino / f"pagina_{i:04d}.txt").write_text(texto, encoding="utf-8")
    (destino / "texto_completo.txt").write_text("\n\n".join(
        f"===== PÁGINA {i} =====\n{t}" for i, t in enumerate(paginas, start=1)),
        encoding="utf-8")
    return destino


def classificar_documento(texto: str) -> str:
    """Classifica o PDF pelo vocabulário característico do conteúdo."""
    t = normalizar(texto[:20000])
    pontuacao = {
        "manual_taxas": sum(t.count(k) for k in ["urm", "taxa de licenciamento",
                                                 "tabela de valores", "valor da urm",
                                                 "recolhimento", "emolumentos"]),
        "trs": sum(t.count(k) for k in ["termo de referencia", "gabarito",
                                        "sondagem", "reposicao florestal",
                                        "prad", "busca ativa", "criterios tecnicos"]),
        "formularios": sum(t.count(k) for k in ["formulario", "identificacao do empreendedor",
                                                "documentacao exigida", "pleito",
                                                "campo bom", "requerente"]),
    }
    return max(pontuacao, key=pontuacao.get) if max(pontuacao.values()) > 0 else "desconhecido"


# ==============================================================================
# EXTRATOR 1 - Manual de Taxas -> config/taxas_urm.draft.json
# ==============================================================================
PADROES_VALOR = re.compile(r"(-?\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)")

PORTES_CONHECIDOS = ["excepcional", "grande", "medio", "pequeno", "minimo"]
FAIXAS_CONHECIDAS = ["> 50 ha", "10 a 50 ha", "5 a 10 ha", "0 a 5 ha", "> 10 ha",
                     "acima de 10 ha", "acima de 50 ha", "ate 5 ha", "ate 10 ha"]


def extrair_taxas(paginas: list[str], fonte: str) -> dict:
    """Extrai rascunho da matriz de URMs do Manual de Taxas.

    Estratégia honesta: capturamos o valor da URM em reais, todas as linhas de
    tabela com valores, e tentamos estruturar as linhas de porte/faixa conhecidos.
    A conferência HUMANA do rascunho é parte obrigatória do processo.
    """
    texto_completo = "\n".join(paginas)
    t_norm = normalizar(texto_completo)

    draft: dict = {
        "fonte": fonte, "revisado": False,
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "valor_urm_reais": None, "matriz": {}, "valores_especie": {},
        "grupos_palavras_chave": {}, "linhas_capturadas": [],
        "aviso": "RASCUNHO automático - conferir com o Manual antes de promover.",
    }

    # Valor da URM em reais (ex.: '1 URM = R$ 125,00' / 'URM R$ ...')
    m = re.search(r"urm[^\n]{0,60}?r\$\s*([\d.]+,\d{2})", t_norm)
    if m:
        draft["valor_urm_reais"] = num_br(m.group(1))

    # Linhas candidatas de tabela: contêm pelo menos 2 valores numéricos monetários
    for pagina_num, pagina in enumerate(paginas, start=1):
        for linha in pagina.splitlines():
            linha_limpa = linha.strip()
            if len(linha_limpa) < 6:
                continue
            valores = re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2}", linha_limpa)
            tem_chave = any(k in normalizar(linha_limpa) for k in
                            PORTES_CONHECIDOS + FAIXAS_CONHECIDAS + ["erb", "lavra",
                                                                     "parcelamento", "loteamento",
                                                                     "autorizacao", "declaracao",
                                                                     "licenca", "urm"])
            if tem_chave and len(valores) >= 1:
                draft["linhas_capturadas"].append({
                    "pagina": pagina_num, "linha": linha_limpa[:200],
                    "valores": [num_br(v) for v in valores],
                })

    # Estruturação das linhas de PORTE conhecidos (grupo GERAL)
    # Aceita: 'Porte Mínimo - Potencial Baixo: LP 52,20 | LI 52,20 | LO 52,20'
    # ou linhas sem potencial explícito (aplica aos três potenciais).
    REGEX_PAR_FASE_VALOR = re.compile(
        r"\b(LP|LI|LO)\b[\s:|=>-]*((?:\d{1,3}(?:\.\d{3})*|\d+)(?:,\d{1,2})?)")

    def _pares_fase_valor(linha: str) -> dict[str, float]:
        pares = REGEX_PAR_FASE_VALOR.findall(linha.upper())
        saida = {}
        for fase, valor in pares:
            v = num_br(valor)
            if v is not None:
                saida[fase] = v
        return saida

    for item in draft["linhas_capturadas"]:
        linha_n = normalizar(item["linha"])
        valores = item["valores"]
        pares = _pares_fase_valor(item["linha"])

        # porte pela posição mais à ESQUERDA na linha (evita confundir
        # 'Pequeno - Potencial Médio' com porte Médio)
        posicoes = [(linha_n.find(p), p.upper()) for p in PORTES_CONHECIDOS
                    if linha_n.find(p) >= 0]
        porte = min(posicoes)[1] if posicoes else None

        if porte:
            m_pot = re.search(r"potencial\s*\w*\s*[:\-|/]?\s*(baixo|medio|alto)\b", linha_n)
            alvos = [m_pot.group(1).upper()] if m_pot else ["BAIXO", "MEDIO", "ALTO"]
            if pares:  # valores explícitos por fase na própria linha
                for pot in alvos:
                    for fase, valor in pares.items():
                        draft["matriz"].setdefault("GERAL", {}).setdefault(porte, {}) \
                            .setdefault(pot, {})[fase] = valor
            elif fase_implicita(linha_n) and valores:
                for pot in alvos:
                    draft["matriz"].setdefault("GERAL", {}).setdefault(porte, {}) \
                        .setdefault(pot, {})[fase_implicita(linha_n)] = valores[0]

        # Espécies simples
        if "autorizacao" in linha_n and valores:
            draft["valores_especie"]["AUTORIZACAO"] = valores[0]
        if "declaracao" in linha_n and valores:
            draft["valores_especie"]["DECLARACAO"] = valores[0]

    # ERB: linha com 'erb' e 3 valores (ou pares fase:valor) -> LP/LI/LO
    for item in draft["linhas_capturadas"]:
        if "erb" in normalizar(item["linha"]):
            pares = _pares_fase_valor(item["linha"])
            if len(pares) >= 3:
                draft["matriz"]["ERB"] = {"FIXO": {"_": pares}}
                break
            if len(item["valores"]) >= 3:
                vals = item["valores"][:3]
                draft["matriz"]["ERB"] = {"FIXO": {"_": {
                    "LP": vals[0], "LI": vals[1], "LO": vals[2]}}}
                break

    def _canonizar_faixa(linha_norm: str):
        """Padroniza a faixa de hectares na mesma chave usada pelo AgenteFinanceiro."""
        mapa = [("0 a 5 ha", [r"0\s*a\s*5\s*ha", r"ate\s*5\s*ha"]),
                ("5 a 10 ha", [r"5\s*a\s*10\s*ha", r"ate\s*10\s*ha"]),
                ("10 a 50 ha", [r"10\s*a\s*50\s*ha"]),
                ("> 50 ha", [r">\s*50\s*ha", r"acima de 50 ha", r"mais de 50 ha"]),
                ("> 10 ha", [r">\s*10\s*ha", r"acima de 10 ha", r"mais de 10 ha"])]
        for canon, padroes in mapa:
            if any(re.search(pat, linha_norm) for pat in padroes):
                return canon
        return None

    def _fase_implicita(linha_norm: str) -> Optional[str]:
        """Detecta a fase citada na linha (LP/LI/LO ou nome por extenso)."""
        if re.search(r"\blp\b|\blicenca previa\b", linha_norm):
            return "LP"
        if re.search(r"\bli\b|\blicenca de instalacao\b", linha_norm):
            return "LI"
        if re.search(r"\blo\b|\blicenca de operacao\b", linha_norm):
            return "LO"
        return None

    # Lavra mineral / parcelamento: linhas com faixa de hectares
    for item in draft["linhas_capturadas"]:
        linha_n = normalizar(item["linha"])
        faixa = _canonizar_faixa(linha_n)
        if not faixa:
            continue
        if any(k in linha_n for k in ["lavra", "mineracao", "extracao mineral", "pedreira"]):
            grupo = "LAVRA_MINERAL"
        elif any(k in linha_n for k in ["parcelamento", "loteamento", "desmembramento"]):
            grupo = "PARCELAMENTO_SOLO"
        else:
            continue
        fases_hoje = draft["matriz"].setdefault(grupo, {}).setdefault(faixa, {}).setdefault("_", {})
        pares = _pares_fase_valor(item["linha"])
        if pares:
            fases_hoje.update(pares)
        elif len(item["valores"]) >= 3:
            fases_hoje.update({"LP": item["valores"][0], "LI": item["valores"][1],
                               "LO": item["valores"][2]})
        elif _fase_implicita(linha_n) and item["valores"]:
            fases_hoje[_fase_implicita(linha_n)] = item["valores"][0]

    return draft


# ==============================================================================
# EXTRATOR 2 - Termos de Referência -> config/gabarito_trs.draft.json
# ==============================================================================
def _primeiro_trecho(paginas: list[str], padrao: re.Pattern, contexto: int = 140):
    """Localiza o padrão e devolve (valor, trecho, página) ou None."""
    for num_pagina, pagina in enumerate(paginas, start=1):
        achou = padrao.search(pagina)
        if achou:
            ini = max(0, achou.start() - 60)
            trecho = re.sub(r"\s+", " ", pagina[ini:achou.end() + contexto]).strip()
            return achou.groups(), trecho, num_pagina
    return None


def extrair_gabarito_trs(paginas: list[str], fonte: str) -> dict:
    """Extrai os parâmetros normativos dos TRs com o trecho que embasa cada um."""
    texto_norm = normalizar("\n".join(paginas))
    parametros: dict[str, dict] = {}

    def registrar(chave, padrao, cast=float, descricao=""):
        achou = _primeiro_trecho(paginas, padrao)
        if achou:
            grupos, trecho, pagina = achou
            bruto = next((g for g in grupos if g), None)
            valor = num_br(bruto) if cast is float else (
                int(bruto) if bruto and bruto.isdigit() else bruto)
            if valor is not None:
                parametros[chave] = {
                    "valor": valor, "trecho": trecho, "pagina": pagina,
                    "descricao": descricao,
                }

    registrar("rscc_distancia_minima_lencol_m",
              re.compile(r"(?:len[çc]ol[^.]{0,200}?m[íi]nim[oa][^.]{0,80}?"
                          r"|m[íi]nim[oa][^.]{0,200}?len[çc]ol[^.]{0,120}?)"
                          r"([\d]+[.,]?\d*)\s*m(?:etros)?\b", re.I),
              descricao="Distância vertical mínima lençol x base do aterro (m)")
    registrar("rscc_sondagem_base",
              re.compile(r"m[íi]nim[oa]\s*(?:de\s*)?(\d+)\s*furos", re.I),
              cast=int, descricao="Nº mínimo de furos de sondagem (base)")
    registrar("rscc_sondagem_por_ha_excedente",
              re.compile(r"(\d+)\s*furo(?:s)?\s*(?:adicional|a mais|extra)[^.]{0,80}?hectare", re.I),
              cast=int, descricao="Furos adicionais por hectare excedente")
    # RFO: extração por PROXIMIDADE dentro da sentença de supressão - pega o
    # 'N mudas' mais próximo antes de 'indivíduo nativo/exótico (suprimido)',
    # descartando números >= 1000 (que são densidade, ex.: 3.000 mudas/ha).
    def _mudas_proximas(sentenca: str, alvo: str, janela: int = 90):
        m_alvo = re.search(alvo, sentenca, re.I)
        if not m_alvo:
            return None
        contexto = sentenca[max(0, m_alvo.start() - janela):m_alvo.start()]
        candidatos = [int(n.replace(".", "")) for n in
                      re.findall(r"(\d{1,3}(?:\.\d{3})*)\s*mudas?", contexto, re.I)]
        candidatos = [n for n in candidatos if 0 < n < 1000]
        return candidatos[-1] if candidatos else None

    for num_pagina, pagina in enumerate(paginas, start=1):
        for sentenca in re.split(r"(?<=[.;])\s+", pagina):
            if "mudas" in normalizar(sentenca) and "suprimid" in normalizar(sentenca):
                trecho = re.sub(r"\s+", " ", sentenca).strip()[:280]
                v_nat = _mudas_proximas(
                    sentenca, r"nativos?\s+suprimid|indiv[íi]duos?\s+nativos?")
                v_exo = _mudas_proximas(
                    sentenca, r"ex[óo]ticos?\s+suprimid|indiv[íi]duos?\s+ex[óo]ticos?")
                if v_nat and "rfo_mudas_por_nativo" not in parametros:
                    parametros["rfo_mudas_por_nativo"] = {
                        "valor": v_nat, "trecho": trecho, "pagina": num_pagina,
                        "descricao": "Mudas nativas por indivíduo nativo suprimido"}
                if v_exo and "rfo_mudas_por_exotico" not in parametros:
                    parametros["rfo_mudas_por_exotico"] = {
                        "valor": v_exo, "trecho": trecho, "pagina": num_pagina,
                        "descricao": "Mudas por indivíduo exótico suprimido"}
        if "rfo_mudas_por_nativo" in parametros and "rfo_mudas_por_exotico" in parametros:
            break

    registrar("rfo_densidade_minima_mudas_ha",
              re.compile(r"(?:m[íi]nim[oa][^.]{0,60})?(\d{1,3}(?:\.\d{3})*)\s*mudas?\s*(?:por|/)\s*(?:ha|hectare)", re.I),
              descricao="Densidade mínima de plantio (mudas/ha)")
    registrar("prad_monitoramento_minimo_anos",
              re.compile(r"monitorament[^.]{0,200}?m[íi]nim[oa][^.]{0,60}?(\d+)\s*anos", re.I),
              cast=int, descricao="Monitoramento mínimo do PRAD (anos)")

    for pagina_num, pagina in enumerate(paginas, start=1):
        p_n = normalizar(pagina)
        if re.search(r"trimestrais?", p_n) and ("supress" in p_n or "movimenta" in p_n) \
                and "pca_periodicidade_supressao" not in parametros:
            trecho = re.sub(r"\s+", " ", pagina[:300])
            parametros["pca_periodicidade_supressao"] = {
                "valor": "trimestral", "trecho": trecho, "pagina": pagina_num,
                "descricao": "Periodicidade dos relatórios - supressão/movimentação de solo"}
        if re.search(r"semestrais?", p_n) and "obra" in p_n \
                and "pca_periodicidade_obras" not in parametros:
            trecho = re.sub(r"\s+", " ", pagina[:300])
            parametros["pca_periodicidade_obras"] = {
                "valor": "semestral", "trecho": trecho, "pagina": pagina_num,
                "descricao": "Periodicidade dos relatórios - fase de obras"}

    # parâmetro de fauna (métodos mínimos) é qualitativo: registra a evidência
    if "busca ativa" in texto_norm and "busca passiva" in texto_norm:
        parametros["fauna_metodos_minimos"] = {
            "valor": "1 busca ativa + 1 busca passiva por grupo",
            "trecho": "detectado no corpo dos TRs", "pagina": None,
            "descricao": "Metodologia mínima do laudo de fauna"}

    return {
        "fonte": fonte, "revisado": False,
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "parametros": parametros,
        "aviso": "RASCUNHO automático - conferir cada valor/trecho antes de promover.",
    }


# ==============================================================================
# EXTRATOR 3 - Formulários -> config/checklists_oficiais.draft.json
# ==============================================================================
REGEX_SECAO_DOCS = re.compile(r"documenta[çc][ãa]o\s+exigida|documentos?\s+exigidos?",
                              re.I)
REGEX_FASES = [
    ("LOR", re.compile(r"opera[çc][ãa]o\s+e\s+regulariza[çc][ãa]o|\(LOR\)", re.I)),
    ("LIR", re.compile(r"instala[çc][ãa]o\s+e\s+regulariza[çc][ãa]o|\(LIR\)", re.I)),
    ("LP", re.compile(r"licen[çc]a\s+pr[ée]via|\(LP\)", re.I)),
    ("LI", re.compile(r"licen[çc]a\s+de\s+instala[çc][ãa]o(?!\s+e\s+reg)|\(LI\)", re.I)),
    ("LO", re.compile(r"licen[çc]a\s+de\s+opera[çc][ãa]o(?!\s+e\s+reg)|\(LO\)", re.I)),
    ("AUTORIZACAO", re.compile(r"autoriza[çc][ãa]o", re.I)),
    ("DECLARACAO", re.compile(r"declara[çc][ãa]o", re.I)),
]


def extrair_checklists(paginas: list[str], fonte: str) -> dict:
    """Extrai os checklists de documentação exigida por fase dos formulários."""
    checklists: dict[str, list[str]] = {}
    fase_corrente: Optional[str] = None
    pleito_formulario: Optional[str] = None  # pleito declarado no título do formulário
    secao_iniciada = False

    for pagina in paginas:
        for linha in pagina.splitlines():
            linha = linha.strip()
            if not linha or len(linha) < 3:
                continue
            linha_n = normalizar(linha)

            # 1) pleito do formulário (título/cabeçalho, antes da seção de documentos)
            if pleito_formulario is None and not secao_iniciada and len(linha) < 160:
                for fase, padrao in REGEX_FASES:
                    if padrao.search(linha_n):
                        pleito_formulario = fase
                        break

            # 2) início da seção de documentação (a linha pode já indicar a fase)
            if REGEX_SECAO_DOCS.search(linha_n):
                secao_iniciada = True
                for fase, padrao in REGEX_FASES:
                    if padrao.search(linha_n):
                        fase_corrente = fase
                        checklists.setdefault(fase_corrente, [])
                        break
                continue

            if not secao_iniciada:
                continue

            # 3) cabeçalho de fase dentro da seção (ex.: 'Documentação para a LI:')
            cabe_fase = None
            if len(linha) < 120:
                for fase, padrao in REGEX_FASES:
                    if padrao.search(linha_n):
                        cabe_fase = fase
                        break
            if cabe_fase:
                fase_corrente = cabe_fase
                checklists.setdefault(fase_corrente, [])
                continue

            # 4) item do checklist (bullets/numeração removidos)
            item = re.sub(r"^([-•*·▪‣◦]|\d{1,2}[.)]|[a-z][.)])\s*", "", linha).strip()
            destino = fase_corrente or pleito_formulario or "_SEM_FASE"
            if item and 3 < len(item) < 220 and not re.match(r"^[\d.,;\s]+$", item):
                lista = checklists.setdefault(destino, [])
                if item.lower() not in (d.lower() for d in lista):
                    lista.append(item)

    # remove fases vazias e registra o pleito detectado (rastreabilidade)
    checklists = {fase: docs for fase, docs in checklists.items() if docs}
    return {
        "fonte": fonte, "revisado": False,
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "pleitos_detectados": sorted({f for f in checklists if f != "_SEM_FASE"}),
        "documentos_por_fase": checklists,
        "aviso": "RASCUNHO automático - conferir os itens por fase antes de promover.",
    }


# ==============================================================================
# Orquestração
# ==============================================================================
def promover_drafts(pasta_config: Path) -> None:
    """Promove todos os *.draft.json para o nome final (após revisão humana)."""
    promovidos = []
    for draft in sorted(pasta_config.glob("*.draft.json")):
        final = draft.with_name(draft.name.replace(".draft.json", ".json"))
        dados = json.loads(draft.read_text(encoding="utf-8"))
        dados["revisado"] = True
        dados["revisado_em"] = datetime.now().isoformat(timespec="seconds")
        final.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
        draft.unlink()
        promovidos.append(final.name)
    print("\n".join(f"  ✓ promovido: {n} (revisado=true)" for n in promovidos)
          if promovidos else "  nenhum .draft.json encontrado para promover.")



def _caminho_rel(caminho: Path) -> str:
    """Caminho relativo à raiz quando possível (senão, absoluto)."""
    try:
        return str(caminho.resolve().relative_to(RAIZ))
    except (ValueError, AttributeError):
        return str(caminho)

def processar_pdf(caminho: Path, pasta_config: Path, pasta_textos: Path) -> dict:
    if caminho.suffix.lower() == ".docx":
        paginas = extrair_texto_docx(caminho)
    elif caminho.suffix.lower() == ".doc":
        resultado = {"arquivo": caminho.name,
                     "status": "formato .doc legado - converter para .docx/.pdf (LibreOffice) ou usar a versão atual no site da Prefeitura"}
        return resultado
    else:
        paginas = extrair_paginas(caminho)
    total_chars = sum(len(p) for p in paginas)
    resultado = {"arquivo": caminho.name, "paginas": len(paginas), "caracteres": total_chars}

    if total_chars < 200:
        resultado["status"] = "SEM CAMADA DE TEXTO (provável digitalização) - requer OCR"
        salvar_textos(caminho.name, paginas, pasta_textos)
        return resultado

    destino = salvar_textos(caminho.name, paginas, pasta_textos)
    tipo = classificar_documento("\n".join(paginas))
    resultado["tipo"] = tipo
    try:
        resultado["textos"] = _caminho_rel(destino)
    except ValueError:  # pasta de textos fora do repositório
        resultado["textos"] = str(destino)

    if tipo == "manual_taxas":
        draft = extrair_taxas(paginas, caminho.name)
        saida = pasta_config / "taxas_urm.draft.json"
        saida.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
        resultado["config_gerada"] = _caminho_rel(saida)
        resultado["resumo"] = {
            "valor_urm_reais": draft["valor_urm_reais"],
            "linhas_capturadas": len(draft["linhas_capturadas"]),
            "entradas_estruturadas": {
                g: list(ps.keys()) for g, ps in draft["matriz"].items()},
        }
    elif tipo == "trs":
        draft = extrair_gabarito_trs(paginas, caminho.name)
        saida = pasta_config / "gabarito_trs.draft.json"
        saida.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
        resultado["config_gerada"] = _caminho_rel(saida)
        resultado["resumo"] = {k: v["valor"] for k, v in draft["parametros"].items()}
    elif tipo == "formularios":
        draft = extrair_checklists(paginas, caminho.name)
        saida = pasta_config / "checklists_oficiais.draft.json"
        existente = {}
        if saida.exists():
            existente = json.loads(saida.read_text(encoding="utf-8")).get("documentos_por_fase", {})
        for fase, docs in draft["documentos_por_fase"].items():
            mesclado = existente.get(fase, [])
            for d in docs:
                if d.lower() not in (x.lower() for x in mesclado):
                    mesclado.append(d)
            existente[fase] = mesclado
        draft["documentos_por_fase"] = existente
        draft["fonte"] = f"formulários oficiais ({len(existente)} fases)"
        saida.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
        resultado["config_gerada"] = _caminho_rel(saida)
        resultado["resumo"] = {f: len(d) for f, d in draft["documentos_por_fase"].items()}
    else:
        resultado["status"] = "tipo não reconhecido - conferir docs_extraidos/"
    return resultado


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingestão dos PDFs oficiais da SEMA Campo Bom")
    parser.add_argument("pdfs", nargs="*", help="caminhos dos PDFs oficiais")
    parser.add_argument("--pasta", help="pasta com PDFs (processa todos)")
    parser.add_argument("--pasta-config", default=str(PASTA_CONFIG_PADRAO))
    parser.add_argument("--pasta-textos", default=str(PASTA_TEXTOS_PADRAO))
    parser.add_argument("--promover", action="store_true",
                        help="promove os *.draft.json revisados para o nome final")
    args = parser.parse_args()

    pasta_config = Path(args.pasta_config)
    pasta_textos = Path(args.pasta_textos)
    pasta_config.mkdir(parents=True, exist_ok=True)
    pasta_textos.mkdir(parents=True, exist_ok=True)

    if args.promover:
        print("== PROMOÇÃO DE RASCUNHOS REVISADOS ==")
        promover_drafts(pasta_config)
        return

    caminhos = [Path(p) for p in args.pdfs]
    if args.pasta:
        for padrao in ("*.pdf", "*.docx", "*.doc"):
            caminhos.extend(sorted(Path(args.pasta).glob(padrao)))
    if not caminhos:
        parser.print_help()
        sys.exit(1)

    print("== INGESTÃO DOS DOCUMENTOS OFICIAIS ==\n")
    relatorios = []
    for caminho in caminhos:
        if not caminho.exists():
            print(f"  ✗ arquivo não encontrado: {caminho}")
            continue
        print(f"  → processando {caminho.name} ...")
        relatorio = processar_pdf(caminho, pasta_config, pasta_textos)
        relatorios.append(relatorio)
        print(json.dumps(relatorio, ensure_ascii=False, indent=2))

    print("\n== PRÓXIMOS PASSOS ==")
    print("  1. Revise os rascunhos em", pasta_config, "(campos 'revisado': false)")
    print("  2. Confira os trechos em", pasta_textos, "quando houver dúvida num valor")
    print("  3. Após a conferência, promova:  python ferramentas/ingestar_pdfs.py --promover")
    print("  4. Rode os testes:  python -m pytest tests/ -v")
    print("\nEnquanto os rascunhos não forem promovidos, os agentes usam os padrões do código.")


if __name__ == "__main__":
    main()
