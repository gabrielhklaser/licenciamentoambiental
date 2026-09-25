# -*- coding: utf-8 -*-
"""
FASE 3 - Agente Técnico de Auditoria (híbrido: determinístico + LLM)
=====================================================================

O AuditorTecnico recebe os textos extraídos dos laudos técnicos (PDF via OCR
ou extração direta) e confronta o conteúdo com os TERMOS DE REFERÊNCIA
OFICIAIS da SEMA Campo Bom (publicados em campobom.rs.gov.br), em duas camadas:

CAMADA DETERMINÍSTICA (funções puras - matemática e lógica):
    - Aterro RSCC (TR 2026): sondagem mínima de 3 pontos até 1,0 ha + 1 ponto
      por hectare ou fração excedente; profundidade de investigação >= 3,0 m;
      distância vertical da base do aterro >= 1,50 m acima do nível MÁXIMO do
      lençol (NBR 15113); VEDADO abaixo de 1,0 m; impermeabilização obrigatória
      na faixa 1,0-2,0 m; ensaios de permeabilidade: 2 até 1 ha (+1/ha).
    - Meio Físico / Parcelamento (TR 2025): sondagem mínima de 4 furos até 1 ha
      + 1 por hectare ou fração; ensaios de permeabilidade: 3 até 1 ha (+1/ha).
    - RFO (TR 2026 / COMDEMA 02/2017): 15 mudas (>1 m) por indivíduo NATIVO e
      3 por EXÓTICO suprimido; densidade >= 3.000 mudas/ha; espécies plantadas
      >= metade das suprimidas; monitoramento mínimo de 2 anos (relatórios
      anuais); falha máxima de 10%; ART com previsão mínima de 2 anos.

CAMADA SEMÂNTICA (LLM com saída estruturada Pydantic):
    - PRAD: cronograma físico-financeiro detalhado (item 4.2) + monitoramento
      mínimo de 2 anos (item 5.7) + relatório de execução em 30 dias (5.1);
    - Fauna (LFS): >= 1 método de busca ativa e 1 de busca passiva por grupo
      inventariado, amostragens em primavera/verão e suficiência amostral pela
      curva do coletor;
    - PCA: relatórios trimestrais (supressão de vegetação, afugentamento de
      fauna e movimentação de solo) e semestrais (obras e estruturas).

Também valida por CHECKLIST DE CONTEÚDO os TRs EIV e LCV (itens mínimos).

Arquitetura de LLM: a classe usa a interface ProvedorLLMBase. Por padrão, o
protótipo opera 100% offline com ProvedorLLMHeuristico (regras determinísticas
de palavras-chave, marcadas como origem="heuristico_local"). Em produção,
defina LICENCIA_PROVEDOR_LLM=langchain e a chave da API para acionar o
ProvedorLLMLangChain (chat model com structured output), sem alterar o resto.
"""

from __future__ import annotations

import logging
import math
import os
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Optional, Type

from pydantic import BaseModel, ValidationError

from .calibracao import Calibracao
from .esquemas_tecnicos import (MetricasRFO, MetricasSondagem, OrigemAnalise,
                                ResultadoValidacao, StatusValidacao, VereditoFauna,
                                VereditoPCA, VereditoPRAD)

logger = logging.getLogger("licenciamento.auditor_tecnico")


# ==============================================================================
# Camada de provedores de LLM (abstração - permite trocar sem tocar nos agentes)
# ==============================================================================
class ProvedorLLMBase:
    """Interface mínima de um provedor de análise semântica estruturada."""

    def analisar(self, esquema: Type[BaseModel], instrucao: str, texto: str) -> BaseModel:
        raise NotImplementedError


class ProvedorLLMHeuristico(ProvedorLLMBase):
    """Protótipo OFFLINE: heurísticas determinísticas de palavras-chave.

    Garante que o sistema funcione sem chave de API. As saídas são marcadas
    com origem='heuristico_local' para transparência do licenciador.
    """

    TERMOS_ATIVOS = ["busca ativa", "busca limitada por encontro", "transecto",
                     "pontos de escuta", "escuta ativa", "rede de neblina", "pitfall",
                     "armadilha de intercepta"]
    TERMOS_PASSIVOS = ["armadilha de fossa", "armadilha de queda", "armadilha fotográfica",
                       "gravador automático", "gravadores automáticos", "escuta passiva",
                       "coleta passiva", "bioacústica passiva"]
    TERMOS_CRONOGRAMA = ["cronograma físico-financeiro", "cronograma fisico-financeiro",
                         "cronograma de execução físico-financeiro", "cronograma de desembolso",
                         "cronograma físico financeiro", "cronograma físico e financeiro",
                         "cronograma físico e financeiro detalhado"]

    @staticmethod
    def _norm(texto: str) -> str:
        texto = unicodedata.normalize("NFKD", texto or "")
        texto = "".join(c for c in texto if not unicodedata.combining(c))
        return re.sub(r"\s+", " ", texto).lower()

    def _trecho(self, texto: str, padrao: re.Pattern, janela: int = 220) -> str:
        achou = padrao.search(texto)
        if not achou:
            return ""
        inicio = max(0, achou.start() - 60)
        return re.sub(r"\s+", " ", texto[inicio:achou.end() + janela]).strip()

    def analisar(self, esquema: Type[BaseModel], instrucao: str, texto: str) -> BaseModel:
        t = self._norm(texto)

        if esquema is VereditoPRAD:
            cron_presente = any(termo in t for termo in self.TERMOS_CRONOGRAMA)
            trecho_cron = self._trecho(texto, re.compile(r"cronograma[\w\s-]{0,40}", re.I))
            m = re.search(r"monitoramento[^.;]{0,240}?(\d{1,2})\s*anos", t)
            anos = int(m.group(1)) if m else None
            trecho_mon = self._trecho(texto, re.compile(r"monitoramento[^.;]{0,80}", re.I))
            m_exec = re.search(r"(relat[óo]rio de execu[çc][ãa]o|30\s*dias)", t)
            just = []
            just.append("Cronograma físico-financeiro detalhado identificado."
                        if cron_presente else
                        "Não foi identificado cronograma físico-financeiro detalhado no documento.")
            if anos is not None:
                just.append(f"Monitoramento previsto por {anos} anos.")
            else:
                just.append("Período de monitoramento não identificado no documento.")
            return VereditoPRAD(
                cronograma_fisico_financeiro_presente=cron_presente,
                periodo_monitoramento_anos=anos,
                menciona_relatorio_execucao=bool(m_exec),
                trecho_cronograma=trecho_cron,
                trecho_monitoramento=trecho_mon,
                justificativa=" ".join(just))

        if esquema is VereditoFauna:
            ativos = [termo for termo in self.TERMOS_ATIVOS if termo in t]
            passivos = [termo for termo in self.TERMOS_PASSIVOS if termo in t]
            estacao = bool(re.search(r"\b(primavera|verao)\b", t))
            curva = bool(re.search(r"(curva do coletor|curva de acumulacao|suficiencia amostral)", t))
            trecho = self._trecho(texto, re.compile(r"metodologia|metodologias", re.I))
            just = []
            just.append(f"Busca ativa comprovada ({', '.join(ativos)})." if ativos
                        else "Não comprovado método de BUSCA ATIVA por grupo inventariado.")
            just.append(f"Busca passiva comprovada ({', '.join(passivos)})." if passivos
                        else "Não comprovado método de BUSCA PASSIVA por grupo inventariado.")
            just.append("Amostragem em primavera/verão comprovada." if estacao
                        else "Amostragens em primavera ou verão não comprovadas.")
            if curva:
                just.append("Suficiência amostral pela curva do coletor comprovada.")
            else:
                just.append("Suficiência amostral (curva do coletor) não comprovada.")
            return VereditoFauna(
                metodos_busca_ativa=ativos,
                metodos_busca_passiva=passivos,
                amostragem_primavera_verao=estacao,
                suficiencia_amostral_curva_coletor=curva,
                trecho_metodologia=trecho,
                justificativa=" ".join(just))

        if esquema is VereditoPCA:
            m_sup = re.search(r"supress[aã]o.{0,160}?(trimestral|semestral|mensal|bimestral)", t)
            m_obra = re.search(r"obras?.{0,200}?(trimestral|semestral|mensal|bimestral)", t)
            sup = m_sup.group(1) if m_sup else None
            obra = m_obra.group(1) if m_obra else None
            trecho = self._trecho(texto, re.compile(r"relat[óo]rios?[\s\w]{0,60}", re.I))
            just = (f"Periodicidade supressão/movimentação de solo: {sup or 'não informada'}; "
                    f"periodicidade obras: {obra or 'não informada'}.")
            return VereditoPCA(
                periodicidade_supressao_movimentacao=sup,
                periodicidade_obras=obra,
                trecho_cronograma=trecho,
                justificativa=just)

        raise ValueError(f"Esquema não suportado pelo provedor heurístico: {esquema.__name__}")


class ProvedorLLMLangChain(ProvedorLLMBase):
    """Provedor de PRODUÇÃO: LangChain + modelo de chat com saída estruturada.

    Requer: pip install langchain langchain-openai  (e a variável OPENAI_API_KEY).
    O uso de structured output (Pydantic) garante JSON tipado, evitando alucinações.
    """

    def __init__(self, modelo: str = "gpt-4o-mini", temperatura: float = 0.0):
        try:
            from langchain_openai import ChatOpenAI  # import lazy (dependência opcional)
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "ProvedorLangChain requer 'pip install langchain langchain-openai'.") from exc
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("Defina a variável de ambiente OPENAI_API_KEY.")
        self.llm = ChatOpenAI(model=modelo, temperature=temperatura)

    def analisar(self, esquema: Type[BaseModel], instrucao: str, texto: str) -> BaseModel:
        llm_tipado = self.llm.with_structured_output(esquema)
        mensagens = [("system",
                      "Você é auditor técnico ambiental de um município. Analise o laudo "
                      "estritamente conforme o Termo de Referência indicado e responda "
                      "apenas no esquema estruturado solicitado. Baseie cada campo em "
                      "trechos literais do documento."),
                     ("human", f"INSTRUÇÃO DO TR: {instrucao}\n\nLAUDO:\n{texto[:12000]}")]
        return llm_tipado.invoke(mensagens)


def _fabricar_provedor_padrao() -> ProvedorLLMBase:
    """Seleciona o provedor conforme variáveis de ambiente (protótipo x produção)."""
    if os.getenv("LICENCIA_PROVEDOR_LLM", "").lower() in ("langchain", "openai"):
        try:
            return ProvedorLLMLangChain()
        except RuntimeError as exc:
            logger.warning("Provedor LangChain indisponível (%s). Usando heurístico local.", exc)
    return ProvedorLLMHeuristico()


