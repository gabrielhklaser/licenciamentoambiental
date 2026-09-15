# -*- coding: utf-8 -*-
"""
FASE 3 - Agente Técnico de Auditoria (híbrido: determinístico + LLM)
=====================================================================

O AuditorTecnico recebe os textos extraídos dos laudos técnicos (PDF via OCR
ou extração direta) e confronta o conteúdo com os Termos de Referência (TRs)
do órgão ambiental, em duas camadas:

CAMADA DETERMINÍSTICA (funções puras - matemática e lógica):
    - Meio Físico / RSCC: distância vertical lençol x base do aterro >= 1,5 m;
      furos de sondagem: mínimo 4 (áreas até 1 ha) + 1 furo por hectare excedente.
    - RFO: 15 mudas nativas (>1 m) por indivíduo NATIVO suprimido e 3 mudas por
      EXÓTICO; densidade de plantio >= 3.000 mudas/hectare.

CAMADA SEMÂNTICA (LLM com saída estruturada Pydantic):
    - PRAD: cronograma físico-financeiro detalhado + monitoramento >= 4 anos;
    - Fauna: >= 1 método de busca ativa e 1 de busca passiva por grupo,
      com amostragens em primavera/verão;
    - PCA: relatórios trimestrais (supressão/movimentação de solo) e
      semestrais (fase de obras).

Arquitetura de LLM: a classe usa a interface ProvedorLLMBase. Por padrão, o
protótipo opera 100% offline com ProvedorLLMHeuristico (regras determinísticas
de palavras-chave, marcadas como origem="heuristico_local"). Em produção,
defina a variável de ambiente LICENCIA_PROVEDOR_LLM=langchain e a chave da API
para acionar o ProvedorLLMLangChain (chat model com structured output), sem
alterar o restante do código.
"""

from __future__ import annotations

