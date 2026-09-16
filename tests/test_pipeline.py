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
from licenciamento.parser_formulario import FormularioParser, normalizar as normalizar_texto

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
    # 20 itens bruto (6 LP + 7 LI + 7 LO, incluindo 'Cópia da Licença Prévia'
    # da LI e 'Cópia da Licença de Instalação' da LO, que são DOCUMENTOS)
    assert estat["total_bruto"] == 20
    assert estat["total_deduplicado"] == 16
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
    assert resultado["resumo"]["total_pendentes"] == 16

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
    """TABELA A oficial (Lei 4.439/2015) + somatório da Res. COMDEMA 003/2017."""
    fin = AgenteFinanceiro()
    r_lor = fin.calcular_taxa("LOR", "Médio", "Alto", "Indústria de madeira")
    assert r_lor["composicao_fases"] == {"LP": 1523.60, "LI": 1508.20, "LO": 1969.60}
    assert r_lor["total_urm"] == pytest.approx(5001.40)

    r_lir = fin.calcular_taxa("LIR", "Pequeno", "Médio", "Oficina mecânica")
    assert r_lir["composicao_fases"] == {"LP": 143.90, "LI": 245.30}
    assert r_lir["total_urm"] == pytest.approx(389.20)


def test_financeiro_excecao_erb():
    fin = AgenteFinanceiro()
    r = fin.calcular_taxa("LI", "Mínimo", "Baixo",
                          "Estação de Rádio Base (ERB) / Transmissão")
    assert r["grupo_atividade"] == "ERB"
    # TABELA B oficial: transmissão/retransmissão = 1.960,00 URM por fase
    # (divergência com a especificação original 612/714/510 documentada no config)
    assert r["composicao_fases"] == {"LI": 1960.00}
    assert r["total_urm"] == pytest.approx(1960.00)
    assert r["porte_ou_faixa"] == "FIXO"


def test_financeiro_excecao_faixas_hectares():
    """TABELAS C e D oficiais: Lavra 0-5 ha (única publicada) e Parcelamento."""
    fin = AgenteFinanceiro()
    lavra = fin.calcular_taxa("LI", "Pequeno", "Baixo",
                              "Extração mineral - Pedreira", area_ha=7.2)
    assert lavra["grupo_atividade"] == "LAVRA_MINERAL"
    assert lavra["porte_ou_faixa"] == "5 a 10 ha"
    # Tabela C publica só 0-5 ha (Médio): acima disso repete a linha + aviso
    assert lavra["total_urm"] == pytest.approx(1105.10)
    assert any("não publicada" in a for a in lavra.get("avisos", []))

    lote = fin.calcular_taxa("LO", "Grande", "Alto",
                             "Parcelamento do solo - Loteamento", area_ha=3.0)
    assert lote["porte_ou_faixa"] == "0 a 5 ha"
    assert lote["total_urm"] == pytest.approx(1386.12)

    lote12 = fin.calcular_taxa("LOR", "Grande", "Alto",
                               "Parcelamento do solo - Loteamento", area_ha=12.0)
    assert lote12["porte_ou_faixa"] == "10 a 20 ha"
    assert lote12["total_urm"] == pytest.approx(2294.76 + 2568.96 + 2568.96)


def test_financeiro_comercio_tabela_e():
    """TABELA E: Comércio em Geral (Baixo) por área construída."""
    fin = AgenteFinanceiro()
    pequena = fin.calcular_taxa("LP", "Pequeno", "Baixo",
                                "Comércio em geral - loja", area_m2=30.0)
    assert pequena["grupo_atividade"] == "COMERCIO"
    assert pequena["total_urm"] == pytest.approx(25.00)

    media = fin.calcular_taxa("LOR", "Pequeno", "Baixo",
                              "Comércio varejista", area_m2=150.0)
    assert media["porte_ou_faixa"] == "50 a 200 m2"
    assert media["total_urm"] == pytest.approx(52.20 * 3)

    grande = fin.calcular_taxa("LI", "Pequeno", "Baixo",
                               "Loja de departamentos", area_m2=320.0)
    assert grande["total_urm"] == pytest.approx(77.20)

    # comércio de potencial Alto NÃO fica na Tabela E (só publica Baixo)
    alto = fin.calcular_taxa("LP", "Médio", "Alto",
                             "Comércio de produtos químicos")
    assert alto["grupo_atividade"] == "GERAL"

    # sem área informada: fallback Tabela A com aviso
    sem_area = fin.calcular_taxa("LP", "Pequeno", "Baixo", "Comércio em geral")
    assert sem_area["grupo_atividade"] == "GERAL"
    assert any("TABELA A" in a for a in sem_area.get("avisos", []))


def test_financeiro_tabela_f_autorizacoes():
    """TABELA F: taxa única de autorizações por tipo/quantidade."""
    fin = AgenteFinanceiro()
    r = fin.calcular_taxa("AUTORIZACAO", "Pequeno", "Baixo",
                          "Supressão de árvores",
                          tipo_autorizacao="supressao_arvores",
                          quantidade_autorizacao=30)
    assert r["grupo_atividade"] == "TABELA_F"
    assert r["total_urm"] == pytest.approx(50.00)  # 21 a 50 árvores

    r2 = fin.calcular_taxa("AUTORIZACAO", "Pequeno", "Baixo",
                           "Movimentação de terras",
                           tipo_autorizacao="movimentacao_terras",
                           quantidade_autorizacao=200)
    assert r2["total_urm"] == pytest.approx(150.00)  # mais de 150 m³

    # sem quantidade: menor faixa provisória + aviso
    r3 = fin.calcular_taxa("AUTORIZACAO", "Pequeno", "Baixo",
                           "Descapoeiramento", tipo_autorizacao="descapoeiramento")
    assert r3["total_urm"] == pytest.approx(20.00)
    assert any("provisória" in a for a in r3.get("avisos", []))

    # autorização genérica (sem tipo): valor interno provisório + aviso
    r4 = fin.calcular_taxa("AUTORIZACAO", "Pequeno", "Baixo", "Autorização ambiental")
    assert r4["total_urm"] == pytest.approx(52.20)
    assert any("Tabela F" in a for a in r4.get("avisos", []))


def test_financeiro_licenca_unica():
    """Licença Única (requerimento oficial): somatório LP+LI+LO."""
    fin = AgenteFinanceiro()
    r = fin.calcular_taxa("LICENCA_UNICA", "Mínimo", "Baixo", "Padaria")
    assert r["total_urm"] == pytest.approx(52.20 * 3)
    assert "Licença Única" in r["regra_aplicada"]


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


