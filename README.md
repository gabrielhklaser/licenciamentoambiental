# 🌿 Sistema de Verificação de Documentação de Licenciamento Ambiental

**Prefeitura de Campo Bom — Secretaria Municipal do Meio Ambiente**

Ecossistema multiagentes para automação da triagem de processos de licenciamento
ambiental municipal, estruturado em **4 fases**:

```
┌──────────────────┐   ┌───────────────────────┐   ┌──────────────────────┐   ┌─────────────────┐
│ FASE 1 · PARSER  │──▶│ FASE 2 · PERSISTÊNCIA │──▶│ FASE 3 · AUDITOR     │──▶│ FASE 4 · UI     │
│ .htm/.html → JSON│   │ PostgreSQL/PostGIS    │   │ TÉCNICO              │   │ Streamlit +     │
│ DOM + RegEx      │   │ + Agente Admin.       │   │ Determinístico + LLM │   │ Ofícios .docx   │
│                  │   │ + Agente Financeiro   │   │ (Termos de Referência)│   │ (aprovação      │
│                  │   │ (checklist + URMs)    │   │                      │   │  humana)        │
└──────────────────┘   └───────────────────────┘   └──────────────────────┘   └─────────────────┘
```

---

## 🚀 Execução rápida

```bash
# 1. Ambiente e dependências
python3 -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Dashboard (Fase 4) — a interface pede o formulário .htm/.html e os laudos
streamlit run app.py

# 3. Demonstração do pipeline completo em linha de comando (Fases 1→4)
python main.py

# 4. Testes automatizados
python -m pytest tests/ -v
```

O dashboard também aceita **🧪 Carregar processo fictício de exemplo (LOR)** —
um processo LOR porte Médio/potencial Alto com duas pendências técnicas
(RFO com densidade de 2.000 mudas/ha e PCA com relatórios mensais), que culmina
na liberação do botão **“Baixar Minuta de Ofício”**.

---

## 📥 Calibração com os documentos oficiais (formulários, TRs e Manual de Taxas)

O sistema opera com **padrões internos** até receber os documentos oficiais.
A calibração é feita em 3 passos com a ferramenta de ingestão:

```bash
# 1. Ingerir os PDFs oficiais (gera texto por página + rascunhos de config)
python ferramentas/ingestar_pdfs.py "Manual de Legislação e Taxas Ambientais - SEMA Campo Bom.pdf" \
       "todos TRs_compressed.pdf" "formularios_compress.-1-60.pdf" "formularios_compress.-61-121.pdf"

# 2. REVISÃO HUMANA: conferir os rascunhos config/*.draft.json (nascem com
#    "revisado": false), usando docs_extraidos/<documento>/pagina_NNNN.txt
#    como memória de consulta (mostra página e trecho de cada valor capturado)

# 3. Promover os rascunhos revisados (ativa a calibração nos agentes)
python ferramentas/ingestar_pdfs.py --promover
python -m pytest tests/ -v    # sanity check
```

O que cada PDF alimenta:

| Documento oficial | Rascunho gerado | Quem consome |
|---|---|---|
| Manual de Legislação e Taxas | `config/taxas_urm.json` | `AgenteFinanceiro` (matriz URMs, valor da URM em R$, ERB, faixas de ha, Autorização/Declaração) |
| Termos de Referência | `config/gabarito_trs.json` | `AuditorTecnico` (distância do lençol, furos de sondagem, mudas por indivíduo, densidade mínima, anos de monitoramento, periodicidades) |
| Formulários oficiais | `config/checklists_oficiais.json` | `FormularioParser` (checklists por fase como fallback quando o HTML não traz a lista) |
| Formulários oficiais | `config/rotulos_formulario.json` | `FormularioParser` (rótulos alternativos de campos) |

Princípios da calibração:

- **Rastreabilidade**: cada valor carrega `fonte`, `revisado` e (nos TRs) o
  `trecho` e a `pagina` do PDF que o embasa — exibidos no dashboard;
