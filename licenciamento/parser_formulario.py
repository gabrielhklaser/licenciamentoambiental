# -*- coding: utf-8 -*-
"""
FASE 1 - Motor de Ingestão e Estruturação de Dados (Parser)
============================================================

Este módulo NÃO toma decisões de aprovação. Ele é estritamente responsável por:

    1. Ler formulários de licenciamento ambiental nos formatos .htm / .html;
    2. Extrair as informações cruciais com abordagem HÍBRIDA:
         - Navegação pelo DOM (BeautifulSoup4) para isolar blocos de tabelas/divs
           (ex.: seção "IDENTIFICAÇÃO DO EMPREENDIMENTO");
         - Expressões regulares (re) para limpar e capturar valores exatos;
    3. Identificar o tipo de pleito (LP, LI, LO, LIR, LOR, Autorização, Declaração);
    4. Extrair o checklist de documentos exigidos, aplicando:
         - Regra de agrupamento: LIR = LP + LI  |  LOR = LP + LI + LO;
         - Desduplicação por normalização de texto + similaridade de strings
           (difflib.SequenceMatcher), suprimindo redundâncias entre fases;
    5. Validar a hard constraint de Responsabilidade Técnica (ART):
         - ART ausente/vazia => status_triagem = "bloqueado_sem_art";
    6. Devolver um dicionário estruturado (JSON) que alimenta as próximas fases.

Princípios de robustez: qualquer exceção de extração é capturada em try/except;
o campo problemático recebe None e a ocorrência é registrada em log (logger),
sem interromper o processamento.
"""

from __future__ import annotations

import html as html_mod
import json
import logging
import re
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Optional

from bs4 import BeautifulSoup

from .calibracao import Calibracao, PASTA_CONFIG_PADRAO

logger = logging.getLogger("licenciamento.parser_formulario")


# ==============================================================================
# Funções utilitárias (funções puras - fáceis de testar unitariamente)
# ==============================================================================
def normalizar(texto: Optional[str]) -> str:
    """Remove acentos, converte para maiúsculas e colapsa espaços em branco.

    Usada para comparar rótulos/documentos de forma imune a variações de layout.
    Espaços em torno de "/" são removidos ('Nome / Razão Social' ==
    'Nome/Razão Social'), pois os formulários oficiais variam a grafia dos
    rótulos compostos.
    """
    if not texto:
        return ""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"\s+", " ", texto).strip().upper()
    return re.sub(r"\s*/\s*", "/", texto)


def para_float(valor_br: Optional[str]) -> Optional[float]:
    """Converte números no padrão brasileiro ('1.234,56' / '12,5') para float."""
    if valor_br is None:
        return None
    try:
        limpo = str(valor_br).strip().replace(".", "").replace(",", ".")
        # caso o valor já venha no padrão internacional ('1234.56')
        if limpo.count(".") > 1:
            limpo = limpo.replace(".", "")
        return float(limpo)
    except (TypeError, ValueError):
        return None


def graus_para_decimal(graus: float, minutos: float, segundos: float, hemisferio: str) -> float:
    """Converte coordenada em GMS (grau/minuto/segundo) para graus decimais."""
    decimal = abs(graus) + minutos / 60.0 + segundos / 3600.0
    if str(hemisferio).strip().upper() in ("S", "W", "O"):
        decimal = -decimal
    return round(decimal, 7)