# ==============================================================================
# MOTIVO DO ENCAMINHAMENTO À SEMA - leitura da MARCAÇÃO (dupla checagem)
# ==============================================================================
FORMULARIO_MOTIVO_TEMPLATE = """<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8"><title>Formulário</title></head>
<body>
<h1>SEMA CAMPO BOM</h1>
<table>
  <tr><td class="rotulo">Nome/Razão Social</td><td>Empresa Teste Ltda</td></tr>
  <tr><td class="rotulo">Nome do Empreendimento</td><td>Atividade Teste</td></tr>
</table>
<h2>3. MOTIVO DO ENCAMINHAMENTO À SEMA</h2>
<table>
  <tr><td>(  ) Licença Prévia (LP)</td></tr>
  <tr><td>(  ) Licença de Instalação (LI)</td></tr>
  <tr><td>(  ) Licença de Operação (LO)</td></tr>
  <tr><td>(  ) Licença de Instalação e Regularização (LIR)</td></tr>
  <tr><td>(  ) Licença de Operação e Regularização (LOR)</td></tr>
</table>
<h2>4. DOCUMENTAÇÃO EXIGIDA</h2>
<p><strong>Documentação exigida para a Licença Prévia (LP):</strong></p>
<ul>
  <li>Formulário assinado</li>
  <li>Cópia do CNPJ</li>
</ul>
<p><strong>Documentação exigida para a Licença de Instalação (LI):</strong></p>
<ul>
  <li>Cópia da Licença Prévia</li>
  <li>Projeto aprovado</li>
</ul>
<p><strong>Documentação exigida para a Licença de Operação (LO):</strong></p>
<ul>
  <li>PGRS</li>
</ul>
</body></html>"""


def test_formulario_oficial_marcado_lp_nao_sai_lor():
    """CENÁRIO CRÍTICO REPORTADO PELO LICENCIADOR: tabela oficial de opções em
    2 colunas com '( X ) Primeira licença' + '( X ) Licença Prévia' -> pleito
    LP (NUNCA LOR). Listagem cobrada = apenas a da LP; taxa cruza o
    Porte/Potencial COMBINADOS com o Manual (Tabela A: Pequeno/Baixo LP)."""
    dados = _parse("formulario_MOTIVO_LP_oficial.htm")
    pleito = dados["pleito"]
    assert pleito["tipo_licenca"] == "LP", pleito
    assert "marcação" in (pleito.get("metodo_deteccao") or "")
    assert pleito["fases_componentes"] == ["LP"]
    # listagem APENAS da fase marcada (5 documentos da LP; lista da LO fora)
    exigencias = dados["documentos_exigidos"]["lista_deduplicada"]
    assert len(exigencias) == 5
    assert any("matrícula do imóvel" in e.lower() for e in exigencias)
    assert not any("Bombeiros" in e for e in exigencias)

    emp = dados["empreendimento"]
    assert emp["porte"] == "Pequeno" and emp["potencial_poluidor"] == "Baixo"
    assert emp["area_intervencao_ha"] == pytest.approx(0.32)
    assert emp["codram"]  # CODRAM lido para o cruzamento com o Manual

    fin = AgenteFinanceiro().calcular_do_parser(dados)
    assert fin["total_urm"] == pytest.approx(72.10)
    assert fin["composicao_fases"] == {"LP": 72.10}


def test_motivo_encaminhamento_checkbox_lor():
    """Checkbox LOR marcado define o pleito; listagem = LP+LI+LO deduplicada."""
    dados = _parse("formulario_LOR_medio_alto.htm")
    pleito = dados["pleito"]
    assert pleito["tipo_licenca"] == "LOR"
    assert "marcação" in (pleito.get("metodo_deteccao") or "")
    assert pleito["fases_componentes"] == ["LP", "LI", "LO"]
    assert len(dados["documentos_exigidos"]["lista_deduplicada"]) == 16


def test_motivo_encaminhamento_marcacao_textual_lir():
    """'( X ) Licença de Instalação e Regularização (LIR)' -> LIR = LP + LI."""
    dados = _parse("formulario_MOTIVO_LIR.htm")
    pleito = dados["pleito"]
    assert pleito["tipo_licenca"] == "LIR"
    assert "marcação" in (pleito.get("metodo_deteccao") or "")
    assert pleito["fases_componentes"] == ["LP", "LI"]
    assert len(dados["documentos_exigidos"]["lista_deduplicada"]) == 7
    # item que menciona fase no TEXTO não pode ser engolido
    assert any("Licença Prévia" in d for d in
               dados["documentos_exigidos"]["lista_deduplicada"])


def test_pleitos_nomes_nas_duas_ordens():
    """As regularizações são reconhecidas nas DUAS ordens de nome usadas nos
    formulários, e a ordem das palavras não troca o tipo (LOR != LIR)."""
    padroes = dict(FormularioParser.PLEITOS)
    for texto in ["Licença de Operação e Regularização (LOR)",
                  "Licença de Regularização e Operação (LOR)",
                  "Licenca de Regularizacao e Operacao"]:
        assert padroes["LOR"].search(normalizar_texto(texto)), texto
        assert not padroes["LIR"].search(normalizar_texto(texto)), texto
    for texto in ["Licença de Instalação e Regularização (LIR)",
                  "Licença de Regularização e Instalação (LIR)",
                  "Licença de Implantação e Regularização (LIR)",
                  "Licenca de Regularizacao e Instalacao"]:
        assert padroes["LIR"].search(normalizar_texto(texto)), texto
        assert not padroes["LOR"].search(normalizar_texto(texto)), texto


def test_motivo_ordem_invertida_lir_e_lor():
    """Marcação com nomes na ordem invertida: LIR e LOR corretos + listagem."""
    rotulo_lir = "Licença de Regularização e Instalação (LIR)"
    html_lir = FORMULARIO_MOTIVO_TEMPLATE.replace(
        "(  ) Licença de Instalação e Regularização (LIR)", f"( X ) {rotulo_lir}")
    dados_lir = FormularioParser(conteudo_html=html_lir).parse()
    assert dados_lir["pleito"]["tipo_licenca"] == "LIR"
    assert dados_lir["pleito"]["fases_componentes"] == ["LP", "LI"]
    assert len(dados_lir["documentos_exigidos"]["lista_deduplicada"]) == 4

    rotulo_lor = "Licença de Regularização e Operação (LOR)"
    html_lor = FORMULARIO_MOTIVO_TEMPLATE.replace(
        "(  ) Licença de Operação e Regularização (LOR)", f"( X ) {rotulo_lor}")
    dados_lor = FormularioParser(conteudo_html=html_lor).parse()
    assert dados_lor["pleito"]["tipo_licenca"] == "LOR"
    assert dados_lor["pleito"]["fases_componentes"] == ["LP", "LI", "LO"]
    assert len(dados_lor["documentos_exigidos"]["lista_deduplicada"]) == 5


def test_secao_sem_marcacao_nao_adivinha_pleito():
    """Seção MOTIVO presente com TODAS as opções sem marca: NÃO adivinhar
    (a varredura geral casaria o LOR da lista) - fica None com aviso."""
    dados = FormularioParser(conteudo_html=FORMULARIO_MOTIVO_TEMPLATE).parse()
    assert dados["pleito"]["tipo_licenca"] is None
    avisos = " ".join(dados.get("avisos_parser") or [])
    assert "MARCADA" in avisos  # aviso de conferência manual para o licenciador


def test_marca_em_celula_isolada_da_tabela():
    """<td>X</td><td>Licença Prévia (LP)</td> (marca em célula própria)."""
    html = FORMULARIO_MOTIVO_TEMPLATE.replace(
        "<tr><td>(  ) Licença Prévia (LP)</td></tr>",
        "<tr><td>X</td><td>Licença Prévia (LP)</td></tr>")
    dados = FormularioParser(conteudo_html=html).parse()
    assert dados["pleito"]["tipo_licenca"] == "LP"


