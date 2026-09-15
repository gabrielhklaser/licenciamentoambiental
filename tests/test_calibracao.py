# -*- coding: utf-8 -*-
"""
Testes da calibração dirigida por configuração e da ingestão dos PDFs oficiais.

Execução:  .venv/bin/python -m pytest tests/test_calibracao.py -v
"""

import json
from pathlib import Path

import pytest

from licenciamento.agente_financeiro import AgenteFinanceiro
from licenciamento.auditor_tecnico import AuditorTecnico
from licenciamento.calibracao import Calibracao
from licenciamento.esquemas_tecnicos import MetricasRFO
from licenciamento.parser_formulario import FormularioParser

RAIZ = Path(__file__).resolve().parents[1]
EXEMPLOS = RAIZ / "exemplos"


@pytest.fixture
def pasta_tmp(tmp_path):
    return tmp_path


def test_calibracao_vazia_nao_ativa():
    c = Calibracao(pasta_config=str(pasta_tmp_var()))
    assert not c.ativa
    assert c.resumo()["ativa"] is False


def pasta_tmp_var():
    import tempfile
    return tempfile.mkdtemp()


def test_agente_financeiro_usa_tabela_calibrada(pasta_tmp):
    """Matriz calibrada SOBRESCREVE os padrões sem alterar a classe."""
    config = {
        "fonte": "Manual de Taxas (teste)", "revisado": True,
        "matriz": {"GERAL": {"MEDIO": {"ALTO": {"LP": 999.0, "LI": 999.0, "LO": 999.0}}}},
        "valores_especie": {"DECLARACAO": 77.0},
    }
    caminho = pasta_tmp / "taxas_urm.json"
    caminho.write_text(json.dumps(config), encoding="utf-8")

    fin = AgenteFinanceiro(caminho_taxas=str(caminho))
    r = fin.calcular_taxa("LOR", "Médio", "Alto", "Indústria")
    assert r["total_urm"] == pytest.approx(999.0 * 3)
    assert r["fonte_tabela"] == "Manual de Taxas (teste)"
    assert r["tabela_revisada"] is True
    assert fin.valores_especie["DECLARACAO"] == 77.0

    # padrão de classe intacto por outras instâncias sem calibração
    # (padrão = TABELA A oficial: Médio/Alto 1.523,60 + 1.508,20 + 1.969,60)
    fin_padrao = AgenteFinanceiro(caminho_taxas=str(pasta_tmp / "inexistente.json"))
    r2 = fin_padrao.calcular_taxa("LOR", "Médio", "Alto", "Indústria")
    assert r2["total_urm"] == pytest.approx(5001.40)

def test_calibracao_tabela_f_autorizacoes(pasta_tmp):
    """TABELA F calibrável via config/taxas_urm.json."""
    config = {
        "fonte": "Tabela F (teste)", "revisado": True,
        "tabela_f_autorizacoes": {
            "supressao_arvores": [
                {"faixa": "ate 10 arvores", "limite": 10, "valor": 8.0},
                {"faixa": "mais de 10", "limite": None, "valor": 40.0},
            ]
        },
    }
    caminho = pasta_tmp / "taxas_f.json"
    caminho.write_text(json.dumps(config), encoding="utf-8")
    fin = AgenteFinanceiro(caminho_taxas=str(caminho))
    r = fin.calcular_taxa("AUTORIZACAO", "Pequeno", "Baixo", "Supressão de árvores",
                          tipo_autorizacao="supressao_arvores",
                          quantidade_autorizacao=25)
    assert r["total_urm"] == pytest.approx(40.0)

def test_config_oficial_taxas_ativo():
    """config/taxas_urm.json (Manual SEMA) é o padrão ativo dos agentes."""
    fin = AgenteFinanceiro()
    assert fin.tabela_revisada is True
    assert "Manual de Legislação e Taxas Ambientais" in fin.fonte_tabela
    # Tabela A oficial (amostra)
    assert fin.matriz["GERAL"]["PEQUENO"]["BAIXO"]["LP"] == pytest.approx(72.10)
    assert fin.matriz["GERAL"]["EXCEPCIONAL"]["ALTO"]["LO"] == pytest.approx(17154.60)
    # Tabelas B/C/D/E oficiais
    assert fin.matriz["ERB"]["FIXO"]["_"]["LI"] == pytest.approx(1960.00)
    assert fin.matriz["LAVRA_MINERAL"]["0 a 5 ha"]["_"]["LO"] == pytest.approx(812.60)
    assert fin.matriz["PARCELAMENTO_SOLO"]["5 a 10 ha"]["_"]["LI"] == pytest.approx(2172.84)
    assert fin.matriz["COMERCIO"]["ate 50 m2"]["_"]["LP"] == pytest.approx(25.00)
    # Tabela F oficial (amostra)
    valores = [f["valor"] for f in fin.tabela_f["SUPRESSAO_ARVORES"]]
    assert valores == [4.0, 20.0, 50.0, 80.0]


