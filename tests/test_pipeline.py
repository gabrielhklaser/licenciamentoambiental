# -*- coding: utf-8 -*-
"""
Testes automatizados do pipeline de triagem (Fases 1, 2 e 3).

Execução:  .venv/bin/python -m pytest tests/ -v
"""

from pathlib import Path

import pytest

from licenciamento.agente_administrativo import AgenteAdministrativo
from licenciamento.agente_financeiro import AgenteFinanceiro
from licenciamento.auditor_tecnico import (AuditorTecnico, MetricasRFO,
                                           MetricasSondagem)
from licenciamento.parser_formulario import FormularioParser

RAIZ = Path(__file__).resolve().parents[1]
EXEMPLOS = RAIZ / "exemplos"


# ==============================================================================
# FASE 1 - Parser
# ==============================================================================
def _parse(nome: str) -> dict:
    return FormularioParser(str(EXEMPLOS / nome)).gerar_json()


def test_parser_extrai_empreendedor_e_pleito_lor():
    dados = _parse("formulario_LOR_medio_alto.htm")
    assert dados["empreendedor"]["nome_razao_social"] == "Madeireira Vale do Sinos Ltda"
    assert dados["empreendedor"]["cpf_cnpj"] == "12.345.678/0001-90"
    assert dados["pleito"]["tipo_licenca"] == "LOR"
    assert dados["pleito"]["fases_componentes"] == ["LP", "LI", "LO"]
    assert dados["status_triagem"] == "liberado_triagem"


def test_parser_extrai_empreendimento_e_coordenadas_utm():
    dados = _parse("formulario_LOR_medio_alto.htm")
    emp = dados["empreendimento"]
    assert emp["porte"] == "Médio"
    assert emp["potencial_poluidor"] == "Alto"
    assert emp["codram"] == "05.412.1-3"
    assert emp["area_total_ha"] == pytest.approx(3.80)
    coords = emp["coordenadas"]
    assert coords["formato"] == "UTM"
    assert coords["datum"] == "SIRGAS 2000"
    assert coords["easting_m"] == pytest.approx(469512.25)
    assert coords["northing_m"] == pytest.approx(6714382.10)
    assert coords["fuso"] == 22


def test_parser_coordenadas_lat_long_e_areas_em_m2():
    dados = _parse("formulario_LP_pequeno_baixo.htm")
    emp = dados["empreendimento"]
    assert emp["porte"] == "Pequeno"
    assert emp["potencial_poluidor"] == "Baixo"
    assert emp["area_total_ha"] == pytest.approx(0.075)   # 750 m² -> ha
    assert emp["coordenadas"]["latitude"] == pytest.approx(-29.6745123)
    assert emp["coordenadas"]["longitude"] == pytest.approx(-50.8739812)


def test_art_ausente_bloqueia_triagem():
    dados = _parse("formulario_LP_sem_art.htm")
    assert dados["status_triagem"] == "bloqueado_sem_art"
    assert not dados["responsavel_tecnico"]["registro_art"]


def test_desduplicacao_lor_lp_li_lo():
    """LOR = LP + LI + LO com desduplicação de documentos repetidos entre fases."""
    dados = _parse("formulario_LOR_medio_alto.htm")
    docs = dados["documentos_exigidos"]
    estat = docs["estatisticas"]
    assert estat["total_bruto"] == 18
    assert estat["total_deduplicado"] == 14
    assert len(estat["removidos"]) == 4
    # a 'Cópia da matrícula do imóvel' (repetida em LP/LI/LO) só aparece uma vez
    matriculas = [d for d in docs["lista_deduplicada"] if "matrícula do imóvel" in d.lower()]
    assert len(matriculas) == 1


def test_deduplicacao_por_similaridade():
    itens = ["Cópia da matrícula do imóvel", "Cópia da matrícula do imovel",
             "Plano de Gerenciamento de Resíduos Sólidos (PGRS)", "Planta de localização"]
    dedup, removidos = FormularioParser._deduplicar_documentos(itens)
    assert len(dedup) == 3
    assert len(removidos) == 1