def test_nome_fantasia_no_metrico_do_frontend():
    """Empreendimento exibe o NOME FANTASIA; sem fantasia, a RAZÃO SOCIAL."""
    dados_fantasia = _parse("formulario_LOR_medio_alto.htm")
    assert dados_fantasia["empreendedor"].get("nome_fantasia") == "Serraria Vale do Sinos"

    dados_sem = _parse("formulario_MOTIVO_LIR.htm")
    assert not dados_sem["empreendedor"].get("nome_fantasia")
    fallback = (dados_sem["empreendedor"].get("nome_fantasia")
                or dados_sem["empreendedor"].get("nome_razao_social")
                or dados_sem["empreendimento"].get("nome_empreendimento"))
    assert fallback == "Oficina Mecânica Menezes Ltda"


def test_conferencia_arquivos_pela_listagem_do_motivo():
    """A conferência de anexos (Fase 2) usa a listagem gerada pela marcação."""
    dados = _parse("formulario_MOTIVO_LIR.htm")
    anexados = ["formulario_enquadramento_assinado.pdf", "copia_cpf_cnpj.pdf",
                "matricula_imovel.pdf", "art_responsavel_tecnico.pdf",
                "projeto_construcao_aprovado.pdf", "pgrs.pdf"]
    resultado = AgenteAdministrativo().auditar(dados, anexados)
    assert resultado["resumo"]["total_ok"] >= 5
    # o fixture LIR não tem ART (hard constraint -> BLOQUEADO é esperado);
    # o escopo deste teste é a CONFERÊNCIA DOS ANEXOS pela listagem marcada
    assert resultado["status_geral"] in ("APROVADO", "PENDENTE", "BLOQUEADO")


# ==============================================================================
# MOTIVO em DUAS COLUNAS (Primeira licença | Renovação) com marca em CÍRCULO
# ==============================================================================
def test_motivo_tabela_2col_circulo_primeira_licenca():
    """Formulário oficial: 1ª linha '[ X ] Primeira licença' | '[  ] Renovação'
    e tipos marcados com círculo '( o )'. O parser lê a PRIMEIRA LINHA, define
    a coluna ativa e depois o TIPO (LP); taxa cruza Porte/Potencial + CODRAM."""
    dados = _parse("formulario_MOTIVO_2col_circulo.htm")
    pleito = dados["pleito"]
    assert pleito["tipo_licenca"] == "LP", pleito
    assert pleito["natureza"] == "Primeira licença"
    assert "tabela" in (pleito.get("metodo_deteccao") or "")
    assert pleito["fases_componentes"] == ["LP"]
    # listagem SOMENTE da fase marcada (LP: 5 itens; LI/LO fora)
    docs = dados["documentos_exigidos"]["lista_deduplicada"]
    assert len(docs) == 5
    assert not any("Bombeiros" in d for d in docs)
    # dados lidos para o cruzamento com o Manual de Taxas
    emp = dados["empreendimento"]
    assert emp["porte"] == "Pequeno" and emp["potencial_poluidor"] == "Baixo"
    assert emp["area_intervencao_ha"] == pytest.approx(0.45)
    assert emp["codram"]
    fin = AgenteFinanceiro().calcular_do_parser(dados)
    assert fin["total_urm"] == pytest.approx(72.10)


def test_motivo_tabela_2col_circulo_renovacao():
    """Marcação na coluna RENOVAÇÃO: natureza 'Renovação' e o TIPO lido na
    coluna 2 (LI marcado) - a ordem das colunas não troca o tipo lido."""
    html = (EXEMPLOS / "formulario_MOTIVO_2col_circulo.htm").read_text(encoding="utf-8")
    html = html.replace("[ X ] <strong>Primeira licença</strong>",
                        "[&nbsp;&nbsp;&nbsp;] Primeira licença")
    html = html.replace("[&nbsp;&nbsp;&nbsp;] Renovação",
                        "[ X ] <strong>Renovação</strong>", 1)
    html = html.replace("""<td>( o ) Licença Prévia (LP)</td>
    <td>(&nbsp;&nbsp;&nbsp;) Licença Prévia (LP)</td>""",
                        """<td>(&nbsp;&nbsp;&nbsp;) Licença Prévia (LP)</td>
    <td>(&nbsp;&nbsp;&nbsp;) Licença Prévia (LP)</td>""")
    html = html.replace("""<td>(&nbsp;&nbsp;&nbsp;) Licença de Instalação (LI)</td>
    <td>(&nbsp;&nbsp;&nbsp;) Licença de Instalação (LI)</td>""",
                        """<td>(&nbsp;&nbsp;&nbsp;) Licença de Instalação (LI)</td>
    <td>( o ) Licença de Instalação (LI)</td>""")
    dados = FormularioParser(conteudo_html=html).gerar_json()
    pleito = dados["pleito"]
    assert pleito["tipo_licenca"] == "LI", pleito
    assert pleito["natureza"] == "Renovação"
    assert pleito["fases_componentes"] == ["LI"]


def test_matricula_validade_90_dias_corridos():
    """Validade da matrícula = 90 DIAS CORRIDOS da emissão (não 30)."""
    from datetime import date, timedelta
    from licenciamento.validador_documentos import ValidadorDocumentos
    validador = ValidadorDocumentos()
    assert validador.matricula_validade_dias == 90
    ref = date(2026, 9, 16)

    def texto_com_emissao(dias: int) -> str:
        emissao = ref - timedelta(days=dias)
        meses = {1: "janeiro", 2: "fevereiro", 3: "março", 4: "abril", 5: "maio",
                 6: "junho", 7: "julho", 8: "agosto", 9: "setembro", 10: "outubro",
                 11: "novembro", 12: "dezembro"}
        data_txt = f"{emissao.day} de {meses[emissao.month]} de {emissao.year}"
        return ("MATRÍCULA Nº 41.203 - Registro de Imóveis de Campo Bom/RS\n"
                "Imóvel: Rua Coronel João Corrêa, 215.\n"
                f"Campo Bom, {data_txt}.\n"
                "Oficial de Registro de Imóveis\n"
                "(documento assinado digitalmente)")

    dentro = validador.validar_matricula("matricula.txt", texto_com_emissao(89), ref)
    assert dentro.status.value == "CONFORME" or dentro.status.name == "CONFORME"
    vencida = validador.validar_matricula("matricula.txt", texto_com_emissao(91), ref)
    msg = " ".join(vencida.itens_reprovados)
    assert "90" in msg and "corridos" in msg
    # a norma citada também declara 'dias corridos'
    assert "corridos" in vencida.norma_tr


def test_sem_botao_exemplo_na_etapa_1():
    """Etapa 1 fica SOMENTE com o upload: sem botão 'Carregar exemplo fictício'."""
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(RAIZ / "app.py"), default_timeout=120).run()
    assert not at.exception
    rotulos = [b.label for b in at.button]
    assert not any("exemplo" in r.lower() for r in rotulos), rotulos
    assert any("Analisar" in r for r in rotulos), rotulos


def test_painel_etapa2_2col_circulo_lp_e_taxa():
    """PONTE A PONTE do formulário do licenciador (2 colunas, círculo '( o )'):
    o painel da Etapa 2 mostra LP (não LOR) com taxa 72,10 URM e a natureza."""
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(RAIZ / "app.py"), default_timeout=180)
    at.run()
    assert not at.exception
    at.radio[0].set_value("LP")                    # licença pleiteada
    at.radio[1].set_value("Primeira licença")      # natureza
    at.run()
    form_bytes = (EXEMPLOS / "formulario_MOTIVO_2col_circulo.htm").read_bytes()
    at.file_uploader[0].set_value(
        [("formulario_2col.htm", form_bytes, "text/html")])
    at.run()
    assert not at.exception
    [b for b in at.button if "Analisar" in b.label][0].click()
    at.run()
    assert not at.exception, [e.value[:300] for e in at.exception]
    metricas = {m.label: m.value for m in at.metric}
    assert metricas.get("Licença pleiteada") == "LP", metricas
    assert metricas.get("Taxa (URMs)") == "72,10", metricas
    captions = " | ".join(c.value for c in at.caption)
    assert "seleção sua na Etapa 1" in captions, captions
    assert "Primeira licença" in captions