- **Precedência**: config oficial > padrões do código (jamais o contrário);
- **Segurança**: enquanto o rascunho não for promovido (`revisado: true`),
  os agentes usam os padrões internos — nada entra em produção sem conferência
  do licenciador;
- O dashboard mostra na barra lateral o estado da calibração (✅ revisada /
  🟡 rascunho) por componente.

---

## 📂 Estrutura do projeto

| Caminho | Fase | Descrição |
|---|---|---|
| `licenciamento/parser_formulario.py` | 1 | `FormularioParser`: leitura de formulários `.htm/.html` com abordagem híbrida (BeautifulSoup4 no DOM + `re` nos valores), JSON estruturado, deduplicação de documentos, gatilho `bloqueado_sem_art` |
| `licenciamento/models.py` | 2 | Modelos SQLAlchemy: `Empreendimento`, `ProcessoLicenciamento`, `DocumentoAnexado`, `ResponsavelTecnico`, `TabelaURM` (PostGIS SIRGAS 2000 / SRID 4674) |
| `licenciamento/banco.py` | 2 | Engine, criação de esquema, semeadura da tabela de URMs e persistência do processo |
| `licenciamento/agente_administrativo.py` | 2 | Hard constraints (CPF/CNPJ, matrícula, ART) + cruzamento exigidos × anexados (normalização, siglas e similaridade) |
| `licenciamento/agente_financeiro.py` | 2 | Taxas em URMs: matriz Porte × Potencial × Fase; somatório LIR=LP+LI e LOR=LP+LI+LO; exceções ERB (valores fixos) e Lavra Mineral/Parcelamento de Solo (faixas de hectares) |
| `licenciamento/esquemas_tecnicos.py` | 3 | Contratos Pydantic (saída estritamente JSON tipado) |
| `licenciamento/auditor_tecnico.py` | 3 | `AuditorTecnico`: validadores matemáticos (RSCC: lençol ≥ 1,5 m; furos de sondagem; RFO: 15/3 mudas e densidade ≥ 3.000/ha) + validações semânticas (PRAD, Fauna, PCA) via LLM estruturado |
| `licenciamento/gerador_oficios.py` | 4 | `gerar_oficio_complementacao()`: minuta `.docx` com pendências, justificativas, trechos de referência e base legal |
| `app.py` | 4 | Dashboard Streamlit: upload múltiplo, semáforo em expanders, aprovação humana, download do ofício |
| `main.py` | 1→4 | Demonstração ponta a ponta em linha de comando |
| `exemplos/` | — | Formulários fictícios (LOR, LP, ERB, sem ART) e laudos PDF de exemplo |

---

## ⚙️ Fase 1 — Parser (`FormularioParser`)

- **Híbrido**: mapa de rótulos via DOM (linhas de tabelas `tr/td`, `dt/dd`) para isolar
  seções como *“IDENTIFICAÇÃO DO EMPREENDIMENTO”*, e RegEx para capturar/limpar valores
  (CPF/CNPJ, CODRAM, ART, coordenadas, áreas).
- **Coordenadas**: aceita UTM (E/N/Fuso) e Lat/Long (decimal ou GMS), normaliza para
  float padrão SIRGAS 2000 (limpeza de separadores, validação de faixas).
- **Pleito**: detecta LP, LI, LO, LIR, LOR, Autorização e Declaração (ordem de prioridade
  LOR/LIR antes de LO/LI para evitar falso positivo por sub-cadeia).
- **Checklist**: varre a seção “DOCUMENTAÇÃO EXIGIDA” por fase; aplica o agrupamento
  **LIR = LP + LI** e **LOR = LP + LI + LO**; **desduplica** por `set` (normalização) +
  similaridade de strings (`difflib.SequenceMatcher` ≥ 0,88 e contenção de sub-cadeia) —
  ex.: *“Cópia da matrícula do imóvel”* repetida em LP/LI/LO aparece **uma única vez**.
- **Robustez**: cada bloco em `try/except`; campo não encontrado → `None` + registro em log
  (`avisos_parser` no JSON).
