# -*- coding: utf-8 -*-
"""
FASE 3 - Contratos de Dados (Pydantic BaseModel)
=================================================

Esquemas que estruturam STRICTLY a saída das análises, garantindo que o
resultado da IA (LLM) seja sempre um JSON tipado e validado - evitando
alucinações e facilitando o consumo pela Fase 4 (dashboard/ofícios).

Contrato único de saída por validação (ResultadoValidacao):
    - documento_analisado: str
    - status: enum ("CONFORME", "PENDENTE", "REVISAO_MANUAL")
    - itens_reprovados: lista de strings com justificativa baseada no TR
    - trecho_referencia: trecho do PDF que baseou a decisão
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class StatusValidacao(str, Enum):
    """Estados possíveis de uma validação de TR."""
    CONFORME = "CONFORME"
    PENDENTE = "PENDENTE"
    REVISAO_MANUAL = "REVISAO_MANUAL"


class OrigemAnalise(str, Enum):
    """Indica qual motor produziu a análise (transparência para o licenciador)."""
    DETERMINISTICO = "deterministico"   # regras matemáticas puras
    LLM = "llm"                          # modelo de linguagem com saída estruturada
    HEURISTICO_LOCAL = "heuristico_local"  # protótipo offline (substituir por LLM)


# ==============================================================================
# Esquemas de MÉTRICAS extraídas dos laudos (entrada dos validadores matemáticos)
# ==============================================================================
class MetricasSondagem(BaseModel):
    """Parâmetros de Meio Físico / Aterros (RSCC) extraídos do laudo."""
    contexto: Optional[str] = Field(None, description="RSCC (aterro) ou PARCELAMENTO")
    profundidade_lencol_m: Optional[float] = Field(None, description="Profundidade do lençol freático (m)")
    cota_base_aterro_m: Optional[float] = Field(None, description="Cota base do aterro (m)")
    distancia_vertical_m: Optional[float] = Field(None, description="Distância vertical lençol - base (m)")
    distancia_vertical_informada_m: Optional[float] = Field(
        None, description="Distância vertical informada diretamente no laudo, se houver")
    area_ha: Optional[float] = Field(None, description="Área do aterro/projeto (ha)")
    furos_informados: Optional[int] = Field(None, description="Pontos de sondagem/trincheiras informados")
    profundidade_investigacao_m: Optional[float] = Field(
        None, description="Profundidade de investigação das sondagens (m)")
    ensaios_permeabilidade_informados: Optional[int] = Field(
        None, description="Ensaios de permeabilidade informados")
    impermeabilizacao_prevista: Optional[bool] = Field(
        None, description="O laudo prevê impermeabilização da base (argila compactada)?")


class MetricasRFO(BaseModel):
    """Parâmetros de Reposição Florestal Obrigatória (RFO) extraídos do laudo."""
    nativos_suprimidos: Optional[int] = None
    exoticos_suprimidos: Optional[int] = None
    mudas_nativas_propostas: Optional[int] = None
    mudas_exoticas_propostas: Optional[int] = None
    mudas_exigidas: Optional[int] = None
    densidade_proposta_mudas_ha: Optional[float] = None
    densidade_minima_mudas_ha: float = 3000.0
    especies_plantadas: Optional[int] = Field(None, description="Nº de espécies do plantio proposto")
    especies_suprimidas: Optional[int] = Field(None, description="Nº de espécies suprimidas")
    monitoramento_anos: Optional[int] = Field(None, description="Período de monitoramento proposto (anos)")
    percentual_falha_admitido: Optional[float] = Field(None, description="% de falha admitido no projeto")


# ==============================================================================
# Esquemas de SAÍDA estruturada do LLM (validações semânticas)
# ==============================================================================
class VereditoPRAD(BaseModel):
    """Saída estruturada do LLM para o TR de PRAD (áreas degradadas)."""
    cronograma_fisico_financeiro_presente: bool = Field(
        ..., description="Existe cronograma físico-financeiro DETALHADO?")
    periodo_monitoramento_anos: Optional[int] = Field(
        None, description="Período de monitoramento informado, em anos")
    menciona_relatorio_execucao: bool = Field(
        False, description="Prevê relatório de execução (prazo de 30 dias)?")
    trecho_cronograma: str = ""
    trecho_monitoramento: str = ""
    justificativa: str = ""


class VereditoFauna(BaseModel):
    """Saída estruturada do LLM para o TR de Laudo de Fauna."""
    metodos_busca_ativa: list[str] = Field(default_factory=list,
                                           description="Métodos de busca ATIVA identificados")
    metodos_busca_passiva: list[str] = Field(default_factory=list,
                                             description="Métodos de busca PASSIVA identificados")
    amostragem_primavera_verao: bool = Field(
        False, description="Amostragens em primavera ou verão comprovadas?")
    suficiencia_amostral_curva_coletor: bool = Field(
        False, description="Suficiência amostral determinada pela curva do coletor?")
    trecho_metodologia: str = ""
    justificativa: str = ""


class VereditoPCA(BaseModel):
    """Saída estruturada do LLM para o TR do Plano de Controle Ambiental."""
    periodicidade_supressao_movimentacao: Optional[str] = Field(
        None, description="Periodicidade dos relatórios na fase de supressão/movimentação de solo")
    periodicidade_obras: Optional[str] = Field(
        None, description="Periodicidade dos relatórios na fase de obras")
    trecho_cronograma: str = ""
    justificativa: str = ""


# ==============================================================================
# Contrato FINAL de cada avaliação de TR (vai para o dashboard e para o ofício)
# ==============================================================================
class ResultadoValidacao(BaseModel):
    """Objeto JSON padronizado de saída de cada validação do AuditorTecnico."""
    documento_analisado: str
    norma_tr: str = ""                      # identificação do Termo de Referência
    status: StatusValidacao = StatusValidacao.REVISAO_MANUAL
    itens_reprovados: list[str] = Field(default_factory=list)
    trecho_referencia: str = ""
    metricas: dict[str, Any] = Field(default_factory=dict)
    origem: OrigemAnalise = OrigemAnalise.DETERMINISTICO

    def resumo_para_oficio(self) -> str:
        """Linha consolidada usada pelo gerador de ofícios (Fase 4)."""
        itens = " ".join(self.itens_reprovados) if self.itens_reprovados else \
            "Análise concluída sem reprovações."
        return f"[{self.norma_tr or 'TR'}] {itens}"