# ==============================================================================
# AuditorTecnico
# ==============================================================================
class AuditorTecnico:
    """Agente Técnico de Auditoria: regras matemáticas (TR) + validações semânticas.

    Os parâmetros abaixo seguem os TRs OFICIAIS da SEMA Campo Bom e podem ser
    sobrescritos por config/gabarito_trs.json (fonte com trechos e páginas).
    """

    # ---- Parâmetros dos TRs oficiais (valores padrão = gabarito oficial) -----
    # Aterro RSCC (TR 2026, itens 2.3 e 2.4)
    RSCC_SONDAGEM_BASE = 3                  # 3 pontos até 1,0 ha
    RSCC_SONDAGEM_POR_HA = 1                # +1 por hectare ou fração excedente
    RSCC_PROFUNDIDADE_M = 3.0               # profundidade mínima de investigação
    RSCC_DISTANCIA_MINIMA_M = 1.5           # base >= 1,50 m acima do nível máximo do lençol
    RSCC_DISTANCIA_VEDADA_M = 1.0           # vedado dispor a menos de 1,0 m
    RSCC_IMPERM_MIN_M = 1.0                 # faixa de impermeabilização obrigatória
    RSCC_IMPERM_MAX_M = 2.0
    RSCC_ENSAIOS_BASE = 2                   # 2 ensaios de permeabilidade até 1,0 ha
    RSCC_ENSAIOS_POR_HA = 1
    # Meio Físico / Parcelamento (TR 2025, itens 2.1.5 e 2.1.6)
    PARC_SONDAGEM_BASE = 4                  # 4 furos até 1 ha
    PARC_SONDAGEM_POR_HA = 1
    PARC_PROFUNDIDADE_M = 3.0
    PARC_ENSAIOS_BASE = 3
    PARC_ENSAIOS_POR_HA = 1
    # RFO (TR 2026, itens 3.3, 3.7 e 3.9)
    RFO_MUDAS_POR_NATIVO = 15
    RFO_MUDAS_POR_EXOTICO = 3
    RFO_DENSIDADE_MINIMA = 3000.0
    RFO_RAZAO_ESPECIES = 0.5                # espécies plantadas >= metade das suprimidas
    RFO_MONITORAMENTO_ANOS = 2
    RFO_FALHA_MAX_PCT = 10.0
    # PRAD (TR 2026, itens 4.2, 5.1 e 5.7)
    PRAD_MONITORAMENTO_ANOS = 2             # TR oficial (especificação original pedia 4)
    PRAD_EXECUCAO_DIAS = 30
    PRAD_PRIMEIRO_RELATORIO_MESES = 6
    # PCA (TR 2026, item 5.1)
    PCA_TRIMESTRAL = "trimestral"
    PCA_SEMESTRAL = "semestral"

    PARAMETROS_PADRAO = {
        "rscc_distancia_minima_lencol_m": RSCC_DISTANCIA_MINIMA_M,
        "rscc_distancia_vedada_m": RSCC_DISTANCIA_VEDADA_M,
        "rscc_impermeabilizacao_min_m": RSCC_IMPERM_MIN_M,
        "rscc_impermeabilizacao_max_m": RSCC_IMPERM_MAX_M,
        "rscc_sondagem_base": RSCC_SONDAGEM_BASE,
        "rscc_sondagem_por_ha_excedente": RSCC_SONDAGEM_POR_HA,
        "rscc_profundidade_investigacao_m": RSCC_PROFUNDIDADE_M,
        "rscc_ensaios_permeabilidade_base": RSCC_ENSAIOS_BASE,
        "rscc_ensaios_por_ha_excedente": RSCC_ENSAIOS_POR_HA,
        "parcelamento_sondagem_base": PARC_SONDAGEM_BASE,
        "parcelamento_sondagem_por_ha_excedente": PARC_SONDAGEM_POR_HA,
        "parcelamento_profundidade_investigacao_m": PARC_PROFUNDIDADE_M,
        "parcelamento_ensaios_permeabilidade_base": PARC_ENSAIOS_BASE,
        "parcelamento_ensaios_por_ha_excedente": PARC_ENSAIOS_POR_HA,
        "rfo_mudas_por_nativo": RFO_MUDAS_POR_NATIVO,
        "rfo_mudas_por_exotico": RFO_MUDAS_POR_EXOTICO,
        "rfo_densidade_minima_mudas_ha": RFO_DENSIDADE_MINIMA,
        "rfo_razao_minima_especies": RFO_RAZAO_ESPECIES,
        "rfo_monitoramento_minimo_anos": RFO_MONITORAMENTO_ANOS,
        "rfo_falha_maxima_percentual": RFO_FALHA_MAX_PCT,
        "rfo_art_previsao_minima_anos": 2,
        "prad_monitoramento_minimo_anos": PRAD_MONITORAMENTO_ANOS,
        "prad_relatorio_execucao_dias": PRAD_EXECUCAO_DIAS,
        "prad_primeiro_monitoramento_meses": PRAD_PRIMEIRO_RELATORIO_MESES,
        "pca_periodicidade_supressao": PCA_TRIMESTRAL,
        "pca_periodicidade_obras": PCA_SEMESTRAL,
        "fauna_min_busca_ativa_por_grupo": 1,
        "fauna_min_busca_passiva_por_grupo": 1,
        "fauna_suficiencia_curva_coletor": True,
        "fauna_app_entorno_m": 100,
        "fauna_uc_raio_km": 10,
    }

    def __init__(self, provedor_llm: Optional[ProvedorLLMBase] = None,
                 caminho_gabarito: Optional[str] = None):
        self.parametros: dict[str, Any] = dict(self.PARAMETROS_PADRAO)
        self.fonte_gabarito = "TRs oficiais SEMA Campo Bom (padrões internos do código)"
        self.gabarito_revisado = False
        self._aplicar_gabarito(caminho_gabarito)
        self.provedor = provedor_llm or _fabricar_provedor_padrao()

    def _aplicar_gabarito(self, caminho_gabarito: Optional[str] = None) -> None:
        """Sobrescreve os parâmetros com config/gabarito_trs.json (se existir)."""
        import json as _json
        config = Calibracao().gabarito_trs
        if caminho_gabarito:
            caminho = Path(caminho_gabarito)
            config = _json.loads(caminho.read_text(encoding="utf-8")) if caminho.exists() else None
        if not config:
            return
        try:
            parametros = config.get("parametros", {})
            for chave, valor in parametros.items():
                if chave not in self.parametros:
                    continue
                # formatos aceitos: valor direto OU draft da ingestão
                # ({'valor': X, 'trecho': ..., 'pagina': ...})
                if isinstance(valor, dict) and "valor" in valor:
                    valor = valor["valor"]
                self.parametros[chave] = valor
            self.fonte_gabarito = config.get("fonte", "config/gabarito_trs.json")
            self.gabarito_revisado = bool(config.get("revisado", False))
            logger.info("Gabarito de TRs calibrado via %s (revisado=%s)",
                        self.fonte_gabarito, self.gabarito_revisado)
        except Exception as exc:  # noqa: BLE001
            logger.error("Falha ao aplicar gabarito de TRs: %s (usando padrões)", exc)

    # ==========================================================================
    # Extração de texto dos laudos (PDF/texto; gancho para OCR)
    # ==========================================================================
    @staticmethod
    def extrair_texto(nome_arquivo: str, conteudo: bytes | str) -> str:
        """Extrai texto de .pdf (pypdf) ou .txt. PDFs digitalizados exigem OCR.

        Para laudos escaneados, instale 'pytesseract' + 'pdf2image' e integre o
        OCR no ponto marcado abaixo; sem OCR, o resultado cai em REVISAO_MANUAL.
        """
        try:
            if nome_arquivo.lower().endswith(".pdf"):
                # LeitorPDF: texto nativo + OCR para PDFs digitalizados
                from licenciamento.leitor_pdf import LeitorPDF
                bruto = conteudo if isinstance(conteudo, bytes) \
                    else conteudo.encode()
                texto, _info = LeitorPDF.extrair(bruto)
                if not texto.strip():
                    logger.warning("PDF sem texto legível (escaneado e OCR "
                                   "indisponível) - %s", nome_arquivo)
                return texto
            return conteudo.decode("utf-8", errors="replace") if isinstance(conteudo, bytes) \
                else str(conteudo)
        except Exception:  # noqa: BLE001
            logger.exception("Falha ao extrair texto de %s", nome_arquivo)
            return ""

    # ==========================================================================
    # CAMADA DETERMINÍSTICA - extração de parâmetros numéricos dos laudos
    # ==========================================================================
    @staticmethod
    def _num(padrao: re.Pattern, texto: str) -> Optional[float]:
        """Extrai número tratando o padrão brasileiro ('1.900' / '2,40' / '3.000,5')."""
        achou = padrao.search(texto)
        if not achou:
            return None
        bruto = achou.group(1).strip()
        try:
            if "," in bruto:  # vírgula decimal: pontos são separadores de milhar
                bruto = bruto.replace(".", "").replace(",", ".")
            elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", bruto):
                bruto = bruto.replace(".", "")  # ponto de milhar
            return float(bruto)
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _fmt_br(valor: Optional[float], casas: int = 0) -> str:
        """Formata número no padrão brasileiro para as mensagens de justificativa."""
        if valor is None:
            return "n/d"
        texto = f"{valor:,.{casas}f}"
        return texto.replace(",", "X").replace(".", ",").replace("X", ".")

    @staticmethod
    def _detectar_contexto_sondagem(texto: str, nome_documento: Optional[str] = None) -> str:
        """Define o gabarito de sondagem: 'RSCC' (aterro) ou 'PARCELAMENTO'."""
        if nome_documento:
            nd = ProvedorLLMHeuristico._norm(nome_documento)
            if any(k in nd for k in ["parcelamento", "loteamento", "laudo geologico", "desmembramento", "condominio"]):
                return "PARCELAMENTO"
            if any(k in nd for k in ["aterro de rscc", "aterro sanitario"]):
                return "RSCC"
        t = ProvedorLLMHeuristico._norm(texto)
        if any(k in t for k in ["parcelamento", "loteamento", "condominio", "laudo geologico",
                                "desmembramento", "gleba a ser parcelada"]):
            return "PARCELAMENTO"
        if any(k in t for k in ["aterro de rscc", "aterro sanitario", "residuos da construcao civil",
                                "células de deposicao", "celulas de deposicao"]):
            return "RSCC"
        return "PARCELAMENTO"

    def extrair_parametros_sondagem(self, texto: str,
                                    contexto: Optional[str] = None) -> MetricasSondagem:
        """Extrai lençol, cota base, área, sondagens, profundidade e ensaios."""
        contexto = contexto or self._detectar_contexto_sondagem(texto)

        prof = self._num(re.compile(
            r"len[çc]ol\s+fre[áa]tico[^.;\d]{0,120}?(-?\d{1,2}[.,]?\d{0,2})\s*m", re.I), texto) \
            or self._num(re.compile(
                r"profundidade\s+do\s+len[çc]ol[^.;\d]{0,60}?(-?\d{1,2}[.,]?\d{0,2})\s*m", re.I), texto)
        cota = self._num(re.compile(
            r"cota\s+base[^.;\d\-]{0,80}?(-?\d{1,2}[.,]?\d{0,2})", re.I), texto) \
            or self._num(re.compile(
                r"base\s+do\s+aterro[^.;\d\-]{0,80}?(-?\d{1,2}[.,]?\d{0,2})", re.I), texto)
        area = self._num(re.compile(
            r"[áa]rea[^.;\d]{0,60}?(\d{1,3}[.,]?\d{0,2})\s*(?:ha|hectare)", re.I), texto)
        furos = self._num(re.compile(
            r"(\d{1,2})\s*(?:furos?|pontos?|trincheiras?)\s+(?:de\s+)?(?:sondagem|investiga)", re.I), texto) \
            or self._num(re.compile(
                r"(?:sondagens?|trincheiras?|furos?)[^.;\d]{0,40}?(\d{1,2})\s*(?:pontos?|furos?)?", re.I), texto)
        dist_info = self._num(re.compile(
            r"dist[âa]ncia\s+vertical[^.;\d]{0,100}?(\d{1,2}[.,]?\d{0,2})\s*m", re.I), texto)
        prof_inv = self._num(re.compile(
            r"(?:profundidade|profundidades)[^.;]{0,60}?(\d{1,2}[.,]?\d{0,2})\s*m(?:etros)?\s+"
            r"de\s+(?:profundidade|investiga)", re.I), texto) \
            or self._num(re.compile(
                r"(?:sondagem|investiga[çc][ãa]o|trincheira)s?[^.;]{0,80}?(\d{1,2}[.,]?\d{0,2})\s*"
                r"m(?:etros)?\s+de\s+profundidade", re.I), texto)
        ensaios = self._num(re.compile(
            r"(\d{1,2})\s*ensaios?\s+de\s+permeabilidade", re.I), texto) \
            or self._num(re.compile(
                r"ensaios?\s+de\s+permeabilidade[^.;\d]{0,40}?(\d{1,2})", re.I), texto)
        imperm = bool(re.search(r"impermeabiliza|argila\s+compactada", texto, re.I))

        # Localização de página conforme GabeBrain
        from .leitor_pdf import LeitorPDF
        m_evid = re.search(r"len[çc]ol\s+fre[áa]tico|cota\s+base|sondagem|furos?", texto, re.I)
        pag = LeitorPDF.localizar_pagina(texto, m_evid.start()) if m_evid else None

        return MetricasSondagem(
            contexto=contexto,
            profundidade_lencol_m=prof, cota_base_aterro_m=cota,
            distancia_vertical_informada_m=dist_info, area_ha=area,
            furos_informados=int(furos) if furos is not None else None,
            profundidade_investigacao_m=prof_inv,
            ensaios_permeabilidade_informados=int(ensaios) if ensaios is not None else None,
            impermeabilizacao_prevista=imperm if (imperm or "aterro" in ProvedorLLMHeuristico._norm(texto)) else None,
            pagina_referencia=pag)

    @staticmethod
    def _densidade_proposta(texto: str) -> Optional[float]:
        """Extrai a densidade de plantio PROPOSTA (mudas/ha), ignorando valores
        citados como mínimo normativo (ex.: laudo que transcreve o TR:
        'densidade mínima de 3.000 mudas/hectare')."""
        padrao = re.compile(
            r"(\d{1,3}(?:[.]\d{3})*(?:[.,]\d+)?)\s*mudas?\s*(?:/|por\s+)?\s*(?:ha|hectare)", re.I)
        for achou in padrao.finditer(texto):
            contexto_anterior = texto[max(0, achou.start() - 45):achou.start()].lower()
            if re.search(r"m[íi]nim", contexto_anterior):
                continue  # mínimo normativo citado - não é a proposta
            bruto = achou.group(1)
            if "," in bruto:
                bruto = bruto.replace(".", "").replace(",", ".")
            else:
                bruto = bruto.replace(".", "")
            return float(bruto)
        return None

    def extrair_parametros_rfo(self, texto: str) -> MetricasRFO:
        """Extrai supressões, mudas, densidade, espécies e monitoramento do laudo RFO."""
        nativos = self._num(re.compile(
            r"(\d{1,5})\s*indiv[íi]duos?\s+nativos", re.I), texto) \
            or self._num(re.compile(
                r"suprimidos?[^.;]{0,80}?(\d{1,5})[^.;]{0,40}?nativos?", re.I), texto)
        exoticos = self._num(re.compile(
            r"(\d{1,5})\s*indiv[íi]duos?\s+ex[óo]ticos", re.I), texto) \
            or self._num(re.compile(
                r"suprimidos?[^.;]{0,80}?(\d{1,5})[^.;]{0,40}?ex[óo]ticos?", re.I), texto)
        mudas_nativas = self._num(re.compile(
            r"(\d{1,3}(?:[.]\d{3})*)\s*mudas?\s+nativas", re.I), texto)
        mudas_exoticas = self._num(re.compile(
            r"(\d{1,3}(?:[.]\d{3})*)\s*mudas?\s+de\s+(?:esp[ée]cies\s+)?ex[óo]ticas?", re.I), texto)
        densidade = self._densidade_proposta(texto)
        especies_plantadas = self._num(re.compile(
            r"(\d{1,3})\s*esp[ée]cies?[^\n.;]{0,80}?(?:plantio|plantad|utilizadas|compensa|reposi)", re.I), texto)
        especies_suprimidas = self._num(re.compile(
            r"(\d{1,3})\s*esp[ée]cies?[^\n.;]{0,60}?suprimid", re.I), texto)
        monitoramento = self._num(re.compile(
            r"monitorament[^\n.;]{0,120}?(\d{1,2})\s*anos", re.I), texto)
        falha = self._num(re.compile(
            r"(\d{1,2})\s*%?\s*de\s*falha", re.I), texto) \
            or self._num(re.compile(r"falha[^\n.;]{0,40}?(\d{1,2})\s*%", re.I), texto)

        from .leitor_pdf import LeitorPDF
        m_rfo = re.search(r"(\d{1,5})\s*indiv[íi]duos?\s+nativos|mudas?\s+nativas|reposicao\s+florestal", texto, re.I)
        pag = LeitorPDF.localizar_pagina(texto, m_rfo.start()) if m_rfo else None

        return MetricasRFO(
            nativos_suprimidos=int(nativos) if nativos is not None else None,
            exoticos_suprimidos=int(exoticos) if exoticos is not None else None,
            mudas_nativas_propostas=int(mudas_nativas) if mudas_nativas is not None else None,
            mudas_exoticas_propostas=int(mudas_exoticas) if mudas_exoticas is not None else None,
            densidade_proposta_mudas_ha=densidade,
            especies_plantadas=int(especies_plantadas) if especies_plantadas is not None else None,
            especies_suprimidas=int(especies_suprimidas) if especies_suprimidas is not None else None,
            monitoramento_anos=int(monitoramento) if monitoramento is not None else None,
            percentual_falha_admitido=falha,
            pagina_referencia=pag)

    # ==========================================================================
    # CAMADA DETERMINÍSTICA - regras puras (funções testáveis)
    # ==========================================================================
    def calcular_pontos_sondagem_exigidos(self, area_ha: float,
                                          contexto: str = "RSCC") -> int:
        """TR: pontos/furos de sondagem mínimos conforme a área e o contexto.

        RSCC (TR Aterro 2026, 2.3): 3 pontos até 1,0 ha + 1 por hectare ou fração;
        Parcelamento (TR Meio Físico 2025, 2.1.5.2): 4 furos até 1 ha + 1 por ha
        ou fração que ultrapasse 1 ha.
        """
        if contexto == "PARCELAMENTO":
            base = int(self.parametros["parcelamento_sondagem_base"])
            por_ha = int(self.parametros["parcelamento_sondagem_por_ha_excedente"])
        else:
            base = int(self.parametros["rscc_sondagem_base"])
            por_ha = int(self.parametros["rscc_sondagem_por_ha_excedente"])
        if area_ha <= 1.0:
            return base
        hectares_excedentes = math.ceil(area_ha - 1.0)  # 'cada hectare ou fração'
        return base + hectares_excedentes * por_ha

    # Compatibilidade com chamadas antigas dos testes (contexto RSCC por padrão)
    def calcular_furos_exigidos(self, area_ha: float, contexto: str = "RSCC") -> int:
        return self.calcular_pontos_sondagem_exigidos(area_ha, contexto)

    def calcular_ensaios_permeabilidade_exigidos(self, area_ha: float,
                                                 contexto: str = "RSCC") -> int:
        """TR: ensaios de permeabilidade mínimos (RSCC: 2 até 1 ha; Parcelamento: 3)."""
        if contexto == "PARCELAMENTO":
            base = int(self.parametros["parcelamento_ensaios_permeabilidade_base"])
            por_ha = int(self.parametros["parcelamento_ensaios_por_ha_excedente"])
        else:
            base = int(self.parametros["rscc_ensaios_permeabilidade_base"])
            por_ha = int(self.parametros["rscc_ensaios_por_ha_excedente"])
        if area_ha <= 1.0:
            return base
        return base + math.ceil(area_ha - 1.0) * por_ha

    def calcular_mudas_exigidas(self, nativos: int, exoticos: int) -> int:
        """TR RFO: mudas por nativo suprimido + mudas por exótico suprimido."""
        return nativos * int(self.parametros["rfo_mudas_por_nativo"]) \
            + exoticos * int(self.parametros["rfo_mudas_por_exotico"])

    # --------------------------------------------------------------------------
    def validar_sondagem_aterramento(self, nome_documento: str,
                                     metricas: MetricasSondagem) -> ResultadoValidacao:
        """Valida Meio Físico conforme o CONTEXTO do laudo (RSCC ou PARCELAMENTO).

        RSCC (TR Aterro 2026): lençol >= 1,50 m (vedado < 1,0 m), impermeabilização
        obrigatória na faixa 1,0-2,0 m, sondagens >= 3 + 1/ha, ensaios >= 2 + 1/ha,
        profundidade >= 3,0 m.
        PARCELAMENTO (TR Meio Físico 2025): sondagens >= 4 + 1/ha, ensaios >= 3 + 1/ha,
        profundidade >= 3,0 m (sem regra de distância do lençol).
        """
        prefixo = "parcelamento" if metricas.contexto == "PARCELAMENTO" else "rscc"
        norma = ("TR Meio Físico - Aterro RSCC (sondagem/lençol - 2026)"
                 if prefixo == "rscc" else
                 "TR Meio Físico - Laudo Geológico Parcelamento (2025)")

        # distância vertical: prioriza a informada no laudo; senão calcula
        if metricas.distancia_vertical_informada_m is not None:
            metricas.distancia_vertical_m = metricas.distancia_vertical_informada_m
        elif metricas.profundidade_lencol_m is not None and metricas.cota_base_aterro_m is not None:
            metricas.distancia_vertical_m = round(
                metricas.profundidade_lencol_m - metricas.cota_base_aterro_m, 2)

        reprovados: list[str] = []
        trechos: list[str] = []

        # ---- Distância do lençol (exclusivo do contexto RSCC) ---------------
        if prefixo == "rscc":
            if metricas.distancia_vertical_m is None:
                return ResultadoValidacao(
                    documento_analisado=nome_documento, norma_tr=norma,
                    status=StatusValidacao.REVISAO_MANUAL,
                    itens_reprovados=["Parâmetros de sondagem (lençol freático/cota base) não "
                                      "localizados no texto - conferência manual necessária."],
                    metricas=metricas.model_dump(), origem=OrigemAnalise.DETERMINISTICO)

            d = metricas.distancia_vertical_m
            vedada = float(self.parametros["rscc_distancia_vedada_m"])
            minima = float(self.parametros["rscc_distancia_minima_lencol_m"])
            imp_min = float(self.parametros["rscc_impermeabilizacao_min_m"])
            imp_max = float(self.parametros["rscc_impermeabilizacao_max_m"])

            if d < vedada:
                reprovados.append(
                    f"IMPOSSIBILIDADE DE IMPLANTAÇÃO: distância vertical entre a base do aterro e o "
                    f"lençol freático de {self._fmt_br(d, 2)} m é INFERIOR a {self._fmt_br(vedada, 1)} m, "
                    f"hipótese terminantemente PROIBIDA pelo TR Aterro RSCC (item 2.4) e pela NBR 15113.")
            elif d < minima:
                reprovados.append(
                    f"Furo de sondagem insuficiente conforme exigência técnica mínima: distância "
                    f"vertical de {self._fmt_br(d, 2)} m é inferior ao mínimo de "
                    f"{self._fmt_br(minima, 1)} m acima do nível MÁXIMO do lençol freático "
                    f"(TR Aterro RSCC, item 2.4 / NBR 15113).")
            trechos.append(f"distância vertical: {self._fmt_br(d, 2)} m "
                           f"(mínimo {self._fmt_br(minima, 1)} m)")

            # Impermeabilização obrigatória na faixa 1,0-2,0 m
            if imp_min <= d < imp_max and metricas.impermeabilizacao_prevista is not True:
                reprovados.append(
                    f"IMPERMEABILIZAÇÃO OBRIGATÓRIA: com distância vertical de "
                    f"{self._fmt_br(d, 2)} m (faixa entre {self._fmt_br(imp_min, 1)} e "
                    f"{self._fmt_br(imp_max, 1)} m), o TR Aterro RSCC (item 2.4) exige "
                    f"impermeabilização da base com camada de argila compactada de no mínimo "
                    f"20 cm (k entre 10⁻⁶ e 10⁻⁷ cm/s) e o laudo não comprova sua previsão.")

        # ---- Sondagens (nº de pontos/furos) ---------------------------------
        if metricas.area_ha is not None:
            exigidos = self.calcular_pontos_sondagem_exigidos(metricas.area_ha, metricas.contexto)
            if metricas.furos_informados is None:
                reprovados.append("Quantidade de pontos de sondagem/trincheiras não informada no laudo.")
            elif metricas.furos_informados < exigidos:
                reprovados.append(
                    f"Quantidade de pontos de sondagem insuficiente: informados "
                    f"{metricas.furos_informados} pontos para área de {self._fmt_br(metricas.area_ha, 2)} ha "
                    f"(exigência do TR: {exigidos} pontos - base {self._fmt_br(exigidos - (0 if metricas.area_ha <= 1.0 else 0))} "
                    f"+ 1 por hectare ou fração excedente).")
            trechos.append(f"pontos de sondagem: {metricas.furos_informados or 'n/d'} "
                           f"(exigidos: {exigidos})")

            # ---- Profundidade de investigação (>= 3,0 m em ambos os TRs) ----
            prof_min = float(self.parametros[f"{prefixo}_profundidade_investigacao_m"])
            if metricas.profundidade_investigacao_m is not None \
                    and metricas.profundidade_investigacao_m < prof_min:
                reprovados.append(
                    f"Profundidade de investigação insuficiente: {self._fmt_br(metricas.profundidade_investigacao_m, 1)} m "
                    f"(mínimo de {self._fmt_br(prof_min, 1)} m, ou até o nível freático/embasamento).")

            # ---- Ensaios de permeabilidade -----------------------------------
            exigidos_ens = self.calcular_ensaios_permeabilidade_exigidos(
                metricas.area_ha, metricas.contexto)
            if metricas.ensaios_permeabilidade_informados is None:
                reprovados.append(
                    f"Ensaios de permeabilidade não identificados no laudo - mínimo de {exigidos_ens} "
                    f"ensaios para áreas até 1,0 ha (+1 por hectare ou fração excedente), conforme "
                    f"NBR 7229/13969.")
            elif metricas.ensaios_permeabilidade_informados < exigidos_ens:
                reprovados.append(
                    f"Ensaios de permeabilidade insuficientes: informados "
                    f"{metricas.ensaios_permeabilidade_informados} (exigidos: {exigidos_ens}).")
            trechos.append(f"ensaios de permeabilidade: "
                           f"{metricas.ensaios_permeabilidade_informados or 'n/d'} (exigidos: {exigidos_ens})")
        else:
            reprovados.append("Área do projeto (ha) não identificada no laudo - não é possível "
                              "verificar o critério amostral de sondagens e ensaios.")

        trecho_ref = " | ".join(t for t in trechos if t)
        if metricas.pagina_referencia:
            trecho_ref = f"[pág. {metricas.pagina_referencia}] {trecho_ref}"

        return ResultadoValidacao(
            documento_analisado=nome_documento,
            norma_tr=norma,
            status=StatusValidacao.CONFORME if not reprovados else StatusValidacao.PENDENTE,
            itens_reprovados=reprovados,
            trecho_referencia=trecho_ref,
            pagina_referencia=metricas.pagina_referencia,
            metricas=metricas.model_dump(),
            origem=OrigemAnalise.DETERMINISTICO)

    # --------------------------------------------------------------------------
    def validar_rfo(self, nome_documento: str, metricas: MetricasRFO) -> ResultadoValidacao:
        """Valida RFO (TR 2026): mudas 15/3, densidade 3.000/ha, espécies >= 1/2 das
        suprimidas, monitoramento >= 2 anos e falha <= 10%."""
        reprovados: list[str] = []
        trechos: list[str] = []

        if metricas.nativos_suprimidos is None and metricas.exoticos_suprimidos is None:
            return ResultadoValidacao(
                documento_analisado=nome_documento,
                norma_tr="TR Reposição Florestal Obrigatória (RFO)",
                status=StatusValidacao.REVISAO_MANUAL,
                itens_reprovados=["Não foi possível extrair os indivíduos suprimidos do laudo - "
                                  "conferência manual necessária."],
                metricas=metricas.model_dump(), origem=OrigemAnalise.DETERMINISTICO)

        nativos = metricas.nativos_suprimidos or 0
        exoticos = metricas.exoticos_suprimidos or 0
        metricas.mudas_exigidas = self.calcular_mudas_exigidas(nativos, exoticos)
        propostas = (metricas.mudas_nativas_propostas or 0) + (metricas.mudas_exoticas_propostas or 0)

        # ---- 1) Quantidade de mudas (15/nativo + 3/exótico) --------------
        if propostas < metricas.mudas_exigidas:
            reprovados.append(
                f"Quantidade de mudas insuficiente: propostas {self._fmt_br(propostas)} mudas "
                f"para {nativos} indivíduos nativos e {exoticos} exóticos suprimidos "
                f"(exigência do TR RFO/COMDEMA 02/2017: 15 mudas > 1 m por nativo + 3 por "
                f"exótico = {self._fmt_br(metricas.mudas_exigidas)} mudas).")
        trechos.append(f"proposta: {self._fmt_br(propostas)} mudas "
                       f"(exigidas: {self._fmt_br(metricas.mudas_exigidas)})")

        # ---- 2) Densidade >= 3.000 mudas/ha ------------------------------
        if metricas.densidade_proposta_mudas_ha is not None:
            dens_min = float(self.parametros["rfo_densidade_minima_mudas_ha"])
            if metricas.densidade_proposta_mudas_ha < dens_min:
                reprovados.append(
                    f"Densidade de plantio abaixo do mínimo técnico: proposta de "
                    f"{self._fmt_br(metricas.densidade_proposta_mudas_ha)} mudas/hectare, sendo "
                    f"exigido no mínimo {self._fmt_br(dens_min)} mudas/hectare (TR RFO, item 3.9).")
            trechos.append(f"densidade proposta: "
                           f"{self._fmt_br(metricas.densidade_proposta_mudas_ha)} mudas/ha")

        # ---- 3) Diversidade: espécies plantadas >= metade das suprimidas --
        razao = float(self.parametros["rfo_razao_minima_especies"])
        if metricas.especies_suprimidas and metricas.especies_plantadas is not None:
            minimo_esp = math.ceil(metricas.especies_suprimidas * razao)
            if metricas.especies_plantadas < minimo_esp:
                reprovados.append(
                    f"Diversidade de espécies insuficiente: propostas {metricas.especies_plantadas} "
                    f"espécies para {metricas.especies_suprimidas} espécies suprimidas - o TR RFO "
                    f"(item 3.3) exige no mínimo a metade ({minimo_esp} espécies), nativas do Bioma "
                    f"Mata Atlântica com distribuição natural na região.")
            trechos.append(f"espécies: {metricas.especies_plantadas} plantadas / "
                           f"{metricas.especies_suprimidas} suprimidas")

        # ---- 4) Monitoramento mínimo (2 anos, relatórios anuais) ---------
        mon_min = int(self.parametros["rfo_monitoramento_minimo_anos"])
        if metricas.monitoramento_anos is None:
            reprovados.append(
                f"Período de monitoramento não comprovado no projeto - o TR RFO (item 3.7) exige "
                f"monitoramento por no mínimo {mon_min} anos com envio ANUAL de relatórios técnicos.")
        elif metricas.monitoramento_anos < mon_min:
            reprovados.append(
                f"Monitoramento insuficiente: proposto {metricas.monitoramento_anos} ano(s), sendo "
                f"exigido no mínimo {mon_min} anos (TR RFO, item 3.7).")
        trechos.append(f"monitoramento: {metricas.monitoramento_anos or 'n/d'} anos "
                       f"(mínimo {mon_min})")

        # ---- 5) Percentual de falha admitido (<= 10%) ---------------------
        falha_max = float(self.parametros["rfo_falha_maxima_percentual"])
        if metricas.percentual_falha_admitido is not None \
                and metricas.percentual_falha_admitido > falha_max:
            reprovados.append(
                f"Percentual de falha admitido ({self._fmt_br(metricas.percentual_falha_admitido)}%) "
                f"acima do máximo de {self._fmt_br(falha_max)}% estabelecido no TR RFO (item 3.7).")

        trecho_ref = " | ".join(t for t in trechos if t)
        if metricas.pagina_referencia:
            trecho_ref = f"[pág. {metricas.pagina_referencia}] {trecho_ref}"

        return ResultadoValidacao(
            documento_analisado=nome_documento,
            norma_tr="TR Reposição Florestal Obrigatória (RFO)",
            status=StatusValidacao.CONFORME if not reprovados else StatusValidacao.PENDENTE,
            itens_reprovados=reprovados,
            trecho_referencia=trecho_ref,
            pagina_referencia=metricas.pagina_referencia,
            metricas=metricas.model_dump(),
            origem=OrigemAnalise.DETERMINISTICO)

    # ==========================================================================
    # CAMADA SEMÂNTICA - LLM com saída estruturada (Pydantic)
    # ==========================================================================
    def _concluir_llm(self, nome_documento: str, norma_tr: str,
                      veredito: BaseModel, reprovacao: str | list[str],
                      texto: Optional[str] = None) -> ResultadoValidacao:
        dados = veredito.model_dump()
        if isinstance(reprovacao, list):
            itens = [r.strip() for r in reprovacao if r and r.strip()]
            conforme = not itens
        else:
            conforme = not reprovacao
            itens = [] if conforme else [reprovacao.strip()]

        trecho = (dados.get("trecho_cronograma") or dados.get("trecho_metodologia")
                  or dados.get("trecho_monitoramento") or "")
        pag = None
        if texto and trecho:
            from .leitor_pdf import LeitorPDF
            pag = LeitorPDF.localizar_pagina(texto, trecho)
            if pag is not None and f"[pág. {pag}]" not in trecho:
                trecho = f"[pág. {pag}] {trecho}"

        return ResultadoValidacao(
            documento_analisado=nome_documento,
            norma_tr=norma_tr,
            status=StatusValidacao.CONFORME if conforme else StatusValidacao.PENDENTE,
            itens_reprovados=itens,
            trecho_referencia=trecho,
            pagina_referencia=pag,
            metricas=dados,
            origem=OrigemAnalise.LLM if isinstance(self.provedor, ProvedorLLMLangChain)
            else OrigemAnalise.HEURISTICO_LOCAL)

    def validar_prad(self, nome_documento: str, texto: str) -> ResultadoValidacao:
        """TR PRAD (2026): cronograma físico-financeiro (4.2), monitoramento mínimo
        de 2 anos (5.7) e relatório de execução em 30 dias (5.1)."""
        instrucao = ("Verifique se existe cronograma físico e financeiro detalhado (item 4.2), "
                     "o período de monitoramento em anos e se prevê relatório de execução no "
                     "prazo de 30 dias (item 5.1).")
        try:
            veredito: VereditoPRAD = self.provedor.analisar(VereditoPRAD, instrucao, texto)
        except (ValidationError, NotImplementedError) as exc:
            logger.exception("Falha na análise PRAD: %s", exc)
            return ResultadoValidacao(
                documento_analisado=nome_documento, norma_tr="TR PRAD - Áreas Degradadas",
                status=StatusValidacao.REVISAO_MANUAL,
                itens_reprovados=[f"Falha na análise semântica: {exc}"],
                origem=OrigemAnalise.HEURISTICO_LOCAL)

        minimo_anos = int(self.parametros["prad_monitoramento_minimo_anos"])
        reprovacoes: list[str] = []
        if not veredito.cronograma_fisico_financeiro_presente:
            reprovacoes.append("Ausência de cronograma físico e financeiro detalhado (TR PRAD, item 4.2).")
        if veredito.periodo_monitoramento_anos is None:
            reprovacoes.append(f"Período de monitoramento não identificado - mínimo exigido: {minimo_anos} anos (TR PRAD, item 5.7).")
        elif veredito.periodo_monitoramento_anos < minimo_anos:
            reprovacoes.append(f"Período de monitoramento proposto ({veredito.periodo_monitoramento_anos} anos) inferior ao mínimo de {minimo_anos} anos (TR PRAD, item 5.7).")
        return self._concluir_llm(nome_documento, "TR PRAD - Áreas Degradadas",
                                  veredito, reprovacoes, texto=texto)

    def validar_fauna(self, nome_documento: str, texto: str) -> ResultadoValidacao:
        """TR Laudo de Fauna Silvestre: busca ativa + passiva por grupo, primavera/
        verão e suficiência amostral pela curva do coletor."""
        instrucao = ("Verifique se a metodologia comprova, por grupo inventariado, no mínimo um "
                     "método de busca ativa e um de busca passiva, amostragens em primavera ou "
                     "verão e a determinação da suficiência amostral pela curva do coletor.")
        try:
            veredito: VereditoFauna = self.provedor.analisar(VereditoFauna, instrucao, texto)
        except (ValidationError, NotImplementedError) as exc:
            logger.exception("Falha na análise de Fauna: %s", exc)
            return ResultadoValidacao(
                documento_analisado=nome_documento, norma_tr="TR Laudo de Fauna Silvestre (LFS)",
                status=StatusValidacao.REVISAO_MANUAL,
                itens_reprovados=[f"Falha na análise semântica: {exc}"],
                origem=OrigemAnalise.HEURISTICO_LOCAL)

        reprovacoes: list[str] = []
        if not veredito.metodos_busca_ativa:
            reprovacoes.append("Metodologia não comprova uso de método de busca ativa por grupo inventariado (TR LFS).")
        if not veredito.metodos_busca_passiva:
            reprovacoes.append("Metodologia não comprova uso de método de busca passiva por grupo inventariado (TR LFS).")
        if not veredito.amostragem_primavera_verao:
            reprovacoes.append("Amostragens em pelo menos um período de primavera ou verão não comprovadas no laudo (TR LFS).")
        if self.parametros.get("fauna_suficiencia_curva_coletor") \
                and not veredito.suficiencia_amostral_curva_coletor:
            reprovacoes.append("Suficiência amostral não determinada pela estabilização da curva do coletor, conforme exige o TR LFS.")
        return self._concluir_llm(nome_documento, "TR Laudo de Fauna Silvestre (LFS)",
                                  veredito, reprovacoes, texto=texto)

    def validar_pca(self, nome_documento: str, texto: str) -> ResultadoValidacao:
        """TR PCA (2026, item 5.1): relatórios trimestrais na supressão de vegetação,
        afugentamento de fauna e movimentação de solo; semestrais nas obras."""
        instrucao = ("Verifique se o cronograma de relatórios estipula periodicidade trimestral "
                     "para supressão de vegetação, afugentamento de fauna e movimentação de solo, "
                     "e semestral para as fases de implantação de obras e estruturas.")
        try:
            veredito: VereditoPCA = self.provedor.analisar(VereditoPCA, instrucao, texto)
        except (ValidationError, NotImplementedError) as exc:
            logger.exception("Falha na análise do PCA: %s", exc)
            return ResultadoValidacao(
                documento_analisado=nome_documento, norma_tr="TR Plano de Controle Ambiental",
                status=StatusValidacao.REVISAO_MANUAL,
                itens_reprovados=[f"Falha na análise semântica: {exc}"],
                origem=OrigemAnalise.HEURISTICO_LOCAL)

        reprovacoes: list[str] = []
        if (veredito.periodicidade_supressao_movimentacao or "").lower() \
                != str(self.parametros["pca_periodicidade_supressao"]).lower():
            reprovacoes.append(
                f"Periodicidade dos relatórios na fase de supressão/movimentação de solo "
                f"'{veredito.periodicidade_supressao_movimentacao or 'não informada'}', "
                f"sendo exigido '{self.parametros['pca_periodicidade_supressao']}' pelo TR PCA (item 5.1)."
            )
        if (veredito.periodicidade_obras or "").lower() \
                != str(self.parametros["pca_periodicidade_obras"]).lower():
            reprovacoes.append(
                f"Periodicidade dos relatórios na fase de implantação de obras "
                f"'{veredito.periodicidade_obras or 'não informada'}', sendo "
                f"exigido '{self.parametros['pca_periodicidade_obras']}' pelo TR PCA (item 5.1)."
            )
        return self._concluir_llm(nome_documento, "TR Plano de Controle Ambiental (PCA)",
                                  veredito, reprovacoes, texto=texto)

    # ==========================================================================
    # Validação genérica por CHECKLIST DE CONTEÚDO (EIV, LCV e demais TRs)
    # ==========================================================================
    def validar_checklist_tr(self, nome_documento: str, norma_tr: str, texto: str,
                             itens: dict[str, list[str]]) -> ResultadoValidacao:
        """Verifica a presença dos itens mínimos de conteúdo do TR no documento.

        Cada item possui palavras-chave alternativas; um item é considerado
        atendido quando qualquer palavra-chave aparece no texto normalizado.
        """
        t = ProvedorLLMHeuristico._norm(texto)
        ausentes: list[str] = []
        for item, palavras in itens.items():
            if not any(p in t for p in palavras):
                ausentes.append(item)

        # trecho de referência: primeira ocorrência reconhecida (contexto)
        trecho = ""
        pag = None
        for item, palavras in itens.items():
            for p in palavras:
                idx = t.find(p)
                if idx >= 0:
                    trecho = re.sub(r"\s+", " ", texto[max(0, idx - 40):idx + 160]).strip()
                    from .leitor_pdf import LeitorPDF
                    pag = LeitorPDF.localizar_pagina(texto, idx)
                    if pag is not None:
                        trecho = f"[pág. {pag}] {trecho}"
                    break
            if trecho:
                break

        if not ausentes:
            status, itens_reprovados = StatusValidacao.CONFORME, []
        elif len(ausentes) < len(itens):
            status = StatusValidacao.PENDENTE
            itens_reprovados = [f"Item obrigatório do {norma_tr} não identificado: {a}."
                                for a in ausentes]
        else:
            status = StatusValidacao.REVISAO_MANUAL
            itens_reprovados = [f"Nenhum item do conteúdo mínimo do {norma_tr} foi reconhecido "
                                 f"no documento - conferência manual necessária."]
        return ResultadoValidacao(
            documento_analisado=nome_documento, norma_tr=norma_tr,
            status=status, itens_reprovados=itens_reprovados,
            trecho_referencia=trecho,
            pagina_referencia=pag,
            metricas={"itens_ausentes": ausentes},
            origem=OrigemAnalise.HEURISTICO_LOCAL)

    # ==========================================================================
    # Roteamento: audita um laudo aplicando os TRs aplicáveis ao seu conteúdo
    # ==========================================================================
    def _rotear_trs(self, texto: str, nome_documento: Optional[str] = None,
                    tipo_documento: Optional[str] = None) -> list[str]:
        """Identifica o TR aplicável ao documento (no máximo UM TR por arquivo).

        Cada documento técnico em licenciamento ambiental corresponde a um estudo
        específico (EIV, Meio Físico/Sondagem, LCV, Fauna, PCA, PRAD ou RFO).
        TRs nunca são cruzados nem aplicados a outros arquivos (ex.: o EIV
        não recebe TR de geologia nem de PCA; ARTs e peças administrativas não
        recebem TRs de conteúdo).
        """
        # 1. Se o tipo já foi identificado formalmente
        if tipo_documento:
            t_doc = str(tipo_documento).upper().strip()
            if t_doc in ("EIV", "LCV", "FAUNA", "SONDAGEM", "PCA", "PRAD", "RFO"):
                return [t_doc]
            if t_doc in (
                "ART", "MATRICULA_IMOVEL", "CNPJ", "CONTRATO_SOCIAL", "ALVARA_BOMBEIROS",
                "ALVARA_MUNICIPAL", "PROJETO_ARQUITETONICO", "RELATORIO_FOTOGRAFICO",
                "CERTIDAO_ZONEAMENTO", "VIABILIDADE_RESIDUOS", "VIABILIDADE_AGUA",
                "VIABILIDADE_ESGOTO", "VIABILIDADE_ENERGIA", "DECLARACAO_ALAGAMENTO",
                "DIRETRIZES_URBANISTICAS", "FORMULARIO", "FORMULARIO_ENQUADRAMENTO"
            ):
                return []

        # 2. Documentos que são ART/RTT ou Projeto Urbanístico não recebem TR de conteúdo
        if self.identificar_art_rtt(texto, nome_documento=nome_documento) is not None:
            return []
        if self._eh_projeto_urbanistico(texto):
            return []

        score: dict[str, int] = {}

        def sinal(tr: str, pontos: int) -> None:
            if pontos > 0:
                score[tr] = score.get(tr, 0) + pontos

        if nome_documento:
            nd = ProvedorLLMHeuristico._norm(nome_documento)
            # Tipos técnicos claros no nome do arquivo (prioridade máxima)
            if any(k in nd for k in ["vizinhanca", "eiv"]):
                sinal("EIV", 10)
            elif any(k in nd for k in ["cobertura vegetal", "lcv", "inventario florestal"]):
                sinal("LCV", 10)
            elif any(k in nd for k in ["inventario de fauna", "laudo de fauna", "fauna silvestre", "lfs", "fauna"]):
                sinal("FAUNA", 10)
            elif any(k in nd for k in ["laudo geologico", "estudo geologico", "geotecnico", "sondagem", "meio fisico"]):
                sinal("SONDAGEM", 10)
            elif any(k in nd for k in ["plano de controle", "pca"]):
                sinal("PCA", 10)
            elif any(k in nd for k in ["recuperacao de area degradada", "prad"]):
                sinal("PRAD", 10)
            elif any(k in nd for k in ["reposicao florestal", "rfo"]):
                sinal("RFO", 10)
            elif any(k in nd for k in [
                "formulario", "declaracao", "certidao", "procuracao",
                "contrato social", "cnh", "cnpj", "matricula", "croqui",
                "diretrizes urbanisticas", "relatorio fotografico",
                "viabilidade", "art", "rrt", "projeto urbanistico",
                "projeto arquitetonico", "planta"
            ]):
                return []

        t = ProvedorLLMHeuristico._norm(texto)

        # 3. Pontua o conteúdo textual
        # --- EIV (estudo de impacto de vizinhança) ---
        if "impacto de vizinhanca" in t or "estudo de impacto de vizinhanca" in t:
            sinal("EIV", 4)
        elif "eiv" in t and ("vizinhanca" in t or "impacto" in t):
            sinal("EIV", 2)

        # --- Sondagem / Meio Físico (RSCC ou Parcelamento) ---
        if "laudo geologico" in t or "estudo geologico" in t:
            sinal("SONDAGEM", 4)
        if "sondagem" in t or "trincheira" in t:
            sinal("SONDAGEM", 2)
        if "infiltracao" in t:
            sinal("SONDAGEM", 2)
        if "geolog" in t or "geotecnic" in t:
            sinal("SONDAGEM", 1)
        if "lencol freatico" in t or ("cota base" in t and "aterro" in t):
            sinal("SONDAGEM", 2)
        if "furos" in t and ("ha " in t or "hectare" in t):
            sinal("SONDAGEM", 1)

        # --- Reposição Florestal Obrigatória ---
        if "reposicao florestal" in t or "densidade de plantio" in t:
            sinal("RFO", 2)
        if ("individuos nativos" in t and "mudas" in t) or \
                (("suprimid" in t or "supressao" in t) and "mudas" in t):
            sinal("RFO", 2)

        # --- PRAD ---
        if ("prad" in t or "plano de recuperacao de area degradada" in t
                or "projeto de recuperacao de area degradada" in t):
            sinal("PRAD", 2)
        if "area degradada" in t and ("cronograma" in t or "monitoramento" in t):
            sinal("PRAD", 1)

        # --- Fauna ---
        if "inventario de fauna" in t or "laudo de fauna" in t or "fauna silvestre" in t:
            sinal("FAUNA", 4)
        if any(k in t for k in ["mastofauna", "avifauna", "herpetofauna",
                                "ictiofauna", "entomofauna"]):
            sinal("FAUNA", 2)
        if "fauna" in t or "lfs" in t:
            sinal("FAUNA", 1)

        # --- PCA ---
        if "plano de controle ambiental" in t:
            sinal("PCA", 3)
        elif "pca" in t and ("controle ambiental" in t or "medidas de controle" in t):
            sinal("PCA", 2)
        if "relatorios" in t and ("periodicidade" in t or "cronograma de relatorios" in t):
            sinal("PCA", 1)

        # --- LCV (laudo de cobertura vegetal) ---
        if ("laudo de cobertura vegetal" in t or "inventario florestal" in t
                or "fitossociolog" in t):
            sinal("LCV", 2)
        if "cobertura vegetal" in t:
            sinal("LCV", 1)

        # Se houver um TR dominante indicado no nome do documento (score >= 10),
        # prioriza-o e suprime qualquer outro TR
        if any(s >= 10 for s in score.values()):
            ordem = ["SONDAGEM", "RFO", "PRAD", "FAUNA", "PCA", "EIV", "LCV"]
            vencedores = [tr for tr in ordem if score.get(tr, 0) >= 10]
            return [vencedores[0]]

        # Disputa RFO x LCV: mantém apenas o TR de melhor aderência
        if score.get("RFO") and score.get("LCV"):
            if score["RFO"] >= score["LCV"]:
                score.pop("LCV", None)
            else:
                score.pop("RFO", None)

        # Disputa EIV x outros laudos: o EIV é um estudo de impacto amplo
        # que cita diagnóstico do meio físico (sondagem/geologia), biótico (fauna/flora)
        # e medidas de controle (PCA). O corpo do EIV NÃO pode disparar TRs secundários.
        if score.get("EIV"):
            for outro in ("SONDAGEM", "PCA", "FAUNA", "LCV", "PRAD", "RFO"):
                score.pop(outro, None)

        # Disputa PCA x Meio Físico/Sondagem: o PCA menciona obras e solo, não é sondagem
        if score.get("PCA") and score.get("SONDAGEM"):
            if score["PCA"] >= score["SONDAGEM"]:
                score.pop("SONDAGEM", None)
            else:
                score.pop("PCA", None)

        # Disputa Meio Físico/Sondagem x Fauna/Flora:
        if score.get("SONDAGEM") and (score.get("FAUNA") or score.get("LCV")):
            if score["SONDAGEM"] >= max(score.get("FAUNA", 0), score.get("LCV", 0)):
                score.pop("FAUNA", None)
                score.pop("LCV", None)
            else:
                score.pop("SONDAGEM", None)

        # Ao final, cada arquivo técnico deve receber no máximo UM único TR (o de melhor aderência)
        if not score:
            return []

        melhor_item = max(score.items(), key=lambda par: par[1])
        if melhor_item[1] >= 2:
            return [melhor_item[0]]
        return []

    # ------------------------------------------------------------------
    # ART/RTT: documento PRÓPRIO (não é laudo - não recebe TR de conteúdo)
    # ------------------------------------------------------------------
    RE_NUM_ART_DOC = re.compile(
        r"(?:\bART\b|\bRTT\b|\bRRT\b)\s*(?:N[ºo°.]+|N[ÚU]MERO|DO\s+RRT)?\s*[:\-]?\s*([A-Za-z0-9./\-]*?\d{4,12}[A-Za-z0-9./\-]*)",
        re.I)
    RE_NOME_ART_DOC = re.compile(
        r"(?:nome\s*(?:civil\s*/\s*social)?|(?:2\.?\s*)?nome|contratad[oa]|respons[aá]vel t[eé]cnic[oa]|profissional|"
        r"titular|t[eé]cnico respons[aá]vel)\s*(?:\(a\))?\s*[:\-]?\s*"
        r"([^\n:]{4,60})", re.I)

    @classmethod
    def identificar_art_rtt(cls, texto: str, nome_documento: Optional[str] = None) -> Optional[dict]:
        """Identifica se o documento É uma ART/RTT (anotação de responsabilidade),
        extraindo o NÚMERO e o NOME do profissional. Documentos escaneados de
        foto chegam aqui já com o texto extraído via OCR.

        Exige o PAR forte: nº da ART declarado + (CREA/CAU/CRBio ou RTT/anotação) -
        um laudo que apenas MENCIONA 'com ART de responsável técnico' não é
        uma ART. Retorna {'numero', 'nome'} ou None."""
        if nome_documento:
            nd = ProvedorLLMHeuristico._norm(nome_documento)
            if any(k in nd for k in ["formulario", "eiv", "laudo", "inventario", "declaracao", "contrato", "certidao", "projeto urbanistico", "projeto arquitetonico"]):
                return None
        t = ProvedorLLMHeuristico._norm(texto)
        tem_orgao = any(k in t for k in [
            "crea", "cau", "crbio", "crbi", "confea", "mutua",
            "anotacao de responsabilidade", "registro de responsabilidade", "caubr"
        ])
        tem_termo_art = bool(re.search(r"\b(art|rrt|rtt)\b", t))
        if not (tem_orgao or tem_termo_art):
            return None

        m_num = cls.RE_NUM_ART_DOC.search(texto)
        if not m_num:
            return None

        raw_num = m_num.group(1).strip()
        m_dig = re.search(r"\d{6,12}", raw_num)
        if m_dig:
            numero = m_dig.group(0)
        else:
            numero = re.sub(r"\D", "", raw_num) or None

        if not numero or len(numero) < 5:
            return None

        nome = None
        m_nome = cls.RE_NOME_ART_DOC.search(texto)
        if m_nome:
            cand = re.sub(r"\s+", " ", m_nome.group(1)).strip(" .;-")
            cand = re.split(r"\b(?:cpf|cnpj|registro|titulo|nº)\b", cand, flags=re.I)[0].strip()
            if len(cand) >= 4 and not any(w in cand.upper() for w in ["CONSELHO", "SERVIÇO", "CPF", "CNPJ", "MARIA JOAQUINA"]):
                nome = cand

        if not nome:
            m_prof = re.search(r"([A-ZÀ-ÿ\s]{6,50})[\r\n]+\s*(?:Ge[óo]logo|Engenheir|Arquiteto|Biólogo|Biol[óo]g)", texto)
            if m_prof:
                cand = re.sub(r"\s+", " ", m_prof.group(1)).strip(" .;-")
                if len(cand) >= 4 and not any(w in cand.upper() for w in ["CONSELHO", "SERVIÇO", "CPF", "CNPJ", "MARIA JOAQUINA", "EMPRESA"]):
                    nome = cand

        return {"numero": numero, "nome": nome}

    def _conferir_art_com_formulario(
            self, nome_documento: str, art_doc: dict,
            arts_formulario: Optional[list[dict]],
            texto: str = "") -> ResultadoValidacao:
        """Conferência da ART/RTT do DOCUMENTO com as declaradas no formulário
        HTML. Dupla blindagem:
          - NÚMERO: dígitos da ART declarada presentes no documento;
          - NOME: o nome do profissional SEMPRE consta nos primeiros dados da
            ART - os tokens do nome declarado são procurados no TEXTO do
            documento (mesmo com OCR imperfeito), além do nome extraído;
          - ATIVIDADE: 'licenciamento ambiental' na descrição sumária marca a
            ART atribuída à responsabilidade técnica pelo licenciamento, que
            deve pertencer ao RT da seção 8 do formulário."""
        norma = "Conferência ART/RTT × formulário HTML"
        numero_doc, nome_doc = art_doc.get("numero"), art_doc.get("nome")
        t_doc = ProvedorLLMHeuristico._norm(texto or "")
        metricas = {"art_documento": numero_doc, "nome_documento": nome_doc,
                    "arts_declaradas": arts_formulario or []}
        if not arts_formulario:
            return ResultadoValidacao(
                documento_analisado=nome_documento, norma_tr=norma,
                status=StatusValidacao.REVISAO_MANUAL,
                itens_reprovados=["Documento identificado como ART/RTT "
                                  f"(nº {numero_doc or 'não legível'}) e nenhum "
                                  "formulário HTML carregado para a "
                                  "conferência número/nome."],
                metricas=metricas, origem=OrigemAnalise.DETERMINISTICO)

        def _tokens(nome: str) -> list[str]:
            return [x for x in ProvedorLLMHeuristico._norm(nome or "").split()
                    if len(x) >= 4]

        atividade_lic = "licenciamento ambiental" in t_doc
        metricas["atividade_licenciamento_ambiental_na_art"] = atividade_lic
        trecho_ativ = ""
        if atividade_lic:
            idx = t_doc.find("licenciamento ambiental")
            trecho_ativ = re.sub(r"\s+", " ", (texto or "")[max(0, idx - 80):
                                                            idx + 60]).strip()

        lista_declaradas = list(arts_formulario)
        if atividade_lic:
            lista_declaradas.sort(
                key=lambda d: 0 if (str(d.get("secao")) == "8" or "licenciamento" in str(d.get("etapa", "")).lower()) else 1
            )

        for declarada in lista_declaradas:
            num_decl = re.sub(r"\D", "", declarada.get("numero") or "")
            bate_num = bool(numero_doc and num_decl
                            and numero_doc == num_decl)
            nome_decl = declarada.get("nome") or ""
            tokens_decl = _tokens(nome_decl)
            no_doc = False
            if tokens_decl:
                tokens_encontrados = sum(1 for tk in tokens_decl if tk in t_doc)
                if tokens_encontrados >= 1:
                    no_doc = True
                else:
                    palavras_doc = set(t_doc.split())
                    for tk in tokens_decl:
                        if any(SequenceMatcher(None, tk, pw).ratio() >= 0.8 for pw in palavras_doc):
                            no_doc = True
                            break
            no_extraido = bool(nome_doc and (
                set(_tokens(nome_decl)) & set(_tokens(nome_doc))
                or SequenceMatcher(None, nome_decl.lower(), nome_doc.lower()).ratio() >= 0.75
            ))
            bate_nome = no_doc or no_extraido
            if bate_num:
                metricas["nome_localizado_no_documento"] = bool(no_doc)
                if bate_nome:
                    eh_rt_licenciamento = (str(declarada.get("secao")) == "8"
                                           or "licenciamento" in str(declarada.get("etapa", "")).lower())
                    if atividade_lic and eh_rt_licenciamento:
                        return ResultadoValidacao(
                            documento_analisado=nome_documento, norma_tr=norma,
                            status=StatusValidacao.CONFORME, itens_reprovados=[],
                            trecho_referencia=(trecho_ativ
                                               or f"ART nº {numero_doc}"),
                            metricas={**metricas, "papel": (
                                "ART ATRIBUÍDA À RESPONSABILIDADE TÉCNICA "
                                "pelo LICENCIAMENTO AMBIENTAL (atividade "
                                "declarada na ART); profissional confere com "
                                "a seção 8 (RESPONSÁVEL PELO LICENCIAMENTO "
                                "AMBIENTAL) do formulário")},
                            origem=OrigemAnalise.DETERMINISTICO)
                    aviso = []
                    if eh_rt_licenciamento and not atividade_lic:
                        aviso = [
                            "ART do responsável pelo licenciamento (seção 8) "
                            "NÃO menciona 'licenciamento ambiental' na "
                            "descrição da atividade - conferir se esta é a "
                            "ART da responsabilidade técnica pelo "
                            "licenciamento."]
                    elif atividade_lic and not eh_rt_licenciamento:
                        metricas["observacao"] = (
                            "ART confere (nº e nome) e menciona atividade no "
                            "âmbito do licenciamento ambiental para a etapa "
                            f"'{declarada.get('etapa') or 'declarada'}'.")
                    return ResultadoValidacao(
                        documento_analisado=nome_documento, norma_tr=norma,
                        status=(StatusValidacao.REVISAO_MANUAL if aviso
                                else StatusValidacao.CONFORME),
                        itens_reprovados=aviso,
                        trecho_referencia=f"ART nº {numero_doc} - "
                                          f"{nome_decl}",
                        metricas=metricas,
                        origem=OrigemAnalise.DETERMINISTICO)
                return ResultadoValidacao(
                    documento_analisado=nome_documento, norma_tr=norma,
                    status=StatusValidacao.REVISAO_MANUAL,
                    itens_reprovados=[
                        f"ART nº {numero_doc} confere com o formulário, mas o "
                        "NOME do profissional não foi localizado no documento "
                        "(conferir os dados iniciais da ART)."],
                    metricas=metricas, origem=OrigemAnalise.DETERMINISTICO)
        declaradas_txt = ", ".join(
            f"{d.get('numero') or '?'} ({d.get('nome') or 'sem nome'})"
            for d in arts_formulario) or "nenhuma"
        return ResultadoValidacao(
            documento_analisado=nome_documento, norma_tr=norma,
            status=StatusValidacao.REVISAO_MANUAL,
            itens_reprovados=[
                f"ART/RTT nº {numero_doc or 'não legível'} do documento NÃO "
                f"confere com as declaradas no formulário HTML "
                f"({declaradas_txt}) - pode ser ART de outra etapa; conferir."],
            metricas=metricas, origem=OrigemAnalise.DETERMINISTICO)

    # ------------------------------------------------------------------
    # PROJETOS URBANÍSTICOS/ARQUITETÔNICOS (plantas): profissional + áreas
    # ------------------------------------------------------------------
    RE_AREA_DOC = re.compile(
        r"[aáAÁ]rea\s*(total|util|de\s+intervenc[aã]o|construida|do\s+lote|"
        r"do\s+terreno)?[^\d\n]{0,30}"
        r"(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?)\s*"
        r"(m2|m²|ha|hectare[s]?|metros?\s*quadrados?)", re.I)

    @classmethod
    def _num_pt(cls, bruto: str) -> Optional[float]:
        """Converte número no padrão brasileiro ('12.500,00' -> 12500.0)."""
        try:
            return float(bruto.strip().replace(".", "").replace(",", "."))
        except (TypeError, ValueError):
            return None

    @classmethod
    def _extrair_areas_doc(cls, texto: str) -> list[dict]:
        """Áreas declaradas no documento: [{'rotulo', 'valor', 'ha'}]."""
        saida: list[dict] = []
        for m in cls.RE_AREA_DOC.finditer(texto or ""):
            rotulo = re.sub(r"\s+", " ", (m.group(1) or "informada").lower())
            valor = cls._num_pt(m.group(2))
            unidade = (m.group(3) or "").lower()
            if valor is None:
                continue
            ha = (valor / 10000.0
                  if ("m2" in unidade or "m²" in unidade
                      or "quadrado" in unidade) else valor)
            saida.append({"rotulo": rotulo, "valor": valor,
                          "ha": round(ha, 6)})
        return saida

    @classmethod
    def _eh_projeto_urbanistico(cls, texto: str) -> bool:
        cabecalho = ProvedorLLMHeuristico._norm(texto or "")[:350]
        return any(k in cabecalho for k in (
            "projeto urbanistico", "projeto arquitetonico",
            "planta de situacao", "projeto de implantacao"))

    def conferir_projeto_urbanistico(
            self, nome_documento: str, texto: str,
            arts_formulario: Optional[list[dict]],
            areas_formulario: Optional[dict]) -> ResultadoValidacao:
        """Dupla checagem de projetos urbanísticos com plantas: o PROFISSIONAL
        que assina (nome/ART) bate com o formulário E as ÁREAS (total/útil)
        batem com os valores do formulário - ponto a ponto."""
        norma = ("Conferência projeto urbanístico × formulário "
                 "(profissional + áreas)")
        t_doc = ProvedorLLMHeuristico._norm(texto or "")
        areas_doc = self._extrair_areas_doc(texto or "")
        reprov: list[str] = []

        # ---- profissional que assina ----
        profissional_ok, prof_info = None, None
        m_art = self.RE_NUM_ART_DOC.search(texto or "")
        num_doc = None
        if m_art:
            m_dig = re.search(r"\d{6,12}", m_art.group(1))
            num_doc = m_dig.group(0) if m_dig else re.sub(r"\D", "", m_art.group(1))

        for declarada in (arts_formulario or []):
            tokens = [x for x in ProvedorLLMHeuristico._norm(
                declarada.get("nome") or "").split() if len(x) >= 4]
            bate_nome = False
            if tokens:
                if any(tk in t_doc for tk in tokens):
                    bate_nome = True
                else:
                    palavras_doc = set(t_doc.split())
                    for tk in tokens:
                        if any(SequenceMatcher(None, tk, pw).ratio() >= 0.8 for pw in palavras_doc):
                            bate_nome = True
                            break
            num_decl = re.sub(r"\D", "", declarada.get("numero") or "")
            bate_num = bool(num_doc and num_decl and num_doc == num_decl)
            reg_decl = re.sub(r"[^\w]", "", (declarada.get("registro") or "").lower())
            bate_reg = bool(reg_decl and len(reg_decl) >= 4 and reg_decl in re.sub(r"[^\w]", "", t_doc))

            if bate_nome or bate_num or bate_reg:
                profissional_ok = True
                prof_info = (f"{declarada.get('nome') or '?'}"
                             + (f" (ART nº {num_decl})" if num_decl else "")
                             + (f" [Registro {declarada.get('registro')}]" if declarada.get('registro') else ""))
                break
        if not profissional_ok:
            reprov.append(
                "Profissional que assina o projeto NÃO confere com o "
                "formulário HTML (nome/ART não localizados entre os "
                "responsáveis declarados).")

        # ---- áreas x formulário ----
        areas_checadas = 0
        for area in areas_doc:
            alvo_chave = ("area_total_ha" if "total" in area["rotulo"]
                          else "area_util_ha"
                          if ("util" in area["rotulo"]
                              or "intervenc" in area["rotulo"]) else None)
            if not alvo_chave:
                continue
            alvo = (areas_formulario or {}).get(alvo_chave)
            if alvo is None:
                continue
            areas_checadas += 1
            tolerancia = max(0.02 * alvo, 0.005)
            if abs(area["ha"] - alvo) > tolerancia:
                reprov.append(
                    f"Área {area['rotulo']} do projeto ({area['valor']}"
                    f" = {area['ha']:.4f} ha) DIVERGE do formulário "
                    f"({alvo:.4f} ha) - conferir.")
        if not areas_doc:
            reprov.append("Nenhuma ÁREA legível no projeto (total/útil) para "
                          "a conferência com o formulário - conferir "
                          "manualmente.")
        metricas = {"areas_documento": areas_doc,
                    "areas_formulario": areas_formulario or {},
                    "profissional": prof_info,
                    "art_no_documento": num_doc}
        return ResultadoValidacao(
            documento_analisado=nome_documento, norma_tr=norma,
            status=(StatusValidacao.CONFORME if not reprov and profissional_ok
                    else StatusValidacao.PENDENTE),
            itens_reprovados=reprov, metricas=metricas,
            trecho_referencia=(f"Profissional: {prof_info}" if prof_info
                               else ""),
            origem=OrigemAnalise.DETERMINISTICO)


    # o que cada tipo de laudo DECLARA SER no título -> TR que DEVE aplicar
    TR_ESPERADO_PELO_TITULO = [
        ("laudo geologico", "SONDAGEM"), ("estudo geologico", "SONDAGEM"),
        ("geotecnico", "SONDAGEM"), ("sondagem", "SONDAGEM"),
        ("cobertura vegetal", "LCV"), ("inventario florestal", "LCV"),
        ("manejo da flora", "LCV"), ("lcv", "LCV"),
        ("inventario de fauna", "FAUNA"), ("laudo de fauna", "FAUNA"),
        ("fauna silvestre", "FAUNA"), ("lfs", "FAUNA"),
        ("impacto de vizinhanca", "EIV"), ("eiv", "EIV"),
        ("plano de controle ambiental", "PCA"), ("pca", "PCA"),
        ("recuperacao de area degradada", "PRAD"), ("prad", "PRAD"),
        ("reposicao florestal", "RFO"), ("rfo", "RFO"),
    ]

    @classmethod
    def _tr_esperado_pelo_titulo(cls, texto: str, nome_documento: Optional[str] = None) -> Optional[str]:
        """O que o documento se DECLARA SER, lido no nome do arquivo ou no título/abertura."""
        if nome_documento:
            nome_norm = ProvedorLLMHeuristico._norm(nome_documento)
            if any(p in nome_norm for p in ("declaracao", "atestado", "certidao", "requerimento", "matricula", "contrato", "art", "rrt", "cnh", "cnpj", "croqui")):
                return None
            for palavra, tr in cls.TR_ESPERADO_PELO_TITULO:
                if palavra in nome_norm:
                    return tr
        cabecalho = ProvedorLLMHeuristico._norm(texto)[:1200]
        for palavra, tr in cls.TR_ESPERADO_PELO_TITULO:
            if palavra in cabecalho:
                return tr
        return None

    def auditar_com_dupla_checagem(self, nome_documento: str, texto: str,
                                   arts_formulario: Optional[list[dict]] = None,
                                   areas_formulario: Optional[dict] = None,
                                   tipo_documento: Optional[str] = None
                                   ) -> list[ResultadoValidacao]:
        """AUDITORIA TÉCNICA COM DUPLA CHECAGEM (sempre nesta fase):
        1ª e 2ª EXECUÇÃO - cada TR é revalidado; resultados divergentes
        viram REVISAO_MANUAL explícita;
        ROTEAMENTO - o que o documento DECLARA SER no título deve ter o TR
        correspondente aplicado; se não, sinaliza (nunca silencia)."""
        pass_1 = self.auditar_documento(nome_documento, texto, arts_formulario,
                                        areas_formulario, tipo_documento=tipo_documento)
        pass_2 = self.auditar_documento(nome_documento, texto, arts_formulario,
                                        areas_formulario, tipo_documento=tipo_documento)

        def chave(res: ResultadoValidacao):
            return (res.norma_tr, res.status.value,
                    tuple(sorted(res.itens_reprovados)))

        if [chave(r) for r in pass_1] != [chave(r) for r in pass_2]:
            pass_1.append(ResultadoValidacao(
                documento_analisado=nome_documento,
                norma_tr="Dupla checagem dos TRs",
                status=StatusValidacao.REVISAO_MANUAL,
                itens_reprovados=[
                    "Resultados DIVERGENTES entre a 1ª e a 2ª execução da "
                    "auditoria técnica - conferir manualmente.",
                    "1ª execução: " + ("; ".join(
                        f"{r.norma_tr}={r.status.value}" for r in pass_1) or "vazia"),
                    "2ª execução: " + ("; ".join(
                        f"{r.norma_tr}={r.status.value}" for r in pass_2) or "vazia")],
                origem=OrigemAnalise.DETERMINISTICO))
        else:
            titulo = self._tr_esperado_pelo_titulo(texto, nome_documento=nome_documento)
            aplicados = {("SONDAGEM" if "Meio Físico" in r.norma_tr
                          else "LCV" if "Cobertura" in r.norma_tr
                          else "FAUNA" if "Fauna" in r.norma_tr
                          else "EIV" if "Vizinhan" in r.norma_tr
                          else "PRAD" if "PRAD" in r.norma_tr
                          else "RFO" if "RFO" in r.norma_tr
                          else "PCA" if "PCA" in r.norma_tr
                          else r.norma_tr) for r in pass_1}
            for r in pass_1:
                r.metricas = {**(r.metricas or {}),
                              "dupla_checagem": "OK - 2ª execução idêntica"}
                if r.itens_reprovados:
                    r.itens_reprovados = list(dict.fromkeys(r.itens_reprovados))
                if titulo and titulo in aplicados:
                    r.metricas["tr_confirmado_pelo_titulo"] = (
                        "sim - documento declara ser do tipo coberto por "
                        "este TR")
            if titulo and titulo not in aplicados:
                pass_1.append(ResultadoValidacao(
                    documento_analisado=nome_documento,
                    norma_tr="Dupla checagem de roteamento de TRs",
                    status=StatusValidacao.REVISAO_MANUAL,
                    itens_reprovados=[
                        f"O documento se declara do tipo '{titulo}' no título, "
                        "mas NENHUM TR correspondente foi aplicado - verificar "
                        "o roteamento antes de concluir."],
                    metricas={"tr_esperado": titulo,
                              "trs_aplicados": sorted(aplicados)},
                    origem=OrigemAnalise.DETERMINISTICO))
        return pass_1

    def auditar_documento(self, nome_documento: str, texto: str,
                          arts_formulario: Optional[list[dict]] = None,
                          areas_formulario: Optional[dict] = None,
                          tipo_documento: Optional[str] = None
                          ) -> list[ResultadoValidacao]:
        """Aplica os TRs aplicáveis ao laudo e devolve os resultados.

        ARTs/RTTs (documento próprio, às vezes foto/scan lido por OCR) NÃO
        recebem TR de conteúdo: são conferidas (número + nome do profissional)
        com as ARTs declaradas no formulário HTML (arts_formulario)."""
        resultados: list[ResultadoValidacao] = []
        # PROJETOS URBANÍSTICOS com plantas: dupla checagem profissional + áreas
        if self._eh_projeto_urbanistico(texto):
            resultados.append(self.conferir_projeto_urbanistico(
                nome_documento, texto, arts_formulario, areas_formulario))
            return resultados
        art_doc = self.identificar_art_rtt(texto, nome_documento=nome_documento)
        if art_doc is not None:
            resultados.append(self._conferir_art_com_formulario(
                nome_documento, art_doc, arts_formulario, texto=texto))
            return resultados
        if len(texto.strip()) < 40:
            resultados.append(ResultadoValidacao(
                documento_analisado=nome_documento,
                norma_tr="Extração de texto",
                status=StatusValidacao.REVISAO_MANUAL,
                itens_reprovados=["Documento sem texto extraível (PDF escaneado/OCR pendente) "
                                  "ou arquivo vazio - conferência manual necessária."],
                origem=OrigemAnalise.DETERMINISTICO))
            return resultados

        contexto = self._detectar_contexto_sondagem(texto)
        for tr in self._rotear_trs(texto, nome_documento=nome_documento,
                                   tipo_documento=tipo_documento):
            try:
                if tr == "SONDAGEM":
                    resultados.append(self.validar_sondagem_aterramento(
                        nome_documento,
                        self.extrair_parametros_sondagem(texto, contexto=contexto)))
                elif tr == "RFO":
                    resultados.append(self.validar_rfo(
                        nome_documento, self.extrair_parametros_rfo(texto)))
                elif tr == "PRAD":
                    resultados.append(self.validar_prad(nome_documento, texto))
                elif tr == "FAUNA":
                    resultados.append(self.validar_fauna(nome_documento, texto))
                elif tr == "PCA":
                    resultados.append(self.validar_pca(nome_documento, texto))
                elif tr in ("EIV", "LCV"):
                    config_tr = (Calibracao().gabarito_trs or {}).get(
                        "trs_checklist_conteudo", {}).get(tr)
                    if config_tr and config_tr.get("itens"):
                        nome_tr = str(config_tr.get("nome", tr))
                        if not nome_tr.upper().startswith("TR"):
                            nome_tr = f"TR {nome_tr}"
                        resultados.append(self.validar_checklist_tr(
                            nome_documento, nome_tr,
                            texto, config_tr["itens"]))
                    else:
                        # fallback embutido (sem config oficial carregada)
                        itens_padrao = self._checklists_padrao().get(tr, {})
                        resultados.append(self.validar_checklist_tr(
                            nome_documento, f"TR {tr}", texto, itens_padrao))
            except Exception as exc:  # noqa: BLE001 - um TR não deve derrubar os demais
                logger.exception("Falha ao aplicar TR %s em %s", tr, nome_documento)
                resultados.append(ResultadoValidacao(
                    documento_analisado=nome_documento, norma_tr=tr,
                    status=StatusValidacao.REVISAO_MANUAL,
                    itens_reprovados=[f"Erro interno na validação do TR {tr}: {exc}"]))
        # Enriquece com métricas estruturais (GabeBrain)
        tk_est = int(len(texto) / 4)
        extenso = tk_est > 50000
        for r in resultados:
            if r.tokens_estimados is None:
                r.tokens_estimados = tk_est
            if r.documento_extenso is None:
                r.documento_extenso = extenso

        # Documentos que não são laudos (matrícula, contrato social, CNPJ...)
        # simplesmente não têm TR a aplicar: NENHUMA mensagem de erro aqui
        # (o usuário não envia TR - o sistema confronta com os NOSSOS TRs).
        return resultados

    @staticmethod
    def _checklists_padrao() -> dict[str, dict[str, list[str]]]:
        """Checklists mínimos embutidos (espelham config/gabarito_trs.json)."""
        return {
            "EIV": {
                "Identificação do empreendimento (razão social, CNPJ, logradouro, bairro)":
                    ["razao social", "cnpj", "logradouro", "bairro"],
                "Caracterização geral e descrição do empreendimento":
                    ["descricao do empreendimento", "caracterizacao geral", "dados do empreendimento", "justificativa do empreendimento"],
                "Geração de tráfego, carga e descarga": ["trafego", "carga e descarga"],
                "Geração de ruídos e vibrações": ["ruidos", "vibracoes", "decibeis"],
                "Medidas de controle/mitigação dos impactos":
                    ["medidas para controle", "medidas mitigadoras", "medidas de controle"],
                "ART dos responsáveis": ["art", "anotacao de responsabilidade tecnica"],
            },
            "LCV": {
                "Área de estudo com georreferenciamento": ["georreferenciamento", "imagem de satelite"],
                "Método de inventário florestal": ["inventario", "esforco amostral"],
                "Inventário fitossociológico": ["fitossociolog", "indice de valor de importancia"],
                "Estágio sucessional": ["estagio sucessional"],
                "APPs": ["app", "area de preservacao permanente"],
                "Relatório fotográfico": ["relatorio fotografico", "fotograf"],
                "Parecer técnico conclusivo": ["parecer tecnico conclusivo"],
            },
        }

    def auditar_lote(self, laudos: dict[str, str]) -> list[ResultadoValidacao]:
        """Audita um conjunto de laudos {nome_arquivo: texto_extraido}."""
        resultados: list[ResultadoValidacao] = []
        for nome, texto in laudos.items():
            resultados.extend(self.auditar_documento(nome, texto))
        return resultados