- **Hard constraint**: ART ausente/vazia → `status_triagem: "bloqueado_sem_art"`.

## ⚙️ Fase 2 — Persistência e agentes

- **PostgreSQL + PostGIS** via `DATABASE_URL` (ex.:
  `postgresql+psycopg2://usuario:senha@localhost:5432/licenciamento` + `CREATE EXTENSION postgis;`).
  O protótipo roda em SQLite sem mudar uma linha (a coluna `geometria` guarda WKT fora do PostGIS).
- **AgenteAdministrativo**: barra o processo por ausência de **CPF/CNPJ**, **matrícula do
  imóvel** ou **ART**; cruza o checklist deduplicado com os anexos (na prototipagem a
  submissão é uma lista simulada no dashboard) e devolve `ok` × `pendente`.
- **AgenteFinanceiro**: matriz URMs com regra de **somatório** para LIR/LOR, **exceção ERB**
  (LP=612, LI=714, LO=510 — fixos pela espécie) e **exceção por faixa de hectares** para
  Lavra Mineral e Parcelamento de Solo (0–5 ha, 5–10 ha…).

> ⚠️ **Calibração da tabela de URMs**: os valores do Porte **Mínimo** (52,20 URMs em todas
> as fases/potenciais) e da **ERB** refletem os valores informados. As demais linhas da
> `MATRIZ_URM` (em `agente_financeiro.py`) estão marcadas com `# AJUSTAR` e devem ser
> substituídas pelos valores do **Manual de Legislação** oficial. A mesma tabela é semeada
> na tabela `TabelaURM` do banco.

## ⚙️ Fase 3 — Auditor Técnico

**Gabarito calibrado com os TERMOS DE REFERÊNCIA OFICIAIS da SEMA Campo Bom**
(publicados em `campobom.rs.gov.br` — ver `documentos_oficiais/trs/FONTES.md` e os
trechos literais em `config/gabarito_trs.json`):

| Validação | TR oficial | Regra vigente |
|---|---|---|
| Meio Físico — **Aterro RSCC** | TR Aterro RSCC Classe A (2026) | Sondagem: **3 pontos** até 1,0 ha + 1 por hectare **ou fração** excedente; profundidade ≥ 3,0 m; base do aterro **≥ 1,50 m** acima do nível **máximo** do lençol (NBR 15113); **VEDADO < 1,0 m**; **impermeabilização obrigatória** na faixa 1,0–2,0 m (argila ≥ 20 cm, k 10⁻⁶–10⁻⁷); ensaios de permeabilidade: **2 + 1/ha** |
| Meio Físico — **Parcelamento** | TR Laudo Geológico/Hidrológico (2025) | Sondagem: **4 furos** até 1 ha + 1 por hectare ou fração; ensaios: **3 + 1/ha**; profundidade ≥ 3,0 m (o auditor detecta o contexto do laudo e aplica o gabarito certo) |
| **RFO** | TR RFO + COMDEMA 02/2017 | **15 mudas** (>1 m) por nativo e **3** por exótico suprimido; densidade **≥ 3.000 mudas/ha**; espécies plantadas **≥ metade** das suprimidas; monitoramento **≥ 2 anos** (relatórios anuais); falha **≤ 10%**; ART ≥ 2 anos |
| **PRAD** | TR PRAD (2026) | Cronograma **físico e financeiro** detalhado (4.2); relatório de execução em **30 dias** e 1º monitoramento em **6 meses** (5.1); monitoramento mínimo **2 anos** (5.7)* |
| **Fauna (LFS)** | TR Laudo de Fauna Silvestre | ≥ 1 método de **busca ativa** e 1 de **busca passiva** por grupo; amostragem em **primavera/verão**; suficiência amostral pela **curva do coletor** |
| **PCA** | TR PCA (2026, 5.1) | Relatórios **trimestrais** (supressão de vegetação, afugentamento de fauna e movimentação de solo) e **semestrais** (obras e estruturas) |
| **EIV / LCV** | TRs de conteúdo | Checklist de itens mínimos do TR (identificação, tráfego, ruídos, medidas, ART / inventário, estágio sucessional, APPs, parecer conclusivo…) |