def test_auditor_tecnico_usa_gabarito_calibrado(pasta_tmp):
    """Gabarito calibrado altera os parâmetros dos TRs na instância."""
    config = {
        "fonte": "TRs oficiais (teste)", "revisado": True,
        "parametros": {
            "rfo_densidade_minima_mudas_ha": 2500.0,
            "rscc_sondagem_base": 6,
        },
    }
    caminho = pasta_tmp / "gabarito_trs.json"
    caminho.write_text(json.dumps(config), encoding="utf-8")

    at = AuditorTecnico(caminho_gabarito=str(caminho))
    assert at.parametros["rfo_densidade_minima_mudas_ha"] == 2500.0
    assert at.calcular_pontos_sondagem_exigidos(1.0, "RSCC") == 6
    # mensagem cita o valor calibrado (2.500 em vez de 3.000)
    r = at.validar_rfo("laudo.pdf", MetricasRFO(
        nativos_suprimidos=10, exoticos_suprimidos=0,
        mudas_nativas_propostas=150, densidade_proposta_mudas_ha=2000.0,
        monitoramento_anos=2))
    assert any("2.500" in i for i in r.itens_reprovados)

    # densidade 2000 < 2500 continua reprovando (sem monitoramento informado também pende)

def test_parser_usa_checklist_oficial_como_fallback(pasta_tmp):
    """Sem checklist no HTML, o parser usa o checklist oficial calibrado."""
    pasta = pasta_tmp / "config"
    pasta.mkdir()
    config = {
        "fonte": "Formulários oficiais (teste)", "revisado": True,
        "documentos_por_fase": {
            "LI": ["Projeto executivo da torre", "Laudo de campos eletromagnéticos"],
        },
    }
    (pasta / "checklists_oficiais.json").write_text(
        json.dumps(config), encoding="utf-8")

    html_sem_checklist = """
    <html><body><h1>Formulário de Licenciamento</h1>
    <table><tr><td>Tipo de Licença</td><td>Licença de Instalação (LI)</td></tr>
    <tr><td>Nome/Razão Social</td><td>Teste Ltda</td></tr></table></body></html>
    """
    parser = FormularioParser(conteudo_html=html_sem_checklist, caminho_config=str(pasta))
    dados = parser.gerar_json()
    docs = dados["documentos_exigidos"]
    assert "checklist oficial" in docs["fonte_checklist"]
    assert "Projeto executivo da torre" in docs["lista_deduplicada"]
    assert len(docs["lista_deduplicada"]) == 2


def test_parser_prefere_checklist_do_html(pasta_tmp):
    """Quando o HTML traz o checklist, a config oficial fica apenas de reserva."""
    pasta = pasta_tmp / "config"
    pasta.mkdir()
    config = {"fonte": "TRs (teste)", "revisado": True,
              "documentos_por_fase": {"LP": ["Documento oficial X"]}}
    (pasta / "checklists_oficiais.json").write_text(
        json.dumps(config), encoding="utf-8")

    parser = FormularioParser(
        caminho_arquivo=str(EXEMPLOS / "formulario_LOR_medio_alto.htm"),
        caminho_config=str(pasta))
    dados = parser.gerar_json()
    assert dados["documentos_exigidos"]["fonte_checklist"] == "formulário HTML"
    assert "Documento oficial X" not in dados["documentos_exigidos"]["lista_deduplicada"]
    assert len(dados["documentos_exigidos"]["lista_deduplicada"]) == 14