import logging
import math
import os
import re
import unicodedata
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

    # vocabulário técnico dos TRs
    TERMOS_ATIVOS = ["busca ativa", "busca limitada por encontro", "transecto",
                     "pontos de escuta", "escuta ativa", "rede de neblina", "pitfall",
                     "armadilha de intercepta"]
    TERMOS_PASSIVOS = ["armadilha de fossa", "armadilha de queda", "armadilha fotográfica",
                       "gravador automático", "gravadores automáticos", "escuta passiva",
                       "coleta passiva", "bioacústica passiva"]
    TERMOS_CRONOGRAMA = ["cronograma físico-financeiro", "cronograma fisico-financeiro",
                         "cronograma de execução físico-financeiro", "cronograma de desembolso",
                         "cronograma físico financeiro"]

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
            m4 = re.search(r"monitoramento[^.;]{0,240}?(quatro|4)\s*anos", t)
            trecho_mon = self._trecho(texto, re.compile(r"monitoramento[^.;]{0,80}", re.I))
            quatro_ok = bool(m4) or (anos is not None and anos >= 4)
            just = []
            just.append("Cronograma físico-financeiro detalhado identificado."
                        if cron_presente else
                        "Não foi identificado cronograma físico-financeiro detalhado no documento.")
            if quatro_ok:
                just.append(f"Monitoramento previsto por {'4 (quatro)' if m4 else anos} anos (>= 4 anos).")
            else:
                just.append("Não comprovado monitoramento por período mínimo de 4 anos.")
            return VereditoPRAD(
                cronograma_fisico_financeiro_presente=cron_presente,
                monitoramento_minimo_4_anos=quatro_ok,
                periodo_monitoramento_anos=anos,
                trecho_cronograma=trecho_cron,
                trecho_monitoramento=trecho_mon,
                justificativa=" ".join(just))

        if esquema is VereditoFauna:
            ativos = [termo for termo in self.TERMOS_ATIVOS if termo in t]
            passivos = [termo for termo in self.TERMOS_PASSIVOS if termo in t]
            estacao = bool(re.search(r"\b(primavera|verao)\b", t))
            trecho = self._trecho(texto, re.compile(r"metodologia|metodologias", re.I))
            just = []
            if ativos:
                just.append(f"Busca ativa comprovada ({', '.join(ativos)}).")
            else:
                just.append("Não comprovado método de BUSCA ATIVA por grupo inventariado.")
            if passivos:
                just.append(f"Busca passiva comprovada ({', '.join(passivos)}).")
            else:
                just.append("Não comprovado método de BUSCA PASSIVA por grupo inventariado.")
            if estacao:
                just.append("Amostragem em primavera/verão comprovada.")
            else:
                just.append("Amostragens em primavera ou verão não comprovadas.")
            return VereditoFauna(
                metodos_busca_ativa=ativos,
                metodos_busca_passiva=passivos,
                amostragem_primavera_verao=estacao,
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
    """Agente Técnico de Auditoria: regras matemáticas (TR) + validações semânticas."""

    # ---- Constantes dos Termos de Referência (gabarito) ----------------------
    DISTANCIA_MINIMA_LENCOl_M = 1.5        # RSCC: lençol >= 1,5 m da base do aterro
    FUROS_BASE = 4                          # sondagem: 4 furos até 1 ha
    FUROS_POR_HA_EXCEDENTE = 1              # +1 furo por hectare excedente
    RFO_MUDAS_POR_NATIVO = 15               # 15 mudas nativas (>1 m) / indivíduo nativo
    RFO_MUDAS_POR_EXOTICO = 3               # 3 mudas / indivíduo exótico
    RFO_DENSIDADE_MINIMA = 3000.0           # mudas/hectare
    PRAD_MONITORAMENTO_MINIMO_ANOS = 4
    PCA_TRIMESTRAL = "trimestral"           # supressão/movimentação de solo
    PCA_SEMESTRAL = "semestral"             # fase de obras

    # Parâmetros efetivos (sobrescritos por config/gabarito_trs.json quando presente,
    # gerado pela ingestão dos Termos de Referência oficiais)
    PARAMETROS_PADRAO = {
        "rscc_distancia_minima_lencol_m": DISTANCIA_MINIMA_LENCOl_M,
        "rscc_furos_base": FUROS_BASE,
        "rscc_furos_por_ha_excedente": FUROS_POR_HA_EXCEDENTE,
        "rfo_mudas_por_nativo": RFO_MUDAS_POR_NATIVO,
        "rfo_mudas_por_exotico": RFO_MUDAS_POR_EXOTICO,
        "rfo_densidade_minima_mudas_ha": RFO_DENSIDADE_MINIMA,
        "prad_monitoramento_minimo_anos": PRAD_MONITORAMENTO_MINIMO_ANOS,
        "pca_periodicidade_supressao": PCA_TRIMESTRAL,
        "pca_periodicidade_obras": PCA_SEMESTRAL,
    }

    def __init__(self, provedor_llm: Optional[ProvedorLLMBase] = None,
                 caminho_gabarito: Optional[str] = None):
        self.parametros: dict[str, Any] = dict(self.PARAMETROS_PADRAO)
        self.fonte_gabarito = "padrões internos do código (aguardando TRs oficiais)"
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
                from io import BytesIO
                from pypdf import PdfReader
                leitor = PdfReader(BytesIO(conteudo if isinstance(conteudo, bytes)
                                           else conteudo.encode()))
                texto = "\n".join((pagina.extract_text() or "") for pagina in leitor.pages)
                if len(texto.strip()) < 40:
                    logger.warning("PDF sem camada de texto (provável digitalização) - %s",
                                   nome_arquivo)
                    # >>> gancho de OCR (pytesseract/pdf2image) para produção <<<
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

    def extrair_parametros_sondagem(self, texto: str) -> MetricasSondagem:
        """Extrai profundidade do lençol, cota base do aterro, área e nº de furos."""
        prof = self._num(re.compile(
            r"len[çc]ol\s+fre[áa]tico[^.;\d]{0,120}?(-?\d{1,2}[.,]?\d{0,2})\s*m", re.I), texto) \
            or self._num(re.compile(
                r"profundidade[^.;\d]{0,80}?(-?\d{1,2}[.,]?\d{0,2})\s*m", re.I), texto)
        cota = self._num(re.compile(
            r"cota\s+base[^.;\d\-]{0,80}?(-?\d{1,2}[.,]?\d{0,2})", re.I), texto) \
            or self._num(re.compile(
                r"base\s+do\s+aterro[^.;\d\-]{0,80}?(-?\d{1,2}[.,]?\d{0,2})", re.I), texto)
        area = self._num(re.compile(
            r"[áa]rea[^.;\d]{0,60}?(\d{1,3}[.,]?\d{0,2})\s*(?:ha|hectare)", re.I), texto)
        furos = self._num(re.compile(
            r"(\d{1,2})\s*furos?\s+de\s+sondagem", re.I), texto) \
            or self._num(re.compile(
                r"furos?\s+de\s+sondagem[^.;\d]{0,40}?(\d{1,2})", re.I), texto)
        return MetricasSondagem(
            profundidade_lencol_m=prof, cota_base_aterro_m=cota, area_ha=area,
            furos_informados=int(furos) if furos is not None else None)

    def extrair_parametros_rfo(self, texto: str) -> MetricasRFO:
        """Extrai supressões, mudas propostas e densidade de plantio do laudo RFO."""
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
        densidade = self._num(re.compile(
            r"(\d{1,3}(?:[.]\d{3})*(?:[.,]\d+)?)\s*mudas?\s*(?:/|por\s+)?\s*(?:ha|hectare)", re.I), texto)
        return MetricasRFO(
            nativos_suprimidos=int(nativos) if nativos is not None else None,
            exoticos_suprimidos=int(exoticos) if exoticos is not None else None,
            mudas_nativas_propostas=int(mudas_nativas) if mudas_nativas is not None else None,
            mudas_exoticas_propostas=int(mudas_exoticas) if mudas_exoticas is not None else None,
            densidade_proposta_mudas_ha=densidade)

    # ==========================================================================
    # CAMADA DETERMINÍSTICA - regras puras (funções testáveis)
    # ==========================================================================
    def calcular_furos_exigidos(self, area_ha: float) -> int:
        """TR RSCC: mínimo de furos de sondagem conforme a área (hectares)."""
        base = int(self.parametros["rscc_furos_base"])
        por_ha = int(self.parametros["rscc_furos_por_ha_excedente"])
        if area_ha <= 1.0:
            return base
        hectares_excedentes = math.ceil(area_ha - 1.0)
        return base + hectares_excedentes * por_ha

    def calcular_mudas_exigidas(self, nativos: int, exoticos: int) -> int:
        """TR RFO: mudas por nativo suprimido + mudas por exótico suprimido."""
        return nativos * int(self.parametros["rfo_mudas_por_nativo"]) \
            + exoticos * int(self.parametros["rfo_mudas_por_exotico"])

    # --------------------------------------------------------------------------
    def validar_sondagem_aterramento(self, nome_documento: str,
                                     metricas: MetricasSondagem) -> ResultadoValidacao:
        """Valida Meio Físico (RSCC): distância do lençol >= 1,5 m e nº de furos."""
        metricas.distancia_vertical_m = (
            round(metricas.profundidade_lencol_m - metricas.cota_base_aterro_m, 2)
            if metricas.profundidade_lencol_m is not None
            and metricas.cota_base_aterro_m is not None else None)

        reprovados: list[str] = []
        trechos: list[str] = []

        if metricas.distancia_vertical_m is None:
            return ResultadoValidacao(
                documento_analisado=nome_documento, norma_tr="TR Meio Físico - RSCC (sondagem/aterro)",
                status=StatusValidacao.REVISAO_MANUAL,
                itens_reprovados=["Parâmetros de sondagem (lençol freático/cota base) não "
                                  "localizados no texto - conferência manual necessária."],
                metricas=metricas.model_dump(), origem=OrigemAnalise.DETERMINISTICO)

        if metricas.distancia_vertical_m < self.parametros['rscc_distancia_minima_lencol_m']:
            reprovados.append(
                f"Furo de sondagem insuficiente conforme exigência técnica mínima: distância "
                f"vertical entre a cota base do aterro ({self._fmt_br(metricas.cota_base_aterro_m, 2)} m) "
                f"e o lençol freático ({self._fmt_br(metricas.profundidade_lencol_m, 2)} m) é de "
                f"{self._fmt_br(metricas.distancia_vertical_m, 2)} m, inferior ao mínimo de "
                f"{self._fmt_br(self.parametros['rscc_distancia_minima_lencol_m'], 1)} m.")
        trechos.append(f"lençol freático: {metricas.profundidade_lencol_m} m; "
                       f"cota base: {metricas.cota_base_aterro_m} m")

        if metricas.area_ha is not None:
            exigidos = self.calcular_furos_exigidos(metricas.area_ha)
            if metricas.furos_informados is None:
                reprovados.append("Quantidade de furos de sondagem não informada no laudo.")
            elif metricas.furos_informados < exigidos:
                reprovados.append(
                    f"Quantidade de furos de sondagem insuficiente: informados "
                    f"{metricas.furos_informados} furos para área de {metricas.area_ha} ha "
                    f"(exigência mínima do TR: {exigidos} furos - 4 base + 1 por hectare excedente).")
            trechos.append(f"furos informados: {metricas.furos_informados} "
                           f"(exigidos: {exigidos})")

        return ResultadoValidacao(
            documento_analisado=nome_documento,
            norma_tr="TR Meio Físico - RSCC (sondagem/aterro)",
            status=StatusValidacao.CONFORME if not reprovados else StatusValidacao.PENDENTE,
            itens_reprovados=reprovados,
            trecho_referencia=" | ".join(t for t in trechos if t),
            metricas=metricas.model_dump(),
            origem=OrigemAnalise.DETERMINISTICO)

    # --------------------------------------------------------------------------
    def validar_rfo(self, nome_documento: str, metricas: MetricasRFO) -> ResultadoValidacao:
        """Valida Reposição Florestal Obrigatória (proporção de mudas + densidade)."""
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
        propostas_nativas = metricas.mudas_nativas_propostas or 0

        if propostas_nativas < metricas.mudas_exigidas:
            reprovados.append(
                f"Quantidade de mudas insuficiente: propostas {self._fmt_br(propostas_nativas)} "
                f"mudas nativas para {nativos} indivíduos nativos e {exoticos} exóticos "
                f"suprimidos (exigência do TR: 15 mudas por nativo + 3 por exótico = "
                f"{self._fmt_br(metricas.mudas_exigidas)} mudas).")
        trechos.append(f"proposta: {self._fmt_br(propostas_nativas)} mudas nativas "
                       f"(exigidas: {self._fmt_br(metricas.mudas_exigidas)})")

        if metricas.densidade_proposta_mudas_ha is not None:
            if metricas.densidade_proposta_mudas_ha < self.parametros['rfo_densidade_minima_mudas_ha']:
                reprovados.append(
                    f"Densidade de plantio abaixo do mínimo técnico: proposta de "
                    f"{self._fmt_br(metricas.densidade_proposta_mudas_ha)} mudas/hectare, sendo "
                    f"exigido no mínimo {self._fmt_br(self.parametros['rfo_densidade_minima_mudas_ha'])} mudas/hectare "
                    f"pelo TR.")
            trechos.append(f"densidade proposta: "
                           f"{self._fmt_br(metricas.densidade_proposta_mudas_ha)} mudas/ha")
        else:
            reprovados.append("Densidade de plantio (mudas/hectare) não informada no laudo.")

        return ResultadoValidacao(
            documento_analisado=nome_documento,
            norma_tr="TR Reposição Florestal Obrigatória (RFO)",
            status=StatusValidacao.CONFORME if not reprovados else StatusValidacao.PENDENTE,
            itens_reprovados=reprovados,
            trecho_referencia=" | ".join(t for t in trechos if t),
            metricas=metricas.model_dump(),
            origem=OrigemAnalise.DETERMINISTICO)

    # ==========================================================================
    # CAMADA SEMÂNTICA - LLM com saída estruturada (Pydantic)
    # ==========================================================================
    def _concluir_llm(self, nome_documento: str, norma_tr: str,
                      veredito: BaseModel, reprovacao: str) -> ResultadoValidacao:
        dados = veredito.model_dump()
        conforme = not reprovacao
        return ResultadoValidacao(
            documento_analisado=nome_documento,
            norma_tr=norma_tr,
            status=StatusValidacao.CONFORME if conforme else StatusValidacao.PENDENTE,
            itens_reprovados=[] if conforme else [reprovacao],
            trecho_referencia=(dados.get("trecho_cronograma") or dados.get("trecho_metodologia")
                               or dados.get("trecho_monitoramento") or ""),
            metricas=dados,
            origem=OrigemAnalise.LLM if isinstance(self.provedor, ProvedorLLMLangChain)
            else OrigemAnalise.HEURISTICO_LOCAL)

    def validar_prad(self, nome_documento: str, texto: str) -> ResultadoValidacao:
        """TR PRAD: cronograma físico-financeiro detalhado + monitoramento >= 4 anos."""
        instrucao = ("Verifique se existe cronograma físico-financeiro detalhado e se há "
                     "previsão expressa de monitoramento por período mínimo de 4 anos.")
        try:
            veredito: VereditoPRAD = self.provedor.analisar(VereditoPRAD, instrucao, texto)
        except (ValidationError, NotImplementedError) as exc:
            logger.exception("Falha na análise PRAD: %s", exc)
            return ResultadoValidacao(
                documento_analisado=nome_documento, norma_tr="TR PRAD - Áreas Degradadas",
                status=StatusValidacao.REVISAO_MANUAL,
                itens_reprovados=[f"Falha na análise semântica: {exc}"],
                origem=OrigemAnalise.HEURISTICO_LOCAL)

        reprovacao = ""
        if not veredito.cronograma_fisico_financeiro_presente:
            reprovacao += ("O PRAD não apresenta cronograma físico-financeiro detalhado, "
                           "conforme exigência do Termo de Referência. ")
        if not veredito.monitoramento_minimo_4_anos:
            reprovacao += ("Não comprovado monitoramento por período mínimo de 4 anos, "
                           "conforme exigência do Termo de Referência do PRAD. ")
        if veredito.periodo_monitoramento_anos is not None \
                and veredito.periodo_monitoramento_anos < self.parametros['prad_monitoramento_minimo_anos'] \
                and not veredito.monitoramento_minimo_4_anos:
            reprovacao += (f"Período declarado de {veredito.periodo_monitoramento_anos} anos "
                           f"é inferior ao mínimo de {self.parametros['prad_monitoramento_minimo_anos']} anos. ")
        if reprovacao and veredito.justificativa:
            reprovacao += veredito.justificativa
        return self._concluir_llm(nome_documento, "TR PRAD - Áreas Degradadas",
                                  veredito, reprovacao.strip())

    def validar_fauna(self, nome_documento: str, texto: str) -> ResultadoValidacao:
        """TR Fauna: busca ativa + passiva por grupo, amostragem primavera/verão."""
        instrucao = ("Verifique se a metodologia comprova, por grupo inventariado, o uso de "
                     "no mínimo um método de busca ativa e um de busca passiva, com "
                     "amostragens na primavera ou verão.")
        try:
            veredito: VereditoFauna = self.provedor.analisar(VereditoFauna, instrucao, texto)
        except (ValidationError, NotImplementedError) as exc:
            logger.exception("Falha na análise de Fauna: %s", exc)
            return ResultadoValidacao(
                documento_analisado=nome_documento, norma_tr="TR Laudo de Fauna",
                status=StatusValidacao.REVISAO_MANUAL,
                itens_reprovados=[f"Falha na análise semântica: {exc}"],
                origem=OrigemAnalise.HEURISTICO_LOCAL)

        reprovacao = ""
        if not veredito.metodos_busca_ativa:
            reprovacao += ("Metodologia não comprova uso de método de BUSCA ATIVA por grupo "
                           "inventariado, conforme Termo de Referência. ")
        if not veredito.metodos_busca_passiva:
            reprovacao += ("Metodologia não comprova uso de método de BUSCA PASSIVA por grupo "
                           "inventariado, conforme Termo de Referência. ")
        if not veredito.amostragem_primavera_verao:
            reprovacao += ("Amostragens em primavera ou verão não comprovadas no laudo. ")
        if reprovacao and veredito.justificativa:
            reprovacao += veredito.justificativa
        return self._concluir_llm(nome_documento, "TR Laudo de Fauna",
                                  veredito, reprovacao.strip())

    def validar_pca(self, nome_documento: str, texto: str) -> ResultadoValidacao:
        """TR PCA: relatórios trimestrais (supressão/solo) e semestrais (obras)."""
        instrucao = ("Verifique se o cronograma de relatórios estipula periodicidade "
                     "trimestral para as fases de supressão/movimentação de solo e semestral "
                     "para a fase de obras.")
        try:
            veredito: VereditoPCA = self.provedor.analisar(VereditoPCA, instrucao, texto)
        except (ValidationError, NotImplementedError) as exc:
            logger.exception("Falha na análise do PCA: %s", exc)
            return ResultadoValidacao(
                documento_analisado=nome_documento, norma_tr="TR Plano de Controle Ambiental",
                status=StatusValidacao.REVISAO_MANUAL,
                itens_reprovados=[f"Falha na análise semântica: {exc}"],
                origem=OrigemAnalise.HEURISTICO_LOCAL)

        reprovacao = ""
        if (veredito.periodicidade_supressao_movimentacao or "").lower() != self.parametros['pca_periodicidade_supressao']:
            reprovacao += (f"Periodicidade dos relatórios na fase de supressão/movimentação de "
                           f"solo '{veredito.periodicidade_supressao_movimentacao or 'não informada'}', "
                           f"sendo exigido '{self.parametros['pca_periodicidade_supressao']}' pelo Termo de Referência. ")
        if (veredito.periodicidade_obras or "").lower() != self.parametros['pca_periodicidade_obras']:
            reprovacao += (f"Periodicidade dos relatórios na fase de obras "
                           f"'{veredito.periodicidade_obras or 'não informada'}', sendo exigido "
                           f"'{self.parametros['pca_periodicidade_obras']}' pelo Termo de Referência. ")
        if reprovacao and veredito.justificativa:
            reprovacao += veredito.justificativa
        return self._concluir_llm(nome_documento, "TR Plano de Controle Ambiental",
                                  veredito, reprovacao.strip())

    # ==========================================================================
    # Roteamento: audita um laudo aplicando os TRs aplicáveis ao seu conteúdo
    # ==========================================================================
    def _rotear_trs(self, texto: str) -> list[str]:
        """Identifica quais TRs são aplicáveis ao documento (por palavras-chave).

        Os sinais são combinados para evitar falsos positivos (ex.: um PCA que
        apenas menciona 'supressão de vegetação' não deve disparar a RFO).
        """
        t = ProvedorLLMHeuristico._norm(texto)
        trs = []

        # --- Sondagem / Meio Físico (RSCC) ---
        if ("sondagem" in t or "lencol freatico" in t
                or ("cota base" in t and "aterro" in t)):
            trs.append("SONDAGEM")

        # --- Reposição Florestal Obrigatória ---
        if ("reposicao florestal" in t
                or (("suprimid" in t or "supressao" in t) and "mudas" in t)
                or ("individuos nativos" in t and "mudas" in t)
                or "densidade de plantio" in t):
            trs.append("RFO")

        # --- PRAD ---
        if ("prad" in t or "plano de recuperacao de area degradada" in t
                or ("area degradada" in t and ("cronograma" in t or "monitoramento" in t))):
            trs.append("PRAD")

        # --- Fauna ---
        if any(k in t for k in ["fauna", "mastofauna", "avifauna", "herpetofauna",
                                "ictiofauna", "entomofauna"]):
            trs.append("FAUNA")

        # --- PCA ---
        if ("plano de controle ambiental" in t
                or ("relatorios" in t and ("periodicidade" in t or "cronograma de relatorios" in t))):
            trs.append("PCA")
        return trs

    def auditar_documento(self, nome_documento: str, texto: str) -> list[ResultadoValidacao]:
        """Aplica todos os TRs aplicáveis ao laudo e devolve a lista de resultados."""
        resultados: list[ResultadoValidacao] = []
        if len(texto.strip()) < 40:
            resultados.append(ResultadoValidacao(
                documento_analisado=nome_documento,
                norma_tr="Extração de texto",
                status=StatusValidacao.REVISAO_MANUAL,
                itens_reprovados=["Documento sem texto extraível (PDF escaneado/OCR pendente) "
                                  "ou arquivo vazio - conferência manual necessária."],
                origem=OrigemAnalise.DETERMINISTICO))
            return resultados

        for tr in self._rotear_trs(texto):
            try:
                if tr == "SONDAGEM":
                    resultados.append(self.validar_sondagem_aterramento(
                        nome_documento, self.extrair_parametros_sondagem(texto)))
                elif tr == "RFO":
                    resultados.append(self.validar_rfo(
                        nome_documento, self.extrair_parametros_rfo(texto)))
                elif tr == "PRAD":
                    resultados.append(self.validar_prad(nome_documento, texto))
                elif tr == "FAUNA":
                    resultados.append(self.validar_fauna(nome_documento, texto))
                elif tr == "PCA":
                    resultados.append(self.validar_pca(nome_documento, texto))
            except Exception as exc:  # noqa: BLE001 - um TR não deve derrubar os demais
                logger.exception("Falha ao aplicar TR %s em %s", tr, nome_documento)
                resultados.append(ResultadoValidacao(
                    documento_analisado=nome_documento, norma_tr=tr,
                    status=StatusValidacao.REVISAO_MANUAL,
                    itens_reprovados=[f"Erro interno na validação do TR {tr}: {exc}"]))
        if not resultados:
            resultados.append(ResultadoValidacao(
                documento_analisado=nome_documento,
                norma_tr="Classificação de TR",
                status=StatusValidacao.REVISAO_MANUAL,
                itens_reprovados=["Nenhum Termo de Referência reconhecido no conteúdo do "
                                  "documento - triagem manual."],
                origem=OrigemAnalise.DETERMINISTICO))
        return resultados

    def auditar_lote(self, laudos: dict[str, str]) -> list[ResultadoValidacao]:
        """Audita um conjunto de laudos {nome_arquivo: texto_extraido}."""
        resultados: list[ResultadoValidacao] = []
        for nome, texto in laudos.items():
            resultados.extend(self.auditar_documento(nome, texto))
        return resultados
