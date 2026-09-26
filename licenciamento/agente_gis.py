# -*- coding: utf-8 -*-
"""
FASE 3 - Agente GIS (leitura de mapas e camadas)
================================================

Conforme a skill `gis-multicamadas` do GabeBrain, mapas e camadas geoespaciais
têm confronto PRÓPRIO - geométrico - e NUNCA recebem Termo de Referência de
conteúdo (TR de geologia, LCV, fauna, PCA, PRAD, RFO, EIV). Aplicar um TR de
laudo a um KMZ de curvas de nível, ou o TR de geologia a um mapa de APP, é
exatamente a aplicação cruzada que este agente existe para impedir.

O que o AgenteGIS confere:
  1. SRS de cada camada (SIRGAS 2000 oficial; reprojeção necessária -> aviso);
  2. integridade geométrica (anéis fechados, feições degeneradas);
  3. ÁREA da camada de propriedade/projeto x área declarada no formulário;
  4. CONTENÇÃO do ponto (ou polígono) do empreendimento na poligonal;
  5. DISTÂNCIA do empreendimento a corpos hídricos/APP (raio de 500 m);
  6. PRESENÇA dos temas exigidos pelo checklist (projeto urbanístico, curvas
     de nível, mapa de APPs).

Saída no mesmo contrato `ResultadoValidacao` do restante do sistema, com
`norma_tr` declarando explicitamente que nenhum TR de conteúdo foi aplicado.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .esquemas_tecnicos import (OrigemAnalise, ResultadoValidacao,
                                StatusValidacao)
from .leitor_gis import (Camada, PacoteGIS, area_ha, classificar_camada,
                         distancia_haversine_m, ponto_em_aneis, validar_srs)

logger = logging.getLogger("licenciamento.agente_gis")

#: rótulo fixo - a camada GIS não tem TR de conteúdo
NORMA_GIS = "Camadas GIS (KMZ/KML/GeoJSON) - conferência geométrica, sem TR de conteúdo"

#: raio de segurança padrão da skill gis-multicamadas (m)
RAIO_SEGURANCA_M = 500.0

#: tolerância relativa na conferência de áreas (2%)
TOLERANCIA_AREA = 0.02


class AgenteGIS:
    """Agente determinístico de leitura e conferência de camadas GIS."""

    def __init__(self, raio_seguranca_m: float = RAIO_SEGURANCA_M):
        self.raio_seguranca_m = float(raio_seguranca_m)

    # ------------------------------------------------------------------
    # 1) LEITURA
    # ------------------------------------------------------------------
    @staticmethod
    def ler(nome_arquivo: str, conteudo: bytes | str) -> PacoteGIS:
        """Lê um arquivo geoespacial (delega para o LeitorGIS)."""
        from .leitor_gis import LeitorGIS
        return LeitorGIS.ler_arquivo(nome_arquivo, conteudo)

    # ------------------------------------------------------------------
    # 2) CONFERÊNCIA GEOMÉTRICA
    # ------------------------------------------------------------------
    def conferir(self, nome_arquivo: str, pacote: PacoteGIS,
                 areas_formulario: Optional[dict] = None,
                 ponto_empreendimento: Optional[tuple[float, float]] = None
                 ) -> list[ResultadoValidacao]:
        """Confere as camadas de UM arquivo geoespacial.

        Args:
            nome_arquivo: nome do anexo (rastreabilidade no painel).
            pacote: PacoteGIS já lido (LeitorGIS).
            areas_formulario: {'area_total_ha': x, 'area_util_ha': y} do
                formulário - confrontada com a área da camada de propriedade.
            ponto_empreendimento: (lon, lat) do empreendimento (formulário),
                usada na contenção e na distância a corpos hídricos.

        Returns:
            Uma lista de ResultadoValidacao - um por tema conferido. NENHUM
            resultado carrega TR de conteúdo de laudo.
        """
        resultados: list[ResultadoValidacao] = []

        if pacote.erros and not pacote.camadas:
            return [ResultadoValidacao(
                documento_analisado=nome_arquivo, norma_tr=NORMA_GIS,
                status=StatusValidacao.REVISAO_MANUAL,
                itens_reprovados=[f"Arquivo geoespacial não legível: {e}"
                                  for e in pacote.erros],
                metricas={"tr_aplicado": None},
                origem=OrigemAnalise.DETERMINISTICO)]

        # ---- (a) SRS e integridade de cada camada ----------------------
        reprovados: list[str] = []
        metricas: dict[str, Any] = {
            "camadas": [c.resumo() for c in pacote.camadas],
            "tr_aplicado": None,
            "raio_seguranca_m": self.raio_seguranca_m,
        }
        for camada in pacote.camadas:
            aviso = validar_srs(camada)
            if aviso:
                reprovados.append(aviso)

        # ---- (b) área declarada x área da camada -----------------------
        camada_area = self._camada_de_area(pacote)
        if camada_area is not None:
            area = area_ha(camada_area)
            metricas["area_camada_ha"] = area
            metricas["camada_area"] = camada_area.nome
            alvo = (areas_formulario or {}).get("area_total_ha")
            if alvo and area:
                tolerancia = max(TOLERANCIA_AREA * float(alvo), 0.005)
                if abs(area - float(alvo)) > tolerancia:
                    reprovados.append(
                        f"Área da camada '{camada_area.nome}' ({area:.4f} ha) "
                        f"DIVERGE da área total declarada no formulário "
                        f"({float(alvo):.4f} ha) - conferir o georreferenciamento.")
        else:
            metricas["area_camada_ha"] = None

        # ---- (c) contenção do empreendimento na poligonal --------------
        if ponto_empreendimento and camada_area is not None:
            contido = self._ponto_contido(ponto_empreendimento, camada_area)
            metricas["empreendimento_contido_na_poligonal"] = contido
            if contido is False:
                reprovados.append(
                    "O ponto do empreendimento declarado no formulário está "
                    "FORA da poligonal da camada de propriedade/projeto - "
                    "conferir as coordenadas e o SRS das camadas.")

        # ---- (d) distância a corpos hídricos / APP --------------------
        if ponto_empreendimento:
            dist = self._distancia_corpo_hidrico(ponto_empreendimento, pacote)
            metricas["distancia_corpo_hidrico_m"] = dist
            if dist is not None and dist < self.raio_seguranca_m:
                reprovados.append(
                    f"Empreendimento a {dist:.0f} m de corpo hídrico/APP "
                    f"mapeado - dentro do raio de segurança de "
                    f"{self.raio_seguranca_m:.0f} m; conferir APP e afastamento.")

        # ---- (e) temas exigidos pelo checklist ------------------------
        temas = {c.tema for c in pacote.camadas}
        metricas["temas_presentes"] = sorted(temas)
        if not (temas & {"PROJETO_URBANISTICO", "PROPRIEDADE"}):
            reprovados.append(
                "Nenhuma camada de PROJETO URBANÍSTICO/POLIGONAL identificada - "
                "o checklist exige o arquivo do projeto urbanístico "
                "georreferenciado.")

        resultados.append(ResultadoValidacao(
            documento_analisado=nome_arquivo, norma_tr=NORMA_GIS,
            status=(StatusValidacao.CONFORME if not reprovados
                    else StatusValidacao.PENDENTE),
            itens_reprovados=reprovados, metricas=metricas,
            trecho_referencia=pacote.resumo_textual()[:400],
            origem=OrigemAnalise.DETERMINISTICO))
        return resultados

    # ------------------------------------------------------------------
    # 3) LOTE
    # ------------------------------------------------------------------
    def conferir_lote(self, pacotes: dict[str, PacoteGIS],
                      areas_formulario: Optional[dict] = None,
                      ponto_empreendimento: Optional[tuple[float, float]] = None
                      ) -> list[ResultadoValidacao]:
        """Confere todos os arquivos geoespaciais do processo."""
        resultados: list[ResultadoValidacao] = []
        for nome, pacote in pacotes.items():
            try:
                resultados.extend(self.conferir(
                    nome, pacote, areas_formulario=areas_formulario,
                    ponto_empreendimento=ponto_empreendimento))
            except Exception as exc:  # noqa: BLE001 - um mapa não derruba o lote
                logger.exception("Falha na conferência GIS de %s", nome)
                resultados.append(ResultadoValidacao(
                    documento_analisado=nome, norma_tr=NORMA_GIS,
                    status=StatusValidacao.REVISAO_MANUAL,
                    itens_reprovados=[f"Erro interno na conferência GIS: {exc}"],
                    metricas={"tr_aplicado": None},
                    origem=OrigemAnalise.DETERMINISTICO))
        return resultados

    # ------------------------------------------------------------------
    # Auxiliares
    # ------------------------------------------------------------------
    @staticmethod
    def _camada_de_area(pacote: PacoteGIS) -> Optional[Camada]:
        """Camada que representa a poligonal (projeto urbanístico/propriedade)."""
        for tema in ("PROJETO_URBANISTICO", "PROPRIEDADE"):
            for camada in pacote.camadas:
                if camada.tema == tema and camada.tipo_geometria == "Polygon":
                    return camada
        for camada in pacote.camadas:
            if camada.tipo_geometria == "Polygon":
                return camada
        return None

    @staticmethod
    def _ponto_contido(ponto: tuple[float, float],
                       camada: Camada) -> Optional[bool]:
        """True/False se alguma feição da camada contém (ou exclui) o ponto."""
        resposta: Optional[bool] = None
        for feicao in camada.feicoes_validas:
            if feicao.aneis:
                dentro = ponto_em_aneis(ponto, feicao.aneis)
            elif feicao.coordenadas:
                continue  # linha/ponto não delimita área
            else:
                continue
            if dentro:
                return True
            resposta = False
        return resposta

    def _distancia_corpo_hidrico(self, ponto: tuple[float, float],
                                 pacote: PacoteGIS) -> Optional[float]:
        """Distância do ponto à feição mais próxima de drenagem/APP."""
        melhor: Optional[float] = None
        for camada in pacote.camadas:
            if camada.tema not in ("DRENAGEM", "APP"):
                continue
            for feicao in camada.feicoes_validas:
                verts = feicao.coordenadas or (feicao.aneis[0] if feicao.aneis
                                               else [])
                for v in verts:
                    d = distancia_haversine_m(ponto, v)
                    if melhor is None or d < melhor:
                        melhor = d
        return None if melhor is None else round(melhor, 1)