# ==============================================================================
# FASE 2 - Agentes
# ==============================================================================
def test_administrativo_bloqueia_sem_art_e_cruza_checklist():
    dados = _parse("formulario_LOR_medio_alto.htm")
    resultado = AgenteAdministrativo().auditar(dados, [])
    assert resultado["status_geral"] == "BLOQUEADO" or resultado["bloqueios"] == []
    # sem nenhum anexo, todos os exigidos ficam pendentes
    assert resultado["resumo"]["total_pendentes"] == 14

    resultado2 = AgenteAdministrativo().auditar(
        dados, ["formulario_enquadramento_assinado.pdf", "copia_cpf_cnpj.pdf"])
    assert resultado2["resumo"]["total_ok"] >= 2
    assert resultado2["status_geral"] == "PENDENTE"


def test_financeiro_porte_minimo_oficial():
    """Valores oficiais do Mínimo: LP=LI=LO=52,20 URMs (todos os potenciais)."""
    fin = AgenteFinanceiro()
    for potencial in ("Baixo", "Médio", "Alto"):
        r = fin.calcular_taxa("LP", "Mínimo", potencial, "Padaria")
        assert r["total_urm"] == pytest.approx(52.20)


def test_financeiro_soma_lir_lor():
    fin = AgenteFinanceiro()
    r_lor = fin.calcular_taxa("LOR", "Médio", "Alto", "Indústria de madeira")
    # LOR = LP + LI + LO (placeholder 417,60 por fase na tabela provisória)
    assert r_lor["composicao_fases"] == {"LP": 417.60, "LI": 417.60, "LO": 417.60}
    assert r_lor["total_urm"] == pytest.approx(1252.80)

    r_lir = fin.calcular_taxa("LIR", "Pequeno", "Médio", "Oficina mecânica")
    assert r_lir["total_urm"] == pytest.approx(156.60 * 2)


def test_financeiro_excecao_erb():
    fin = AgenteFinanceiro()
    r = fin.calcular_taxa("LI", "Mínimo", "Baixo",
                          "Estação de Rádio Base (ERB) / Transmissão")
    assert r["grupo_atividade"] == "ERB"
    assert r["total_urm"] == pytest.approx(714.00)  # tabela própria da ERB


def test_financeiro_excecao_faixas_hectares():
    fin = AgenteFinanceiro()
    lavra = fin.calcular_taxa("LI", "Pequeno", "Baixo",
                              "Extração mineral - Pedreira", area_ha=7.2)
    assert lavra["grupo_atividade"] == "LAVRA_MINERAL"
    assert lavra["porte_ou_faixa"] == "5 a 10 ha"

    lote = fin.calcular_taxa("LO", "Grande", "Alto",
                             "Parcelamento do solo - Loteamento", area_ha=3.0)
    assert lote["porte_ou_faixa"] == "0 a 5 ha"


# ==============================================================================
# FASE 3 - Auditor Técnico
# ==============================================================================
def test_pontos_sondagem_regra_da_area():
    """TRs oficiais: RSCC = 3 pontos até 1 ha; Parcelamento = 4 furos até 1 ha."""
    at = AuditorTecnico()
    # Aterro RSCC (TR 2026): 3 pontos até 1,0 ha + 1 por hectare ou fração
    assert at.calcular_pontos_sondagem_exigidos(0.8, "RSCC") == 3
    assert at.calcular_pontos_sondagem_exigidos(1.0, "RSCC") == 3
    assert at.calcular_pontos_sondagem_exigidos(1.1, "RSCC") == 4
    assert at.calcular_pontos_sondagem_exigidos(3.5, "RSCC") == 6
    # Meio Físico/Parcelamento (TR 2025): 4 furos até 1 ha + 1 por ha ou fração
    assert at.calcular_pontos_sondagem_exigidos(0.8, "PARCELAMENTO") == 4
    assert at.calcular_pontos_sondagem_exigidos(3.5, "PARCELAMENTO") == 7
    assert at.calcular_ensaios_permeabilidade_exigidos(1.0, "RSCC") == 2
    assert at.calcular_ensaios_permeabilidade_exigidos(1.0, "PARCELAMENTO") == 3