# ==============================================================================
# Reconhecimento robusto do MOTIVO (spans do Word) + documentos com nome alterado
# ==============================================================================
def test_motivo_lista_simples_wingdings():
    """Formulário com a lista SIMPLES do item 3 (Licença Única, LP, LI, LO,
    LIR, LOR) e símbolos Wingdings do Word: vazio='o' em spans que o
    get_text separa do rótulo; marcado='þ' em Licença Prévia -> LP."""
    dados = _parse("formulario_MOTIVO_lista_simples.htm")
    pleito = dados["pleito"]
    assert pleito["tipo_licenca"] == "LP", pleito
    assert pleito["fases_componentes"] == ["LP"]
    assert len(dados["documentos_exigidos"]["lista_deduplicada"]) == 5
    fin = AgenteFinanceiro().calcular_do_parser(dados)
    assert fin["total_urm"] == pytest.approx(72.10)


def test_motivo_todos_com_circulo_menos_o_marcado():
    """Todas as opções com '( o )' e UMA com '( X )': a marca é o símbolo
    MINORITÁRIO (diferença entre as linhas) - e não a primeira linha."""
    linhas_opcoes = ["Licença Única", "Licença Prévia", "Licença de Instalação",
                     "Licença de Operação",
                     "Licença de Instalação e Regularização",
                     "Licença de Operação e Regularização"]
    corpo = "".join(
        f"<p>( {'X' if nome == 'Licença Prévia' else 'o'} ) {nome}</p>"
        for nome in linhas_opcoes)
    html = ("<!DOCTYPE html><html><body><h2>3. MOTIVO DO ENCAMINHAMENTO À SEMA</h2>"
            f"{corpo}<h2>4. DOCUMENTAÇÃO</h2><ul><li>Formulário assinado</li></ul>"
            "</body></html>")
    dados = FormularioParser(conteudo_html=html).gerar_json()
    assert dados["pleito"]["tipo_licenca"] == "LP", dados["pleito"]


def test_documento_nome_alterado_reconhecido_e_aprendido(tmp_path):
    """Pipeline do licenciador: nome alterado ('°Cópia da matrícula
    atualizada') reconhece PELO NOME; nome esdrúxulo ('doc_escaneado_0912')
    reconhece PELO CONTEÚDO e o sistema APRENDE o nome para os próximos
    processos; a matrícula ausente no formulário é COMPLETADA pelo anexo."""
    from licenciamento.identificador_documentos import IdentificadorDocumentos

    idf = IdentificadorDocumentos(caminho=tmp_path / "aprendido.json")
    html = (EXEMPLOS / "formulario_MOTIVO_lista_simples.htm").read_text(
        encoding="utf-8")
    dados = FormularioParser(conteudo_html=html).gerar_json()
    assert dados["empreendimento"].get("matricula_imovel") is None

    matricula_txt = ("CERTIDÃO DE INTEIRO TEOR\nMATRÍCULA Nº 41.203\n"
                     "Registro de Imóveis de Campo Bom/RS - Serventia e Registro\n"
                     "Campo Bom, 10 de setembro de 2026.")
    art_txt = ("Anotação de Responsabilidade Técnica\nART Nº 55210201145\n"
               "CREA-RS - Responsável Técnico: Eng. Ambiental Paulo Nunes")
    anexos = ["°Cópia da matrícula atualizada.txt", "doc_escaneado_0912.txt"]
    textos = {anexos[0]: matricula_txt, anexos[1]: art_txt}

    resultado = AgenteAdministrativo().auditar(
        dados, anexos, textos_anexados=textos, identificador=idf)
    # campo crítico completado pelo documento (não derruba mais o processo)
    assert dados["empreendimento"]["matricula_imovel"] == "41.203"
    assert any("COMPLETADO" in a for a in resultado["avisos"])
    assert not any("Matrícula" in b for b in resultado["bloqueios"])
    # ART esdrúxula: reconhecida pelo CONTEÚDO e APRENDIDA
    assert dados["responsavel_tecnico"]["registro_art"] == "55210201145"
    aprendidos = {i["nome"]: i["tipo"] for i in idf.aprendidos}
    assert aprendidos.get("doc escaneado 0912") == "ART"
    via_art = resultado["origem_ok"].get("ART do responsável técnico", {})
    assert via_art.get("via") in ("conteudo", "aprendido")

    # aprendizado persiste: nova instância reconhece a variação do nome
    idf2 = IdentificadorDocumentos(caminho=tmp_path / "aprendido.json")
    de_novo = idf2.identificar("doc_escaneado_0912_versao_final.txt", None)
    assert de_novo["tipo"] == "ART"
    assert de_novo["via"] in ("aprendido", "nome")


def test_painel_lista_simples_mostra_lp_e_taxa():
    """Ponte a ponte do formulário com a lista simples do item 3 (símbolos
    separados por spans): o painel exibe LP e a taxa 72,10 URM."""
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(RAIZ / "app.py"), default_timeout=180)
    at.run()
    assert not at.exception
    at.radio[0].set_value("LP")
    at.radio[1].set_value("Primeira licença")
    at.run()
    form_bytes = (EXEMPLOS / "formulario_MOTIVO_lista_simples.htm").read_bytes()
    at.file_uploader[0].set_value(
        [("formulario_lista_simples.htm", form_bytes, "text/html")])
    at.run()
    assert not at.exception
    [b for b in at.button if "Analisar" in b.label][0].click()
    at.run()
    assert not at.exception, [e.value[:300] for e in at.exception]
    metricas = {m.label: m.value for m in at.metric}
    assert metricas.get("Licença pleiteada") == "LP", metricas
    assert metricas.get("Taxa (URMs)") == "72,10", metricas


# ==============================================================================
# OUTROS MEIOS de leitura: HTML bruto com inputs mapeados + confirmação manual
# ==============================================================================
def test_leitura_html_bruto_com_inputs_de_formulario():
    """MEIO ALTERNATIVO: quando get_text não entrega símbolo algum (controles
    <input> do Word), o parser reconstrói a seção do HTML BRUTO mapeando cada
    input para seu símbolo (marcado->'☒', vazio->'○') e reattacha ao rótulo."""
    html = ("<html><body><h2>3. MOTIVO DO ENCAMINHAMENTO À SEMA</h2><div>"
            "<input type=\"checkbox\"><span>Licença Única</span><br>"
            "<input type=\"checkbox\" checked><span>Licença Prévia</span><br>"
            "<input type=\"checkbox\"><span>Licença de Instalação</span><br>"
            "<input type=\"checkbox\"><span>Licença de Operação</span></div>"
            "<h2>4. DOCUMENTAÇÃO</h2></body></html>")
    parser = FormularioParser(conteudo_html=html)
    sinteticas = parser._linhas_da_secao_em_texto_sintetico()
    assert "☒ Licença Prévia" in sinteticas, sinteticas
    dados = parser.gerar_json()
    assert dados["pleito"]["tipo_licenca"] == "LP", dados["pleito"]