# ==============================================================================
# Classe principal - FormularioParser
# ==============================================================================
class FormularioParser:
    """Extrai e estrutura os dados de um formulário HTML de licenciamento ambiental.

    Uso:
        parser = FormularioParser(caminho_arquivo="formulario.htm")
        dados = parser.gerar_json()          # dicionário estruturado
        parser.gerar_json(caminho_saida="saida.json")  # também grava em disco
    """

    # --------------------------------------------------------------------------
    # Padrões centralizados (CALIBRÁVEIS conforme os formulários reais do município)
    # --------------------------------------------------------------------------
    PORTES_VALIDOS = ("MINIMO", "PEQUENO", "MEDIO", "GRANDE", "EXCEPCIONAL")
    POTENCIAIS_VALIDOS = ("BAIXO", "MEDIO", "ALTO")

    # Rótulos amigáveis (com acentuação correta) para o JSON de saída
    ROTULO_AMIGAVEL = {
        "MINIMO": "Mínimo", "PEQUENO": "Pequeno", "MEDIO": "Médio",
        "GRANDE": "Grande", "EXCEPCIONAL": "Excepcional",
        "BAIXO": "Baixo", "ALTO": "Alto",
    }

    # Rótulos alternativos (normalizados, sem acento) para busca no DOM e no texto
    ROTULOS = {
        "nome_razao_social": ["NOME/RAZAO SOCIAL", "RAZAO SOCIAL", "NOME DO EMPREENDEDOR",
                              "NOME OU RAZAO SOCIAL", "EMPREENDEDOR (NOME/RAZAO SOCIAL)"],
        "nome_fantasia": ["NOME FANTASIA", "NOME FANTASIA (QUANDO HOUVER)",
                          "NOME DE FANTASIA", "NOME COMERCIAL"],
        "cpf_cnpj": ["CPF/CNPJ", "CPF OU CNPJ", "CNPJ/CPF", "CNPJ OU CPF"],
        "nome_empreendimento": ["NOME DO EMPREENDIMENTO", "EMPREENDIMENTO", "DENOMINACAO DO EMPREENDIMENTO"],
        "ramo_atividade": ["RAMO DE ATIVIDADE", "RAMO DA ATIVIDADE", "ATIVIDADE", "DESCRICAO DA ATIVIDADE"],
        "codram": ["CODRAM", "CODIGO DO RAMO DE ATIVIDADE", "COD. RAMO", "CODIGO RAMO DE ATIVIDADE"],
        "porte": ["PORTE DO EMPREENDIMENTO", "PORTE"],
        "potencial_poluidor": ["POTENCIAL POLUIDOR", "POTENCIAL DE POLUICAO",
                               "POTENCIAL POLUIDOR/DEGRADADOR",
                               "PORTE/POTENCIAL POLUIDOR", "PORTE/POTENCIAL"],
        "area_total": ["AREA TOTAL DO IMOVEL", "AREA TOTAL", "AREA TOTAL (HA)",
                      "AREA DO IMOVEL (HA)", "AREA TOTAL DO EMPREENDIMENTO",
                      "AREA TOTAL (M2)", "SUPERFICIE TOTAL",
                      "AREA DO EMPREENDIMENTO", "AREA DO PARCELAMENTO",
                      "AREA TOTAL DO PARCELAMENTO", "AREA DO LOTEAMENTO",
                      "AREA TOTAL DO LOTEAMENTO", "AREA DA PROPRIEDADE",
                      "AREA TOTAL DA PROPRIEDADE", "AREA DO TERRENO"],
        "area_util": ["AREA UTIL/DE INTERVENCAO", "AREA UTIL", "AREA DE INTERVENCAO",
                      "AREA UTIL DO EMPREENDIMENTO", "AREA DE INTERVENCAO (HA)",
                      "AREA TOTAL DE INTERVENCAO", "AREA DA INTERVENCAO",
                      "AREA UTIL TOTAL"],
        "matricula_imovel": ["MATRICULA DO IMOVEL", "MATRICULA IMOVEL", "N. DA MATRICULA",
                             "MATRICULA (CARTORIO DE REGISTRO DE IMOVEIS)", "MATRICULA GERAL"],
        "endereco_empreendimento": ["ENDERECO DO EMPREENDIMENTO", "LOCALIZACAO DO EMPREENDIMENTO",
                                    "ENDERECO/LOCALIZACAO"],
        "nome_responsavel_tecnico": ["NOME DO RESPONSAVEL TECNICO", "RESPONSAVEL TECNICO (NOME)",
                                     "RESPONSAVEL TECNICO", "RESPONSAVEL TECNICO/AUTOR DO PROJETO"],
        "registro_art": ["N. DA ART", "NUMERO DA ART", "ART", "ART N.", "ART (ANOTACAO DE RESPONSABILIDADE TECNICA)",
                         "ANOTACAO DE RESPONSABILIDADE TECNICA", "N ART"],
        "registro_crea": ["REGISTRO CREA", "CREA", "N. REGISTRO CREA", "CREA/CAU"],
        "tipo_licenca": ["TIPO DE LICENCA", "ESPECIE DO PLEITO", "PLEITO", "LICENCA REQUERIDA",
                         "TIPO/ESPECIE DO PLEITO", "OBJETO DO PLEITO"],
        "coordenadas_utm": ["COORDENADAS UTM", "COORDENADA UTM",
                            "COORDENADAS UTM (SIRGAS 2000)", "COORDENADAS (SIRGAS 2000)",
                            "COORDENADAS DO EMPREENDIMENTO", "COORDENADAS", "UTM"],
        "coordenada_e": ["E (EASTING)", "COORDENADA E", "E (M)", "E"],
        "coordenada_n": ["N (NORTHING)", "COORDENADA N", "N (M)", "N"],
        "fuso": ["FUSO", "FUSO UTM"],
        "latitude": ["LATITUDE", "LAT", "LATITUDE (GMS)", "LATITUDE (GRAUS DECIMAIS)"],
        "longitude": ["LONGITUDE", "LONG", "LON", "LONGITUDE (GMS)", "LONGITUDE (GRAUS DECIMAIS)"],
        "municipio": ["MUNICIPIO", "CIDADE"],
    }

    # Padrões RegEx de captura de valores específicos (aplicados ao texto original)
    REGEX = {
        "cpf_cnpj": re.compile(
            r"(\d{3}\.?\d{3}\.?\d{3}-?\d{2}|\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2})"
        ),
        "codram": re.compile(r"CODRAM\s*[:\-]?\s*([0-9][0-9.\-/]{0,15})", re.I),
        "art": re.compile(
            r"\bART\b\s*(?:N[ºo°.]?|NUMERO|N\s*\.)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9./\-]{2,20})", re.I
        ),
        "matricula": re.compile(
            r"MATRICULA\s*(?:DO\s+IM[ÓO]VEL|GERAL|N[ºo°.]*)?\s*[:\-]?\s*([\d][\d.\-/]{0,20})", re.I
        ),
        "latitude_grau_decimal": re.compile(r"LAT(?:ITUDE)?\s*[:=]?\s*(-?\d{1,2}[.,]\d{2,8})", re.I),
        "longitude_grau_decimal": re.compile(r"LON(?:GITUDE|G)?\s*[:=]?\s*(-?\d{1,3}[.,]\d{2,8})", re.I),
        "latitude_gms": re.compile(
            r"LAT(?:ITUDE)?\s*[:=]?\s*(\d{1,2})[ºo°]\s*(\d{1,2})['′]\s*([\d.,]+)[\"″]?\s*([SsNn])?", re.I
        ),
        "longitude_gms": re.compile(
            r"LON(?:GITUDE|G)?\s*[:=]?\s*(\d{1,3})[ºo°]\s*(\d{1,2})['′]\s*([\d.,]+)[\"″]?\s*([WwOoEe])?", re.I
        ),
        "utm_e": re.compile(r"\bE(?:ASTING)?\s*[:=]?\s*(-?\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?)", re.I),
        "utm_n": re.compile(r"\bN(?:ORTHING)?\s*[:=]?\s*(-?\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?)", re.I),
        "fuso": re.compile(r"FUSO\s*[:\-]?\s*(2[23])", re.I),
        "area": re.compile(r"(-?\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,4})?)\s*(ha|m2|m²|m\xb2)?", re.I),
    }

    # Classificação do pleito em ordem de prioridade (LOR/LIR antes de LO/LI para
    # evitar falsos positivos por sub-cadeia). Padrões aplicados sobre texto normalizado.
    PLEITOS = [
        # LOR/LOR reconhecem as DUAS ordens usadas nos formulários:
        # "Licença de Operação e Regularização" e "Licença de Regularização e Operação"
        ("LOR", re.compile(r"LICENCA[S]? DE (?:OPERACAO E REGULARIZACAO|"
                           r"REGULARIZACAO E OPERACAO)|\(\s*LOR\s*\)|\bLOR\b")),
        ("LIR", re.compile(r"LICENCA[S]? DE (?:INSTALACAO|IMPLANTACAO) E REGULARIZACAO|"
                           r"LICENCA[S]? DE REGULARIZACAO E (?:INSTALACAO|IMPLANTACAO)|"
                           r"\(\s*LIR\s*\)|\bLIR\b")),
        ("LP", re.compile(r"LICENCA[S]? PREVIA|\(\s*LP\s*\)|\bLP\b")),
        ("LI", re.compile(r"LICENCA[S]? DE INSTALACAO|\(\s*LI\s*\)|\bLI\b")),
        ("LO", re.compile(r"LICENCA[S]? DE OPERACAO(?!\s+E\s+REGULARIZACAO)|\(\s*LO\s*\)|\bLO\b")),
        ("LICENCA_UNICA", re.compile(r"LICENCA\s+UNICA|\(\s*LICENCA\s+UNICA\s*\)")),
        ("ALVARA_FLORESTAL", re.compile(r"ALVARA\s+FLORESTAL")),
        ("AUTORIZACAO", re.compile(r"AUTORIZACAO\s+AMBIENTAL|\(\s*AUTORIZACAO\s*\)|AUTORIZACAO")),
        ("DECLARACAO", re.compile(r"DECLARACAO\s+AMBIENTAL|\(\s*DECLARACAO\s*\)|DECLARACAO")),
    ]

    # Fases que compõem cada espécie de pleito (regra de agrupamento das regularizações)
    FASES_COMPONENTES = {
        "LP": ["LP"], "LI": ["LI"], "LO": ["LO"],
        "LIR": ["LP", "LI"],           # LIR exige o somatório LP + LI
        "LOR": ["LP", "LI", "LO"],     # LOR exige o somatório LP + LI + LO
        "LICENCA_UNICA": ["LP", "LI", "LO"],  # licença que substitui as 3 fases
        "AUTORIZACAO": ["AUTORIZACAO"],
        "DECLARACAO": ["DECLARACAO"],
        "ALVARA_FLORESTAL": ["ALVARA_FLORESTAL"],
    }

    # Cabeçalhos que marcam o início da seção de documentação exigida (texto normalizado)
    REGEX_SECAO_DOCS = re.compile(
        r"DOCUMENTACAO\s+EXIGIDA|DOCUMENTOS\s+EXIGIDOS|DOCUMENTACAO\s+NECESSARIA|"
        r"RELACAO\s+DE\s+DOCUMENTOS|CHECKLIST\s+DE\s+DOCUMENTOS"
    )

    # Cabeçalhos de sub-seção por fase dentro do checklist (ordem de prioridade)
    REGEX_SUBSECOES_FASE = [
        ("LOR", re.compile(r"LICENCA DE OPERACAO E REGULARIZACAO|\(LOR\)|PARA LOR")),
        ("LIR", re.compile(r"LICENCA DE INSTALACAO E REGULARIZACAO|\(LIR\)|PARA LIR")),
        ("LP", re.compile(r"LICENCA PREVIA|\(LP\)|PARA LP")),
        ("LI", re.compile(r"LICENCA DE INSTALACAO(?!\s+E\s+REGULARIZACAO)|\(LI\)|PARA LI")),
        ("LO", re.compile(r"LICENCA DE OPERACAO(?!\s+E\s+REGULARIZACAO)|\(LO\)|PARA LO")),
        ("AUTORIZACAO", re.compile(r"AUTORIZACAO")),
        ("DECLARACAO", re.compile(r"DECLARACAO")),
    ]

    # Cabeçalhos que iniciam a seção do MOTIVO DO ENCAMINHAMENTO / tipo do pleito
    # (o tipo da licença vem da MARCAÇÃO feita dentro dela, não das listas de docs)
    REGEX_SECAO_PLEITO = re.compile(
        r"MOTIVO\s+DO\s+ENCAMINHAMENTO|MOTIVO\s+DO\s+REQUERIMENTO|"
        r"TIPO\s+DE\s+LICENCIAMENTO|TIPO\s+DE\s+LICENCA\s+PRETENDIDA|"
        r"IDENTIFICACAO\s+DO\s+PLEITO", re.I)

    # Marcadores de marcação em texto (formulários convertidos de Word):
    # (X) ( x ) [x] (o) ( O ) (✓) ☒ ✔ ● - logo antes do rótulo da opção
    # ('o'/'O' = CÍRCULO marcado no formulário oficial; '○' vazio não marca)
    REGEX_MARCADOR_MARCADO = re.compile(
        r"[(\[\{]\s*[xXoO✓☒✔☑■●◉⬤⊙]\s*[)\]\}]|[✓☒✔☑■●◉⬤⊙þÞýÝüÜûÛÐð]")
    # glifos de caixa MARCADA que o Word exporta (fontes Wingdings/Symbol):
    # þ = caixa com visto; ý/Ð = caixa com X; ü/û = variações

    # Marca isolada em célula própria da tabela: <td>X</td><td>Licença Prévia</td>
    # 'o'/'O' = marca em CÍRCULO '( o )' (radio impresso do formulário oficial);
    # círculos CHEIOS (●◉⬤⊙) = marcados; o círculo VAZIO '○' NÃO é marca
    CELULAS_MARCA = {"X", "XX", "O", "(X)", "( X )", "(O)", "( O )", "[X]",
                     "[ X ]", "[O]", "[ O ]", "✓", "☒", "✔", "■",
                     "●", "◉", "⬤", "⊙", "þ", "Þ", "ý", "Ý", "ü", "Ü",
                     "û", "Û", "Ð", "ð"}

    # Cabeçalhos da tabela de MOTIVO em DUAS COLUNAS (formulário oficial SEMA):
    # coluna 1 = 'Primeira licença', coluna 2 = 'Renovação' (tipos marcados com
    # círculo '( o )' logo abaixo do cabeçalho da coluna ativa)
    REGEX_PRIMEIRA_LICENCA = re.compile(r"PRIMEIRAS?\s+LICENCAS?\b", re.I)
    REGEX_RENOVACAO = re.compile(r"RENOVACA\w*", re.I)

    # Linha que contém APENAS o símbolo de marcação: o get_text() do Word
    # separa o <span> do símbolo ('( X )', 'o', 'þ', '●'...) do rótulo, que
    # cai na LINHA SEGUINTE - os dois precisam ser reattachados para a leitura
    RE_SIMBOLO_SOZINHO = re.compile(
        r"^(?:[(\[\{]\s*[^\w\s]{0,3}\s*[)\]\}]"
        r"|[oOxX✓☒✔☑■●◉⬤⊙þÞýÝüÜûÛÐð□☐])$")

    # Porte/Potencial combinados num único campo: 'Pequeno/Baixo'
    RE_PORTE_POTENCIAL = re.compile(
        r"\b(MINIMO|PEQUENO|MEDIO|GRANDE|EXCEPCIONAL)\s*/\s*(BAIXO|MEDIO|ALTO)\b")

    # Formulários oficiais conhecidos (pacote SEMA): detecção pelo título/texto
    # para escolher o checklist oficial específico de config/checklists_oficiais.json
    FORMULARIOS_CONHECIDOS = {
        "sitios_de_lazer": re.compile(r"SITIO[S]?\s+DE\s+LAZER|AREA\s+DE\s+LAZER"),
        "exploracao_eventual_arvores_nativas": re.compile(
            r"EXPLORACAO\s+EVENTUAL\s+DE\s+ARVORES\s+NATIVAS"),
        "arvores_imunes_ao_corte": re.compile(r"IMUNES\s+AO\s+CORTE"),
        "baixo_impacto_app": re.compile(
            r"BAIXO\s+IMPACTO\s+EM\s+AREA\s+DE\s+PRESERVACAO|BAIXO\s+IMPACTO\s+EM\s+APP"),
        "manejo_estagio_medio_2ha": re.compile(r"ESTAGIO\s+MEDIO\s+DE\s+REGENERACAO"),
        "abertura_acude": re.compile(r"ABERTURA\s+DE\s+ACUDE"),
    }

    def __init__(self,
                 caminho_arquivo: Optional[str] = None,
                 conteudo_html: Optional[str] = None,
                 limiar_similaridade: float = 0.88,
                 caminho_config: Optional[str] = None,
                 rotulos_extras: Optional[dict[str, list[str]]] = None,
                 checklist_oficial: Optional[dict[str, list[str]]] = None):
        """Inicializa o parser com o conteúdo HTML (arquivo em disco ou string).

        Args:
            caminho_arquivo: caminho do .htm/.html do formulário.
            conteudo_html: conteúdo HTML já carregado em memória (alternativa).
            limiar_similaridade: razão mínima (0-1) de similaridade de strings para
                considerar dois documentos como duplicados na desduplicação.
            caminho_config: pasta de calibração (padrão: 'config'). Se existirem
                `rotulos_formulario.json` e `checklists_oficiais.json` (gerados pela
                ingestão dos formulários oficiais), eles são carregados daí.
            rotulos_extras: rótulos adicionais por campo (mesclados com os padrões).
            checklist_oficial: {fase: [documentos]} oficial do órgão, usado como
                fonte de fallback quando o HTML não traz o checklist.
        """
        if not caminho_arquivo and not conteudo_html:
            raise ValueError("Informe 'caminho_arquivo' ou 'conteudo_html'.")

        self.caminho_arquivo = caminho_arquivo
        self.limiar_similaridade = limiar_similaridade
        self.log_erros: list[str] = []          # registro de falhas de extração
        self.soup: Optional[BeautifulSoup] = None
        self._mapa_rotulos: dict[str, str] = {} # mapa DOM: rótulo normalizado -> valor
        self._texto_original: str = ""
        self._texto_norm: str = ""
        self.dados: dict[str, Any] = {}         # resultado consolidado

        self.checklist_oficial: dict[str, list[str]] = dict(checklist_oficial or {})
        self.checklists_por_formulario: dict[str, dict] = {}
        self._fonte_checklist_oficial = "checklist oficial SEMA"
        self.fonte_checklist = "formulário HTML"
        # cópia por instância: a mescla de rótulos extras não contamina a classe
        self.ROTULOS = {campo: list(lista) for campo, lista in self.ROTULOS.items()}
        if caminho_config or Path(PASTA_CONFIG_PADRAO).exists():
            calibracao = Calibracao(caminho_config)
            if calibracao.checklists_oficiais:
                listas = calibracao.checklists_oficiais.get("documentos_por_fase", {})
                if isinstance(listas, dict) and listas:
                    self.checklist_oficial = {k: list(v) for k, v in listas.items()}
                    self._fonte_checklist_oficial = calibracao.checklists_oficiais.get(
                        "fonte", "checklist oficial")
                por_form = calibracao.checklists_oficiais.get("checklists_por_formulario", {})
                if isinstance(por_form, dict):
                    self.checklists_por_formulario = por_form
            if calibracao.rotulos_formulario:
                extras = calibracao.rotulos_formulario.get("rotulos", {})
                for campo, lista in extras.items():
                    if campo in self.ROTULOS and isinstance(lista, list):
                        self.ROTULOS[campo] = self.ROTULOS[campo] + [r for r in lista
                                                                     if r not in self.ROTULOS[campo]]
        if rotulos_extras:
            for campo, lista in rotulos_extras.items():
                if campo in self.ROTULOS and isinstance(lista, list):
                    self.ROTULOS[campo] = self.ROTULOS[campo] + [r for r in lista
                                                                 if r not in self.ROTULOS[campo]]

        self._carregar(conteudo_html, caminho_arquivo)

    # ------------------------------------------------------------------
    # Carregamento e indexação do HTML
    # ------------------------------------------------------------------
    def _carregar(self, conteudo_html: Optional[str], caminho_arquivo: Optional[str]) -> None:
        """Carrega o HTML e prepara as estruturas de busca (texto + mapa de rótulos)."""
        try:
            if caminho_arquivo:
                conteudo = Path(caminho_arquivo).read_text(encoding="utf-8", errors="replace")
            else:
                conteudo = conteudo_html or ""
            # 'html.parser' evita dependência obrigatória do lxml (usado se disponível)
            self.soup = BeautifulSoup(conteudo, "html.parser")
        except Exception as exc:  # noqa: BLE001 - falha de leitura não deve travar o sistema
            logger.exception("Falha ao carregar o HTML: %s", exc)
            self.log_erros.append(f"CARREGAMENTO: {exc}")
            self.soup = BeautifulSoup("", "html.parser")

        # Texto plano completo (linhas não vazias) para as buscas por RegEx
        self._texto_original = self.soup.get_text("\n").replace("\xa0", " ")
        self._texto_norm = normalizar(self._texto_original)

        # Mapa de rótulos via DOM: percorre linhas de tabelas (tr) e pares dt/dd,
        # que são o formato típico dos formulários do órgão ambiental.
        try:
            for tr in self.soup.find_all("tr"):
                celulas = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
                celulas = [c for c in celulas if c]
                if len(celulas) >= 2:
                    rotulo, valor = normalizar(celulas[0]), " - ".join(celulas[1:])
                    self._mapa_rotulos.setdefault(rotulo, valor)
            for dt in self.soup.find_all("dt"):
                dd = dt.find_next_sibling("dd")
                if dd:
                    self._mapa_rotulos.setdefault(normalizar(dt.get_text(" ", strip=True)),
                                                  dd.get_text(" ", strip=True))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Falha ao indexar o DOM: %s", exc)
            self.log_erros.append(f"INDEXACAO_DOM: {exc}")

    # ------------------------------------------------------------------
    # Helpers de busca (híbrido: DOM + RegEx)
    # ------------------------------------------------------------------
    def _buscar_valor(self, chaves: list[str],
                      padrao_regex: Optional[re.Pattern] = None) -> Optional[str]:
        """Busca um valor por rótulo (DOM -> texto) e, opcionalmente, por RegEx."""
        # 1) Busca exata no mapa de rótulos do DOM
        for chave in chaves:
            chave_norm = normalizar(chave)
            if chave_norm in self._mapa_rotulos:
                valor = self._mapa_rotulos[chave_norm].strip()
                if valor:
                    return valor
        # 2) Busca por prefixo no mapa (rótulos compostos, ex.: 'PORTE DO EMPREENDIMENTO')
        for chave in chaves:
            chave_norm = normalizar(chave)
            for rotulo, valor in self._mapa_rotulos.items():
                if rotulo.startswith(chave_norm) and valor.strip():
                    return valor.strip()
        # 3) Busca no texto plano: 'ROTULO: valor' (linha única ou célula contínua)
        for chave in chaves:
            chave_norm = re.escape(normalizar(chave))
            padrao = re.compile(chave_norm + r"\s*[:\-]\s*([^\n\r]{1,200})", re.I)
            achou = padrao.search(self._texto_original)
            if achou:
                return achou.group(1).strip()
        # 4) Busca por padrão RegEx específico (último recurso)
        if padrao_regex is not None:
            achou = padrao_regex.search(self._texto_original)
            if achou:
                return achou.group(1).strip()
        return None

    def _registrar_falha(self, metodo: str, detalhe: str) -> None:
        """Registra a falha de extração no log e na lista de avisos do parser."""
        logger.warning("[%s] Campo não encontrado/inválido: %s", metodo, detalhe)
        self.log_erros.append(f"{metodo}: {detalhe}")

    # ------------------------------------------------------------------
    # Bloco 1 - Dados do Empreendedor e do Empreendimento
    # ------------------------------------------------------------------
    def extrair_dados_empreendedor(self) -> dict[str, Any]:
        """Extrai Nome/Razão Social e CPF/CNPJ do empreendedor."""
        resultado: dict[str, Any] = {"nome_razao_social": None, "nome_fantasia": None,
                                     "cpf_cnpj": None}
        try:
            resultado["nome_razao_social"] = self._buscar_valor(self.ROTULOS["nome_razao_social"])
            resultado["nome_fantasia"] = self._buscar_valor(self.ROTULOS["nome_fantasia"])
            if not resultado["nome_razao_social"]:
                self._registrar_falha("extrair_dados_empreendedor", "nome/razão social ausente")

            bruto = self._buscar_valor(self.ROTULOS["cpf_cnpj"], self.REGEX["cpf_cnpj"])
            if bruto:
                achou = self.REGEX["cpf_cnpj"].search(bruto)
                # preserva a formatação encontrada (com pontuação)
                resultado["cpf_cnpj"] = achou.group(1) if achou else bruto.strip()
            else:
                achou = self.REGEX["cpf_cnpj"].search(self._texto_original)
                if achou:
                    resultado["cpf_cnpj"] = achou.group(1)
            if not resultado["cpf_cnpj"]:
                self._registrar_falha("extrair_dados_empreendedor", "CPF/CNPJ ausente")
        except Exception as exc:  # noqa: BLE001
            self._registrar_falha("extrair_dados_empreendedor", str(exc))
        return resultado

    @staticmethod
    def _coordenada_de_valor(valor_bruto: Optional[str]) -> Optional[float]:
        """Converte o valor de uma célula de coordenada (decimal ou GMS) em float.

        Ex.: '-29.6745123' -> -29.6745123 | '29º 40' 12,34" S' -> -29.670094
        Retorna None quando não há valor numérico válido.
        """
        if not valor_bruto:
            return None
        m = FormularioParser.REGEX["latitude_gms"].search(valor_bruto) or \
            FormularioParser.REGEX["longitude_gms"].search(valor_bruto)
        if m:
            return graus_para_decimal(
                float(str(m.group(1)).replace(",", ".")),
                float(str(m.group(2)).replace(",", ".")),
                float(str(m.group(3)).replace(",", ".")),
                m.group(4) or "S")
        m = re.search(r"(-?\d{1,3}[.,]\d{2,8})", valor_bruto)
        if not m:
            return None
        numero = m.group(1)
        if "," in numero:  # vírgula decimal brasileira
            numero = numero.replace(".", "").replace(",", ".")
        valor = float(numero)
        if -180.0 <= valor <= 180.0:
            return round(valor, 7)
        return None

    def extrair_coordenadas(self) -> dict[str, Any]:
        """Extrai e LIMPA as coordenadas geográficas para o padrão SIRGAS 2000.

        Aceita UTM (E/N/Fuso) e Latitude/Longitude (graus decimais ou GMS).
        Os valores são convertidos para float com separador decimal '.' e
        validados contra as faixas plausíveis de cada sistema.
        """
        saida: dict[str, Any] = {
            "formato": None, "datum": "SIRGAS 2000",
            "easting_m": None, "northing_m": None, "fuso": None,
            "latitude": None, "longitude": None,
            "valores_brutos": None,
        }
        try:
            bruto = self._buscar_valor(self.ROTULOS["coordenadas_utm"])
            if bruto:
                saida["valores_brutos"] = bruto

            # ---- UTM -----------------------------------------------------
            texto_busca = bruto or self._texto_original
            def _arredondar(valor: Any, casas: int):
                """round resiliente: valor não numérico vira None (sem exceção)."""
                return round(valor, casas) if isinstance(valor, (int, float)) else None

            e = self.REGEX["utm_e"].search(texto_busca)
            n = self.REGEX["utm_n"].search(texto_busca)
            valor_e, valor_n = para_float(e.group(1)) if e else None, para_float(n.group(1)) if n else None
            if valor_e is None or valor_n is None:
                # tenta pelos rótulos de célula ('E (m)' / 'N (m)')
                ve = self._buscar_valor([c for c in self.ROTULOS["coordenada_e"]])
                vn = self._buscar_valor([c for c in self.ROTULOS["coordenada_n"]])
                if valor_e is None and ve:
                    valor_e = para_float(re.sub(r"[^\d.,\-]", "", ve))
                if valor_n is None and vn:
                    valor_n = para_float(re.sub(r"[^\d.,\-]", "", vn))
            if valor_e is not None and valor_n is not None and 16000 < valor_e < 900000 and 0 < valor_n < 10000000:
                fuso = self.REGEX["fuso"].search(texto_busca)
                saida.update({
                    "formato": "UTM",
                    "easting_m": _arredondar(valor_e, 2),
                    "northing_m": _arredondar(valor_n, 2),
                    "fuso": int(fuso.group(1)) if fuso else 22,  # RS = fuso 22S
                    "hemisferio": "S",
                })

            # ---- Latitude / Longitude ------------------------------------
            # 1) rótulos próprios (linhas 'Latitude'/'Longitude' separadas);
            # 2) texto corrido (formato decimal ou GMS).
            saida["latitude"] = self._coordenada_de_valor(
                self._buscar_valor(self.ROTULOS["latitude"]))
            saida["longitude"] = self._coordenada_de_valor(
                self._buscar_valor(self.ROTULOS["longitude"]))
            if saida["latitude"] is None or saida["longitude"] is None:
                lat_dec = self.REGEX["latitude_grau_decimal"].search(texto_busca)
                lon_dec = self.REGEX["longitude_grau_decimal"].search(texto_busca)
                if lat_dec and lon_dec:
                    saida["latitude"] = _arredondar(para_float(lat_dec.group(1)), 7)
                    saida["longitude"] = _arredondar(para_float(lon_dec.group(1)), 7)
                else:
                    lat_gms = self.REGEX["latitude_gms"].search(texto_busca)
                    lon_gms = self.REGEX["longitude_gms"].search(texto_busca)
                    if lat_gms and lon_gms:
                        saida["latitude"] = graus_para_decimal(
                            float(str(lat_gms.group(1)).replace(",", ".")),
                            float(str(lat_gms.group(2)).replace(",", ".")),
                            float(str(lat_gms.group(3)).replace(",", ".")),
                            lat_gms.group(4) or "S")
                        saida["longitude"] = graus_para_decimal(
                            float(str(lon_gms.group(1)).replace(",", ".")),
                            float(str(lon_gms.group(2)).replace(",", ".")),
                            float(str(lon_gms.group(3)).replace(",", ".")),
                            lon_gms.group(4) or "W")
            if saida["latitude"] is not None:
                saida["latitude"] = _arredondar(saida["latitude"], 7)   # limpeza SIRGAS 2000
                saida["longitude"] = _arredondar(saida["longitude"], 7)
            if saida["formato"] is None and saida["latitude"] is not None:
                saida["formato"] = "GEOGRAFICA"
            if saida["formato"] is None:
                self._registrar_falha("extrair_coordenadas", "coordenadas não localizadas")
        except Exception as exc:  # noqa: BLE001
            self._registrar_falha("extrair_coordenadas", str(exc))
        return saida

    def _extrair_area(self, chaves: list[str]) -> Optional[float]:
        """Extrai um valor de área em hectares, convertendo m² quando necessário."""
        bruto = self._buscar_valor(chaves)
        if not bruto:
            return None
        achou = self.REGEX["area"].search(bruto)
        if not achou:
            return None
        valor = para_float(achou.group(1))
        unidade = (achou.group(2) or "ha").lower()
        if valor is not None and unidade in ("m2", "m²", "m\xb2"):
            valor = round(valor / 10000.0, 4)  # converte m² -> ha
        return valor

    def extrair_dados_empreendimento(self) -> dict[str, Any]:
        """Extrai os dados da seção 'IDENTIFICAÇÃO DO EMPREENDIMENTO'."""
        resultado: dict[str, Any] = {
            "nome_empreendimento": None, "ramo_atividade": None, "codram": None,
            "porte": None, "potencial_poluidor": None,
            "area_total_ha": None, "area_util_ha": None, "area_intervencao_ha": None,
            "coordenadas": {}, "matricula_imovel": None, "endereco": None, "municipio": None,
        }
        try:
            resultado["nome_empreendimento"] = self._buscar_valor(self.ROTULOS["nome_empreendimento"])
            resultado["ramo_atividade"] = self._buscar_valor(self.ROTULOS["ramo_atividade"])
            codram = self._buscar_valor(self.ROTULOS["codram"], self.REGEX["codram"])
            if codram:
                achou = self.REGEX["codram"].search(codram)
                resultado["codram"] = (achou.group(1).strip(" .-") if achou else codram.strip(" .-"))

            # Porte (Mínimo, Pequeno, Médio, Grande, Excepcional)
            # Formulários oficiais usam campo COMBINADO 'Porte/Potencial
            # Poluidor: Pequeno/Baixo' -> divide nos dois campos
            bruto_porte = self._buscar_valor(self.ROTULOS["porte"])
            if bruto_porte:
                combinado = self.RE_PORTE_POTENCIAL.search(normalizar(bruto_porte))
                if combinado:
                    resultado["porte"] = self.ROTULO_AMIGAVEL[combinado.group(1)]
                    resultado["potencial_poluidor"] = self.ROTULO_AMIGAVEL[combinado.group(2)]
                    bruto_porte = None
            if bruto_porte:
                porte_norm = normalizar(bruto_porte)
                for porte in self.PORTES_VALIDOS:
                    if porte in porte_norm:
                        resultado["porte"] = self.ROTULO_AMIGAVEL[porte]
                        break
            if resultado["porte"] is None:
                achou = re.search(r"\b(MINIMO|PEQUENO|MEDIO|GRANDE|EXCEPCIONAL)\b", self._texto_norm)
                if achou:
                    resultado["porte"] = self.ROTULO_AMIGAVEL[achou.group(1)]
            if resultado["porte"] is None:
                self._registrar_falha("extrair_dados_empreendimento", "porte ausente")

            # Potencial Poluidor (Baixo, Médio, Alto)
            bruto_potencial = self._buscar_valor(self.ROTULOS["potencial_poluidor"])
            if bruto_potencial and resultado["potencial_poluidor"] is None:
                combinado = self.RE_PORTE_POTENCIAL.search(normalizar(bruto_potencial))
                if combinado:
                    resultado["potencial_poluidor"] = self.ROTULO_AMIGAVEL[combinado.group(2)]
                    if resultado["porte"] is None:
                        resultado["porte"] = self.ROTULO_AMIGAVEL[combinado.group(1)]
                    bruto_potencial = None
            if bruto_potencial and resultado["potencial_poluidor"] is None:
                potencial_norm = normalizar(bruto_potencial)
                for potencial in self.POTENCIAIS_VALIDOS:
                    if potencial in potencial_norm:
                        resultado["potencial_poluidor"] = self.ROTULO_AMIGAVEL[potencial]
                        break
            if resultado["potencial_poluidor"] is None:
                achou = re.search(r"\b(BAIXO|MEDIO|ALTO)\b", self._texto_norm)
                if achou:
                    resultado["potencial_poluidor"] = self.ROTULO_AMIGAVEL[achou.group(1)]
            if resultado["potencial_poluidor"] is None:
                self._registrar_falha("extrair_dados_empreendimento", "potencial poluidor ausente")

            resultado["area_total_ha"] = self._extrair_area(self.ROTULOS["area_total"])
            resultado["area_util_ha"] = self._extrair_area(self.ROTULOS["area_util"])
            resultado["area_intervencao_ha"] = resultado["area_util_ha"]

            matricula = self._buscar_valor(self.ROTULOS["matricula_imovel"], self.REGEX["matricula"])
            if matricula:
                achou = self.REGEX["matricula"].search(matricula)
                resultado["matricula_imovel"] = achou.group(1).strip(" .-") if achou else matricula.strip(" .-")

            resultado["endereco"] = self._buscar_valor(self.ROTULOS["endereco_empreendimento"])
            resultado["municipio"] = self._buscar_valor(self.ROTULOS["municipio"]) or "Campo Bom"
            resultado["coordenadas"] = self.extrair_coordenadas()

            if resultado["area_total_ha"] is None:
                # Fallback: procura 'ÁREA (TOTAL)? DO/DA ... <n> ha|m²' no texto
                # (formulários de parcelamento costumam variar o rótulo)
                achou = re.search(
                    r"AREA\s*(?:TOTAL\s*)?(?:DO|DA|DE)?\s*"
                    r"(?:EMPREENDIMENTO|PARCELAMENTO|LOTEAMENTO|PROPRIEDADE|"
                    r"IMOVEL|TERRENO)?[^0-9\n]{0,40}"
                    r"(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?)\s*"
                    r"(HA|HECTARES?|M\s*[²2])\b",
                    self._texto_norm)
                if achou:
                    valor = para_float(achou.group(1))
                    unidade = achou.group(2).upper()
                    if valor is not None:
                        if unidade.startswith("M"):
                            valor = round(valor / 10000.0, 4)
                        resultado["area_total_ha"] = valor
                        self._registrar_falha(
                            "extrair_dados_empreendimento",
                            f"área total lida do texto por padrão geral "
                            f"({achou.group(0).strip()})")
            if resultado["area_total_ha"] is None:
                self._registrar_falha("extrair_dados_empreendimento", "área total ausente")
        except Exception as exc:  # noqa: BLE001
            self._registrar_falha("extrair_dados_empreendimento", str(exc))
        return resultado

    # ------------------------------------------------------------------
    # Bloco 2 - Responsável Técnico e ART (hard constraint)
    # ------------------------------------------------------------------
    def extrair_responsavel_tecnico(self) -> dict[str, Any]:
        """Extrai nome e número da ART do responsável técnico.

        Regra de negócio: ART ausente/vazia => status_triagem = 'bloqueado_sem_art'.
        """
        resultado: dict[str, Any] = {"nome": None, "registro_art": None, "registro_crea": None, "empresa": None}
        try:
            resultado["nome"] = self._buscar_valor(self.ROTULOS["nome_responsavel_tecnico"])
            if resultado["nome"]:
                # remove possíveis quebras de célula ('Nome - CREA 123' etc.)
                resultado["nome"] = re.split(r"\s*[-–|]\s*", resultado["nome"])[0].strip()

            art_bruto = self._buscar_valor(self.ROTULOS["registro_art"], self.REGEX["art"])
            if art_bruto:
                achou = self.REGEX["art"].search(art_bruto)
                resultado["registro_art"] = achou.group(1).strip(" .-") if achou else art_bruto.strip(" .-")
            if not resultado["registro_art"]:
                # varredura direta no texto: padrões 'ART nº 123456' / 'ART: AB-2026/123'
                achou = self.REGEX["art"].search(self._texto_original)
                if achou:
                    resultado["registro_art"] = achou.group(1).strip(" .-")
            if not resultado["registro_art"]:
                self._registrar_falha("extrair_responsavel_tecnico",
                                      "ART ausente => gatilho 'bloqueado_sem_art'")

            resultado["registro_crea"] = self._buscar_valor(self.ROTULOS["registro_crea"])
            if resultado["registro_crea"]:
                resultado["registro_crea"] = re.split(r"\s*[-–|]\s*", resultado["registro_crea"])[0].strip()
        except Exception as exc:  # noqa: BLE001
            self._registrar_falha("extrair_responsavel_tecnico", str(exc))
        return resultado

    # ------------------------------------------------------------------
    # Bloco 3 - Identificação do Pleito (tipo de licença)
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    def _coletar_secao_da_ancora(self, ancora) -> list:
        """Devolve os elementos que compõem a seção iniciada pela âncora
        (elementos seguintes até o próximo heading de nível igual/superior)."""
        nivel_ancora = None
        elemento = ancora
        # sobe até o heading que inicia a seção (ex.: <strong> dentro de <p> ou <h3>)
        while elemento is not None:
            if getattr(elemento, "name", None) in ("h1", "h2", "h3", "h4", "h5"):
                nivel_ancora = int(elemento.name[1])
                break
            elemento = elemento.parent
        if elemento is None:
            elemento = ancora
        secao: list = []
        for irmao in elemento.find_next_siblings():
            nome = getattr(irmao, "name", None)
            if nome in ("h1", "h2", "h3", "h4", "h5"):
                if int(nome[1]) <= (nivel_ancora or 9):
                    break
            secao.append(irmao)
        return secao

    def _texto_da_marcacao(self, input_marcado) -> Optional[str]:
        """Recupera o rótulo associado a um checkbox/radio marcado no DOM."""
        try:
            # 1) <label for="id_do_input">
            input_id = input_marcado.get("id")
            if input_id:
                label = self.soup.find("label", attrs={"for": input_id})
                if label and label.get_text(" ", strip=True):
                    return label.get_text(" ", strip=True)
            # 2) <label> ... <input> ... </label> (input dentro do label)
            pai_label = input_marcado.find_parent("label")
            if pai_label and pai_label.get_text(" ", strip=True):
                return pai_label.get_text(" ", strip=True)
            # 3) célula/linha/pai textual (tabelas de formulário municipal)
            for ancestral in input_marcado.parents:
                nome = getattr(ancestral, "name", None)
                if nome in ("td", "li", "p", "div"):
                    texto = ancestral.get_text(" ", strip=True)
                    if texto:
                        return texto[:200]
        except Exception as exc:  # noqa: BLE001
            self._registrar_falha("_texto_da_marcacao", str(exc))
        return None

    def _detectar_pleito_tabela_natureza(self) -> Optional[dict[str, Any]]:
        """Tabela oficial do MOTIVO em DUAS COLUNAS: a 1ª linha traz os checkboxes
        '[ ] Primeira licença' (coluna 1) e '[ ] Renovação' (coluna 2) e, logo
        abaixo de cada um, os tipos de licença marcados com círculo '( o )'.

        Algoritmo pedido pelo licenciador: ler a PRIMEIRA LINHA para saber em
        qual das duas colunas a marcação está (Primeira licença x Renovação) e
        só depois ler o TIPO de licença marcado nessa coluna. Regras:
          - cabeçalho marcado define a coluna ativa e a natureza do pleito;
          - sem cabeçalho marcado, a coluna ativa é a única que contém marca
            nos tipos (natureza inferida da posição); duas colunas marcadas
            com cabeçalhos vazios = ambíguo (None + conferência manual).
        Retorna {'tipo_licenca', 'descricao_pleito', 'metodo', 'natureza'} ou None.
        """
        try:
            if not self.soup:
                return None
            for tabela in self.soup.find_all("table"):
                texto_tabela = normalizar(tabela.get_text(" "))
                if not (self.REGEX_PRIMEIRA_LICENCA.search(texto_tabela)
                        and self.REGEX_RENOVACAO.search(texto_tabela)):
                    continue  # não é a tabela do motivo em 2 colunas
                linhas = tabela.find_all("tr")
                if len(linhas) < 2:
                    continue
                celulas_cab = linhas[0].find_all(["td", "th"])
                col_primeira = col_renov = None
                coluna_ativa: Optional[int] = None
                natureza: Optional[str] = None
                for i, celula in enumerate(celulas_cab):
                    t = normalizar(celula.get_text(" ", strip=True))
                    if self.REGEX_PRIMEIRA_LICENCA.search(t):
                        col_primeira = i
                        if self._marca_em_celula(celula):
                            coluna_ativa, natureza = i, "Primeira licença"
                    elif self.REGEX_RENOVACAO.search(t):
                        col_renov = i
                        if self._marca_em_celula(celula):
                            coluna_ativa, natureza = i, "Renovação"
                # ---- tipos marcados por coluna (para inferência/ambiguidade) --
                marcadas: dict[int, tuple[int, Any]] = {}
                for ri, linha_tr in enumerate(linhas[1:], start=1):
                    celulas = linha_tr.find_all(["td", "th"])
                    for ci, celula in enumerate(celulas):
                        if self._marca_em_celula(celula) and self._pleito_no_texto(
                                celula.get_text(" ", strip=True)):
                            marcadas.setdefault(ci, (ri, celula))
                if coluna_ativa is None:
                    ativas = sorted(marcadas)
                    if len(ativas) == 1:
                        coluna_ativa = ativas[0]
                        natureza = ("Primeira licença"
                                    if coluna_ativa == col_primeira else "Renovação")
                    elif len(ativas) > 1:
                        self._registrar_falha(
                            "_detectar_pleito_tabela_natureza",
                            "tabela Primeira licença/Renovação com marcações nas DUAS "
                            "colunas e cabeçalhos sem marca - conferir manualmente")
                        return None
                if coluna_ativa is None or coluna_ativa not in marcadas:
                    continue  # nada marcado nesta tabela; tenta as demais camadas
                _, celula_tipo = marcadas[coluna_ativa]
                descricao = celula_tipo.get_text(" ", strip=True)[:160]
                for sigla, padrao in self.PLEITOS:
                    if padrao.search(normalizar(descricao)):
                        return {"tipo_licenca": sigla,
                                "descricao_pleito": descricao,
                                "metodo": "marcação no formulário (tabela "
                                          "Primeira licença/Renovação)",
                                "natureza": natureza}
        except Exception as exc:  # noqa: BLE001
            self._registrar_falha("_detectar_pleito_tabela_natureza", str(exc))
        return None

    def _marca_em_celula(self, celula) -> bool:
        """Há marca de seleção na célula? (input checked, texto '( X )'/'( o )',
        glyph cheio, ou célula que É a marca isolada)."""
        try:
            for entrada in celula.find_all("input",
                                           attrs={"type": ["checkbox", "radio"]}):
                if entrada.has_attr("checked"):
                    return True
            texto = celula.get_text(" ", strip=True)
            if not texto:
                return False
            if texto.upper().strip("()[] ") in self.CELULAS_MARCA:
                return True
            return bool(self.REGEX_MARCADOR_MARCADO.search(texto))
        except Exception:  # noqa: BLE001
            return False

    def _pleito_no_texto(self, texto: str) -> Optional[str]:
        """Sigla do pleito citado no texto da célula (LP/LI/LO/LIR/LOR...)."""
        try:
            for sigla, padrao in self.PLEITOS:
                if padrao.search(normalizar(texto)):
                    return sigla
        except Exception:  # noqa: BLE001
            return None
        return None

    def _linhas_secao_pleito(self) -> Optional[list[str]]:
        """Linhas do TEXTO PLANO entre o título da seção do pleito (MOTIVO DO
        ENCAMINHAMENTO À SEMA e variantes) e o próximo cabeçalho/seção.

        Retorna None quando o formulário NÃO possui tal seção - sinal usado
        para decidir se o fallback pode varrer o texto inteiro.
        """
        linhas = self._texto_original.splitlines()
        for i, linha in enumerate(linhas):
            if self.REGEX_SECAO_PLEITO.search(normalizar(linha)):
                secao: list[str] = []
                for proxima in linhas[i + 1:]:
                    n = normalizar(proxima)
                    if self.REGEX_SECAO_DOCS.search(n):
                        break  # começou a listagem de documentação
                    if re.match(r"^\s*\d+\s*[.)]\s+\S", proxima.strip()) \
                            and not self.REGEX_SECAO_PLEITO.search(n):
                        break  # próximo cabeçalho numerado (ex.: '4. RESPONSÁVEL...')
                    secao.append(proxima)
                return secao
        return None

    def _linhas_opcoes_pleito(self) -> list[str]:
        """Linhas da seção do motivo com os SÍMBOLOS DE MARCAÇÃO reattachados
        ao rótulo: a extração de texto do HTML costuma quebrar '( o )' / 'þ'
        (span isolado do Word) numa linha e o nome da licença na seguinte."""
        saida: list[str] = []
        pendente = ""
        for linha in (self._linhas_secao_pleito() or []):
            limpa = linha.strip()
            if not limpa:
                continue
            if self.RE_SIMBOLO_SOZINHO.match(limpa):
                pendente = f"{pendente} {limpa}".strip()
                continue
            saida.append(f"{pendente} {limpa}".strip() if pendente
                         else linha.rstrip())
            pendente = ""
        if pendente:
            saida.append(pendente)
        return saida

    def _linhas_da_secao_em_texto_sintetico(self) -> list[str]:
        """OUTRO MEIO de leitura (independente do get_text): reconstrói o texto
        da seção do motivo a partir do HTML BRUTO, mapeando cada controle de
        formulário para o seu símbolo (marcado -> '☒', vazio -> '○') e
        descartando as demais tags. Funciona mesmo quando a extração de texto
        separa o símbolo do rótulo ou reordena células/spans do Word."""
        saida: list[str] = []
        try:
            if not self.soup:
                return saida
            ancora = None
            for tag in self.soup.find_all(["h1", "h2", "h3", "h4", "h5", "strong",
                                           "b", "p", "legend", "td", "th"]):
                texto = tag.get_text(" ", strip=True)
                if texto and self.REGEX_SECAO_PLEITO.search(normalizar(texto)):
                    ancora = tag
                    break
            for contêiner in (self._coletar_secao_da_ancora(ancora) if ancora else []):
                bruto = str(contêiner)
                # controles de formulário viram símbolos NO LUGAR correto
                bruto = re.sub(
                    r"<input[^>]*type=[\"']?(?:checkbox|radio)[\"']?[^>]*>",
                    lambda m: "☒" if re.search(r"\bchecked\b", m.group(0), re.I)
                    else "○",
                    bruto, flags=re.I)
                bruto = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ",
                               bruto, flags=re.S | re.I)
                bruto = re.sub(r"<[^>]+>", "\n", bruto)
                bruto = html_mod.unescape(bruto)
                for linha in bruto.splitlines():
                    linha = re.sub(r"\s+", " ", linha).strip()
                    if linha:
                        saida.append(linha)
        except Exception as exc:  # noqa: BLE001
            self._registrar_falha("_linhas_da_secao_em_texto_sintetico", str(exc))
            return saida
        # reattach: símbolo isolado ('○', '☒', '( X )'...) cola no rótulo seguinte
        mescladas: list[str] = []
        pendente = ""
        for linha in saida:
            if self.RE_SIMBOLO_SOZINHO.match(linha) or linha == "○":
                pendente = f"{pendente} {linha}".strip()
                continue
            mescladas.append(f"{pendente} {linha}".strip() if pendente else linha)
            pendente = ""
        if pendente:
            mescladas.append(pendente)
        return mescladas

    def _detectar_pleito_por_marcacao(self) -> Optional[dict[str, Any]]:
        """Lê qual opção está MARCADA na seção do MOTIVO DO ENCAMINHAMENTO.

        Estratégia em camadas (todas COLETAM candidatos; a decisão é central):
          0) tabela oficial 2 colunas 'Primeira licença' x 'Renovação';
          a) checkbox/radio com `checked` no DOM;
          b) célula isolada com o símbolo da marca ('X', '( o )', 'þ', '●'...);
          c) marcador textual junto ao rótulo ('(X) Licença Prévia', 'þ LOR');
          d) DIFERENÇA DE SÍMBOLOS: entre as linhas de opções, o símbolo
             MINORITÁRIO é a marca (o formulário marca só uma opção) - cobre
             qualquer esquema de símbolos, inclusive Wingdings do Word.

        NUNCA infere o tipo pela presença do rótulo: linha sem marca não é
        candidata. Várias opções com a mesma marca = ambíguo (None + aviso).
        """
        try:
            candidatos: list[dict[str, Any]] = []

            # ---- (0) tabela oficial Primeira licença x Renovação ---------
            tabela_natureza = self._detectar_pleito_tabela_natureza()
            if tabela_natureza:
                return tabela_natureza

            # ---- (a) DOM: checkbox/radio MARCADO -------------------------
            if self.soup:
                ancora = None
                for tag in self.soup.find_all(["h1", "h2", "h3", "h4", "h5",
                                               "strong", "b", "p", "legend",
                                               "td", "th"]):
                    texto = tag.get_text(" ", strip=True)
                    if texto and self.REGEX_SECAO_PLEITO.search(normalizar(texto)):
                        ancora = tag
                        break
                secao_dom = self._coletar_secao_da_ancora(ancora) if ancora else []
                for contêiner in secao_dom:
                    for entrada in contêiner.find_all(
                            "input", attrs={"type": ["checkbox", "radio"]}):
                        if entrada.has_attr("checked"):
                            texto = self._texto_da_marcacao(entrada)
                            if texto:
                                sigla = self._pleito_no_texto(texto)
                                if sigla:
                                    candidatos.append({
                                        "tipo_licenca": sigla,
                                        "descricao_pleito": texto.strip()[:160],
                                        "metodo": "marcação no formulário "
                                                  "(checkbox/radio)",
                                        "marca": "checked"})

                # ---- (b) DOM: célula isolada com o símbolo da marca ------
                for contêiner in secao_dom:
                    for linha_tr in contêiner.find_all(["tr", "li", "p"]):
                        marca_token = None
                        for celula in linha_tr.find_all(["td", "th"]):
                            txt = celula.get_text(" ", strip=True)
                            if txt and txt.upper().strip("()[] ") \
                                    in self.CELULAS_MARCA:
                                marca_token = txt.upper().strip()
                                break
                            # símbolo INLINE na própria célula do rótulo
                            # ('þ Licença Prévia', '( X ) Licença Única')
                            inline = (self.REGEX_MARCADOR_MARCADO
                                      .search(txt) if txt else None)
                            if inline and self._pleito_no_texto(txt):
                                marca_token = inline.group(0).upper().strip()
                                break
                        if marca_token is None:
                            continue  # linha sem marca identificável
                        texto_linha = linha_tr.get_text(" ", strip=True)
                        sigla = self._pleito_no_texto(texto_linha)
                        if sigla:
                            candidatos.append({
                                "tipo_licenca": sigla,
                                "descricao_pleito": texto_linha.strip()[:160],
                                "metodo": "marcação no formulário "
                                          "(célula com símbolo)",
                                "marca": marca_token})

            # ---- (c) TEXTO: marcadores afirmativos junto ao rótulo ------
            # (linhas com o símbolo REATTACHADO ao rótulo - spans do Word) +
            # OUTRO MEIO: linhas sintetizadas do HTML bruto (inputs mapeados)
            linhas_secao = self._linhas_opcoes_pleito()
            linhas_sinteticas = self._linhas_da_secao_em_texto_sintetico()
            for linha in linhas_secao + linhas_sinteticas:
                marcas = list(self.REGEX_MARCADOR_MARCADO.finditer(linha))
                for i, m in enumerate(marcas):
                    fim = marcas[i + 1].start() if i + 1 < len(marcas) else len(linha)
                    segmento = linha[m.end():fim]
                    sigla = self._pleito_no_texto(segmento)
                    if sigla:
                        candidatos.append({
                            "tipo_licenca": sigla,
                            "descricao_pleito": linha.strip()[:160],
                            "metodo": "marcação no formulário (texto)",
                            "marca": m.group(0).upper().strip()})

            # ---- (d) DIFERENÇA DE SÍMBOLOS entre as linhas de opções ----
            candidatos.extend(self._candidatos_por_diferenca(linhas_secao))
            if linhas_sinteticas:
                candidatos.extend(self._candidatos_por_diferenca(linhas_sinteticas))

            return self._decidir_candidatos(candidatos)
        except Exception as exc:  # noqa: BLE001
            self._registrar_falha("_detectar_pleito_por_marcacao", str(exc))
        return None

    def _candidatos_por_diferenca(self, linhas: list[str]) -> list[dict[str, Any]]:
        """Camada 'diferença de símbolos': coleta o PREFIXO de cada linha de
        opção (tudo antes do nome do pleito) e aponta a linha cujo símbolo é
        MINORITÁRIO - o formulário marca apenas uma opção, então o símbolo
        que difere da maioria é a marca (funciona com '( o )', 'þ', '●'...).
        Exige: >= 3 linhas de opções, exatamente 2 símbolos distintos e o
        minoritário aparecendo no máx. 2 vezes."""
        candidatos: list[dict[str, Any]] = []
        try:
            prefixos: dict[str, list[str]] = {}
            for linha in linhas:
                inicio_nome: Optional[int] = None
                for _, padrao in self.PLEITOS:
                    m = padrao.search(linha)
                    if m and (inicio_nome is None or m.start() < inicio_nome):
                        inicio_nome = m.start()
                if inicio_nome is None or inicio_nome == 0:
                    continue  # não é linha de opção (ou não tem prefixo)
                token = re.sub(r"\s+", "", linha[:inicio_nome])
                if not token:
                    continue  # linha de opção sem símbolo algum
                prefixos.setdefault(token, []).append(linha)
            if len(prefixos) == 2 and sum(len(v) for v in prefixos.values()) >= 3:
                (tok_maj, linhas_maj), (tok_min, linhas_min) = sorted(
                    prefixos.items(), key=lambda kv: len(kv[1]))
                if len(linhas_min) <= 2 and len(linhas_min) < len(linhas_maj):
                    for linha in linhas_min:
                        sigla = self._pleito_no_texto(linha)
                        if sigla:
                            candidatos.append({
                                "tipo_licenca": sigla,
                                "descricao_pleito": linha.strip()[:160],
                                "metodo": "marcação no formulário "
                                          "(símbolo diferente das demais)",
                                "marca": tok_min.upper()})
        except Exception as exc:  # noqa: BLE001
            self._registrar_falha("_candidatos_por_diferenca", str(exc))
        return candidatos

    def _decidir_candidatos(self, candidatos: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        """Decisão central das camadas de marcação (dupla checagem).
        1 candidato -> aceito; vários com a MESMA sigla -> aceito; vários com
        siglas distintas -> o símbolo MINORITÁRIO vence (a marca é única);
        persistindo a ambiguidade -> None + aviso de conferência manual."""
        try:
            if not candidatos:
                return None
            # camadas distintas que concordam (mesma sigla+marca) votam 1x
            unicos: list[dict[str, Any]] = []
            vistos: set[tuple[str, str]] = set()
            for c in candidatos:
                chave = (c["tipo_licenca"], c["marca"])
                if chave not in vistos:
                    vistos.add(chave)
                    unicos.append(c)
            candidatos = unicos
            siglas = {c["tipo_licenca"] for c in candidatos}
            if len(candidatos) == 1 or len(siglas) == 1:
                return candidatos[0]
            contagem: dict[str, int] = {}
            for c in candidatos:
                contagem[c["marca"]] = contagem.get(c["marca"], 0) + 1
            if len(contagem) == 2:
                ordenados = sorted(contagem.items(), key=lambda kv: -kv[1])
                (tok_maj, n_maj), (tok_min, n_min) = ordenados
                if n_min < n_maj and n_min <= 2 and tok_min.strip("()[] "):
                    minoritarios = [c for c in candidatos if c["marca"] == tok_min]
                    if len(minoritarios) == 1:
                        return minoritarios[0]
            self._registrar_falha(
                "extrair_tipo_licenca",
                "mais de uma opção da seção do motivo do encaminhamento "
                "apresenta marca - conferir o tipo de licença manualmente")
            return None
        except Exception as exc:  # noqa: BLE001
            self._registrar_falha("_decidir_candidatos", str(exc))
            return None

    def _natureza_da_marcacao(self) -> Optional[str]:
        """Natureza do pleito ('Primeira licença'/'Renovação') lida da marcação
        dos cabeçalhos na seção do MOTIVO (formulários fora da tabela 2 colunas)."""
        try:
            for linha in (self._linhas_secao_pleito() or []):
                if not self.REGEX_MARCADOR_MARCADO.search(linha):
                    continue  # só linhas com marca identificam a natureza
                n = normalizar(linha)
                if self.REGEX_PRIMEIRA_LICENCA.search(n):
                    return "Primeira licença"
                if self.REGEX_RENOVACAO.search(n):
                    return "Renovação"
        except Exception as exc:  # noqa: BLE001
            self._registrar_falha("_natureza_da_marcacao", str(exc))
        return None

    def extrair_tipo_licenca(self) -> dict[str, Any]:
        """Identifica a espécie do pleito: LP, LI, LO, LIR, LOR, Licença Única, Alvará Florestal, Autorização ou Declaração.

        Prioridade: (0) MARCAÇÃO na seção MOTIVO DO ENCAMINHAMENTO À SEMA;
        (1) campo textual 'Tipo de Licença'; (2) 'Pleito:' no texto;
        (3) varredura geral (último recurso).
        """
        resultado: dict[str, Any] = {
            "tipo_licenca": None, "descricao_pleito": None, "fases_componentes": [],
            "natureza": None,
            "metodo_deteccao": None,
        }
        try:
            # 0) Prioridade MÁXIMA: marcação na seção de motivo do encaminhamento
            marcacao = self._detectar_pleito_por_marcacao()
            if marcacao:
                resultado["tipo_licenca"] = marcacao["tipo_licenca"]
                resultado["descricao_pleito"] = marcacao["descricao_pleito"]
                resultado["metodo_deteccao"] = marcacao["metodo"]
                resultado["natureza"] = marcacao.get("natureza")

            # Natureza do pleito (Primeira licença x Renovação) lida dos
            # cabeçalhos da seção, quando a camada 0 não a forneceu
            if resultado["tipo_licenca"] and resultado.get("natureza") is None:
                resultado["natureza"] = self._natureza_da_marcacao()

            # 1) Campo explícito do formulário (rótulo 'Tipo de Licença' / 'Espécie do Pleito')
            if resultado["tipo_licenca"] is None:
                descricao = self._buscar_valor(self.ROTULOS["tipo_licenca"])
                if descricao:
                    resultado["descricao_pleito"] = descricao.strip()
                    desc_norm = normalizar(descricao)
                    for sigla, padrao in self.PLEITOS:
                        if padrao.search(desc_norm):
                            resultado["tipo_licenca"] = sigla
                            resultado["metodo_deteccao"] = "campo 'Tipo de Licença'"
                            break

            # 2) Fallback: 'Pleito: ...' no texto plano
            if resultado["tipo_licenca"] is None:
                achou = re.search(r"PLEITO\s*[:\-]?\s*([^\n\r]{1,120})", self._texto_original, re.I)
                if achou:
                    desc_norm = normalizar(achou.group(1))
                    resultado["descricao_pleito"] = achou.group(1).strip()
                    for sigla, padrao in self.PLEITOS:
                        if padrao.search(desc_norm):
                            resultado["tipo_licenca"] = sigla
                            break

            # 3) Último fallback: varredura do texto completo (ordem de prioridade).
            # GUARDA CRÍTICA: se o formulário TEM seção de marcação do pleito,
            # a varredura geral é PROIBIDA - a lista de opções cita todos os
            # tipos e produziria um falso positivo (ex.: LOR para um LP marcado).
            # Transparência ao licenciador: guarda o TEXTO BRUTO lido na
            # seção do motivo (exibido no painel quando o tipo não fecha)
            if self._linhas_secao_pleito() is not None:
                resultado["leitura_bruta_secao"] = [
                    l.strip() for l in self._linhas_opcoes_pleito() if l.strip()][:14]

            if resultado["tipo_licenca"] is None:
                secao_pleito = self._linhas_secao_pleito() is not None
                if secao_pleito:
                    self._registrar_falha(
                        "extrair_tipo_licenca",
                        "seção do motivo do encaminhamento encontrada, mas nenhuma "
                        "opção MARCADA identificada - conferir o tipo de licença "
                        "manualmente (não foi possível ler a marcação)")
                else:
                    for sigla, padrao in self.PLEITOS:
                        if padrao.search(self._texto_norm):
                            resultado["tipo_licenca"] = sigla
                            break
                # se cair aqui, a descrição pode ter pego o título do formulário
                if resultado["descricao_pleito"] is None:
                    titulo = self.soup.find(["h1", "h2", "h3"]) if self.soup else None
                    if titulo:
                        resultado["descricao_pleito"] = titulo.get_text(" ", strip=True)[:120]

            if resultado["tipo_licenca"] is None:
                self._registrar_falha("extrair_tipo_licenca", "tipo de licença não identificado")
            else:
                resultado["fases_componentes"] = self.FASES_COMPONENTES[resultado["tipo_licenca"]]
                if resultado["metodo_deteccao"] is None:
                    resultado["metodo_deteccao"] = "inferido do texto"
        except Exception as exc:  # noqa: BLE001
            self._registrar_falha("extrair_tipo_licenca", str(exc))
        return resultado

    # ------------------------------------------------------------------
    # Bloco 4 - Checklist de documentos exigidos + desduplicação
    # ------------------------------------------------------------------
    def _classificar_subsecao(self, texto: str) -> Optional[str]:
        """Verifica se um texto de cabeçalho corresponde a uma sub-seção de fase."""
        t = normalizar(texto)
        if not t or len(t) > 160:
            return None
        for fase, padrao in self.REGEX_SUBSECOES_FASE:
            if padrao.search(t):
                return fase
        return None

    def _detectar_formulario(self) -> Optional[str]:
        """Identifica o formulário oficial pelo título/texto (pacote SEMA).

        Retorna a chave de `checklists_por_formulario` correspondente ou None.
        """
        try:
            for chave, padrao in self.FORMULARIOS_CONHECIDOS.items():
                if padrao.search(self._texto_norm):
                    return chave
        except Exception as exc:  # noqa: BLE001
            self._registrar_falha("_detectar_formulario", str(exc))
        return None

    def extrair_documentos_exigidos(self, tipo_licenca: Optional[str] = None) -> dict[str, Any]:
        """Varre o final do formulário e extrai o checklist de documentos por fase.

        Aplica as regras de negócio:
            - LIR => somatório das listas de LP + LI;
            - LOR => somatório das listas de LP + LI + LO;
            - Desduplicação (normalização + similaridade) elimina redundâncias,
              mantendo cada documento apenas UMA vez na lista final.
        """
        saida: dict[str, Any] = {
            "por_fase": {}, "lista_deduplicada": [],
            "fonte_checklist": self.fonte_checklist,
            "estatisticas": {"total_bruto": 0, "total_deduplicado": 0, "removidos": []},
        }
        try:
            tipo_licenca = tipo_licenca or (self.dados.get("pleito", {}).get("tipo_licenca"))
            if tipo_licenca is None:
                tipo_licenca = self.extrair_tipo_licenca().get("tipo_licenca")

            por_fase = self._varrer_checklist_dom()

            # Fallback 1: se o DOM não renderizou listas, tenta extração por RegEx no texto
            if not any(por_fase.values()):
                por_fase = self._varrer_checklist_regex()

            # Fallback 2 (oficial): usa os checklists calibrados a partir dos
            # formulários oficiais do órgão (config/checklists_oficiais.json).
            # Se o formulário é um dos tipos conhecidos do pacote SEMA, usa o
            # checklist específico daquele formulário.
            if not any(por_fase.values()):
                tipo_form = self._detectar_formulario()
                dados_form = self.checklists_por_formulario.get(tipo_form or "", {})
                if tipo_form and isinstance(dados_form.get("documentos"), list):
                    fases = self.FASES_COMPONENTES.get(
                        tipo_licenca, [tipo_licenca or "DOCUMENTOS"])
                    por_fase = {fases[0]: list(dados_form["documentos"])}
                    self.fonte_checklist = (
                        f"checklist oficial do formulário '{tipo_form}' "
                        f"({self._fonte_checklist_oficial})")
                elif self.checklist_oficial:
                    por_fase = {fase: list(docs)
                                for fase, docs in self.checklist_oficial.items() if docs}
                    # filtra pelas fases do pleito, quando conhecidas
                    fases_pleito = self.FASES_COMPONENTES.get(tipo_licenca, [])
                    if fases_pleito:
                        filtrado = {fase: docs for fase, docs in por_fase.items()
                                    if fase in fases_pleito}
                        if filtrado:
                            por_fase = filtrado
                    self.fonte_checklist = (
                        f"checklist oficial ({self._fonte_checklist_oficial})")

            # Se o formulário não sub-dividiu por fase, atribui tudo à fase do pleito
            itens_soltos = por_fase.pop("_SEM_FASE", [])
            if itens_soltos and not any(por_fase.values()) and tipo_licenca:
                fases = self.FASES_COMPONENTES.get(tipo_licenca, [tipo_licenca])
                # Se o checklist único já representa o somatório (ex.: LOR),
                # registra direto na lista consolidada, sem mesclar duplicado.
                por_fase = {fases[0]: itens_soltos}

            saida["por_fase"] = {k: v for k, v in por_fase.items() if v}

            # --- Consolidação com regra de agrupamento -------------------
            if tipo_licenca in ("LIR", "LOR"):
                fases_necessarias = self.FASES_COMPONENTES[tipo_licenca]
                # usa as listas presentes no formulário; fases ausentes contribuem vazio
                bruto: list[str] = []
                for fase in fases_necessarias:
                    bruto.extend(por_fase.get(fase, []))
                # caso o formulário só tenha uma lista única já somada
                if not bruto and itens_soltos:
                    bruto = itens_soltos
            else:
                bruto = []
                for itens in por_fase.values():
                    bruto.extend(itens)
                if not bruto and itens_soltos:
                    bruto = itens_soltos
                # Dupla checagem: pleito de fase ÚNICA (LP, LI ou LO) cobra
                # APENAS a listagem da fase escolhida - as demais listas do
                # formulário pertencem a outras fases do licenciamento
                if tipo_licenca in por_fase and por_fase[tipo_licenca]:
                    bruto = por_fase[tipo_licenca]

            deduplicada, removidos = self._deduplicar_documentos(bruto)
            saida["lista_deduplicada"] = deduplicada
            saida["estatisticas"] = {
                "total_bruto": len(bruto),
                "total_deduplicado": len(deduplicada),
                "removidos": removidos,
            }
            # rastreabilidade final da fonte efetiva do checklist
            saida["fonte_checklist"] = self.fonte_checklist
            if not deduplicada:
                self._registrar_falha("extrair_documentos_exigidos",
                                      "nenhum documento exigido localizado no checklist")
        except Exception as exc:  # noqa: BLE001
            self._registrar_falha("extrair_documentos_exigidos", str(exc))
        return saida

    def _varrer_checklist_dom(self) -> dict[str, list[str]]:
        """Percorre o DOM em ordem de documento: localiza a seção de documentação,
        alterna a fase corrente conforme os cabeçalhos e coleta os itens de lista."""
        por_fase: dict[str, list[str]] = {}
        secao_iniciada = False
        fase_corrente: Optional[str] = None

        for tag in self.soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "strong", "b", "p", "td", "th", "li"]):
            texto = tag.get_text(" ", strip=True)
            if not texto:
                continue
            t_norm = normalizar(texto)

            # Detecta o início da seção de documentação (cabeçalhos que não são <li>)
            if not secao_iniciada:
                if tag.name != "li" and self.REGEX_SECAO_DOCS.search(t_norm):
                    secao_iniciada = True
                continue

            # Cabeçalhos de fase alteram a fase corrente (ignora textos dentro de <li>)
            if tag.name == "li":
                # itens de lista são SEMPRE documentos: não re-classificam a fase
                # (ex.: "Cópia da Licença Prévia" na lista da LI NÃO é cabeçalho LP)
                if fase_corrente and len(t_norm) > 2 and not self.REGEX_SECAO_DOCS.search(t_norm):
                    # considera apenas <li> de primeiro nível (evita listas aninhadas duplicadas)
                    if tag.find_parent("li") is None:
                        por_fase.setdefault(fase_corrente, []).append(texto)
            else:
                subsecao = self._classificar_subsecao(texto)
                if subsecao:
                    fase_corrente = subsecao
                elif self.REGEX_SECAO_DOCS.search(t_norm):
                    fase_corrente = fase_corrente  # novo bloco da mesma seção
        return por_fase

    def _varrer_checklist_regex(self) -> dict[str, list[str]]:
        """Extração do checklist por RegEx sobre o texto plano (fallback)."""
        por_fase: dict[str, list[str]] = {"_SEM_FASE": []}
        fase_corrente: Optional[str] = None
        secao_iniciada = False

        for linha in self._texto_original.splitlines():
            linha = linha.strip()
            if not linha:
                continue
            t_norm = normalizar(linha)
            if not secao_iniciada:
                if self.REGEX_SECAO_DOCS.search(t_norm):
                    secao_iniciada = True
                continue
            subsecao = self._classificar_subsecao(linha)
            if subsecao:
                fase_corrente = subsecao
                por_fase.setdefault(fase_corrente, [])
                continue
            if secao_iniciada and re.match(r"^([-•*·▪]|\d+[.)]|[a-z][.)])\s+", linha):
                destino = fase_corrente or "_SEM_FASE"
                por_fase.setdefault(destino, []).append(re.sub(r"^([-•*·▪]|\d+[.)]|[a-z][.)])\s+", "", linha))
        return por_fase

    @staticmethod
    def _deduplicar_documentos(itens: list[str], limiar: float = 0.88) -> tuple[list[str], list[dict]]:
        """Elimina documentos repetidos entre fases usando sets + similaridade.

        1ª passada: conjuntos (sets) por texto normalizado (duplicidade exata);
        2ª passada: similaridade de strings (difflib.SequenceMatcher >= limiar)
        e contenção de sub-cadeia (ex.: 'Cópia da matrícula do imóvel' vs
        'Cópia da matrícula do imóvel atualizada').

        Retorna (lista_deduplicada, lista_de_removidos_com_justificativa).
        """
        selecionados: list[tuple[str, str]] = []  # (normalizado, original)
        removidos: list[dict] = []

        for item in itens:
            item = re.sub(r"\s+", " ", item).strip()
            if not item:
                continue
            n_item = normalizar(item)
            duplicado_de, parecenca = None, 0.0
            for n_sel, sel in selecionados:
                if n_item == n_sel:                      # duplicidade exata (set)
                    duplicado_de, parecenca = sel, 1.0
                    break
                ratio = SequenceMatcher(None, n_item, n_sel).ratio()
                contido = n_sel in n_item or n_item in n_sel
                if ratio >= limiar or contido:
                    duplicado_de, parecenca = sel, max(ratio, 1.0 if contido else 0.0)
                    break
            if duplicado_de:
                removidos.append({
                    "documento": item,
                    "similar_a": duplicado_de,
                    "similaridade": round(parecenca, 3),
                })
            else:
                selecionados.append((n_item, item))
        return [original for _, original in selecionados], removidos

    # ------------------------------------------------------------------
    # Bloco 5 - Consolidação final (JSON)
    # ------------------------------------------------------------------
    def parse(self) -> dict[str, Any]:
        """Executa todos os blocos de extração e consolida o dicionário final."""
        empreendedor = self.extrair_dados_empreendedor()
        empreendimento = self.extrair_dados_empreendimento()
        responsavel = self.extrair_responsavel_tecnico()
        pleito = self.extrair_tipo_licenca()

        # Garante que o checklist use o pleito já identificado
        self.dados = {"pleito": pleito}
        documentos = self.extrair_documentos_exigidos(tipo_licenca=pleito.get("tipo_licenca"))

        # Hard constraint: ART é obrigatória em todo processo de licenciamento
        status_triagem = ("liberado_triagem"
                          if responsavel.get("registro_art")
                          else "bloqueado_sem_art")

        self.dados = {
            "arquivo_origem": self.caminho_arquivo,
            "data_processamento": datetime.now().isoformat(timespec="seconds"),
            "sistema": "Sistema de Verificação de Licenciamento Ambiental - Campo Bom/SMMA",
            "status_triagem": status_triagem,
            "empreendedor": empreendedor,
            "empreendimento": empreendimento,
            "responsavel_tecnico": responsavel,
            "pleito": pleito,
            "documentos_exigidos": documentos,
            "avisos_parser": self.log_erros,
        }
        return self.dados

    def gerar_json(self, caminho_saida: Optional[str] = None) -> dict[str, Any]:
        """Consolida os dados extraídos e exporta o JSON estruturado.

        Args:
            caminho_saida: se informado, grava o JSON também neste arquivo.

        Returns:
            Dicionário Python estruturado (contrato de dados entre as fases 1 e 2).
        """
        if not self.dados:
            self.parse()
        if caminho_saida:
            try:
                Path(caminho_saida).write_text(
                    json.dumps(self.dados, ensure_ascii=False, indent=2),
                    encoding="utf-8")
                logger.info("JSON consolidado gravado em %s", caminho_saida)
            except Exception as exc:  # noqa: BLE001
                self._registrar_falha("gerar_json", f"falha ao gravar arquivo: {exc}")
        return self.dados

    def aplicar_pleito_manual(self, tipo_licenca: str,
                              natureza: Optional[str] = None) -> dict[str, Any]:
        """Aplica o pleito SELECIONADO pelo licenciador na Etapa 1 (fonte da
        verdade do tipo) e RE-MONTA a listagem de documentos exigidos para as
        fases desse tipo (LIR=LP+LI; LOR=LP+LI+LO; demais: a própria fase).

        Se a marcação lida no formulário indicar outro tipo, a divergência é
        registrada em pleito['divergencia_selecao'] e em avisos_parser (nunca
        resolvida em silêncio).
        """
        try:
            if not self.dados:
                self.parse()
            tipo = (tipo_licenca or "").strip().upper()
            if tipo not in self.FASES_COMPONENTES:
                self._registrar_falha(
                    "aplicar_pleito_manual",
                    f"tipo de licença inválido para seleção manual: {tipo!r}")
                return self.dados
            pleito = self.dados.setdefault("pleito", {})
            # preserva o tipo ORIGINALMENTE lido do formulário (1ª aplicação)
            if "tipo_lido_do_formulario" not in pleito:
                pleito["tipo_lido_do_formulario"] = pleito.get("tipo_licenca")
            tipo_lido = pleito.get("tipo_lido_do_formulario")
            pleito["tipo_licenca"] = tipo
            pleito["fases_componentes"] = list(self.FASES_COMPONENTES[tipo])
            pleito["metodo_deteccao"] = "selecionado pelo licenciador na Etapa 1"
            if natureza:
                pleito["natureza"] = natureza
            if tipo_lido and tipo_lido != tipo:
                msg = (f"DIVERGÊNCIA: a marcação do formulário indicava "
                       f"{tipo_lido}, mas a seleção do licenciador na Etapa 1 "
                       f"prevalece ({tipo}). Conferir a marcação do item 3.")
                pleito["divergencia_selecao"] = msg
                self._registrar_falha("aplicar_pleito_manual", msg)
            else:
                pleito.pop("divergencia_selecao", None)
            self.dados["documentos_exigidos"] = self.extrair_documentos_exigidos(
                tipo_licenca=tipo)
        except Exception as exc:  # noqa: BLE001
            self._registrar_falha("aplicar_pleito_manual", str(exc))
        return self.dados


# ==============================================================================
# Exemplo de uso (execução direta: python -m licenciamento.parser_formulario)
# ==============================================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    import sys
    alvo = sys.argv[1] if len(sys.argv) > 1 else "exemplos/formulario_LOR_medio_alto.htm"
    p = FormularioParser(caminho_arquivo=alvo)
    resultado = p.gerar_json(caminho_saida="saida_parser.json")
    print(json.dumps(resultado, ensure_ascii=False, indent=2))
