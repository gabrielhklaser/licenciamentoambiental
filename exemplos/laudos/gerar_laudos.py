# -*- coding: utf-8 -*-
"""
Gera os laudos técnicos FICTÍCIOS (PDFs) usados na demonstração do sistema.

Uso:  .venv/bin/python exemplos/laudos/gerar_laudos.py   (requer reportlab)
Observação: reportlab é uma dependência de DEV (apenas para gerar os exemplos);
o sistema em si só precisa de pypdf para LER os PDFs.
"""

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

ESTILOS = getSampleStyleSheet()
ESTILO_TITULO = ParagraphStyle("titulo", parent=ESTILOS["Heading2"], spaceAfter=10)
ESTILO_CORPO = ParagraphStyle("corpo", parent=ESTILOS["BodyText"], leading=15)

Laudos = {
    # ---------------------------------------------------------------
    # Cenário da especificação: RFO com densidade 2.000 mudas/ha
    # (deve resultar em PENDENTE com justificativa matemática)
    # ---------------------------------------------------------------
    "laudo_rfo_densidade_2000.pdf": [
        "PROJETO DE REPOSIÇÃO FLORESTAL OBRIGATÓRIA (RFO)",
        "Empreendimento: Serraria e Beneficiamento de Madeira Vale do Sinos - Campo Bom/RS",
        "",
        "1. LEVANTAMENTO DA SUPRESSÃO DE VEGETAÇÃO",
        "O levantamento fitossociológico identificou 120 indivíduos nativos e 30 indivíduos "
        "exóticos passíveis de supressão na área de intervenção do empreendimento.",
        "",
        "2. PROJETO DE COMPENSAÇÃO FLORESTAL",
        "Propõe-se o plantio de 1.900 mudas nativas com altura superior a 1 metro em área de "
        "preservação, bem como 90 mudas de espécies exóticas no entorno.",
        "",
        "3. DENSIDADE DE PLANTIO",
        "O plantio será implantado com densidade de 2.000 mudas por hectare, com corramento, "
        "coroamento e controle de formigas por 24 meses.",
    ],
    # ---------------------------------------------------------------
    # Laudo CONFORME: meio físico (sondagem) - lençol a 1,90 m da base
    # e 5 furos para 1,8 ha (exigência: 4 + 1 = 5)
    # ---------------------------------------------------------------
    "laudo_sondagem_conforme.pdf": [
        "LAUDO DE INVESTIGAÇÃO GEOTÉCNICA - MEIO FÍSICO (RSCC)",
        "Empreendimento: Aterro de Resíduos da Construção Civil - Campo Bom/RS",
        "",
        "1. SONDOMAGEM E LENÇOL FREÁTICO",
        "Foram executados 5 furos de sondagem para caracterização da área de 1,8 hectare do "
        "aterro. Em todos os furos, o nível do lençol freático foi medido a 2,40 m de "
        "profundidade.",
        "",
        "2. COTA BASE DO ATERRAMENTO",
        "A cota base do aterro sanitário foi definida em 0,50 m, garantindo a distância "
        "vertical mínima exigida pela norma.",
    ],
    # ---------------------------------------------------------------
    # PRAD CONFORME: cronograma detalhado + monitoramento 5 anos
    # ---------------------------------------------------------------
    "prad_recuperacao_area_degradada.pdf": [
        "PLANO DE RECUPERAÇÃO DE ÁREA DEGRADADA (PRAD)",
        "Empreendimento: Serraria e Beneficiamento de Madeira Vale do Sinos - Campo Bom/RS",
        "",
        "1. DIAGNÓSTICO AMBIENTAL",
        "A área degradada pela extração de cascalho abrange 2,3 hectares com solo exposto e "
        "compactado.",
        "",
        "2. CRONOGRAMA FÍSICO-FINANCEIRO DETALHADO",
        "O cronograma físico-financeiro detalhado prevê as etapas de gradeamento pesado, "
        "incorporação de matéria orgânica, plantio de mudas e manutenção, com custos unitários "
        "e desembolso trimestral por etapa ao longo de 36 meses.",
        "",
        "3. PROGRAMA DE MONITORAMENTO",
        "Será realizado monitoramento da revegetação e das águas subterrâneas por período de "
        "5 anos, com relatórios semestrais e indicadores de sobrevivência das mudas.",
    ],
    # ---------------------------------------------------------------
    # PCA PENDENTE: relatórios MENSais na supressão (TR exige trimestral)
    # ---------------------------------------------------------------
    "pca_plano_controle_ambiental.pdf": [
        "PLANO DE CONTROLE AMBIENTAL (PCA)",
        "Empreendimento: Serraria e Beneficiamento de Madeira Vale do Sinos - Campo Bom/RS",
        "",
        "1. PROGRAMA DE ACOMPANHAMENTO E MONITORAMENTO",
        "Os relatórios de acompanhamento na fase de supressão de vegetação e movimentação de "
        "solo serão apresentados com periodicidade mensal.",
        "",
        "2. FASE DE OBRAS",
        "Durante a fase de obras civis, os relatórios de acompanhamento ambiental serão "
        "apresentados com periodicidade semestral, conforme cronograma de relatórios anexo.",
    ],
}


def gerar() -> None:
    pasta = Path(__file__).parent
    for nome, paragrafos in Laudos.items():
        doc = SimpleDocTemplate(str(pasta / nome), pagesize=A4,
                                topMargin=2 * cm, bottomMargin=2 * cm)
        historia = [Paragraph(t, ESTILO_TITULO if i == 0 else ESTILO_CORPO)
                    for i, t in enumerate(paragrafos) if t]
        historia.append(Spacer(1, 12))
        doc.build(historia)
        print(f"  gerado: {nome}")


if __name__ == "__main__":
    gerar()