def test_sondagem_rscc_distancia_e_impermeabilizacao():
    """TR Aterro RSCC: d entre 1,0 e 1,5 m -> abaixo do mínimo + impermeabilização."""
    at = AuditorTecnico()
    metricas = MetricasSondagem(contexto="RSCC", profundidade_lencol_m=2.00,
                                cota_base_aterro_m=1.00, area_ha=1.0,
                                furos_informados=3, ensaios_permeabilidade_informados=2)
    r = at.validar_sondagem_aterramento("laudo.pdf", metricas)
    assert r.status.value == "PENDENTE"
    assert any("1,5" in item for item in r.itens_reprovados)
    assert any("IMPERMEABILIZAÇÃO" in item for item in r.itens_reprovados)
    assert r.metricas["distancia_vertical_m"] == pytest.approx(1.0)


def test_sondagem_rscc_distancia_vedada():
    """TR Aterro RSCC: d < 1,0 m é terminantemente proibido."""
    at = AuditorTecnico()
    metricas = MetricasSondagem(contexto="RSCC", profundidade_lencol_m=1.20,
                                cota_base_aterro_m=0.50, area_ha=1.0,
                                furos_informados=3, ensaios_permeabilidade_informados=2)
    r = at.validar_sondagem_aterramento("laudo.pdf", metricas)
    assert r.status.value == "PENDENTE"
    assert any("PROIBIDA" in item or "IMPOSSIBILIDADE" in item for item in r.itens_reprovados)


def test_sondagem_rscc_conforme():
    """d >= 2,0 m, sondagens e ensaios suficientes -> CONFORME."""
    at = AuditorTecnico()
    metricas = MetricasSondagem(contexto="RSCC", profundidade_lencol_m=3.50,
                                cota_base_aterro_m=0.50, area_ha=1.0,
                                furos_informados=3, ensaios_permeabilidade_informados=2)
    r = at.validar_sondagem_aterramento("laudo.pdf", metricas)
    assert r.status.value == "CONFORME"


def test_rfo_densidade_2000_gera_pendente():
    """Cenário da especificação: densidade de 2.000 mudas/ha -> PENDENTE."""
    at = AuditorTecnico()
    metricas = MetricasRFO(nativos_suprimidos=120, exoticos_suprimidos=30,
                           mudas_nativas_propostas=1900, mudas_exoticas_propostas=90,
                           densidade_proposta_mudas_ha=2000.0)
    r = at.validar_rfo("laudo_rfo.pdf", metricas)
    assert r.status.value == "PENDENTE"
    assert r.metricas["mudas_exigidas"] == 1890  # 15*120 + 3*30
    assert any("2.000" in item and "3.000" in item for item in r.itens_reprovados)


def test_rfo_conforme():
    at = AuditorTecnico()
    metricas = MetricasRFO(nativos_suprimidos=10, exoticos_suprimidos=0,
                           mudas_nativas_propostas=150, densidade_proposta_mudas_ha=3100.0,
                           monitoramento_anos=2)
    r = at.validar_rfo("laudo.pdf", metricas)
    assert r.status.value == "CONFORME"


def test_rfo_monitoramento_e_especies():
    """TR RFO: monitoramento >= 2 anos e espécies >= metade das suprimidas."""
    at = AuditorTecnico()
    metricas = MetricasRFO(nativos_suprimidos=10, exoticos_suprimidos=0,
                           mudas_nativas_propostas=150, densidade_proposta_mudas_ha=3100.0,
                           monitoramento_anos=1, especies_suprimidas=8, especies_plantadas=3)
    r = at.validar_rfo("laudo.pdf", metricas)
    assert r.status.value == "PENDENTE"
    assert any("monitoramento" in i.lower() and "2 anos" in i for i in r.itens_reprovados)
    assert any("espécies" in i.lower() for i in r.itens_reprovados)