# ==============================================================================
# Extratores da ingestão (com texto sintético representativo)
# ==============================================================================
def test_extrator_gabarito_trs_sintetico():
    from ferramentas.ingestar_pdfs import extrair_gabarito_trs
    paginas = [(
        "TR 1 - ATERROS: A distância vertical mínima entre a cota base do aterro e o "
        "lençol freático deverá ser de 1,5 m. Será exigido um mínimo de 4 furos de "
        "sondagem por área de até 1 hectare, acrescido de 1 furo adicional por hectare "
        "excedente. TR 2 - REPOSIÇÃO FLORESTAL: Serão exigidas 15 mudas nativas por "
        "indivíduo nativo suprimido e 3 mudas por indivíduo exótico suprimido, com "
        "densidade mínima de 3.000 mudas por hectare. TR 3 - PRAD: monitoramento com "
        "período mínimo de 4 anos. TR 4 - PCA: relatórios trimestrais na supressão de "
        "vegetação e semestrais na fase de obras."
    )]
    draft = extrair_gabarito_trs(paginas, "teste.pdf")
    p = {k: v["valor"] for k, v in draft["parametros"].items()}
    assert p["rscc_distancia_minima_lencol_m"] == pytest.approx(1.5)
    assert p["rscc_sondagem_base"] == 4
    assert p["rscc_sondagem_por_ha_excedente"] == 1
    assert p["rfo_mudas_por_nativo"] == 15
    assert p["rfo_mudas_por_exotico"] == 3
    assert p["rfo_densidade_minima_mudas_ha"] == pytest.approx(3000.0)
    assert p["prad_monitoramento_minimo_anos"] == 4
    assert p["pca_periodicidade_supressao"] == "trimestral"
    assert p["pca_periodicidade_obras"] == "semestral"
    assert draft["revisado"] is False


def test_extrator_taxas_sintetico():
    from ferramentas.ingestar_pdfs import extrair_taxas
    paginas = [(
        "MANUAL DE TAXAS - valor da URM é de R$ 125,00\n"
        "Porte Mínimo - Potencial Baixo: LP 52,20 | LI 52,20 | LO 52,20\n"
        "Porte Pequeno - Potencial Médio: LP 156,60 | LI 156,60 | LO 156,60\n"
        "TABELA ERB: LP 612,00 | LI 714,00 | LO 510,00\n"
        "Lavra Mineral: 5 a 10 ha: LP 313,20 | LI 313,20 | LO 313,20\n"
        "Autorização Ambiental: 52,20 URM\n"
    )]
    draft = extrair_taxas(paginas, "manual.pdf")
    assert draft["valor_urm_reais"] == pytest.approx(125.0)
    assert draft["matriz"]["GERAL"]["MINIMO"]["BAIXO"]["LP"] == pytest.approx(52.20)
    assert draft["matriz"]["GERAL"]["PEQUENO"]["MEDIO"]["LI"] == pytest.approx(156.60)
    assert draft["matriz"]["ERB"]["FIXO"]["_"]["LI"] == pytest.approx(714.00)
    assert draft["matriz"]["LAVRA_MINERAL"]["5 a 10 ha"]["_"]["LP"] == pytest.approx(313.20)
    assert draft["valores_especie"]["AUTORIZACAO"] == pytest.approx(52.20)


def test_extrator_checklists_sintetico():
    from ferramentas.ingestar_pdfs import extrair_checklists
    paginas = [(
        "FORMULÁRIO - LICENÇA PRÉVIA (LP)\nIDENTIFICAÇÃO DO EMPREENDEDOR\n"
        "DOCUMENTAÇÃO EXIGIDA\n"
        "1. Formulário de enquadramento preenchido e assinado\n"
        "2. Cópia da matrícula do imóvel\n"
        "3. Cópia da ART do responsável técnico\n"
    )]
    draft = extrair_checklists(paginas, "form.pdf")
    docs = draft["documentos_por_fase"]["LP"]
    assert len(docs) == 3
    assert any("matrícula do imóvel" in d.lower() for d in docs)


def test_fluxo_ingestao_e_promocao(pasta_tmp):
    """Draft gerado -> promovido -> agentes calibrados com revisado=true."""
    from ferramentas.ingestar_pdfs import extrair_gabarito_trs
    draft = extrair_gabarito_trs(
        ["mínimo de 4 furos de sondagem"], "trs.pdf")
    pasta = pasta_tmp / "config"
    caminho_draft = pasta / "gabarito_trs.draft.json"
    pasta.mkdir(parents=True)
    caminho_draft.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")

    # promoção via subprocess (mesma rotina do CLI)
    import subprocess, sys
    subprocess.run(
        [sys.executable, str(RAIZ / "ferramentas" / "ingestar_pdfs.py"),
         "--promover", "--pasta-config", str(pasta)],
        check=True, capture_output=True)
    assert not caminho_draft.exists()
    final = pasta / "gabarito_trs.json"
    assert final.exists()
    dados = json.loads(final.read_text(encoding="utf-8"))
    assert dados["revisado"] is True

    at = AuditorTecnico(caminho_gabarito=str(final))
    assert at.gabarito_revisado is True
    assert at.calcular_pontos_sondagem_exigidos(0.5, "RSCC") == 4