\* **Divergência documentada:** a especificação original pedia 4 anos de monitoramento no PRAD;
o TR oficial vigente (5.7) fixa 2 anos — o gabarito segue o oficial, ajustável em
`config/gabarito_trs.json` sem tocar em código.

- Saída padronizada (`ResultadoValidacao`): `documento_analisado`, `status`
  (CONFORME/PENDENTE/REVISAO_MANUAL), `itens_reprovados` (justificativa do TR),
  `trecho_referencia` (trecho do PDF que embasou a decisão).
- **LLM plugável**: por padrão roda offline com heurísticas determinísticas
  (`origem=heuristico_local`). Para produção, instale `langchain langchain-openai`,
  defina `OPENAI_API_KEY` e `LICENCIA_PROVEDOR_LLM=langchain` — a saída continua sendo
  Pydantic estruturado (sem alucinação de formato).
- PDFs escaneados ficam em `REVISAO_MANUAL` com gancho de OCR (`pytesseract`) documentado.

## ⚙️ Fase 4 — Dashboard em duas etapas (upload → análise + parecer)

**Etapa 1 — Upload:** a página inicial pede apenas a subida dos documentos do processo:
**`.htm`/`.html`** (formulário), **`.pdf`**, **Word (`.docx`)**, **Excel (`.xlsx`)**,
`.txt`/`.csv` **e IMAGENS (`.png`, `.jpg`, `.jpeg`, `.webp`, `.bmp`, `.tif`, `.gif`)** —
documentações às vezes chegam como foto/escaneamento: o conteúdo é lido por **OCR**
(`pytesseract`, opcional no servidor) e, sem OCR, o documento entra para **conferência
manual com preview da imagem** no painel (o casamento com a exigência usa o nome do
arquivo, ex.: `matricula_imovel.jpg`).

**Etapa 2 — Avaliação:** após carregar os arquivos e clicar em "Analisar documentação":

- **Quadro resumo da documentação**: cada exigência da licença é classificada como
  **✅ em conformidade**, **🟡 com pendência(s)** ou **❌ não apresentada**, com o arquivo
  correspondente e a descrição exata das pendências;
- **Análise por documento recebido**: identificação do tipo (matrícula, CNPJ, ART, PGRS,
  alvarás, laudos de TR…) e validações específicas — destaque para a **matrícula do imóvel**:
  a data de emissão é procurada no **final do documento (canto inferior esquerdo**, fechamento
  do oficial de registro/certificação digital) e o prazo de validade de **90 dias** é conferido
  (`config/regras_documentos.json`; o formulário oficial cita 30 dias pelo Provimento
  037/2018-CGJ — divergência sinalizada no JSON);
- **Auditoria técnica** pelos Termos de Referência (RSCC/Parcelamento, RFO, PRAD, Fauna, PCA,
  EIV/LCV) + triagem administrativa + taxa em URMs;
- **📄 Emissão do Parecer Técnico (.docx)**: botão final que gera o parecer formal da SEMA
  com identificação do processo, documentação apresentada, quadro de exigências, pendências
  administrativas e técnicas e, na **conclusão, a lista numerada do que falta** para
  contemplar toda a documentação da licença (com prazo e assinatura).
  O ofício de complementação (`gerar_oficio_complementacao`) permanece disponível no pipeline.

---

## 🔧 Calibração com os formulários oficiais

Os padrões de extração (rótulos, RegEx e cabeçalhos de checklist) estão centralizados
nas constantes de classe de `FormularioParser` (`ROTULOS`, `REGEX`, `REGEX_SECAO_DOCS`).
Ao receber os formulários oficiais em `.htm`, basta ajustar as constantes (e rodar
`python -m pytest` + o próprio parser: `python -m licenciamento.parser_formulario arquivo.htm`).