def test_rfo_laudo_que_cita_minimo_normativo():
    """Laudo que transcreve o TR ('densidade mínima de 3.000 mudas/ha') não deve
    ter o mínimo confundido com a proposta (regressão)."""
    at = AuditorTecnico()
    texto = ("Medidas de reposição florestal devem considerar o plantio de 15 mudas com mais de "
             "um metro de altura por indivíduo nativo e 3 mudas com mais de um metro de altura por "
             "indivíduo exótico suprimido, conforme RESOLUÇÃO COMDEMA nº 02/2017. Os plantios devem "
             "ser feitos com uma densidade mínima de 3.000 mudas/hectare. "
             "Projeto com 120 indivíduos nativos e 30 exóticos suprimidos, plantio de 1.900 mudas "
             "nativas, densidade de 2.000 mudas/hectare, monitoramento por 2 anos.")
    metricas = at.extrair_parametros_rfo(texto)
    assert metricas.densidade_proposta_mudas_ha == pytest.approx(2000.0)  # não 3.000!
    r = at.validar_rfo("laudo_cita_tr.pdf", metricas)
    assert r.status.value == "PENDENTE"
    assert any("2.000" in i and "3.000" in i for i in r.itens_reprovados)


def test_gabarito_oficial_carregado():
    """config/gabarito_trs.json (TRs oficiais) é aplicado automaticamente."""
    at = AuditorTecnico()
    assert at.gabarito_revisado is True
    assert "campobom.rs.gov.br" in at.fonte_gabarito
    assert at.parametros["rscc_sondagem_base"] == 3            # TR Aterro RSCC 2026
    assert at.parametros["parcelamento_sondagem_base"] == 4    # TR Meio Físico 2025
    assert at.parametros["prad_monitoramento_minimo_anos"] == 2  # TR PRAD 5.7
    assert at.parametros["rfo_razao_minima_especies"] == 0.5   # TR RFO 3.3
    assert at.parametros["rscc_distancia_vedada_m"] == 1.0     # TR Aterro RSCC 2.4


def test_checklist_conteudo_eiv():
    """TR EIV: validação por checklist de conteúdo mínimo."""
    at = AuditorTecnico()
    texto_completo = ("ESTUDO DE IMPACTO DE VIZINHANÇA. Razão social: Empresa X - CNPJ 12.345/0001-90. "
                      "Logradouro: Rua A, Bairro Centro. Descrição do empreendimento: comércio. "
                      "Geração de tráfego e carga e descarga: ... Ruídos e vibrações: 60 decibéis. "
                      "Medidas de controle e medidas mitigadoras: ... ART anexada.")
    r_ok = at.validar_checklist_tr("eiv.pdf", "TR EIV", texto_completo,
                                   AuditorTecnico._checklists_padrao()["EIV"])
    assert r_ok.status.value == "CONFORME"

    texto_parcial = "Estudo de Impacto de Vizinhança. Razão social: Empresa X. CNPJ 12.345."
    r_parc = at.validar_checklist_tr("eiv2.pdf", "TR EIV", texto_parcial,
                                     AuditorTecnico._checklists_padrao()["EIV"])
    assert r_parc.status.value == "PENDENTE"
    assert r_parc.itens_reprovados  # lista os itens ausentes


def test_laudo_rfo_pdf_do_exemplo():
    texto = AuditorTecnico.extrair_texto(
        "laudo_rfo_densidade_2000.pdf",
        (EXEMPLOS / "laudos" / "laudo_rfo_densidade_2000.pdf").read_bytes())
    at = AuditorTecnico()
    resultado = at.validar_rfo("laudo_rfo_densidade_2000.pdf",
                               at.extrair_parametros_rfo(texto))
    assert resultado.status.value == "PENDENTE"
    assert resultado.itens_reprovados
