# -*- coding: utf-8 -*-
"""
FASE 2 - Agente Financeiro (cálculo de taxas em URMs)
======================================================

Agente DETERMINÍSTICO que calcula a taxa de licenciamento em Unidades de
Referência Municipal (URM) a partir da matriz: Porte x Potencial Poluidor x
Fase da Licença, aplicando as regras específicas:

    1. REGRA GERAL: valor da tabela geral para cada fase do pleito;
    2. REGULAES (LIR/LOR): a taxa é o SOMATÓRIO das fases componentes
       (LIR = LP + LI; LOR = LP + LI + LO);
    3. EXCEÇÃO ERB: Estações de Rádio Base / Transmissão têm tabela própria,
       fixada diretamente pela espécie (porte/potencial ignorados);
    4. EXCEÇÃO POR FAIXA DE ÁREA: Lavra Mineral e Parcelamento de Solo não usam
       porte Mínimo/Pequeno/Médio, mas faixas de hectares (ex.: 0 a 5 ha).

ATENÇÃO (calibração): os valores da MATRIZ_URM abaixo seguem a estrutura do
manual municipal. A linha do Porte Mínimo e a tabela ERB refletem os valores
informados pela Secretaria. As demais linhas estão marcadas com "# AJUSTAR"
e devem ser conferidas/substituídas conforme o Manual de Legislação oficial.
"""

from __future__ import annotations

import copy
import logging
import math
import re
import unicodedata
from pathlib import Path
from typing import Any, Optional

from .calibracao import Calibracao

logger = logging.getLogger("licenciamento.agente_financeiro")


