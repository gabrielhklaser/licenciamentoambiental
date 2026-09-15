# -*- coding: utf-8 -*-
"""
FASE 2 - Agente Financeiro (cálculo de taxas em URMs)
======================================================

Agente DETERMINÍSTICO que calcula a taxa de licenciamento em Unidades de
Referência Municipal (URM) a partir das TABELAS OFICIAIS do Manual de
Legislação e Taxas Ambientais da SEMA Campo Bom:

    TABELA A  - Empreendimentos em Geral (Porte × Potencial Poluidor × Fase)
                - Lei Municipal 4.439/2015, Anexo Único;
    TABELA B  - Sistemas de transmissão/retransmissão (rádio, TV, telefonia):
                valor FIXO 1.960,00 URMs por fase (LP=LI=LO);
    TABELA C  - Lavra Mineral: faixa 0-5 ha tributada como Médio/Médio
                (única faixa publicada no Manual);
    TABELA D  - Parcelamento do Solo: faixas 0-5 / 5,01-10 / 10,01-20 ha;
    TABELA E  - Comércio em Geral: faixas por área construída (m²), potencial
                Baixo (até 50 = 25,00; 50,01-200 = 52,20; >200 = 77,20);
    TABELA F  - Autorizações (taxa única): descapoeiramento (m²), supressão
                de árvores (unidades) e movimentação de terras (m³).

REGRAS DE NEGÓCIO:
    1. REGRA GERAL: valor da tabela para cada fase do pleito;
    2. REGULARIZAÇÕES (Res. COMDEMA 003/2017): LIR = LP + LI;
       LOR = LP + LI + LO (somatório pelo fato gerador omitido);
    3. LICENÇA ÚNICA: substitui as 3 fases - somatório LP+LI+LO
       (interpretação revisável: Manual não detalha a taxa);
    4. FAIXAS DE ÁREA: Lavra Mineral e Parcelamento não usam porte.

Divergências entre o Manual oficial e a especificação original do sistema
estão documentadas em `config/taxas_urm.json` (nota_revisao): ERB/Transmissão
(1.960,00 oficial vs 612/714/510 da especificação) e limites das faixas de
Lavra/Parcelamento publicadas.
"""

from __future__ import annotations

import copy
import logging
import re
import unicodedata
from pathlib import Path
from typing import Any, Optional

from .calibracao import Calibracao

logger = logging.getLogger("licenciamento.agente_financeiro")


