# -*- coding: utf-8 -*-
"""Testes do ValidadorDocumentos (análise documental e quadro resumo)."""
from datetime import date

import pytest

from licenciamento.esquemas_tecnicos import StatusValidacao
from licenciamento.validador_documentos import (ValidadorDocumentos, normalizar)

MATRICULA_MODELO = """
MATRÍCULA Nº 39.715 - Registro de Imóveis da Comarca de Campo Bom/RS
Imóvel: estrada municipal RS-239, distrito sede, lote 4 da quadra F.
Área total: 12.000,00 m². Proprietária: Madeireira Vale do Sinos Ltda,
inscrita no CNPJ sob o nº 12.345.678/0001-90.
Ônus: hipoteca em favor do Banco Fictício S.A. (contrato 901/2025).
Servidão de passagem registrada à margem.
Campo Bom, {data_longo}.
Oficial Registrador - Registro de Imóveis
Documento eletrônico assinado conforme Lei 11.419/2006 (canto inferior esquerdo)
"""


@pytest.fixture
def validador() -> ValidadorDocumentos:
    return ValidadorDocumentos()


@pytest.fixture
def pasta_tmp(tmp_path):
    """Pasta temporária para arquivos de teste."""
    return tmp_path


# ==============================================================================
# MATRÍCULA - data de emissão no FINAL do documento + prazo de 90 dias
# ==============================================================================
def test_matricula_dentro_do_prazo_90_dias(validador):
    """Emitida há 40 dias (canto inferior esquerdo) => CONFORME."""
    emissao = date(2026, 8, 6)  # 40 dias antes da referência 15/09/2026
    texto = MATRICULA_MODELO.format(data_longo="06 de agosto de 2026")
    r = validador.validar_matricula("matricula_imovel.pdf", texto,
                                    data_referencia=date(2026, 9, 15))
    assert r.status == StatusValidacao.CONFORME
    assert r.metricas["data_emissao"] == "2026-08-06"
    assert r.metricas["dias_desde_emissao"] == 40
    assert r.metricas["prazo_validade_dias"] == 90
    assert not r.itens_reprovados


def test_matricula_vencida_alem_de_90_dias(validador):
    """Emitida há mais de 90 dias => PENDENTE com mensagem do prazo."""
    texto = MATRICULA_MODELO.format(data_longo="05 de março de 2026")
    r = validador.validar_matricula("matricula.pdf", texto,
                                    data_referencia=date(2026, 9, 15))
    assert r.status == StatusValidacao.PENDENTE
    assert any("90 dias" in item for item in r.itens_reprovados)
    assert r.metricas["dias_desde_emissao"] == 194
    assert r.metricas["dias_restantes"] == -104


def test_matricula_data_nao_localizada(validador):
    """Sem data no final => REVISAO_MANUAL (conferência humana)."""
    texto = ("MATRÍCULA Nº 39.715 - Registro de Imóveis. Área total 12.000 m². "
             "Proprietária Madeireira Ltda.")
    r = validador.validar_matricula("matricula.pdf", texto,
                                    data_referencia=date(2026, 9, 15))
    assert r.status == StatusValidacao.REVISAO_MANUAL
    assert any("localizada" in item.lower() for item in r.itens_reprovados)


def test_matricula_data_no_formato_curto(validador):
    """Formato dd/mm/aaaa no fechamento também é aceito."""
    texto = ("MATRÍCULA Nº 12.345. Registrada no Livro 2. "
             "Campo Bom/RS, 10/07/2026.\nOficial de Registro")
    r = validador.validar_matricula("matricula.pdf", texto,
                                    data_referencia=date(2026, 9, 15))
    assert r.status == StatusValidacao.CONFORME  # 67 dias <= 90
    assert r.metricas["data_emissao"] == "2026-07-10"


def test_matricula_prazo_calibravel(validador):
    """O prazo de validade vem de config/regras_documentos.json (90 dias)."""
    assert validador.matricula_validade_dias == 90
    assert validador.regras_revisadas is True
    assert any("90 DIAS" in nota.upper() for nota in validador.notas_regras)


# ==============================================================================
# IDENTIFICAÇÃO E ANÁLISE POR TIPO DE DOCUMENTO
# ==============================================================================
def test_identificar_matricula_por_conteudo(validador):
    texto = MATRICULA_MODELO.format(data_longo="06 de agosto de 2026")
    assert validador.identificar_tipo("documento_qualquer.pdf", texto) == \
        "MATRICULA_IMOVEL"


def test_identificar_por_nome_do_arquivo(validador):
    assert validador.identificar_tipo("matricula_imovel_atualizada.pdf", "") == \
        "MATRICULA_IMOVEL"
    assert validador.identificar_tipo("pgrs_da_empresa.pdf", "") == "PGRS"
    assert validador.identificar_tipo("nota_qualquer.pdf", "texto solto") is None


def test_analisar_documento_pdf_sem_texto(validador):
    """Anexo escaneado/vazio => REVISAO_MANUAL com orientação."""
    r = validador.analisar_documento("laudo_escaneado.pdf", "",
                                     data_referencia=date(2026, 9, 15))
    assert r.status == StatusValidacao.REVISAO_MANUAL
    assert any("sem texto" in i.lower() for i in r.itens_reprovados)