def test_selecao_etapa1_destrava_fluxo_sem_marca_no_formulario():
    """NOVA ABORDAGEM: o licenciador DECLARA o pleito na Etapa 1 (LP) e o
    formulário cuja marca não sobreviveu não trava nada - a listagem segue
    a LP e a taxa é calculada (72,10 URM, Tabela A Pequeno/Baixo)."""
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(RAIZ / "app.py"), default_timeout=180)
    at.run()
    assert not at.exception
    at.radio[0].set_value("LP")                    # licença pleiteada
    at.radio[1].set_value("Primeira licença")      # natureza
    at.run()
    sem_marca = (EXEMPLOS / "formulario_MOTIVO_sem_marca.htm").read_bytes()
    at.file_uploader[0].set_value(
        [("formulario_sem_marca.htm", sem_marca, "text/html")])
    at.run()
    assert not at.exception
    [b for b in at.button if "Analisar" in b.label][0].click()
    at.run()
    assert not at.exception, [e.value[:300] for e in at.exception]
    metricas = {m.label: m.value for m in at.metric}
    assert metricas.get("Licença pleiteada") == "LP", metricas
    assert metricas.get("Taxa (URMs)") == "72,10", metricas
    captions = " | ".join(c.value for c in at.caption)
    assert "seleção sua na Etapa 1" in captions, captions


def test_divergencia_marcao_x_selecao_sinalizada():
    """Formulário MARCADO como LP, licenciador seleciona LOR: prevalece a
    seleção e a divergência é sinalizada no painel (nunca em silêncio)."""
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(RAIZ / "app.py"), default_timeout=180)
    at.run()
    assert not at.exception
    at.radio[0].set_value("LOR")
    at.radio[1].set_value("Primeira licença")
    at.run()
    form_bytes = (EXEMPLOS / "formulario_MOTIVO_2col_circulo.htm").read_bytes()
    at.file_uploader[0].set_value(
        [("formulario_2col.htm", form_bytes, "text/html")])
    at.run()
    assert not at.exception
    [b for b in at.button if "Analisar" in b.label][0].click()
    at.run()
    assert not at.exception, [e.value[:300] for e in at.exception]
    metricas = {m.label: m.value for m in at.metric}
    assert metricas.get("Licença pleiteada") == "LOR", metricas
    avisos = [w.value for w in at.warning]
    assert any("DIVERGÊNCIA" in a for a in avisos), avisos


def test_parser_aplicar_pleito_manual_remonta_listagem():
    """aplicar_pleito_manual troca o tipo e RE-MONTA a listagem pelas fases
    (LI -> só a lista da LI; LOR -> LP+LI+LO do próprio formulário) e
    registra divergência quando a marcação lida era outra."""
    html = (EXEMPLOS / "formulario_MOTIVO_2col_circulo.htm").read_text(
        encoding="utf-8")
    parser = FormularioParser(conteudo_html=html)
    dados = parser.parse()
    assert dados["pleito"]["tipo_licenca"] == "LP"  # lido da marcação

    dados_li = parser.aplicar_pleito_manual("LI", "Renovação")
    assert dados_li["pleito"]["tipo_licenca"] == "LI"
    assert dados_li["pleito"]["fases_componentes"] == ["LI"]
    assert len(dados_li["documentos_exigidos"]["lista_deduplicada"]) == 3
    assert dados_li["pleito"]["natureza"] == "Renovação"

    qtde_li = len(dados_li["documentos_exigidos"]["lista_deduplicada"])
    dados_lor = parser.aplicar_pleito_manual("LOR", "Primeira licença")
    # aplicar_pleito_manual muta o MESMO dict (self.dados): a contagem do LI
    # precisa ser capturada antes
    assert dados_lor is dados_li
    assert dados_lor["pleito"]["fases_componentes"] == ["LP", "LI", "LO"]
    assert "DIVERGÊNCIA" in dados_lor["pleito"]["divergencia_selecao"]
    # listagem do LOR vem das 3 fases do PRÓPRIO formulário (deduplicada)
    assert len(dados_lor["documentos_exigidos"]["lista_deduplicada"]) > qtde_li


# ==============================================================================
# Parcelamento de solo (LP): 'Documentos Requeridos' numerados + RTs da seção 4.3
# ==============================================================================
def test_parcelamento_lp_lista_numerada_14_documentos():
    """Formulário de PARCELAMENTO (LP): a seção 'Documentos Requeridos' no
    final fornece a listagem CONSERVANDO a numeração (1..14); a taxa usa a
    Tabela D (faixa 10-20 ha) com a área lida do formulário."""
    dados = _parse("formulario_PARCELAMENTO_LP.htm")
    pleito = dados["pleito"]
    assert pleito["tipo_licenca"] == "LP", pleito
    lista = dados["documentos_exigidos"]["lista_deduplicada"]
    assert len(lista) == 14, lista
    assert lista[0].startswith("1. Diretrizes Urbanísticas")
    assert lista[3].startswith("4. Cópia da matrícula atualizada")
    assert lista[13].startswith("14. ART de profissional habilitado")
    emp = dados["empreendimento"]
    assert emp["area_total_ha"] == pytest.approx(12.5)
    assert emp["codram"]
    fin = AgenteFinanceiro().calcular_do_parser(dados)
    assert fin["total_urm"] == pytest.approx(2294.76)  # Tabela D, 10-20 ha, LP


def test_responsaveis_4_3_extraidos_e_cruzados_nos_anexos():
    """Seção 4.3 (demais responsáveis de etapas): RTT/ART de cada profissional
    é procurada nos documentos com NOME e REGISTRO batendo; ausente => aviso."""
    dados = _parse("formulario_PARCELAMENTO_LP.htm")
    rts = dados["responsaveis_etapas"]
    assert len(rts) == 2, rts
    assert rts[0]["nome"] == "Ana Prado Schneider"
    assert rts[0]["art_rtt"] == "RTT 55210098745"
    assert "CREA" in (rts[0]["registro"] or "")
    assert rts[1]["art_rtt"] == "RTT 55210112458"

    matricula_txt = ("MATRÍCULA Nº 55.888 Registro de Imóveis\n"
                     "Campo Bom, 10 de setembro de 2026.")
    laudo_txt = ("Laudo Geológico com Sondagem e Ensaios de Infiltração\n"
                 "Responsável Técnica: Ana Prado Schneider CREA 84512/D "
                 "RTT 55210098745")
    anexos = ["°Cópia da matrícula atualizada.txt", "copia_cnpj.pdf",
              "contrato_social.pdf", "laudo_geologico_sondagem.pdf"]
    textos = {anexos[0]: matricula_txt, anexos[3]: laudo_txt,
              anexos[1]: "Comprovante de Inscrição CNPJ 45.678.912/0001-03",
              anexos[2]: "Contrato Social da Vale Verde Incorporadora"}
    resultado = AgenteAdministrativo().auditar(
        dados, anexos, textos_anexados=textos)
    conf = resultado["conferencia_responsaveis"]
    assert conf[0]["encontrado"] is True
    assert conf[0]["anexo"] == "laudo_geologico_sondagem.pdf"
    assert "nome e registro" in conf[0]["nivel"]
    assert conf[1]["encontrado"] is False          # RTT do Carlos não veio
    assert any("55210112458" in a for a in resultado["avisos"])
    # documentos reconhecidos mesmo com o nº na frente do exigido
    assert any("matrícula" in d.lower() and "4." in d
               for d in resultado["documentos_ok"])


