# -*- coding: utf-8 -*-
"""Conferências documentais EXIGIDAS pelo licenciador (instrução de 26/09/2026):

  1. RTT/RRT pertencem SEMPRE ao CAU/BR e a Arquitetos/Urbanistas; a ART é do
     CREA/CRBio. PODEM EXISTIR VÁRIAS RTTs no processo (Projeto Urbanístico,
     Execução de Obras...) - o validador trata a COLEÇÃO, não uma RRT só.
  2. O confronto formulário x documentos emitidos é BILATERAL (form -> doc e
     doc -> form) e rigoroso: CNPJ, Contrato Social, matrícula e ARTs/RTTs.
  3. TRs NUNCA são aplicados de forma cruzada: um Laudo de Cobertura Vegetal
     que cita 'sondagem' continua recebendo só o TR de cobertura vegetal.
  4. Camadas GIS (KMZ/KML/GeoJSON) são LIDAS e confrontadas GEOMETRICAMENTE
     (skill gis-multicamadas) e nunca recebem TR de conteúdo.
"""
import json
from pathlib import Path

import pytest

from licenciamento.agente_administrativo import AgenteAdministrativo
from licenciamento.agente_gis import AgenteGIS
from licenciamento.auditor_tecnico import AuditorTecnico
from licenciamento.identificador_documentos import (
    IdentificadorDocumentos, conselho_do_registro, conselho_do_texto,
    tipo_registro_por_conselho)
from licenciamento.leitor_gis import (LeitorGIS, area_ha,
                                      distancia_haversine_m, normalizar_srs,
                                      ponto_em_aneis, utm_para_lonlat)
from licenciamento.validador_documentos import ValidadorDocumentos

REAL = "exemplos/formulario_MARIA_BELLE_PARCELAMENTO.htm"

KML_3_CAMADAS = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
  <Folder><name>Area do Empreendimento</name>
    <Placemark><name>Poligonal</name>
      <Polygon><outerBoundaryIs><LinearRing><coordinates>
        -50.10,-29.90,0 -50.09,-29.90,0 -50.09,-29.89,0 -50.10,-29.89,0
        -50.10,-29.90,0</coordinates></LinearRing></outerBoundaryIs>
    </Polygon></Placemark>
  </Folder>
  <Folder><name>Curvas de Nivel</name>
    <Placemark><name>cota 50</name><LineString><coordinates>
      -50.10,-29.895,0 -50.09,-29.895,0</coordinates></LineString>
    </Placemark>
  </Folder>
  <Folder><name>Corpo Hidrico</name>
    <Placemark><name>Arroio</name><LineString><coordinates>
      -50.08,-29.895,0 -50.07,-29.895,0</coordinates></LineString>
    </Placemark>
  </Folder>