def test_analisar_matricula_integra(validador):
    """analisar_documento roteia matrícula para a validação do prazo."""
    texto = MATRICULA_MODELO.format(data_longo="05 de março de 2026")
    r = validador.analisar_documento("matricula.pdf", texto,
                                     data_referencia=date(2026, 9, 15))
    assert r.status == StatusValidacao.PENDENTE
    assert r.metricas.get("prazo_validade_dias") == 90


def test_normalizar_utilitaria():
    assert normalizar("MATRÍCULA do Imóvel  nº 12") == "matricula do imovel no 12"


# ==============================================================================
# QUADRO RESUMO (checklist x arquivos x análises)
# ==============================================================================
def test_quadro_resumo_completo(validador):
    """Cobra exigências não apresentadas e marca pendências por arquivo."""
    exigencias = ["Cópia da matrícula atualizada do imóvel",
                  "Cópia do CNPJ",
                  "Plano de Gerenciamento de Resíduos Sólidos",
                  "Laudo de Cobertura Vegetal (LCV)"]
    matricula_txt = MATRICULA_MODELO.format(data_longo="05 de março de 2026")
    arquivos = [
        {"nome": "matricula_imovel.pdf", "texto": matricula_txt, "tipo": "MATRICULA_IMOVEL"},
        {"nome": "copia_cnpj.pdf", "texto": "COMPROVANTE DE INSCRIÇÃO CNPJ 12.345.678/0001-90",
         "tipo": "CNPJ"},
    ]
    analises = {
        "matricula_imovel.pdf": validador.analisar_documento(
            "matricula_imovel.pdf", matricula_txt, data_referencia=date(2026, 9, 15)),
        "copia_cnpj.pdf": validador.analisar_documento(
            "copia_cnpj.pdf", arquivos[1]["texto"]),
    }
    linhas, extras = validador.montar_quadro(exigencias, arquivos, analises)
    resumo = validador.resumo_quadro(linhas)

    por_exigencia = {linha["documento"]: linha for linha in linhas}
    # matrícula casada mas VENCIDA => pendente com a justificativa
    linha_mat = por_exigencia["Cópia da matrícula atualizada do imóvel"]
    assert linha_mat["situacao"] == "PENDENTE"
    assert linha_mat["arquivo"] == "matricula_imovel.pdf"
    assert any("90 dias" in p for p in linha_mat["pendencias"])
    # CNPJ casado e legível => em conformidade
    assert por_exigencia["Cópia do CNPJ"]["situacao"] == "CONFORME"
    # PGRS e LCV sem arquivo => não apresentado
    assert por_exigencia["Plano de Gerenciamento de Resíduos Sólidos"]["situacao"] == \
        "NAO_APRESENTADO"
    assert por_exigencia["Laudo de Cobertura Vegetal (LCV)"]["situacao"] == \
        "NAO_APRESENTADO"

    assert resumo == {"CONFORME": 1, "PENDENTE": 1, "NAO_APRESENTADO": 2}
    assert extras == []  # todos os arquivos casaram com alguma exigência


def test_quadro_arquivo_extra_sem_exigencia(validador):
    """Arquivo que não casa com nenhuma exigência volta na lista de extras."""
    exigencias = ["Cópia do CNPJ"]
    arquivos = [{"nome": "copia_cnpj.pdf", "texto": "CNPJ 12.345.678/0001-90"},
                {"nome": "foto_da_area.jpg.txt", "texto": "anotações da vistoria"}]
    linhas, extras = validador.montar_quadro(exigencias, arquivos, {})
    assert len(linhas) == 1 and linhas[0]["situacao"] == "CONFORME"
    assert extras and extras[0]["nome"] == "foto_da_area.jpg.txt"


# ==============================================================================
# EXTRAÇÃO MULTI-FORMATO
# ==============================================================================
def test_extrair_texto_docx_e_xlsx(validador, pasta_tmp):
    """Extração de Word e Excel (formatos aceitos na página de upload)."""
    from docx import Document
    import openpyxl

    doc = Document()
    doc.add_paragraph("PLANO DE GERENCIAMENTO DE RESÍDUOS SÓLIDOS")
    doc.add_paragraph("Empresa: Madeireira Vale do Sinos Ltda")
    doc.save(pasta_tmp / "pgrs.docx")
    r_doc = validador.extrair_texto("pgrs.docx", (pasta_tmp / "pgrs.docx").read_bytes())
    assert "PLANO DE GERENCIAMENTO" in r_doc

    plan = openpyxl.Workbook()
    aba = plan.active
    aba.title = "Resíduos"
    aba.append(["Tipo", "Volume mensal"])
    aba.append(["Orgânico", "120 kg"])
    plan.save(pasta_tmp / "residuos.xlsx")
    r_xls = validador.extrair_texto("residuos.xlsx",
                                    (pasta_tmp / "residuos.xlsx").read_bytes())
    assert "# Aba: Resíduos" in r_xls and "120 kg" in r_xls


def test_extrair_texto_formato_nao_suportado(validador):
    """Extensão estranha ou binário: devolve vazio sem quebrar (log)."""
    assert validador.extrair_texto("arquivo.zip", b"PK\x03\x04...") == ""