def test_deduplicacao_ignora_numeracao_da_listagem():
    """'1. Matrícula...' na LP e '2. Matrícula...' na LO são o MESMO documento:
    a desduplicação compara SEM a numeração e mantém a 1ª ocorrência (c/ nº)."""
    lista, removidos = FormularioParser._deduplicar_documentos(
        ["1. Matrícula do imóvel atualizada", "2. Matrícula do imóvel atualizada",
         "3. PGRS"])
    assert len(lista) == 2
    assert lista[0].startswith("1. Matrícula")
    assert any("2." in r["documento"] for r in removidos)


# ==============================================================================
# FORMULÁRIO REAL (Parcelamento/Condomínios - estrutura Maria Belle, Drive)
# ==============================================================================
REAL = "formulario_MARIA_BELLE_PARCELAMENTO.htm"


def test_formulario_real_leitura_completa_do_cabecalho():
    """Formulário real Word->HTML: Porte/Potencial ETIQUETADO ('Porte mínimo /
    Potencial Poluidor Médio'), CODRAM '3414,40 - ...', matrícula em célula
    vizinha ('Nº matrícula atual do imóvel'), áreas em m² e coordenadas
    'Lat.(º): -29.691629' SIRGAS 2000 decimal."""
    dados = _parse(REAL)
    emp = dados["empreendimento"]
    assert emp["porte"] == "Mínimo" and emp["potencial_poluidor"] == "Médio"
    assert emp["codram"] == "3414,40"
    assert emp["matricula_imovel"] == "33024"
    assert emp["area_intervencao_ha"] == pytest.approx(0.1784)  # 1.783,75 m²
    coords = emp["coordenadas"]
    assert coords["formato"] == "GEOGRAFICA"
    assert coords["latitude"] == pytest.approx(-29.691629)
    assert coords["longitude"] == pytest.approx(-50.054433)
    # ART lida do próprio formulário (seção 8)
    assert dados["responsavel_tecnico"]["registro_art"] == "202613404"


def test_formulario_real_listagem_por_tipo_de_licenca():
    """Listagens REAIS no final do formulário (parágrafos numerados, itens
    quebrados em várias linhas): LP=14, LI=14, LO=5; LOR=29 (33 com as
    repetições entre fases suprimidas: 'ART de profissional' e 'Arquivo
    KMZ/KML e DWG' aparecem 1x); RENOVAÇÃO tem listagem própria de 5 itens."""
    parser = FormularioParser(str(EXEMPLOS / REAL))
    parser.parse()

    lp = parser.aplicar_pleito_manual("LP", "Primeira licença")
    lista_lp = lp["documentos_exigidos"]["lista_deduplicada"]
    assert len(lista_lp) == 14, lista_lp
    assert lista_lp[0].startswith("1. Diretrizes Urbanísticas")
    assert lista_lp[7].startswith("8. Estudo de Impacto de Vizinhança")
    assert "com ART de responsável técnico habilitado;" in lista_lp[7]  # continuação
    assert lista_lp[13].startswith("14. ART de profissional habilitado")
    # item 14 LIMPO: o letreiro entre listagens ('Licença Prévia (LP), ...')
    # NÃO é colado no fim do item
    assert "Licença Prévia (LP)," not in lista_lp[13]

    li = parser.aplicar_pleito_manual("LI", "Primeira licença")
    assert len(li["documentos_exigidos"]["lista_deduplicada"]) == 14
    assert li["documentos_exigidos"]["lista_deduplicada"][0].startswith(
        "1. Projeto de instalação do abastecimento")

    lo = parser.aplicar_pleito_manual("LO", "Primeira licença")
    assert len(lo["documentos_exigidos"]["lista_deduplicada"]) == 5

    lor = parser.aplicar_pleito_manual("LOR", "Primeira licença")
    lista_lor = lor["documentos_exigidos"]["lista_deduplicada"]
    assert len(lista_lor) == 29  # 14 + 14 + 5 - 4 repetições suprimidas
    assert sum(1 for x in lista_lor if "ART de profissional habilitado" in x) == 1
    assert sum(1 for x in lista_lor if "KMZ/KML e DWG" in x) == 1

    ren = parser.aplicar_pleito_manual("LO", "Renovação")
    lista_ren = ren["documentos_exigidos"]["lista_deduplicada"]
    assert len(lista_ren) == 5
    assert lista_ren[0].startswith("1. Cópia da licença a ser renovada")


def test_formulario_real_taxa_tabela_d_parcelamento():
    """Taxa do parcelamento: TABELA D pela área de intervenção (1.783,75 m² =
    0,1784 ha -> faixa 0 a 5 ha) => LP 1.290,70 URM."""
    # fluxo real: o licenciador SELECIONA LP na Etapa 1 (formulário sem
    # marca legível na seção 3 -> o pleito vem da seleção)
    parser = FormularioParser(str(EXEMPLOS / REAL))
    dados = parser.aplicar_pleito_manual("LP", "Primeira licença")
    fin = AgenteFinanceiro().calcular_do_parser(dados)
    assert fin["total_urm"] == pytest.approx(1290.70)
    assert fin["composicao_fases"] == {"LP": 1290.70}


def test_formulario_real_rts_43_art_sem_prefixo_cruzados():
    """Seção 4.3 real: ART vem SEM prefixo na tabela ('17246618') - leitura
    posicional pelo cabeçalho; RTs cruzadas com os documentos apresentados."""
    dados = _parse(REAL)
    rts = dados["responsaveis_etapas"]
    nomes = [r["nome"] for r in rts]
    assert "Raquel Beckes" in nomes
    raquel = next(r for r in rts if r["nome"] == "Raquel Beckes")
    assert raquel["art_rtt"] == "ART 17246618"
    assert raquel["registro"] == "A67154-1"
    assert "urban" in (raquel["etapa"] or "").lower()
    assert sum(1 for r in rts if r["nome"]) == 4  # linhas vazias ignoradas

    texto_urbanistico = ("Projeto Urbanístico do Loteamento\n"
                         "Responsável Técnica: Raquel Beckes, Registro CAU A67154-1, "
                         "ART 17246618")
    anexos = ["projeto_urbanistico.pdf", "inventario_fauna.pdf"]
    textos = {anexos[0]: texto_urbanistico,
              anexos[1]: "Inventário de fauna - ART 99999999 de outro profissional"}
    resultado = AgenteAdministrativo().auditar(dados, anexos, textos_anexados=textos)
    conf = {c["profissional"]: c for c in resultado["conferencia_responsaveis"]}
    assert conf["Raquel Beckes"]["encontrado"] is True
    assert conf["Raquel Beckes"]["anexo"] == "projeto_urbanistico.pdf"
    assert conf["Keli Daiane Bernardes dos Santos"]["encontrado"] is False