class AgenteFinanceiro:
    """Cálculo determinístico de taxas ambientais em URMs.

    A matriz efetiva é `self.matriz` (instância): parte dos valores OFICIAIS
    do Manual de Taxas e pode ser SOBRESCRITA por `config/taxas_urm.json`
    (ou caminho explícito) pelo fluxo de calibração.
    """

    CAMINHO_CONFIG_PADRAO = Path("config/taxas_urm.json")

    # ------------------------------------------------------------------
    # TABELA A (oficial, Lei 4.439/2015) + exceções B/C/D/E
    # ------------------------------------------------------------------
    MATRIZ_URM: dict[str, dict[str, dict[str, dict[str, float]]]] = {
        "GERAL": {
            "MINIMO": {  # Tabela A: Porte Mínimo = 52,20 URMs em todas as fases
                "BAIXO": {"LP": 52.20, "LI": 52.20, "LO": 52.20},
                "MEDIO": {"LP": 52.20, "LI": 52.20, "LO": 52.20},
                "ALTO":  {"LP": 52.20, "LI": 52.20, "LO": 52.20},
            },
            "PEQUENO": {
                "BAIXO": {"LP": 72.10,   "LI": 218.80,  "LO": 102.30},
                "MEDIO": {"LP": 143.90,  "LI": 245.30,  "LO": 172.70},
                "ALTO":  {"LP": 208.30,  "LI": 568.40,  "LO": 488.40},
            },
            "MEDIO": {
                "BAIXO": {"LP": 507.90,  "LI": 774.00,   "LO": 387.60},
                "MEDIO": {"LP": 1015.70, "LI": 1105.10,  "LO": 812.60},
                "ALTO":  {"LP": 1523.60, "LI": 1508.20,  "LO": 1969.60},
            },
            "GRANDE": {
                "BAIXO": {"LP": 2681.50, "LI": 1438.50,  "LO": 1191.80},
                "MEDIO": {"LP": 3575.40, "LI": 2383.60,  "LO": 2383.60},
                "ALTO":  {"LP": 5363.10, "LI": 4171.30,  "LO": 4171.30},
            },
            "EXCEPCIONAL": {
                "BAIXO": {"LP": 8041.20, "LI": 3216.50,  "LO": 3216.50},
                "MEDIO": {"LP": 10721.60, "LI": 4288.70, "LO": 4288.70},
                "ALTO":  {"LP": 18762.90, "LI": 17154.60, "LO": 17154.60},
            },
        },
        # --------------------------------------------------------------
        # TABELA B - Sistemas de transmissão/retransmissão (rádio, TV,
        # telefonia e similares): valor fixo pela espécie, 1.960,00/fase
        # --------------------------------------------------------------
        "ERB": {
            "FIXO": {
                "_": {"LP": 1960.00, "LI": 1960.00, "LO": 1960.00},
            },
        },
        # --------------------------------------------------------------
        # TABELA C - Lavra Mineral (única faixa publicada: 0-5 ha,
        # tributada como Médio/Médio; demais faixas repetem a linha
        # publicada com aviso de revisão manual)
        # --------------------------------------------------------------
        "LAVRA_MINERAL": {
            "0 a 5 ha":    {"_": {"LP": 1015.70, "LI": 1105.10, "LO": 812.60}},
            "5 a 10 ha":   {"_": {"LP": 1015.70, "LI": 1105.10, "LO": 812.60}},
            "10 a 20 ha":  {"_": {"LP": 1015.70, "LI": 1105.10, "LO": 812.60}},
            "> 20 ha":     {"_": {"LP": 1015.70, "LI": 1105.10, "LO": 812.60}},
        },
        # --------------------------------------------------------------
        # TABELA D - Parcelamento do Solo (faixas oficiais até 20 ha;
        # acima disso repete a faixa 10-20 ha com aviso)
        # --------------------------------------------------------------
        "PARCELAMENTO_SOLO": {
            "0 a 5 ha":    {"_": {"LP": 1290.70, "LI": 1386.12, "LO": 1386.12}},
            "5 a 10 ha":   {"_": {"LP": 2004.60, "LI": 2172.84, "LO": 2172.84}},
            "10 a 20 ha":  {"_": {"LP": 2294.76, "LI": 2568.96, "LO": 2568.96}},
            "> 20 ha":     {"_": {"LP": 2294.76, "LI": 2568.96, "LO": 2568.96}},
        },
        # --------------------------------------------------------------
        # TABELA E - Comércio em Geral (potencial Baixo), faixas por
        # área construída (m²)
        # --------------------------------------------------------------
        "COMERCIO": {
            "ate 50 m2":    {"_": {"LP": 25.00, "LI": 25.00, "LO": 25.00}},
            "50 a 200 m2":  {"_": {"LP": 52.20, "LI": 52.20, "LO": 52.20}},
            "acima 200 m2": {"_": {"LP": 77.20, "LI": 77.20, "LO": 77.20}},
        },
    }

    # Palavras-chave de detecção do grupo de exceção no ramo de atividade
    PALAVRAS_ERB = ["ESTACAO DE RADIO BASE", "ESTACAO RADIO BASE", "RADIO BASE",
                    "ERB", "TELECOMUNICACAO", "TRANSMISSAO DE DADOS",
                    "TRANSMISSAO E/OU RETRANSMISSAO", "ANTENA"]
    PALAVRAS_LAVRA = ["LAVRA MINERAL", "MINERACAO", "EXTRACAO MINERAL",
                      "EXTRACAO DE MINERIO", "PEDREIRA", "AREIEIRO", "SAIBREIRA"]
    PALAVRAS_PARCELAMENTO = ["PARCELAMENTO DO SOLO", "PARCELAMENTO DE SOLO",
                             "LOTEAMENTO", "DESMEMBRAMENTO",
                             "CONDOMINIO HABITACIONAL", "REPARCELAMENTO",
                             "REMEMBRAMENTO"]
    PALAVRAS_COMERCIO = ["COMERCIO EM GERAL", "COMERCIO VAREJISTA",
                         "COMERCIO DE PRODUTOS", "LOJA", "VAREJO"]

    # TABELA F - Autorizações (taxa única por tipo de intervenção)
    TABELA_F_PADRAO: dict[str, list[dict[str, Any]]] = {
        "DESCAPOEIRAMENTO": [
            {"rotulo": "até 500,00 m²",   "limite": 500.0, "valor": 20.00},
            {"rotulo": "mais de 500,00 m²", "limite": None, "valor": 100.00},
        ],
        "SUPRESSAO_ARVORES": [
            {"rotulo": "até 5 árvores",     "limite": 5.0,  "valor": 4.00},
            {"rotulo": "de 6 a 20 árvores", "limite": 20.0, "valor": 20.00},
            {"rotulo": "de 21 a 50 árvores", "limite": 50.0, "valor": 50.00},
            {"rotulo": "mais de 50 árvores", "limite": None, "valor": 80.00},
        ],
        "MOVIMENTACAO_TERRAS": [
            {"rotulo": "até 150,00 m³",    "limite": 150.0, "valor": 50.00},
            {"rotulo": "mais de 150,00 m³", "limite": None, "valor": 150.00},
        ],
    }

    # Palavras-chave para detectar o TIPO de intervenção da Tabela F
    TIPOS_TABELA_F = {
        "DESCAPOEIRAMENTO": ["DESCAPOEIRAMENTO", "LIMPEZA DE VEGETACAO RASTEIRA"],
        "SUPRESSAO_ARVORES": ["SUPRESSAO DE ARVORES", "SUPRESSAO ARVORES",
                              "CORTE DE ARVORES", "SUPRESSAO DE VEGETACAO ARBOREA"],
        "MOVIMENTACAO_TERRAS": ["MOVIMENTACAO DE TERRAS", "MOVIMENTACAO DE TERRA",
                                "TERRAPLANAGEM", "TERRAPLENAGEM"],
    }

    # Valores fixos para espécies sem LP/LI/LO (provisórios - ver nota_revisao)
    VALORES_ESPECIE_SIMPLES = {
        "AUTORIZACAO": 52.20,    # provisório: Tabela F não cobre autorização genérica
        "DECLARACAO": 52.20,     # provisório: sem valor publicado no Manual
    }

    FASES_POR_PLEITO = {
        "LP": ["LP"], "LI": ["LI"], "LO": ["LO"],
        "LIR": ["LP", "LI"],            # soma das taxas de LP + LI
        "LOR": ["LP", "LI", "LO"],      # soma das taxas de LP + LI + LO
        "LICENCA_UNICA": ["LP", "LI", "LO"],  # substitui as 3 fases (somatório)
    }

    ROTULO_PORTE = {"MINIMO": "Mínimo", "PEQUENO": "Pequeno", "MEDIO": "Médio",
                    "GRANDE": "Grande", "EXCEPCIONAL": "Excepcional"}

    # ------------------------------------------------------------------
    def __init__(self, caminho_taxas: Optional[str] = None):
        """Prepara a matriz efetiva: padrões do código + calibração oficial.

        Args:
            caminho_taxas: caminho explícito do JSON de taxas (padrão:
                `config/taxas_urm.json`, quando existir).
        """
        # cópia profunda: edições na instância não contaminam a classe
        self.matriz = copy.deepcopy(self.MATRIZ_URM)
        self.valores_especie = dict(self.VALORES_ESPECIE_SIMPLES)
        self.tabela_f = copy.deepcopy(self.TABELA_F_PADRAO)
        self.palavras_grupos = {
            "ERB": list(self.PALAVRAS_ERB),
            "LAVRA_MINERAL": list(self.PALAVRAS_LAVRA),
            "PARCELAMENTO_SOLO": list(self.PALAVRAS_PARCELAMENTO),
            "COMERCIO": list(self.PALAVRAS_COMERCIO),
        }
        self.fonte_tabela = (
            "Manual de Legislação e Taxas Ambientais - SEMA Campo Bom "
            "(Tabelas A-F, Lei 4.439/2015)")
        self.tabela_revisada = True
        self._aplicar_calibracao(caminho_taxas)

    def _aplicar_calibracao(self, caminho_taxas: Optional[str]) -> None:
        """Sobrescreve os padrões com `config/taxas_urm.json` (se existir)."""
        config = Calibracao().taxas_urm
        if caminho_taxas:
            caminho = Path(caminho_taxas)
            config = None
            if caminho.exists():
                import json
                config = json.loads(caminho.read_text(encoding="utf-8"))
        if not config:
            return
        try:
            if isinstance(config.get("matriz"), dict):
                for grupo, portes in config["matriz"].items():
                    self.matriz.setdefault(grupo, {})
                    for porte, potenciais in portes.items():
                        self.matriz[grupo].setdefault(porte, {})
                        for potencial, fases in potenciais.items():
                            self.matriz[grupo][porte].setdefault(potencial, {})
                            for fase, valor in fases.items():
                                self.matriz[grupo][porte][potencial][fase] = float(valor)
            if isinstance(config.get("valores_especie"), dict):
                for especie, valor in config["valores_especie"].items():
                    self.valores_especie[especie.upper()] = float(valor)
            if isinstance(config.get("grupos_palavras_chave"), dict):
                for grupo, palavras in config["grupos_palavras_chave"].items():
                    self.palavras_grupos.setdefault(grupo, []).extend(palavras)
            # TABELA F (faixas de autorizações) calibrável
            if isinstance(config.get("tabela_f_autorizacoes"), dict):
                tabela_f: dict[str, list[dict[str, Any]]] = {}
                for tipo, faixas in config["tabela_f_autorizacoes"].items():
                    tabela_f[tipo.upper()] = [
                        {"rotulo": f.get("faixa", ""),
                         "limite": f.get("limite"), "valor": float(f["valor"])}
                        for f in faixas
                    ]
                self.tabela_f = tabela_f
            self.fonte_tabela = config.get("fonte", "config/taxas_urm.json")
            self.tabela_revisada = bool(config.get("revisado", False))
            logger.info("Tabela de URMs calibrada via %s (revisado=%s)",
                        self.fonte_tabela, self.tabela_revisada)
        except Exception as exc:  # noqa: BLE001
            logger.error("Falha ao aplicar calibração de taxas: %s (usando padrões)", exc)

    # ------------------------------------------------------------------
    @staticmethod
    def _normalizar(texto: Optional[str]) -> str:
        if not texto:
            return ""
        texto = unicodedata.normalize("NFKD", texto)
        texto = "".join(c for c in texto if not unicodedata.combining(c))
        return re.sub(r"\s+", " ", texto).upper()

    # ------------------------------------------------------------------
    def _resolver_grupo(self, ramo_atividade: Optional[str],
                        codram: Optional[str] = None,
                        potencial_poluidor: Optional[str] = None) -> str:
        """Detecta o grupo tributário (ERB, LAVRA_MINERAL, PARCELAMENTO_SOLO,
        COMERCIO ou GERAL).

        A TABELA E (Comércio em Geral) só publica potencial BAIXO: comércios
        de potencial Médio/Alto permanecem na TABELA A (GERAL).
        """
        ramo_n = self._normalizar(ramo_atividade)
        if not ramo_n and codram:
            ramo_n = f"CODRAM {codram}"
        if ramo_n:
            if any(p in ramo_n for p in self.palavras_grupos["ERB"]):
                return "ERB"
            if any(p in ramo_n for p in self.palavras_grupos["LAVRA_MINERAL"]):
                return "LAVRA_MINERAL"
            if any(p in ramo_n for p in self.palavras_grupos["PARCELAMENTO_SOLO"]):
                return "PARCELAMENTO_SOLO"
            if any(p in ramo_n for p in self.palavras_grupos["COMERCIO"]) \
                    and self._normalizar(potencial_poluidor) in ("", "BAIXO"):
                return "COMERCIO"
        return "GERAL"

    @staticmethod
    def _resolver_faixa(area_ha: Optional[float]) -> Optional[str]:
        """Converte a área do empreendimento na faixa de hectares (Tabelas C/D)."""
        if area_ha is None:
            return None
        if area_ha <= 5:
            return "0 a 5 ha"
        if area_ha <= 10:
            return "5 a 10 ha"
        if area_ha <= 20:
            return "10 a 20 ha"
        return "> 20 ha"

    @staticmethod
    def _resolver_faixa_comercio(area_m2: Optional[float]) -> Optional[str]:
        """Converte a área construída (m²) na faixa da TABELA E."""
        if area_m2 is None:
            return None
        if area_m2 <= 50:
            return "ate 50 m2"
        if area_m2 <= 200:
            return "50 a 200 m2"
        return "acima 200 m2"

    # ------------------------------------------------------------------
    def _enquadramento_tabela_f(self, tipo_intervencao: Optional[str],
                                quantidade: Optional[float]) -> Optional[float]:
        """Enquadra a autorização na TABELA F (taxa única por faixa).

        Returns:
            Valor da taxa única, ou None quando não há enquadramento.
        """
        if not tipo_intervencao:
            return None
        tipo_n = self._normalizar(tipo_intervencao)
        chave_tabela: Optional[str] = None
        for chave, palavras in self.TIPOS_TABELA_F.items():
            if any(p in tipo_n for p in self._normalizar(" ".join(palavras)).split()):
                chave_tabela = chave
                break
        if chave_tabela is None:
            # tenta o próprio nome normalizado como chave
            chave_tabela = tipo_n.replace(" ", "_")
        faixas = self.tabela_f.get(chave_tabela)
        if not faixas:
            return None
        if quantidade is None:
            # sem quantidade informada: retorna a MENOR taxa publicada como
            # provisório (o chamador deve sinalizar o aviso)
            return float(faixas[0]["valor"])
        for faixa in faixas:
            limite = faixa.get("limite")
            if limite is None or quantidade <= limite:
                return float(faixa["valor"])
        # acima de todas as faixas com limite: usa a última (sem limite)
        return float(faixas[-1]["valor"])

    def _valor_fase(self, grupo: str, porte: str, potencial: str,
                    fase: str, faixa: Optional[str] = None,
                    faixa_m2: Optional[str] = None) -> Optional[float]:
        """Consulta a matriz na composição solicitada."""
        grupo_tabela = self.matriz.get(grupo, {})
        if grupo in ("LAVRA_MINERAL", "PARCELAMENTO_SOLO"):
            chave_porte = faixa           # grupos por faixa de hectares
        elif grupo == "COMERCIO":
            chave_porte = faixa_m2        # TABELA E: faixa por área construída
        elif grupo == "ERB":
            chave_porte = "FIXO"          # TABELA B: valor fixo pela espécie
        else:
            chave_porte = porte
        tabela = grupo_tabela.get(chave_porte, {})
        por_potencial = tabela.get(potencial, tabela.get("_", {}))
        return por_potencial.get(fase)

    # ------------------------------------------------------------------
    def calcular_taxa(self,
                      tipo_licenca: str,
                      porte: str,
                      potencial_poluidor: str,
                      ramo_atividade: Optional[str] = None,
                      area_ha: Optional[float] = None,
                      codram: Optional[str] = None,
                      area_m2: Optional[float] = None,
                      tipo_autorizacao: Optional[str] = None,
                      quantidade_autorizacao: Optional[float] = None) -> dict[str, Any]:
        """Calcula a taxa final em URMs para o pleito informado.

        Args:
            tipo_licenca: sigla do pleito (LP, LI, LO, LIR, LOR, LICENCA_UNICA,
                AUTORIZACAO, DECLARACAO).
            porte: Mínimo/Pequeno/Médio/Grande/Excepcional (ignorado nas exceções).
            potencial_poluidor: Baixo/Médio/Alto (TABELA E só cobre Baixo).
            ramo_atividade: descrição do ramo (detecta ERB/faixas/comércio).
            area_ha: área total/útil em hectares (faixas Lavra/Parcelamento).
            codram: código do ramo (ajuda na detecção futura, se necessário).
            area_m2: área construída/útil da atividade em m² (TABELA E).
            tipo_autorizacao: tipo de intervenção da TABELA F
                (descapoeiramento / supressao_arvores / movimentacao_terras).
            quantidade_autorizacao: quantidade para a faixa da TABELA F
                (m², nº de árvores ou m³, conforme o tipo).

        Returns:
            Dicionário com grupo detectado, composição por fase e total em URMs.
        """
        avisos: list[str] = []
        try:
            grupo = self._resolver_grupo(ramo_atividade, codram, potencial_poluidor)
            tipo_n = self._normalizar(tipo_licenca).replace(" ", "_")

            # ---- TABELA F: autorizações com tipo de intervenção ----------
            if tipo_n == "AUTORIZACAO" and tipo_autorizacao:
                valor_f = self._enquadramento_tabela_f(
                    tipo_autorizacao, quantidade_autorizacao)
                if valor_f is not None:
                    rotulo = self._normalizar(tipo_autorizacao)
                    if quantidade_autorizacao is None:
                        avisos.append(
                            "Quantidade não informada para a Tabela F - taxa "
                            "provisória pela MENOR faixa publicada; conferir o "
                            "enquadramento (m², nº de árvores ou m³).")
                    return self._montar_resultado(
                        tipo_licenca, "TABELA_F", {"TAXA_UNICA": valor_f},
                        {"TAXA_UNICA": {
                            "tipo_intervencao": rotulo,
                            "quantidade": quantidade_autorizacao,
                            "valor_urm": valor_f}},
                        rotulo, avisos)

            # ---- Espécies simples (Autorização / Declaração) -------------
            if tipo_n in self.valores_especie:
                valor = self.valores_especie[tipo_n]
                if tipo_n == "AUTORIZACAO":
                    avisos.append(
                        "Autorização SEMA genérica sem tipo de intervenção da "
                        "Tabela F (descapoeiramento/supressão de árvores/"
                        "movimentação de terras): valor provisório interno - "
                        "conferir o Manual.")
                return self._montar_resultado(tipo_licenca, grupo, {tipo_n: valor},
                                              {tipo_n: valor}, tipo_n, avisos)

            fases = self.FASES_POR_PLEITO.get(tipo_n)
            if fases is None:
                raise ValueError(f"Tipo de licença desconhecido: {tipo_licenca}")

            porte_n = self._normalizar(porte) or "MINIMO"
            potencial_n = self._normalizar(potencial_poluidor) or "BAIXO"
            usa_faixa_ha = grupo in ("LAVRA_MINERAL", "PARCELAMENTO_SOLO")
            faixa = self._resolver_faixa(area_ha) if usa_faixa_ha else None
            faixa_m2 = self._resolver_faixa_comercio(area_m2) \
                if grupo == "COMERCIO" else None

            # ---- Exceção por faixa: área é obrigatória -------------------
            if usa_faixa_ha and faixa is None:
                raise ValueError(
                    f"Grupo '{grupo}' exige a área (ha) para definir a faixa de cobrança.")
            if grupo == "COMERCIO" and faixa_m2 is None:
                # sem área construída não há enquadramento na TABELA E
                grupo = "GERAL"
                avisos.append(
                    "Comércio sem área construída informada: taxa calculada pela "
                    "TABELA A (Porte × Potencial) em vez da TABELA E.")

            # ---- Avisos de faixa além do publicado no Manual -------------
            if grupo == "LAVRA_MINERAL" and area_ha is not None and area_ha > 5:
                avisos.append(
                    "Lavra Mineral acima de 5,00 ha: faixa não publicada no "
                    "Manual (Tabela C tem apenas 0-5 ha) - taxa provisória pela "
                    "linha publicada; sujeito a revisão.")
            if grupo == "PARCELAMENTO_SOLO" and area_ha is not None and area_ha > 20:
                avisos.append(
                    "Parcelamento acima de 20,00 ha: faixa não publicada no "
                    "Manual (Tabela D vai até 20 ha) - taxa provisória pela "
                    "faixa 10,01-20 ha; sujeito a revisão.")

            # ---- Somatório das fases (regra LIR/LOR/Licença Única) -------
            composicao: dict[str, float] = {}
            detalhe_consulta: dict[str, Any] = {}
            for fase in fases:
                valor = self._valor_fase(grupo, porte_n, potencial_n, fase,
                                         faixa, faixa_m2)
                if valor is None:
                    raise ValueError(
                        f"Composição não encontrada na tabela: grupo={grupo}, "
                        f"porte/faixa={faixa or faixa_m2 or porte_n}, "
                        f"potencial={potencial_n}, fase={fase}. "
                        "Verifique a MATRIZ_URM conforme o Manual de Legislação.")
                composicao[fase] = float(valor)
                detalhe_consulta[fase] = {
                    "grupo": grupo,
                    "porte_ou_faixa": faixa or faixa_m2 or porte_n,
                    "potencial": potencial_n if grupo == "GERAL" else "-",
                    "valor_urm": float(valor),
                }

            rotulo_enquadramento = faixa or faixa_m2 \
                or ("FIXO" if grupo == "ERB" else self.ROTULO_PORTE.get(porte_n, porte_n))
            return self._montar_resultado(tipo_licenca, grupo, composicao,
                                          detalhe_consulta, rotulo_enquadramento,
                                          avisos)

        except Exception as exc:  # noqa: BLE001
            logger.exception("Falha no cálculo da taxa: %s", exc)
            return {
                "agente": "AgenteFinanceiro",
                "tipo_licenca": tipo_licenca,
                "erro": str(exc),
                "total_urm": None,
            }

    def _montar_resultado(self, tipo_licenca: str, grupo: str,
                          composicao: dict[str, float], detalhe: dict[str, Any],
                          porte_ou_faixa: str,
                          avisos: Optional[list[str]] = None) -> dict[str, Any]:
        """Padroniza o dicionário de retorno do cálculo."""
        total = round(sum(composicao.values()), 2)
        resultado = {
            "agente": "AgenteFinanceiro",
            "tipo_licenca": tipo_licenca,
            "grupo_atividade": grupo,
            "porte_ou_faixa": porte_ou_faixa,
            "moeda": "URM",
            "composicao_fases": composicao,
            "detalhe_consulta": detalhe,
            "total_urm": total,
            "fonte_tabela": self.fonte_tabela,
            "tabela_revisada": self.tabela_revisada,
        }
        if tipo_licenca in ("LIR", "LOR", "LICENCA_UNICA"):
            regra = ("Somatório das fases LP + LI + LO (Licença Única substitui "
                     "as 3 fases)" if tipo_licenca == "LICENCA_UNICA" else
                     f"Somatório das fases {' + '.join(composicao.keys())} ({tipo_licenca})")
            resultado["regra_aplicada"] = f"{regra} => {total:,.2f} URMs".replace(
                ",", "X").replace(".", ",").replace("X", ".")
        if avisos:
            resultado["avisos"] = avisos
        logger.info("Taxa calculada (%s/%s): %.2f URMs", tipo_licenca, grupo, total)
        return resultado

    # ------------------------------------------------------------------
    def calcular_do_parser(self, dados_parser: dict) -> dict[str, Any]:
        """Convenience: extrai os parâmetros direto do JSON da Fase 1 e calcula."""
        emp = dados_parser.get("empreendimento", {})
        pleito = dados_parser.get("pleito", {})
        area_util_ha = emp.get("area_util_ha") or emp.get("area_intervencao_ha") \
            or emp.get("area_total_ha")
        area_m2: Optional[float] = None
        if area_util_ha is not None:
            try:
                area_m2 = float(area_util_ha) * 10000.0  # proxy: área útil da atividade
            except (TypeError, ValueError):
                area_m2 = None
        return self.calcular_taxa(
            tipo_licenca=pleito.get("tipo_licenca", ""),
            porte=emp.get("porte", ""),
            potencial_poluidor=emp.get("potencial_poluidor", ""),
            ramo_atividade=emp.get("ramo_atividade"),
            area_ha=area_util_ha,
            area_m2=area_m2,
            codram=emp.get("codram"),
        )
