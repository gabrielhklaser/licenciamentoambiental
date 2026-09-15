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
    """
    if not texto:
        return ""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto).strip().upper()


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
        "cpf_cnpj": ["CPF/CNPJ", "CPF OU CNPJ", "CNPJ/CPF", "CNPJ OU CPF"],
        "nome_empreendimento": ["NOME DO EMPREENDIMENTO", "EMPREENDIMENTO", "DENOMINACAO DO EMPREENDIMENTO"],
        "ramo_atividade": ["RAMO DE ATIVIDADE", "RAMO DA ATIVIDADE", "ATIVIDADE", "DESCRICAO DA ATIVIDADE"],
        "codram": ["CODRAM", "CODIGO DO RAMO DE ATIVIDADE", "COD. RAMO", "CODIGO RAMO DE ATIVIDADE"],
        "porte": ["PORTE DO EMPREENDIMENTO", "PORTE"],
        "potencial_poluidor": ["POTENCIAL POLUIDOR", "POTENCIAL DE POLUICAO", "POTENCIAL POLUIDOR/DEGRADADOR"],
        "area_total": ["AREA TOTAL DO IMOVEL", "AREA TOTAL", "AREA TOTAL (HA)", "AREA DO IMOVEL (HA)"],
        "area_util": ["AREA UTIL/DE INTERVENCAO", "AREA UTIL", "AREA DE INTERVENCAO",
                      "AREA UTIL DO EMPREENDIMENTO", "AREA DE INTERVENCAO (HA)"],
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
        "coordenadas_utm": ["COORDENADAS UTM", "COORDENADA UTM", "COORDENADAS UTM (SIRGAS 2000)"],
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
        ("LOR", re.compile(r"LICENCA[S]? DE OPERACAO E REGULARIZACAO|\(\s*LOR\s*\)|\bLOR\b")),
        ("LIR", re.compile(r"LICENCA[S]? DE INSTALACAO E REGULARIZACAO|\(\s*LIR\s*\)|\bLIR\b")),
        ("LP", re.compile(r"LICENCA[S]? PREVIA|\(\s*LP\s*\)|\bLP\b")),
        ("LI", re.compile(r"LICENCA[S]? DE INSTALACAO|\(\s*LI\s*\)|\bLI\b")),
        ("LO", re.compile(r"LICENCA[S]? DE OPERACAO(?!\s+E\s+REGULARIZACAO)|\(\s*LO\s*\)|\bLO\b")),
        ("AUTORIZACAO", re.compile(r"AUTORIZACAO\s+AMBIENTAL|\(\s*AUTORIZACAO\s*\)|AUTORIZACAO")),
        ("DECLARACAO", re.compile(r"DECLARACAO\s+AMBIENTAL|\(\s*DECLARACAO\s*\)|DECLARACAO")),
    ]

    # Fases que compõem cada espécie de pleito (regra de agrupamento das regularizações)
    FASES_COMPONENTES = {
        "LP": ["LP"], "LI": ["LI"], "LO": ["LO"],
        "LIR": ["LP", "LI"],           # LIR exige o somatório LP + LI
        "LOR": ["LP", "LI", "LO"],     # LOR exige o somatório LP + LI + LO
        "AUTORIZACAO": ["AUTORIZACAO"],
        "DECLARACAO": ["DECLARACAO"],
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
        resultado: dict[str, Any] = {"nome_razao_social": None, "cpf_cnpj": None}
        try:
            resultado["nome_razao_social"] = self._buscar_valor(self.ROTULOS["nome_razao_social"])
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
                    "easting_m": round(valor_e, 2),
                    "northing_m": round(valor_n, 2),
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
                    saida["latitude"] = round(para_float(lat_dec.group(1)), 7)
                    saida["longitude"] = round(para_float(lon_dec.group(1)), 7)
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
                saida["latitude"] = round(saida["latitude"], 7)   # limpeza SIRGAS 2000
                saida["longitude"] = round(saida["longitude"], 7)
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
            bruto_porte = self._buscar_valor(self.ROTULOS["porte"])
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
            if bruto_potencial:
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
    def extrair_tipo_licenca(self) -> dict[str, Any]:
        """Identifica a espécie do pleito: LP, LI, LO, LIR, LOR, Autorização ou Declaração."""
        resultado: dict[str, Any] = {
            "tipo_licenca": None, "descricao_pleito": None, "fases_componentes": [],
        }
        try:
            # 1) Campo explícito do formulário (rótulo 'Tipo de Licença' / 'Espécie do Pleito')
            descricao = self._buscar_valor(self.ROTULOS["tipo_licenca"])
            if descricao:
                resultado["descricao_pleito"] = descricao.strip()
                desc_norm = normalizar(descricao)
                for sigla, padrao in self.PLEITOS:
                    if padrao.search(desc_norm):
                        resultado["tipo_licenca"] = sigla
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

            # 3) Último fallback: varredura do texto completo (ordem de prioridade)
            if resultado["tipo_licenca"] is None:
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
            # formulários oficiais do órgão (config/checklists_oficiais.json)
            if not any(por_fase.values()) and self.checklist_oficial:
                por_fase = {fase: list(docs) for fase, docs in self.checklist_oficial.items()
                            if docs}
                self.fonte_checklist = f"checklist oficial ({self.fonte_checklist})"

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
                subsecao = self._classificar_subsecao(texto)
                if subsecao:
                    fase_corrente = subsecao
                    continue
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