def test_listagem_ancorada_no_titulo_documentos_requeridos():
    """A LISTAGEM é lida EXCLUSIVAMENTE após o título 'Documentos Requeridos'
    (no final do formulário): itens numerados do CORPO (Quadro diagnóstico
    '1. Existe banhado?') ficam FORA da exigência."""
    # fluxo real: licenciador seleciona LP na Etapa 1 (sem marca legível)
    parser = FormularioParser(str(EXEMPLOS / REAL))
    dados = parser.aplicar_pleito_manual("LP", "Primeira licença")
    lista = dados["documentos_exigidos"]["lista_deduplicada"]
    assert len(lista) == 14
    assert not any("banhado" in x.lower() for x in lista)
    assert not any("inundação" in x.lower() for x in lista)
    # fonte registrada como a listagem ancorada no título
    assert "Documentos Requeridos" in dados["documentos_exigidos"]["fonte_checklist"]


# ==============================================================================
# Etapa 1: 'Autorização Geral' e 'PRAD' como tipos selecionáveis
# ==============================================================================
def test_autorizacao_geral_na_etapa1_listagem_e_taxa():
    """'Autorização Geral' selecionada na Etapa 1: NÃO herda as listagens
    LP/LI/LO do formulário - usa o checklist oficial da espécie; taxa fixa
    provisória de 52,20 URM (com aviso)."""
    parser = FormularioParser(str(EXEMPLOS / REAL))
    parser.parse()
    d = parser.aplicar_pleito_manual("AUTORIZACAO", "Primeira licença")
    lista = d["documentos_exigidos"]["lista_deduplicada"]
    assert 3 <= len(lista) <= 12, lista
    assert not any("Diretrizes Urbanísticas" in x for x in lista)  # era da LP
    fin = AgenteFinanceiro().calcular_do_parser(d)
    assert fin["total_urm"] == pytest.approx(52.20)


def test_prad_na_etapa1_sem_listagem_e_taxa_sinalizada():
    """'PRAD - Plano de Recuperação de Área Degradada' na Etapa 1: tipo
    aceito (fases ['PRAD']); a listagem fica VAZIA até chegar o documento de
    referência (Drive) e a taxa é sinalizada explicitamente como não
    publicada no Manual (não é licença por fase)."""
    parser = FormularioParser(str(EXEMPLOS / REAL))
    parser.parse()
    d = parser.aplicar_pleito_manual("PRAD", "Primeira licença")
    assert d["pleito"]["tipo_licenca"] == "PRAD"
    assert d["pleito"]["fases_componentes"] == ["PRAD"]
    assert d["documentos_exigidos"]["lista_deduplicada"] == []
    fin = AgenteFinanceiro().calcular_do_parser(d)
    assert fin["total_urm"] is None
    assert "não publicada" in (fin.get("erro") or "")


def test_prad_e_autorizacao_no_painel_da_etapa2():
    """Ponte a ponte: seleção PRAD na Etapa 1 -> painel mostra 'PRAD' com o
    aviso da taxa; seleção AUTORIZACAO -> 'AUTORIZACAO' com 52,20 URM."""
    from streamlit.testing.v1 import AppTest
    for tipo, taxa_esperada in [("PRAD", "—"), ("AUTORIZACAO", "52,20")]:
        at = AppTest.from_file(str(RAIZ / "app.py"), default_timeout=180)
        at.run()
        assert not at.exception
        at.radio[0].set_value(tipo)
        at.radio[1].set_value("Primeira licença")
        at.run()
        fb = (EXEMPLOS / REAL).read_bytes()
        at.file_uploader[0].set_value([(f"{tipo}.html", fb, "text/html")])
        at.run()
        assert not at.exception
        [b for b in at.button if "Analisar" in b.label][0].click()
        at.run()
        assert not at.exception, [e.value[:300] for e in at.exception]
        metricas = {m.label: m.value for m in at.metric}
        assert metricas.get("Licença pleiteada") == tipo, metricas
        assert metricas.get("Taxa (URMs)") == taxa_esperada, metricas


# ==============================================================================
# BANCO DE CHECKLISTS POR TIPO DE FORMULÁRIO (cabeçalho do HTML + licença)
# ==============================================================================
def _formulario_do_tipo(header: str) -> str:
    return (f"<html><head><title>{header}</title></head><body>"
            f"<h1>Formulário para Licenciamento Ambiental de: {header}</h1>"
            "<h2>1. IDENTIFICAÇÃO</h2><table><tr><td>Nome/Razão Social:</td>"
            "<td>Teste Ltda</td></tr></table></body></html>")


def test_banco_cabecalho_comercios_e_servicos_lp():
    """Cabeçalho 'COMÉRCIOS E SERVIÇOS' + LP na Etapa 1: listagem vem do BANCO
    (16 docs), NÃO do corpo do formulário."""
    parser = FormularioParser(conteudo_html=_formulario_do_tipo("COMÉRCIOS E SERVIÇOS"))
    parser.parse()
    d = parser.aplicar_pleito_manual("LP", "Primeira licença")
    assert d["tipo_formulario"]["chave"] == "comercios_servicos"
    lista = d["documentos_exigidos"]["lista_deduplicada"]
    assert len(lista) == 16, lista
    assert lista[0].startswith("1. Formulário de Licenciamento")
    assert "banco de checklists" in d["documentos_exigidos"]["fonte_checklist"]


def test_banco_erb_lor_criacao_animais_lir_acude_unico():
    """ERB + LOR -> 13 docs próprios; Criação de Animais + LIR -> lista da LP
    (equivalência do documento de referência); Açude (processo único) -> 12
    docs para qualquer licença."""
    p_erb = FormularioParser(conteudo_html=_formulario_do_tipo("ESTAÇÃO RÁDIO-BASE"))
    p_erb.parse()
    d = p_erb.aplicar_pleito_manual("LOR", "Primeira licença")
    assert len(d["documentos_exigidos"]["lista_deduplicada"]) == 13
    assert any("Laudo radiométrico" in x for x in
               d["documentos_exigidos"]["lista_deduplicada"])

    p_cri = FormularioParser(conteudo_html=_formulario_do_tipo("CRIAÇÃO DE ANIMAIS"))
    p_cri.parse()
    d2 = p_cri.aplicar_pleito_manual("LIR", "Primeira licença")
    lista2 = d2["documentos_exigidos"]["lista_deduplicada"]
    assert len(lista2) == 11
    assert any("Criação Animal" in x for x in lista2)

    p_acude = FormularioParser(conteudo_html=_formulario_do_tipo("ABERTURA DE AÇUDE"))
    p_acude.parse()
    d3 = p_acude.aplicar_pleito_manual("AUTORIZACAO", "Primeira licença")
    assert len(d3["documentos_exigidos"]["lista_deduplicada"]) == 12
    assert any("Outorga" in x for x in d3["documentos_exigidos"]["lista_deduplicada"])


def test_banco_industriais_renovacao_e_lir_soma_dedup():
    """Industriais: RENOVAÇÃO tem listagem própria (12); LIR soma LP+LI do
    banco com desduplicação (menos que a soma bruta)."""
    p = FormularioParser(conteudo_html=_formulario_do_tipo("ATIVIDADES INDUSTRIAIS"))
    p.parse()
    d_ren = p.aplicar_pleito_manual("LP", "Renovação")
    assert len(d_ren["documentos_exigidos"]["lista_deduplicada"]) == 12
    assert "RENOVACAO" in d_ren["documentos_exigidos"]["por_fase"]

    d_lir = p.aplicar_pleito_manual("LIR", "Primeira licença")
    lista = d_lir["documentos_exigidos"]["lista_deduplicada"]
    assert 20 < len(lista) < 30  # 16 + 14 brutos, com repetidos suprimidos


