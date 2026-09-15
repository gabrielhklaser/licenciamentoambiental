# Documentos Oficiais — SEMA Campo Bom

**Fontes:** site oficial da Prefeitura de Campo Bom — [Downloads e Utilidades](https://www.campobom.rs.gov.br/downloads-e-utilidades/) → Secretaria de Meio Ambiente → Documentos de Licenciamento — **e pacotes enviados pelo licenciador via Google Drive (15/09/2026)**.

**Data de acesso:** 15/09/2026. Os parâmetros extraídos de cada documento estão em `config/gabarito_trs.json` (TRs), `config/taxas_urm.json` (Manual de Taxas) e `config/checklists_oficiais.json` (formulários), com os **trechos literais** que embasam cada valor.

## 📦 Pacotes enviados pelo licenciador (Google Drive — 15/09/2026)

| Arquivo | Conteúdo | Link | Ingestão |
|---|---|---|---|
| `todos TRs_compressed` | TR Meio Físico (22/12/2025), TR Aterro RSCC Classe A (31/08/2026), TR EIV completo (13 seções) e TR EIV Simplificado (Lei 5.329/2022) | [Drive](https://drive.google.com/file/d/1IllA9j57vTm8UnbLEIZWg_HIaHLkcoqo/view?usp=sharing) | `config/gabarito_trs.json` (conferência integral + checklists EIV/LCV ampliados) |
| **Manual de Legislação e Taxas Ambientais** | Tabelas A–F de URMs (Lei 4.439/2015; Código Tributário 2.397/2002; Leis 3.319/2008 e 4.068/2013; Res. COMDEMA 003/2017) | [Drive](https://drive.google.com/file/d/15YNrc6iV54OZHFHvweKJci02XwIX6K89/view?usp=drive_link) | `config/taxas_urm.json` (revisado=true) |
| `formularios_compress` | Formulários oficiais: Sítios de Lazer 2025-2 (checklists LP/LI/LO/Renovação), Requerimento padrão (LP/LI/LO/LIR/LOR/Licença Única/Autorização/Declaração/Alvará Florestal), Exploração Eventual de Árvores Nativas, Imunes ao Corte 2026, Baixo Impacto em APP 2026, Manejo Estágio Médio 2 ha, Abertura de Açude | [Drive](https://drive.google.com/file/d/1AQLWUw_6bFKjctRas8FSwml1YxOWf8c1/view?usp=drive_link) | `config/checklists_oficiais.json` (revisado=true) |

### ⚠️ Divergência sinalizada — ERB / Transmissão (Tabela B)

O **Manual oficial** fixa **1.960,00 URM por fase (LP = LI = LO)** para "Instalação de sistemas de
transmissão e/ou retransmissão de rádio, televisão, telefonia e similares"; a especificação
original do sistema pedia **612 / 714 / 510 URMs**. Aplicado o valor OFICIAL; divergência
documentada em `config/taxas_urm.json` (nota_revisao). Outros pontos sinalizados: Lavra Mineral
(Manual publica só a faixa 0–5 ha), Parcelamento (faixas até 20 ha) e Licença Única (somatório
LP+LI+LO por substituir as 3 fases — interpretação revisável).

## 🌐 TRs do site oficial da Prefeitura

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

## ⚠️ Divergências registradas nos TRs (decisão do licenciador)

- **PRAD — monitoramento mínimo:** o TR oficial vigente (item 5.7) fixa **2 anos**; a especificação
  original do sistema pedia **4 anos**. O gabarito (`config/gabarito_trs.json`) foi calibrado com o
  valor OFICIAL (2 anos). Se a exigência interna da SEMA for maior, altere
  `prad_monitoramento_minimo_anos` no JSON (não precisa tocar em código).

- **Sondagem — nº de pontos:** os dois TRs divergem por design: **3 pontos** até 1,0 ha no
  **Aterro RSCC** vs. **4 furos** até 1 ha no **Laudo Geológico de Parcelamento**. O auditor
  detecta o contexto do laudo e aplica o gabarito correspondente.

## Formulários oficiais (Fase 1 — checklists calibrados)

Os 6 formulários do pacote foram transcritos em `config/checklists_oficiais.json`
(`documentos_por_fase` para LP/LI/LO/Autorização/Declaração + `checklists_por_formulario`
com o checklist específico de cada tipo) e resumidos em
`documentos_oficiais/formularios/FORMULARIOS.md`. Para reingerir versões futuras:

```bash
python ferramentas/ingestar_pdfs.py <formulários .docx/.pdf> --pasta-config config
# revisar config/checklists_oficiais.draft.json e promover:
python ferramentas/ingestar_pdfs.py --promover
```
