# Termos de Referência Oficiais — SEMA Campo Bom

**Fonte:** site oficial da Prefeitura de Campo Bom — [Downloads e Utilidades](https://www.campobom.rs.gov.br/downloads-e-utilidades/) → Secretaria de Meio Ambiente → Documentos de Licenciamento.

**Data de acesso:** 15/09/2026. Os arquivos originais (.docx/.pdf) ficam hospedados na Prefeitura; os parâmetros extraídos de cada um estão em `config/gabarito_trs.json`, com o **trecho literal** que embasa cada valor.

| TR | Conteúdo | URL oficial |
|---|---|---|
| TR Aterro RSCC (Classe A) | Sondagem (3 pts até 1 ha), lençol (≥1,50 m; vedado <1,0 m; impermeab. 1,0–2,0 m), ensaios permeabilidade (2+1/ha), profundidade 3,0 m | [PDF 2026/09](https://www.campobom.rs.gov.br/wp-content/uploads/2026/09/TR-ATERRO_RSCC.pdf) |
| TR Meio Físico (Laudo Geológico — parcelamento) | Sondagem (4 furos até 1 ha), ensaios (3+1/ha), profundidade 3,0 m | [DOCX 2026/04](https://www.campobom.rs.gov.br/wp-content/uploads/2026/04/31-TR-MEIO-FISICO.docx) |
| TR RFO | 15/3 mudas (>1 m), densidade ≥3.000/ha, espécies ≥½ das suprimidas, monitoramento 2 anos, falha ≤10%, ART ≥2 anos | [DOCX 2026/04](https://www.campobom.rs.gov.br/wp-content/uploads/2026/04/2-TR-RFO.docx) |
| TR PRAD | Cronograma físico e financeiro (4.2), relatório execução 30 dias, 1º monitoramento 6 meses, monitoramento mínimo 2 anos (5.7) | [DOCX 2026/04](https://www.campobom.rs.gov.br/wp-content/uploads/2026/04/20-TR-PRAD.docx) |
| TR PCA | Relatórios trimestrais (supressão/afugentamento/movimentação de solo) e semestrais (obras) — item 5.1 | [DOCX 2026/04](https://www.campobom.rs.gov.br/wp-content/uploads/2026/04/1-TR-PCA.docx) |
| TR LFS (Fauna) | Busca ativa + passiva por grupo, primavera/verão, suficiência pela curva do coletor, APPs 100 m, UC raio 10 km | [DOCX 2026/04](https://www.campobom.rs.gov.br/wp-content/uploads/2026/04/10-TR-LFauna.docx) |
| TR LCV | Checklist de conteúdo do Laudo de Cobertura Vegetal | [DOCX 2026/04](https://www.campobom.rs.gov.br/wp-content/uploads/2026/04/8-TR-LCV.docx) |
| TR EIV / EIV simplificado | Checklist de conteúdo do Estudo de Impacto de Vizinhança | [DOCX](https://www.campobom.rs.gov.br/wp-content/uploads/2026/04/19-TR-EIV.docx) / [PDF 2023](https://www.campobom.rs.gov.br/wp-content/uploads/PDFs/MEIO-AMBIENTE/DOCUMENTOS-DE-LICENCIAMENTO/TR-do-EIV-simplificado-2023.pdf) |
| TR Arborização do passeio público | Conteúdo mínimo do projeto de arborização de calçadas | [DOCX 2026/04](https://www.campobom.rs.gov.br/wp-content/uploads/2026/04/30-TR-PLANO-DE-ARBORIZACAO-DO-PASSEIO-PUBLICO.docx) |
| TR Supervisão e Educação Ambiental | Conteúdo mínimo do programa | [DOCX 2026/04](https://www.campobom.rs.gov.br/wp-content/uploads/2026/04/9-TR-SUPERVISAO-E-EDUCACAO-AMBIENTAL.docx) |

## ⚠️ Divergência registrada (decisão do licenciador)

- **PRAD — monitoramento mínimo:** o TR oficial vigente (item 5.7) fixa **2 anos**; a especificação
  original do sistema pedia **4 anos**. O gabarito (`config/gabarito_trs.json`) foi calibrado com o
  valor OFICIAL (2 anos). Se a exigência interna da SEMA for maior, altere
  `prad_monitoramento_minimo_anos` no JSON (não precisa tocar em código).

- **Sondagem — nº de pontos:** os dois TRs divergem por design: **3 pontos** até 1,0 ha no
  **Aterro RSCC** vs. **4 furos** até 1 ha no **Laudo Geológico de Parcelamento**. O auditor
  detecta o contexto do laudo e aplica o gabarito correspondente.

## Formulários oficiais (para a Fase 1 — pendente de ingestão)

Os formulários oficiais atualizados (indústrias, comércio e serviços, obra civil, loteamentos,
ERB, mineração, movimentação de terra, supressão de vegetação etc.) estão na mesma página de
downloads. Para calibrar os checklists da Fase 1, baixe-os e rode:

```bash
python ferramentas/ingestar_pdfs.py <formulários .docx> --pasta-config config
# revisar config/checklists_oficiais.draft.json e promover:
python ferramentas/ingestar_pdfs.py --promover
```