class AgenteFinanceiro:
    """Cálculo determinístico de taxas ambientais em URMs.

    A matriz efetiva é `self.matriz` (instância): parte dos padrões da
    `MATRIZ_URM` de classe e é SOBRESCRITA por `config/taxas_urm.json` quando
    esse arquivo existe (gerado pela ingestão do Manual de Taxas oficial).
    """

    CAMINHO_CONFIG_PADRAO = Path("config/taxas_urm.json")

    # ------------------------------------------------------------------
    # MATRIZ GERAL - grupo GERAL: porte x potencial poluidor x fase (URM)
    # ------------------------------------------------------------------
    MATRIZ_URM: dict[str, dict[str, dict[str, dict[str, float]]]] = {
        "GERAL": {
            "MINIMO": {  # valores oficiais informados: 52,20 URMs em todas as fases
                "BAIXO": {"LP": 52.20, "LI": 52.20, "LO": 52.20},
                "MEDIO": {"LP": 52.20, "LI": 52.20, "LO": 52.20},
                "ALTO":  {"LP": 52.20, "LI": 52.20, "LO": 52.20},
            },
            "PEQUENO": {  # AJUSTAR conforme Manual de Legislação (valores provisórios)
                "BAIXO": {"LP": 104.40, "LI": 104.40, "LO": 104.40},
                "MEDIO": {"LP": 156.60, "LI": 156.60, "LO": 156.60},
                "ALTO":  {"LP": 208.80, "LI": 208.80, "LO": 208.80},
            },
            "MEDIO": {  # AJUSTAR conforme Manual de Legislação (valores provisórios)
                "BAIXO": {"LP": 208.80, "LI": 208.80, "LO": 208.80},
                "MEDIO": {"LP": 313.20, "LI": 313.20, "LO": 313.20},
                "ALTO":  {"LP": 417.60, "LI": 417.60, "LO": 417.60},
            },
            "GRANDE": {  # AJUSTAR conforme Manual de Legislação (valores provisórios)
                "BAIXO": {"LP": 417.60, "LI": 417.60, "LO": 417.60},
                "MEDIO": {"LP": 522.00, "LI": 522.00, "LO": 522.00},
                "ALTO":  {"LP": 626.40, "LI": 626.40, "LO": 626.40},
            },
            "EXCEPCIONAL": {  # AJUSTAR conforme Manual de Legislação (valores provisórios)
                "BAIXO": {"LP": 626.40, "LI": 626.40, "LO": 626.40},
                "MEDIO": {"LP": 835.20, "LI": 835.20, "LO": 835.20},
                "ALTO":  {"LP": 1044.00, "LI": 1044.00, "LO": 1044.00},
            },
        },
        # --------------------------------------------------------------
        # EXCEÇÃO 1 - Estações de Rádio Base (ERB) / Transmissão:
        # valores PRÓPRIOS, fixados pela espécie (independem de porte/potencial)
        # --------------------------------------------------------------
        "ERB": {
            "FIXO": {
                "_": {  # '_' = potencial não se aplica
                    "LP": 612.00,
                    "LI": 714.00,
                    "LO": 510.00,
                },
            },
        },
        # --------------------------------------------------------------
        # EXCEÇÃO 2 - Lavra Mineral: porte por FAIXA DE HECTARES  (AJUSTAR valores)
        # --------------------------------------------------------------
        "LAVRA_MINERAL": {
            "0 a 5 ha":    {"_": {"LP": 208.80, "LI": 208.80, "LO": 208.80}},
            "5 a 10 ha":   {"_": {"LP": 313.20, "LI": 313.20, "LO": 313.20}},
            "10 a 50 ha":  {"_": {"LP": 522.00, "LI": 522.00, "LO": 522.00}},
            "> 50 ha":     {"_": {"LP": 835.20, "LI": 835.20, "LO": 835.20}},
        },
        # --------------------------------------------------------------
        # EXCEÇÃO 2 - Parcelamento de Solo (Loteamentos): FAIXAS DE HECTARES
        # --------------------------------------------------------------
        "PARCELAMENTO_SOLO": {
            "0 a 5 ha":    {"_": {"LP": 260.00, "LI": 260.00, "LO": 260.00}},   # AJUSTAR
            "5 a 10 ha":   {"_": {"LP": 390.00, "LI": 390.00, "LO": 390.00}},   # AJUSTAR
            "> 10 ha":     {"_": {"LP": 650.00, "LI": 650.00, "LO": 650.00}},   # AJUSTAR
        },
    }

    # Palavras-chave de detecção do grupo de exceção no ramo de atividade
    PALAVRAS_ERB = ["ESTACAO DE RADIO BASE", "ESTACAO RADIO BASE", "RADIO BASE",
                    "ERB", "TELECOMUNICACAO", "TRANSMISSAO DE DADOS", "ANTENA"]
    PALAVRAS_LAVRA = ["LAVRA MINERAL", "MINERACAO", "EXTRACAO MINERAL", "EXTRACAO DE MINERIO",
                      "PEDREIRA", "AREIEIRO", "SAIBREIRA"]
    PALAVRAS_PARCELAMENTO = ["PARCELAMENTO DO SOLO", "PARCELAMENTO DE SOLO", "LOTEAMENTO",
                             "DESMEMBRAMENTO", "CONDOMINIO HABITACIONAL", "REPARCELAMENTO",
                             "REMEMBRAMENTO"]

    # Valores fixos para espécies sem LP/LI/LO (AJUSTAR conforme manual)
    VALORES_ESPECIE_SIMPLES = {
        "AUTORIZACAO": 52.20,    # AJUSTAR
        "DECLARACAO": 52.20,     # AJUSTAR
    }

    FASES_POR_PLEITO = {
        "LP": ["LP"], "LI": ["LI"], "LO": ["LO"],
        "LIR": ["LP", "LI"],            # soma das taxas de LP + LI
        "LOR": ["LP", "LI", "LO"],      # soma das taxas de LP + LI + LO
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
        self.palavras_grupos = {
            "ERB": list(self.PALAVRAS_ERB),
            "LAVRA_MINERAL": list(self.PALAVRAS_LAVRA),
            "PARCELAMENTO_SOLO": list(self.PALAVRAS_PARCELAMENTO),
        }
        self.fonte_tabela = "padrões internos do código (aguardando Manual oficial)"
        self.tabela_revisada = False
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
                        codram: Optional[str] = None) -> str:
        """Detecta o grupo de exceção (ERB, LAVRA_MINERAL, PARCELAMENTO_SOLO ou GERAL)."""
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
        return "GERAL"

    @staticmethod
    def _resolver_faixa(area_ha: Optional[float]) -> Optional[str]:
        """Converte a área do empreendimento na faixa de hectares correspondente."""
        if area_ha is None:
            return None
        if area_ha <= 5:
            return "0 a 5 ha"
        if area_ha <= 10:
            return "5 a 10 ha"
        return "> 10 ha"

    def _valor_fase(self, grupo: str, porte: str, potencial: str,
                    fase: str, faixa: Optional[str] = None) -> Optional[float]:
        """Consulta a matriz na composição solicitada."""
        grupo_tabela = self.matriz.get(grupo, {})
        if grupo in ("LAVRA_MINERAL", "PARCELAMENTO_SOLO"):
            chave_porte = faixa           # grupos por faixa de hectares
        elif grupo == "ERB":
            chave_porte = "FIXO"          # ERB: valor fixo pela espécie
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
                      codram: Optional[str] = None) -> dict[str, Any]:
        """Calcula a taxa final em URMs para o pleito informado.

        Args:
            tipo_licenca: sigla do pleito (LP, LI, LO, LIR, LOR, AUTORIZACAO, DECLARACAO).
            porte: Mínimo/Pequeno/Médio/Grande/Excepcional (ignorado nas exceções).
            potencial_poluidor: Baixo/Médio/Alto (ignorado nas exceções).
            ramo_atividade: descrição do ramo (usada para detectar ERB/faixas).
            area_ha: área usada para enquadrar a faixa (Lavra Mineral/Loteamento).
            codram: código do ramo (ajuda na detecção futura, se necessário).

        Returns:
            Dicionário com grupo detectado, composição por fase e total em URMs.
        """
        try:
            grupo = self._resolver_grupo(ramo_atividade, codram)
            tipo_n = self._normalizar(tipo_licenca).replace(" ", "_")

            # ---- Espécies simples (Autorização / Declaração) -------------
            if tipo_n in self.valores_especie:
                valor = self.valores_especie[tipo_n]
                return self._montar_resultado(tipo_licenca, grupo, {tipo_n: valor},
                                              {tipo_n: valor}, tipo_n)

            fases = self.FASES_POR_PLEITO.get(tipo_n)
            if fases is None:
                raise ValueError(f"Tipo de licença desconhecido: {tipo_licenca}")

            porte_n = self._normalizar(porte) or "MINIMO"
            potencial_n = self._normalizar(potencial_poluidor) or "BAIXO"
            faixa = self._resolver_faixa(area_ha) if grupo in ("LAVRA_MINERAL", "PARCELAMENTO_SOLO") else None

            # ---- Exceção por faixa: área é obrigatória -------------------
            if grupo in ("LAVRA_MINERAL", "PARCELAMENTO_SOLO") and faixa is None:
                raise ValueError(
                    f"Grupo '{grupo}' exige a área (ha) para definir a faixa de cobrança.")

            # ---- Somatório das fases (regra LIR/LOR e fases simples) -----
            composicao: dict[str, float] = {}
            detalhe_consulta: dict[str, Any] = {}
            for fase in fases:
                valor = self._valor_fase(grupo, porte_n, potencial_n, fase, faixa)
                if valor is None:
                    raise ValueError(
                        f"Composição não encontrada na tabela: grupo={grupo}, "
                        f"porte/faixa={faixa or porte_n}, potencial={potencial_n}, fase={fase}. "
                        "Verifique a MATRIZ_URM conforme o Manual de Legislação.")
                composicao[fase] = float(valor)
                detalhe_consulta[fase] = {
                    "grupo": grupo,
                    "porte_ou_faixa": faixa or porte_n,
                    "potencial": potencial_n if grupo == "GERAL" else "-",
                    "valor_urm": float(valor),
                }

            return self._montar_resultado(tipo_licenca, grupo, composicao, detalhe_consulta,
                                          faixa or self.ROTULO_PORTE.get(porte_n, porte_n))

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
                          porte_ou_faixa: str) -> dict[str, Any]:
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
        if tipo_licenca in ("LIR", "LOR"):
            resultado["regra_aplicada"] = (
                f"Somatório das fases {' + '.join(composicao.keys())} "
                f"({tipo_licenca}) => {total:,.2f} URMs".replace(",", "X").replace(".", ",").replace("X", "."))
        logger.info("Taxa calculada (%s/%s): %.2f URMs", tipo_licenca, grupo, total)
        return resultado

    # ------------------------------------------------------------------
    def calcular_do_parser(self, dados_parser: dict) -> dict[str, Any]:
        """Convenience: extrai os parâmetros direto do JSON da Fase 1 e calcula."""
        emp = dados_parser.get("empreendimento", {})
        pleito = dados_parser.get("pleito", {})
        coords_area = emp.get("area_util_ha") or emp.get("area_intervencao_ha") \
            or emp.get("area_total_ha")
        return self.calcular_taxa(
            tipo_licenca=pleito.get("tipo_licenca", ""),
            porte=emp.get("porte", ""),
            potencial_poluidor=emp.get("potencial_poluidor", ""),
            ramo_atividade=emp.get("ramo_atividade"),
            area_ha=coords_area,
            codram=emp.get("codram"),
        )