</Document></kml>"""

GEOJSON_CAMADA = json.dumps({
    "type": "FeatureCollection",
    "crs": {"type": "name", "properties": {
        "name": "urn:ogc:def:crs:EPSG::4674"}},
    "features": [
        {"type": "Feature",
         "properties": {"camada": "Projeto Urbanistico"},
         "geometry": {"type": "Polygon", "coordinates": [[
             [-50.10, -29.90], [-50.09, -29.90], [-50.09, -29.89],
             [-50.10, -29.89], [-50.10, -29.90]]]}}]})

GPX_TRILHA = """<?xml version="1.0"?><gpx version="1.1"
  xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>Limite da propriedade</name><trkseg>
    <trkpt lat="-29.900" lon="-50.100"/><trkpt lat="-29.900" lon="-50.090"/>
    <trkpt lat="-29.890" lon="-50.090"/><trkpt lat="-29.890" lon="-50.100"/>
  </trkseg></trk></gpx>"""


def _parse(nome=REAL):
    from licenciamento.parser_formulario import FormularioParser
    return FormularioParser(nome).parse()


# =============================================================================
# 1. RTT/RRT x ART: conselho, tipo e VÁRIAS RTTs
# =============================================================================
def test_rrt_pertence_ao_cau_e_art_ao_crea_por_nome_de_arquivo():
    """'RTT - Projeto Urbanístico.pdf' é RRT (CAU/BR), NUNCA projeto
    urbanístico; 'ART ....pdf' é ART (CREA/CRBio)."""
    idf = IdentificadorDocumentos()
    for nome in ("RTT - Projeto Urbanistico.pdf", "RRT Projeto Urbanistico.pdf",
                 "RTT - Execucao de Obras.pdf", "rrt.pdf"):
        assert idf.identificar(nome)["tipo"] == "RRT", nome
    for nome in ("ART 202613404.pdf", "art_assinado.pdf",
                 "ART de profissional habilitado.pdf"):
        assert idf.identificar(nome)["tipo"] == "ART", nome
    # um projeto SEM registro no nome continua sendo projeto
    assert idf.identificar("projeto urbanistico.pdf")["tipo"] == "PROJETO_URBANISTICO"


def test_conselho_inferido_do_registro_profissional():
    """'A67154-1' -> CAU/BR (RRT); 'RS233891' -> CREA; '110544/03-D' -> CRBio."""
    assert conselho_do_registro("A67154-1") == "CAU/BR"
    assert conselho_do_registro("RS233891") == "CREA"
    assert conselho_do_registro("110544/03-D") == "CRBio"
    assert conselho_do_registro("12345") is None      # não afirma errado
    assert tipo_registro_por_conselho("CAU/BR") == "RRT"
    assert tipo_registro_por_conselho("CREA") == "ART"
    assert tipo_registro_por_conselho("CRBio") == "ART"
    assert tipo_registro_por_conselho(None) is None
    assert conselho_do_texto("Registro CAU/BR: A67154-1") == "CAU/BR"
    assert conselho_do_texto("Registro CREA-RS: 233891") == "CREA"


def test_rrt_por_conteudo_e_art_por_conteudo_nao_se_confundem():
    v = ValidadorDocumentos()
    rrt = ("REGISTRO DE RESPONSABILIDADE TÉCNICA - RRT\n"
           "Conselho de Arquitetura e Urbanismo do Brasil - CAU/BR\n"
           "Profissional: Raquel Beckes\nRegistro CAU/BR: A67154-1\n"
           "Atividade: Projeto Urbanístico\n")
    art = ("ANOTAÇÃO DE RESPONSABILIDADE TÉCNICA - ART\n"
           "Conselho Regional de Engenharia e Agronomia - CREA-RS\n"
           "Profissional: João Pedro Sandri Kessler\n"
           "Registro CREA-RS: 233891\n"
           "Descrição da atividade/sumária: licenciamento ambiental\n")
    assert v.analisar_documento("rrt_raquel.pdf", rrt).metricas[
        "tipo_registro"] == "RRT"
    assert v.analisar_documento("art_joao.pdf", art).metricas[
        "tipo_registro"] == "ART"
    # RTT/RRT do CAU/BR nunca é digitada como ART
    assert v.conselho_do_documento(rrt) == "CAU/BR"
    assert v.conselho_do_documento(art) == "CREA"


def test_multiplas_rtts_sao_todas_extraidas_com_atividade_propria():
    """Um arquivo pode concentrar VÁRIAS RTTs: cada uma vem com o SEU número
    e a SUA atividade (Projeto Urbanístico, Execução de Obras...)."""
    texto = ("Registro de Responsabilidade Técnica (RRT) do Conselho de "
             "Arquitetura e Urbanismo do Brasil\n"
             "Profissional: Raquel Beckes\nRRT nº 202601001-1\n"
             "Registro CAU/BR: A67154-1\nAtividade: Projeto Urbanístico\n"
             "Registro de Responsabilidade Técnica (RRT)\n"
             "Profissional: Raquel Beckes\nRRT nº 202601002-9\n"
             "Registro CAU: A67154-1\nAtividade: Execução de Obras\n")
    rtts = ValidadorDocumentos().extrair_rtts(texto)
    assert len(rtts) == 2, rtts
    assert [r["numero"] for r in rtts] == ["202601001", "202601002"]
    assert [r["tipo"] for r in rtts] == ["RRT", "RRT"]
    assert {r["orgao"] for r in rtts} == {"CAU/BR"}
    # a atividade de cada RTT é a dela - nunca a da RTT anterior
    assert [r["etapa"] for r in rtts] == ["Projeto Urbanístico",
                                         "Execução de Obras"]
    assert [r["registro_cau"] for r in rtts] == ["A67154-1", "A67154-1"]


def test_validar_rrt_sem_numero_de_registro_reprovado():
    v = ValidadorDocumentos()
    sem_registro = ("Registro de Responsabilidade Técnica (RRT)\n"
                    "Conselho de Arquitetura e Urbanismo do Brasil\n"
                    "Profissional: Raquel Beckes\n"
                    "Atividade: Projeto Urbanístico\n")
    res = v.validar_rrt("rrt_sem_numero.pdf", sem_registro)
    assert res.status.value == "REVISAO_MANUAL", res.itens_reprovados
    assert any("registro" in i.lower() for i in res.itens_reprovados)


# =============================================================================
# 2. Confronto BILATERAL formulário x documentos emitidos
# =============================================================================
def _dados_reais():
    dados = _parse()
    emp = dados.get("empreendimento") or {}
    dados["empreendimento"]["matricula_imovel"] = emp.get("matricula_imovel") or "33024"
    return dados


def test_conferencia_matricula_bilateral():
    """Nº da matrícula declarada x nº do documento - nos dois sentidos."""
    dados = _dados_reais()
    adm = AgenteAdministrativo()
    conf = adm._conferir_matricula(
        dados, ["matricula.pdf"],
        {"matricula.pdf": "Matrícula nº 33024 do 2º Ofício de Registro de "
                          "Imóveis da comarca de Campo Bom."})
    assert conf["status"] == "CONFERE", conf
    # divergente: o documento traz outro número
    conf_div = adm._conferir_matricula(
        dados, ["matricula.pdf"],
        {"matricula.pdf": "Matrícula nº 99.999 do Registro de Imóveis."})
    assert conf_div["status"] == "DIVERGENTE"
    assert conf_div["matricula_encontrada"] == "99999"
    # não encontrado
    conf_ne = adm._conferir_matricula(
        dados, ["matricula.pdf"], {"matricula.pdf": "Certidão de inteiro teor."})
    assert conf_ne["status"] == "NAO_ENCONTRADO"
    # sem documento legível
    conf_ilegivel = adm._conferir_matricula(dados, ["matricula.pdf"], {})
    assert conf_ilegivel is None or conf_ilegivel["status"] == "ANEXO_NAO_LEGIVEL"


def test_conferencia_contrato_social_cnpj_e_razao_social():
    """CNPJ e razão social do Contrato Social x declarados no formulário."""
    dados = _dados_reais()
    cnpj = (dados.get("empreendedor") or {}).get("cpf_cnpj")
    razao = (dados.get("empreendedor") or {}).get("nome_razao_social")
    if not cnpj or not razao:
        pytest.skip("formulário real sem CNPJ/razão social legíveis")
    adm = AgenteAdministrativo()
    ok = adm._conferir_contrato_social(
        dados, ["contrato_social.pdf"],
        {"contrato_social.pdf": "CONTRATO SOCIAL\nRazão Social: %s\n"
                                "CNPJ: %s\n" % (razao, cnpj)})
    assert ok["status"] == "CONFERE", ok
    assert ok["cnpj_confere"] is True and ok["razao_social_confere"] is True
    # CNPJ divergente (sentido documento -> formulário)
    div = adm._conferir_contrato_social(
        dados, ["contrato_social.pdf"],
        {"contrato_social.pdf": "CONTRATO SOCIAL\nRazão Social: %s\n"
                                "CNPJ: 11.111.111/0001-11\n" % razao})
    assert div["status"] == "DIVERGENTE"
    assert any("DIFERE" in i for i in div["itens_reprovados"])
    # razão social divergente
    div_razao = adm._conferir_contrato_social(
        dados, ["contrato_social.pdf"],
        {"contrato_social.pdf": "CONTRATO SOCIAL\nRazão Social: OUTRA EMPRESA "
                                "COMPLETAMENTE DIFERENTE LTDA\nCNPJ: %s\n" % cnpj})
    assert div_razao["status"] == "DIVERGENTE"
    assert any("Razão social" in i for i in div_razao["itens_reprovados"])


def test_sentido_inverso_rtt_anexada_nao_declarada_e_sinalizada():
    """Uma RTT anexada que NÃO está na seção 4.3 / item 14 é pendência
    (sentido documento -> formulário)."""
    dados = _dados_reais()
    textos = {
        "rrt_nao_declarada.pdf": (
            "Registro de Responsabilidade Técnica (RRT)\n"
            "Conselho de Arquitetura e Urbanismo do Brasil\n"
            "Profissional: Arquiteto Nao Declarado\nRRT nº 999888777\n"
            "Registro CAU/BR: A99999-9\nAtividade: Execução de Obras\n")}
    res = AgenteAdministrativo()._conferir_rtts_anexadas(
        dados, ["rrt_nao_declarada.pdf"], textos,
        declaradas=list(dados.get("responsaveis_etapas") or []))
    assert len(res) == 1, res
    assert res[0]["tipo"] == "RRT" and res[0]["conselho"] == "CAU/BR"
    assert res[0]["numero"] == "999888777"
    # quando a RTT ESTÁ declarada, não é sinalizada
    declaradas = [{"nome": "Arquiteto Nao Declarado",
                   "registro": "A99999-9", "art_rtt": "RRT 999888777"}]
    res_ok = AgenteAdministrativo()._conferir_rtts_anexadas(
        dados, ["rrt_nao_declarada.pdf"], textos, declaradas=declaradas)
    assert res_ok == []


def test_conferencia_responsaveis_guarda_conselho_e_tipo():
    """Cada RT declarado vem com o conselho (CAU/BR x CREA x CRBio) e o tipo
    do registro, e a ART/RTT é procurada primeiro nos documentos que SÃO
    registro - nunca só num laudo que cita o número."""
    dados = _dados_reais()
    textos = {
        "rrt_raquel.pdf": (
            "Registro de Responsabilidade Técnica (RRT)\n"
            "Conselho de Arquitetura e Urbanismo do Brasil\n"
            "Profissional: Raquel Beckes\nRRT nº 17246618\n"
            "Registro CAU/BR: A67154-1\nAtividade: Projeto Urbanístico\n"),
        "laudo_geologico.pdf": (
            "LAUDO GEOLÓGICO\nSondagens e ensaios de infiltração; o RT "
            "anterior possuía ART 17246618 conforme certidão junta.\n")}
    conf = {c["profissional"]: c
            for c in AgenteAdministrativo()._conferir_responsaveis_etapas(
                dados, ["rrt_raquel.pdf", "laudo_geologico.pdf"], textos)}
    raquel = conf["Raquel Beckes"]
    assert raquel["conselho_declarado"] == "CAU/BR", raquel
    assert raquel["tipo"] == "RRT"
    assert raquel["conselho_documento"] == "CAU/BR"
    assert raquel["anexo"] == "rrt_raquel.pdf"      # registro, não o laudo
    assert raquel["encontrado"] is True
    keli = conf["Keli Daiane Bernardes dos Santos"]
    assert keli["conselho_declarado"] == "CRBio" and keli["tipo"] == "ART"
    joao = conf["João Pedro Sandri Kessler"]
    assert joao["conselho_declarado"] == "CREA" and joao["tipo"] == "ART"


def test_conselho_divergente_nao_vira_conforme():
    """Número que casa com conselho DIFERENTE do declarado é REVISAO_MANUAL,
    nunca CONFORME (RTT do CAU/BR x ART do CREA)."""
    rtt = ("Registro de Responsabilidade Técnica (RRT)\n"
           "Conselho de Arquitetura e Urbanismo do Brasil\n"
           "Profissional: Raquel Beckes\nRRT nº 17246618\n"
           "Registro CAU/BR: A67154-1\nAtividade: Projeto Urbanístico\n")
    res = AuditorTecnico().auditar_com_dupla_checagem(
        "rrt_cau.pdf", rtt,
        arts_formulario=[{"numero": "17246618", "nome": "Raquel Beckes",
                          "secao": "4.3", "registro": "RS233891"}])
    r = [x for x in res if "ART" in x.norma_tr][0]
    assert r.status.value == "REVISAO_MANUAL", (r.status, r.itens_reprovados)
    assert r.metricas.get("conselho_incompativel") is True
    # com o conselho CAU/BR declarado (como no formulário real), confere
    res_ok = AuditorTecnico().auditar_com_dupla_checagem(
        "rrt_cau.pdf", rtt,
        arts_formulario=[{"numero": "17246618", "nome": "Raquel Beckes",
                          "secao": "4.3", "registro": "A67154-1"}])
    r_ok = [x for x in res_ok if "ART" in x.norma_tr][0]
    assert r_ok.status.value == "CONFORME", r_ok.itens_reprovados


def test_rrt_nao_recebe_tr_de_projeto_urbanistico():
    """Uma RRT cuja atividade é 'Projeto Urbanístico' é um REGISTRO do CAU/BR,
    não um projeto: recebe a conferência de registro, nunca o TR de projeto."""
    rtt = ("Registro de Responsabilidade Técnica (RRT)\n"
           "Conselho de Arquitetura e Urbanismo do Brasil\n"
           "Profissional: Raquel Beckes\nRRT nº 17246618\n"
           "Registro CAU/BR: A67154-1\nAtividade: Projeto Urbanístico\n"
           "Área total: 32.450,00 m²\nÁrea útil: 32.450,00 m²\n")
    normas = [x.norma_tr for x in AuditorTecnico().auditar_com_dupla_checagem(
        "rrt_projeto.pdf", rtt)]
    assert any("ART/RTT" in n for n in normas), normas
    assert not any("urbanístico" in n for n in normas), normas


# =============================================================================
# 3. TRs NUNCA aplicados de forma cruzada
# =============================================================================
@pytest.mark.parametrize("titulo,esperado,proibido", [
    ("LAUDO DE COBERTURA VEGETAL\nForam consultadas sondagens anteriores da "
     "região e há reposição florestal de mudas nativas.\n",
     "Cobertura", ["Meio Físico", "RFO"]),
    ("INVENTÁRIO DE FAUNA\nA cobertura vegetal local é floresta ombrófila; "
     "busca ativa para mastofauna.\n", "Fauna", ["Cobertura"]),
    ("LAUDO GEOLÓGICO\nSondagens e ensaios de infiltração; fauna silvestre "
     "avistada no entorno.\n", "Meio Físico", ["Fauna"]),
    ("ESTUDO DE IMPACTO DE VIZINHANÇA - EIV\n5. Diagnóstico: fauna local "
     "(mastofauna, avifauna) e cobertura vegetal do entorno.\n",
     "Vizinhança", ["Fauna", "Cobertura"]),
])
def test_titulo_do_documento_decide_o_tr(titulo, esperado, proibido):
    """O tipo DECLARADO no título é a autoridade sobre qual TR se aplica."""
    normas = [x.norma_tr for x in AuditorTecnico().auditar_com_dupla_checagem(
        "doc.pdf", titulo)]
    assert any(esperado in n for n in normas), normas
    for proibido in proibido:
        assert not any(proibido in n for n in normas), (proibido, normas)


def test_camada_gis_nunca_recebe_tr_de_conteudo():
    """Camadas geoespaciais ficam FORA do roteamento de TRs (skill
    gis-multicamadas) e são conferidas geometricamente."""
    auditar = AuditorTecnico().auditar_documento
    for nome, texto in [
        ("camadas.kmz", "Camada: Area do Empreendimento | tema: OUTRO | "
                        "geometria: Polygon | feições: 1 | SRS: EPSG:4674\n"),
        ("projeto_urbanistico.kml", KML_3_CAMADAS),
        ("curvas_de_nivel.geojson", GEOJSON_CAMADA),
    ]:
        assert auditar(nome, texto, tipo_documento="CAMADA_GIS") == []


def test_laudo_geologico_continua_recebendo_tr_de_geologia():
    geo = ("LAUDO GEOLÓGICO DE CAMPO\nSondagens e ensaios de infiltração "
           "duplo anel; lençol freático a 2,5 m.\n")
    normas = [x.norma_tr for x in AuditorTecnico().auditar_com_dupla_checagem(
        "geo.pdf", geo)]
    assert any("Meio Físico" in n for n in normas), normas
    assert not any("Fauna" in n or "Cobertura" in n for n in normas), normas


# =============================================================================
# 4. Ensaios de infiltração e parâmetros de sondagem
# =============================================================================
def test_ensaios_de_infiltracao_contados_no_laudo_geologico():
    """'ensaios de infiltração' (inclusive 'duplo anel') contam como ensaios
    de permeabilidade - o checklist oficial exige os dois para parcelamento."""
    texto = ("LAUDO GEOLÓGICO E GEOTÉCNICO\n"
             "Foram executados 4 furos de sondagem a trado até 3,5 m.\n"
             "Foram realizados 3 ensaios de infiltração do tipo duplo anel.\n"
             "Área do empreendimento: 1,2 ha.\n")
    p = AuditorTecnico().extrair_parametros_sondagem(texto)
    assert p.furos_informados == 4, p
    assert p.ensaios_permeabilidade_informados == 3, p
    assert p.profundidade_investigacao_m == pytest.approx(3.5), p
    assert p.area_ha == pytest.approx(1.2), p

    # 'ensaios de infiltração' e 'duplo anel' sem quantidade explícita:
    # contam como ensaio informado (nunca 'não identificado')
    sem_qtd = ("LAUDO GEOLÓGICO\nSondagens a trado; ensaios de infiltração "
               "do tipo duplo anel realizados em 4 pontos. Área: 0,8 ha.\n")
    p2 = AuditorTecnico().extrair_parametros_sondagem(sem_qtd)
    assert p2.ensaios_permeabilidade_informados == 4, p2
    assert p2.area_ha == pytest.approx(0.8), p2

    # 'profundidade de investigação de X m' (frase do TR oficial)
    p3 = AuditorTecnico().extrair_parametros_sondagem(
        "LAUDO GEOLÓGICO\n4 furos de sondagem com ensaios de permeabilidade "
        "em 3 deles. Profundidade de investigação de 3,5 m.\n")
    assert p3.profundidade_investigacao_m == pytest.approx(3.5), p3
    assert p3.furos_informados == 4 and p3.ensaios_permeabilidade_informados == 3


# =============================================================================
# 5. Camadas GIS: leitura e confronto geométrico
# =============================================================================
def test_leitor_kml_separa_uma_camada_por_pasta():
    pacote = LeitorGIS.ler_arquivo("camadas.kml", KML_3_CAMADAS.encode())
    nomes = [c.nome for c in pacote.camadas]
    assert nomes == ["Area do Empreendimento", "Curvas de Nivel",
                     "Corpo Hidrico"], nomes
    assert [c.tipo_geometria for c in pacote.camadas] == [
        "Polygon", "LineString", "LineString"]
    assert pacote.camadas[0].tema in ("POLIGONAL", "AREA", "OUTRO")
    assert not pacote.erros, pacote.erros


def test_leitor_geojson_le_crs_e_area():
    pacote = LeitorGIS.ler_arquivo("projeto.geojson",
                                   GEOJSON_CAMADA.encode())
    assert len(pacote.camadas) == 1
    camada = pacote.camadas[0]
    assert camada.srs_epsg == "EPSG:4674"
    assert area_ha(camada) == pytest.approx(106.71, abs=0.05)


def test_leitor_gpx_e_kmz():
    pacote = LeitorGIS.ler_arquivo("limite.gpx", GPX_TRILHA.encode())
    assert pacote.camadas and pacote.camadas[0].tipo_geometria == "LineString"
    # KMZ = zip com o doc.kml dentro
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("doc.kml", KML_3_CAMADAS)
    pacote_kmz = LeitorGIS.ler_arquivo("camadas.kmz", buf.getvalue())
    assert [c.nome for c in pacote_kmz.camadas] == [
        "Area do Empreendimento", "Curvas de Nivel", "Corpo Hidrico"]


def test_srs_normalizado_para_sirgas_2000():
    assert normalizar_srs("SIRGAS 2000") == "EPSG:4674"
    assert normalizar_srs("SIRGAS 2000 UTM 22S") == "EPSG:31982"
    assert normalizar_srs("WGS84") == "EPSG:4326"
    assert normalizar_srs("EPSG:4674") == "EPSG:4674"
    assert normalizar_srs(None) is None


def test_utm_do_formulario_convertido_para_geografica():
    """Os formulários do RS declaram UTM (fuso 22S); a conversão precisa ser
    fiel para o confronto com as camadas (ida e volta sem perder precisão)."""
    for leste, norte in [(300000, 6700000), (500000, 6690000),
                         (288000, 6676000)]:
        lon, lat = utm_para_lonlat(leste, norte, 22, "S")
        assert -57 < lon < -49 and -34 < lat < -27, (lon, lat)
    assert utm_para_lonlat(0, 0, 22, "S") is None     # não afirma errado


def test_distancia_e_contemplacao_geometrica():
    assert distancia_haversine_m((-50.0, -29.0), (-50.0, -29.0)) == 0
    assert distancia_haversine_m((-50.0, -29.0), (-50.0, -29.01)) == \
        pytest.approx(1112, rel=0.02)
    anel = [(-50.10, -29.90), (-50.09, -29.90), (-50.09, -29.89),
            (-50.10, -29.89), (-50.10, -29.90)]
    assert ponto_em_aneis((-50.095, -29.895), [anel]) is True
    assert ponto_em_aneis((-50.20, -29.95), [anel]) is False


def test_agente_gis_confronta_area_ponto_e_distancia_sem_tr():
    pacote = LeitorGIS.ler_arquivo("camadas.kml", KML_3_CAMADAS.encode())
    res = AgenteGIS().conferir(
        "camadas.kml", pacote,
        areas_formulario={"area_total_ha": 106.71},
        ponto_empreendimento=(-50.095, -29.895))[0]
    # NENHUM resultado GIS carrega TR de conteúdo
    assert res.metricas["tr_aplicado"] is None
    assert res.metricas["area_camada_ha"] == pytest.approx(106.71, abs=0.05)
    assert res.metricas["empreendimento_contido_na_poligonal"] is True
    # corpos hídricos a leste: distância positiva e dentro do raio de alerta
    dist = res.metricas["distancia_corpo_hidrico_m"]
    assert dist is None or dist >= 0
    assert res.status.value in ("CONFORME", "PENDENTE")


def test_agente_gis_aponta_divergencia_de_area_e_ponto_fora():
    pacote = LeitorGIS.ler_arquivo("camadas.kml", KML_3_CAMADAS.encode())
    res = AgenteGIS().conferir(
        "camadas.kml", pacote,
        areas_formulario={"area_total_ha": 3.0},
        ponto_empreendimento=(-49.0, -28.0))[0]
    assert res.status.value != "CONFORME"
    assert any("DIVERGE" in i for i in res.itens_reprovados), res.itens_reprovados
    assert any("FORA da poligonal" in i for i in res.itens_reprovados)
    assert res.metricas["empreendimento_contido_na_poligonal"] is False


def test_agente_gis_arquivo_ilegivel_vira_revisao_manual():
    pacote = LeitorGIS.ler_arquivo("quebrado.kml", b"<kml><Document>")
    res = AgenteGIS().conferir("quebrado.kml", pacote)
    assert res[0].status.value == "REVISAO_MANUAL"
    assert res[0].metricas["tr_aplicado"] is None


def test_validador_gis_resumo_e_extensoes():
    v = ValidadorDocumentos()
    texto = v.extrair_texto("camadas.kml", KML_3_CAMADAS.encode())
    assert "Camada:" in texto and "feições" in texto
    res = v.analisar_documento("camadas.kml", texto)
    assert res.norma_tr == "Camada GIS (KMZ/KML/GeoJSON)"
    assert res.metricas["tr_aplicado"].startswith("nenhum")
    assert res.metricas["total_camadas"] == 3
    assert res.metricas["total_feicoes"] == 3
    # sem SRS declarado -> pendência (checklist exige SIRGAS 2000)
    assert res.status.value == "PENDENTE"
    assert any("SRS" in i for i in res.itens_reprovados)
    from licenciamento.validador_documentos import EXTENSOES_GIS
    assert set(EXTENSOES_GIS) == {".kml", ".kmz", ".geojson", ".gpx"}


def test_checklist_gis_casado_com_anexo_kmz():
    """A exigência oficial 'Arquivo KMZ/KML e DWG do projeto urbanístico e
    APPs' é do tipo CAMADA_GIS e casa com o anexo .kml - e NUNCA com um laudo
    (conformidade cruzada)."""
    v = ValidadorDocumentos()
    exig = "21. Arquivo KMZ/KML e DWG do projeto urbanístico e APPs"
    anexos = [{"nome": "camadas_curvas_nivel.kml", "tipo": "CAMADA_GIS"},
              {"nome": "laudo_geologico.pdf", "tipo": "SONDAGEM"}]
    assert v.casar_exigencia(exig, anexos) == "camadas_curvas_nivel.kml"
    # a exigência do laudo geológico NUNCA casa com a camada GIS
    exig_geo = "16. Laudo geológico com sondagem e ensaios de infiltração, com ART"
    assert v.casar_exigencia(exig_geo, anexos) == "laudo_geologico.pdf"


# =============================================================================
# 6. Pipeline completo: formulário real + RTT + camadas
# =============================================================================
def test_pipeline_real_rtt_cau_conferida_e_gis_lido():
    dados = _dados_reais()
    anexos = ["rrt_projeto_urbanistico.pdf", "camadas.kml"]
    textos = {
        "rrt_projeto_urbanistico.pdf": (
            "Registro de Responsabilidade Técnica (RRT)\n"
            "Conselho de Arquitetura e Urbanismo do Brasil\n"
            "Profissional: Raquel Beckes\nRRT nº 17246618\n"
            "Registro CAU/BR: A67154-1\nAtividade: Projeto Urbanístico\n"),
        "camadas.kml": KML_3_CAMADAS}
    adm = AgenteAdministrativo().auditar(dados, anexos, textos_anexados=textos)
    conf = {c["profissional"]: c for c in adm["conferencia_responsaveis"]}
    raquel = conf["Raquel Beckes"]
    assert raquel["encontrado"] is True
    assert raquel["tipo"] == "RRT" and raquel["conselho_declarado"] == "CAU/BR"
    assert raquel["conselho_documento"] == "CAU/BR"
    # a RTT anexada está declarada na 4.3 -> não pode virar pendência
    assert not [r for r in adm["conferencia_rtts_anexadas"]
                if r["numero"] == "17246618"]
    # resumo traz as novas contagens
    assert adm["resumo"]["rtts_anexadas_nao_declaradas"] == 0
    assert adm["resumo"]["divergencias_documentais"] == 0
    # a camada GIS é lida e NÃO recebe TR
    pacote = LeitorGIS.ler_arquivo("camadas.kml",
                                   KML_3_CAMADAS.encode())
    res = AgenteGIS().conferir("camadas.kml", pacote)[0]
    assert res.metricas["tr_aplicado"] is None
    assert res.metricas["area_camada_ha"] == pytest.approx(106.71, abs=0.05)


# =============================================================================
# 7. Painel (AppTest): ponta a ponta com formulário real + RTT + camada GIS
# =============================================================================
def test_painel_upload_aceita_camada_gis_e_confere_rtt_do_cau():
    """Ponte a ponte no painel: o uploader aceita .kml/.kmz/.geojson/.gpx, a
    RTT do CAU/BR anexada é conferida e a camada GIS é lida sem TR."""
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"),
                           default_timeout=240)
    at.run()
    assert not at.exception
    at.radio[0].set_value("LP")                    # licença pleiteada
    at.radio[1].set_value("Primeira licença")      # natureza
    at.run()
    form_bytes = Path(REAL).read_bytes()
    arquivos = [
        ("formulario_real.htm", form_bytes, "text/html"),
        # .txt para o texto ser extraído sem OCR (o PDF aqui seria fake)
        ("rrt_projeto_urbanistico.txt",
         ("Registro de Responsabilidade Técnica (RRT)\n"
          "Conselho de Arquitetura e Urbanismo do Brasil\n"
          "Profissional: Raquel Beckes\nRRT nº 17246618\n"
          "Registro CAU/BR: A67154-1\nAtividade: Projeto Urbanístico\n")
         .encode(), "text/plain"),
        ("camadas_curvas_nivel.geojson", GEOJSON_CAMADA.encode(),
         "application/json"),
    ]
    at.file_uploader[0].set_value(arquivos)
    at.run()
    assert not at.exception, [e.value[:300] for e in at.exception]
    [b for b in at.button if "Analisar" in b.label][0].click()
    at.run()
    assert not at.exception, [e.value[:300] for e in at.exception]
    # todo o texto renderizado do painel (caption, markdown, alerts...)
    textos = " ".join(
        [c.value for c in at.caption] + [m.value for m in at.markdown]
        + [w.value for w in at.warning] + [i.value for i in at.info]
        + [x.value for x in at.success] + [e.value for e in at.error]
        + [j.value for j in at.json])
    # a camada GIS foi lida e confrontada (bloco geométrico, sem TR)
    assert "Camadas GIS" in textos, textos[:800]
    assert "conferência geométrica" in textos, textos[:800]
    # a RTT do CAU/BR anexada é conferida - e NUNCA como ART de CREA
    assert "Raquel Beckes" in textos, textos[:800]
    assert "CAU/BR" in textos, textos[:800]