def test_banco_nao_casa_condominios_nem_troca_listagem():
    """O formulário de CONDOMÍNIOS HORIZONTAIS/VERTICAIS (Maria Belle) NÃO é
    confundido com 'Parcelamento/Loteamentos' nem 'Desmembramento': sem
    entrada no banco, usa a listagem PRÓPRIA 'Documentos Requeridos' (14)."""
    parser = FormularioParser(str(EXEMPLOS / REAL))
    parser.parse()
    d = parser.aplicar_pleito_manual("LP", "Primeira licença")
    assert d["tipo_formulario"] is None
    lista = d["documentos_exigidos"]["lista_deduplicada"]
    assert len(lista) == 14
    assert "Documentos Requeridos" in d["documentos_exigidos"]["fonte_checklist"]


def test_codram_nao_arrasta_runon_da_tabela():
    """Bug reportado: campo CODRAM vinha com o código + TODO o texto seguinte
    do formulário (run-on da tabela), quebrando a coluna 'Tipo de
    empreendimento' da Etapa 2. Fica só o código."""
    html = ("<html><body><h1>LICENCIAMENTO AMBIENTAL</h1><table>"
            "<tr><td>Nome/Razão Social:</td><td>Empreendimento Teste</td></tr>"
            "<tr><td>Ramo de Atividade:</td><td>Parcelamento</td></tr>"
            "<tr><td>CODRAM:</td><td>3414,40 Parcelamento do solo para fins "
            "residenciais e mistos 2. IDENTIFICAÇÃO DO PLEITO "
            "3. IDENTIFICAÇÃO DO EMPREENDIMENTO Área Total (ha): 5,0 "
            "Matrícula do Imóvel: 12345</td></tr></table></body></html>")
    parser = FormularioParser(conteudo_html=html)
    parser.parse()
    assert parser.dados["empreendimento"]["codram"] == "3414,40"

    # formato oficial pontilhado com dígito verificador também se mantém
    html2 = html.replace("3414,40 Parcelamento", "05.412.1-3 Loteamento")
    parser2 = FormularioParser(conteudo_html=html2)
    parser2.parse()
    assert parser2.dados["empreendimento"]["codram"] == "05.412.1-3"


# ==============================================================================
# PDFs ESCANEADOS (OCR) + CONFERÊNCIAS CNPJ e ART/RTT com o formulário
# ==============================================================================
def _pdf_escaneado(texto: str) -> bytes:
    """Gera um PDF de IMAGEM (página escaneada simulada) com o texto pedido."""
    import io
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (1400, 400), "white")
    d = ImageDraw.Draw(img)
    try:
        fonte = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 40)
    except Exception:
        fonte = None
    d.text((40, 160), texto, fill="black", font=fonte)
    buffer = io.BytesIO()
    img.save(buffer, "PDF", resolution=100)
    return buffer.getvalue()


def test_leitor_pdf_ocr_documento_escaneado():
    """Matrículas/ARTs chegam como PDF de imagens: o leitor detecta a ausência
    de camada de texto e roda OCR (texto vira legível p/ as conferências)."""
    from licenciamento.leitor_pdf import LeitorPDF
    if not LeitorPDF.ocr_disponivel():
        pytest.skip("OCR (rapidocr-onnxruntime) indisponível neste ambiente")
    import re as _re
    pdf = _pdf_escaneado("MATRICULA 33024 CNPJ 12.345.678/0001-95")
    texto, info = LeitorPDF.extrair(pdf)
    assert info["metodo"] == "ocr", (texto, info)
    assert "33024" in _re.sub(r"\D", "", texto), texto
    # plug completo: o validador também lê o escaneado
    from licenciamento.validador_documentos import ValidadorDocumentos
    v = ValidadorDocumentos()
    assert "33024" in _re.sub(r"\D", "",
                              v.extrair_texto("matricula.pdf", pdf))


def test_conferencia_cnpj_matricula_vs_formulario():
    """CNPJ do formulário x NÚMERO DE INSCRIÇÃO na matrícula (1ª linha/coluna):
    CONFERE, DIVERGENTE, NAO_ENCONTRADO, ANEXO_NAO_LEGIVEL e CPF->ignora."""
    from licenciamento.agente_administrativo import AgenteAdministrativo
    dados = {"empreendedor": {"cpf_cnpj": "12.345.678/0001-95"},
             "documentos_exigidos": {"lista_deduplicada": [
                 "Cópia da matrícula atualizada do imóvel"]}}

    def conferir(texto_matricula):
        textos = {"matricula_do_imovel.pdf": texto_matricula}
        return AgenteAdministrativo._conferir_cnpj_matricula(
            dados, ["matricula_do_imovel.pdf"], textos)

    # OCR cola tokens: regex tolerante reconhece mesmo assim
    r1 = conferir("Numero de Inscricao CNPJ12.345.678/0001-95MATRICULA 33024 FALHA 2")
    assert r1["status"] == "CONFERE"  # "2" solto não pode virar CNPJ
    # CNPJ com espaços quebrados pelo OCR
    r2 = conferir("N. INSCRICAO 12 345 678 0001 95 - matricula 33024")
    assert r2["status"] == "CONFERE"
    # divergente
    r = conferir("Inscricao CNPJ 98.765.432/0001-10 matricula 33024")
    assert r["status"] == "DIVERGENTE" and r["cnpj_encontrado"] == "98765432000110"
    # sem CNPJ no texto
    r3 = conferir("Matricula 33024 proprietario joao da silva")
    assert r3["status"] == "NAO_ENCONTRADO"
    # anexo sem texto (escaneado e OCR indisponível)
    r4 = conferir("")
    assert r4["status"] == "ANEXO_NAO_LEGIVEL"
    # formulário com CPF (11 dígitos): não há conferência de CNPJ
    dados_cpf = {**dados, "empreendedor": {"cpf_cnpj": "123.456.789-00"}}
    assert AgenteAdministrativo._conferir_cnpj_matricula(
        dados_cpf, ["matricula_do_imovel.pdf"],
        {"matricula_do_imovel.pdf": "CNPJ 12.345.678/0001-95"}) is None


def test_art_do_responsavel_principal_conferida():
    """TODAS as ARTs/RTTs são conferidas: o responsável principal (item 14)
    entra na conferência nº+nome, sem duplicar quando já listado na 4.3."""
    from licenciamento.agente_administrativo import AgenteAdministrativo
    dados = {"responsavel_tecnico": {"nome": "João Pedro Kessler",
                                     "registro_art": "14572403",
                                     "registro_crea": "RS233891"},
             "responsaveis_etapas": []}
    textos = {"art_joao.pdf": "ART 14572403 responsavel tecnico "
                              "JOAO PEDRO KESSLER registro RS233891"}
    conf = AgenteAdministrativo()._conferir_responsaveis_etapas(
        dados, ["art_joao.pdf"], textos)
    assert len(conf) == 1
    assert conf[0]["encontrado"] and "item 14" in conf[0]["etapa"]

    # mesma ART já listada na seção 4.3: NÃO duplica
    dados_dup = {**dados, "responsaveis_etapas": [
        {"nome": "João Pedro Kessler", "registro": "RS233891",
         "art_rtt": "14572403", "etapa": "Projeto"}]}
    conf2 = AgenteAdministrativo()._conferir_responsaveis_etapas(
        dados_dup, ["art_joao.pdf"], textos)
    assert len(conf2) == 1 and conf2[0]["etapa"] == "Projeto"
