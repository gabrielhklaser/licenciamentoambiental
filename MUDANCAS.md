## [2026-09-25] INTEGRAÇÃO DAS SKILLS GABEBRAIN: LEITURA ESTRUTURAL DE PDFs, OCR CALIBRADO E RASTREAMENTO DE PÁGINAS
- **Arquivos:** `licenciamento/leitor_pdf.py` (métodos GabeBrain 02, 07 e 17 integrados), `licenciamento/esquemas_tecnicos.py` (+pagina_referencia, tokens_estimados, documento_extenso), `licenciamento/auditor_tecnico.py` (rastreamento de página nos achados técnicos), `tests/test_validador_documentos.py` (+5 testes unitários de habilidades GabeBrain).
- **O quê:**
  1. Extração página a página com marcadores `[[pag N]]` via PyMuPDF (método `02_extrair_texto.py` da skill `biblioteca-pesquisavel`);
  2. Utilitários de leitura seletiva/fatiada (`extrair_paginas`) e localização precisa de página de evidências (`localizar_pagina`);
  3. Detecção de camada de texto corrompida (mapeamento quebrado de fontes / mojibake) que força OCR mesmo quando há texto nativo aparente (método `07_ocr_escaneados.py`);
  4. OCR calibrado do GabeBrain: renderização a 300 DPI em escala de cinza e binarização adaptativa (`limiar = max(1, int(img.mean()) - 90)`);
  5. Extração estrutural gratuita de sumário/ToC via PyMuPDF (`doc.get_toc()`), validação de consistência (`outline_util`) e identificação de seções quentes técnicas e documentos extensos (>50k tokens) (método `17_estrutura_documento.py` da skill `biblioteca-mapa-documento`);
  6. Aplicação da regra de ouro da skill `biblioteca-triagem`: *"Todo achado precisa de página"* — os agentes técnicos (`AuditorTecnico`) e esquemas de dados passam a registrar e exibir `[pág. N]` em cada item e trecho de referência;
  7. Bateria completa de testes ampliada de 122 para 127 testes aprovados (100% verde).
- **Por quê:** Solicitação do usuário para utilizar as novas skills instaladas no GabeBrain para leitura de documentos e implementar as melhorias no código.
- **Como reverter:** `git revert <hash deste commit>`.
- **Estado:** 127 testes OK (100% verde).

---

## [2026-09-25] SKILL DOCUMENT-IMAGE-ANALYSIS, OCR RAPIDOCR PARA IMAGENS, CONSOLIDAÇÃO DAS ETAPAS E RESILIÊNCIA WINDOWS
- **Arquivos:** `.claude/skills/document-image-analysis/SKILL.md` (NOVO), `licenciamento/leitor_imagem.py` (NOVO), `licenciamento/validador_documentos.py` (_texto_ocr atualizado com RapidOCR e Pillow), `app.py` (painel executivo de Consolidação das Etapas do Processo), `ferramentas/ingestar_pdfs.py` (sys.stdout UTF-8 resiliente no Windows), `tests/test_validador_documentos.py` (+1 teste de OCR de imagem).
- **O quê:** 
  1. Análise de skills de marketplaces e repositórios para checagem documental e OCR;
  2. Implementação da skill `document-image-analysis` (.claude/skills/document-image-analysis/SKILL.md) e do módulo `LeitorImagem` com RapidOCR/ONNX em CPU e pré-processamento Pillow para formatos de imagem (.png, .jpg, .jpeg, .webp, .tiff, .bmp);
  3. Atualização do `ValidadorDocumentos._texto_ocr` para utilizar o novo pipeline de OCR de imagens com fallback gracioso;
  4. Consolidação das 4 etapas (Administrativa, Financeira, Técnica e Parecer) em painel executivo com dupla checagem integrada no `app.py`;
  5. Correção de encoding UTF-8 no CLI de promoção de drafts no Windows (`ingestar_pdfs.py`);
  6. Bateria completa de testes aprovada (122 passed).
- **Por quê:** Solicitação do usuário para integrar skill de checagem documental e imagem, fazer dupla checagem em todas as etapas, consolidar as etapas e sincronizar via Arena AI e GitHub.
- **Como reverter:** `git revert <hash deste commit>`.
- **Estado:** 122 testes OK (100% verde).

---

## AUDITORIA DE SEGURANÇA — skills security-audit + senior-security

**Arquivos:** `licenciamento/seguranca.py` (NOVO), `app.py` (9 pontos
blindados + link_download + salvar_entrada_real), `licenciamento/
auditor_sistema.py` (+verificar_seguranca, prefixo SEC-, na bateria),
`tests/test_pipeline.py` (+1 teste, 121P), `requirements.txt`
(+setuptools>=83), `SEGURANCA.md` (NOVO — laudo completo),
`.claude/skills/security-audit/SKILL.md` e
`.claude/skills/senior-security/SKILL.md` (NOVOS — skills do Drive
instaladas LOCAL, sem credenciais).

**O quê/por quê:** pedido do usuário — instalar as skills de segurança dos
2 links do Drive, revisar arquivos e scripts e APLICAR as correções
necessárias. Corrigido: (1) XSS armazenado — todo texto derivado de
documentos enviados (trecho, nome de arquivo, itens, justificativas, tipo
do parser) agora passa por md_seguro antes de st.markdown/error/warning;
(2) path traversal — sufixo de upload via sufixo_seguro; (3) quebra de
atributo HTML — link_download com nome_arquivo_seguro + attr_html;
(4) 6 CVEs do setuptools (pip-audit) — >=83.0.0 + piso no requirements.
Documentado/aceito: XSRF/CORS off (exigência do proxy do preview — reverter
em produção), sem login (SSO em produção), LLM externo opt-in (LGPD).
Camada SEC- permanente no auditor detecta regressão de tudo isso.

**Como reverter:** `git revert <hash deste commit>` (skills são aditivas).

**Estado:** 121 testes OK ×2; main.py 0; auditor --rapida 0 achados (SEC
limpa); pip-audit "No known vulnerabilities found"; regressão simulada das
5 classes detectada 100% pelo SEC.

---

## REWORK — Parecer como TEXTO editável + exportação .docx/.pdf

**Arquivos:** `licenciamento/compilador_parecer.py` (NOVO), `app.py` (bloco
da emissão refeito), `tests/test_pipeline.py` (E2E reescrito + 1 teste do
compilador), `requirements.txt` (+reportlab), `MUDANCAS.md`.

**O quê:** o botão da emissão NÃO baixa mais nada direto — ele ACIONA O
COMPILADOR DE TEXTO (pedido do licenciador, pois o download direto não
atravessava o proxy). `compilar_texto_parecer(...)` produz o parecer como
TEXTO estruturado (as mesmas 6 seções do docx oficial); o texto aparece em
prévia EDITÁVEL (`st.text_area` alto dentro de expander aberto) onde o
analista corrige antes de exportar, e pode simplesmente copiar/colar em
outro documento (Ctrl+A/Ctrl+C). Do TEXTO final derivam: `.docx`
(python-docx; títulos do cabeçalho em negrito centralizado, bullets e
seções numeradas em negrito, recuos itálicos) e `.pdf` (reportlab,
validado com pypdf — contém "PARECER TÉCNICO" senão erro). Cada
exportação tem `st.download_button` + LINK data-URI embutido no markdown
(a rota /media/ não atravessa o proxy do preview). Cópias auditáveis em
`saidas/` (.docx e .pdf). `skill pdf` instalada local em
`.claude/skills/pdf/SKILL.md` (commitada; reportlab/pypdf, sem
credenciais).

**Por quê:** ba020b6 gerava docx válido mas o usuário continuava sem
conseguir baixar; direção nova: texto editável primeiro, exportação depois
— e copiar/colar é o caminho garantido.

**Como reverter:** `git revert <hash deste commit>` (o compilador é só
adição; app.py volta ao fluxo ba020b6).

**Estado:** 120 testes OK ×2; main.py 0; auditor --rapida 0 achados;
fumigação com o caso real: texto 1.476 ch → docx 36 KB → pdf 3 KB/1 pág.

---

# LOG DE MUDANÇAS — Sistema de Verificação do Licenciamento Ambiental

> **Para o próximo agente:** este arquivo registra TODA mudança estrutural,
> com o que foi alterado, por quê, e **como reverter** ao ponto que
> funcionava. Ao terminar suas alterações, **adicione uma entrada no topo**
> (formato abaixo) e faça commit. Os testes são a rede de segurança:
> `.venv/bin/python -m pytest tests/ -q` deve passar 100% antes do push.
>
> Formato de entrada:
> ```
> ## [AAAA-MM-DD HH:MM] <título> — commit <hash novo>
> - **Arquivos:** <caminhos>
> - **O quê:** <mudanças>
> - **Por quê:** <erro/pedido do licenciador>
> - **Como reverter:** `git revert <hash>` (ou `git checkout <hash anterior> -- <arquivos>`)
> - **Estado:** <N> testes OK · main.py OK · auditor: <resultado>
> ```

---

## [2026-09-17] EMISSÃO DO PARECER TÉCNICO não baixava — commit deste envio
- **Arquivos:** `app.py` (bloco "PARECER TÉCNICO (botão final)"),
  `.gitignore` (+`saidas/`), `tests/test_pipeline.py` (+1 teste), `MUDANCAS.md`.
- **O quê:** o download_button renderizava, mas o download do Streamlit usa a
  rota `/media/`, que pode não atravessar o proxy do preview -> nenhum arquivo
  baixava. Refeito: (1) docx gerado UMA vez por (número, prazo, comentários) e
  cacheado em session_state (clique re-renderiza sem regenerar); (2)
  st.download_button mantido; (3) NOVO fallback LINK data-URI (base64 embutido
  no conteúdo da página) que SEMPRE baixa, mesmo atrás de proxy; (4) falha de
  geração agora aparece como st.error (antes podia falhar em silêncio);
  (5) cópia auditável em saidas/parecer_tecnico_<nº>.docx (gitignored).
- **Por quê:** reportado pelo licenciador ("o botão de baixar o parecer
  técnico não baixa arquivo algum").
- **Como reverter:** `git revert <hash deste commit>`.
- **Estado:** 119 testes OK (rodada dupla) · teste prova o .docx válido
  (python-docx abre e contém "PARECER TÉCNICO Nº 001/2026").
---

## [2026-09-17] CRASH ValidationError em trecho_referencia — commit deste envio
- **Arquivos:** `licenciamento/auditor_tecnico.py` (1 linha),
  `licenciamento/esquemas_tecnicos.py` (2 linhas), `tests/test_pipeline.py` (+1 teste),
  `MUDANCAS.md` (este arquivo).
- **O quê:** (1) `conferir_projeto_urbanistico` passava `trecho_referencia=None`
  quando o profissional do projeto não é reconhecido — o modelo Pydantic
  `ResultadoValidacao` exige string → `ValidationError` e quebra da análise;
  agora passa `""`. (2) Defesa em profundidade: o campo do modelo aceita
  `Union[str, None]` (None nunca mais derruba o painel); import `Union` added.
- **Por quê:** erro reportado pelo licenciador ao enviar documentos
  (traceback `app.py -> executar_analise -> auditar_com_dupla_checagem ->
  conferir_projeto_urbanistico -> ResultadoValidacao`).
- **Como reverter:** `git revert <hash deste commit>` ou
  `git checkout 75fbf5d -- licenciamento/auditor_tecnico.py licenciamento/esquemas_tecnicos.py`.
- **Estado:** 118 testes OK (rodada dupla) · main.py OK.

---

## [2026-09-17] Conferências documentais (CNPJ/ART/projeto/TR) — commit 75fbf5d
- **Arquivos:** `licenciamento/auditor_tecnico.py`, `licenciamento/agente_administrativo.py`,
  `app.py`, `licenciamento/auditor_sistema.py` (+checks CONF), `tests/test_pipeline.py`.
- **O quê:** (1) CNPJ da matrícula prioriza rótulo "Número de Inscrição"
  (xx.xxx.xxx/xxxx-xx); (2) projetos urbanísticos: dupla checagem
  profissional+áreas (`conferir_projeto_urbanistico` — NOVO método);
  (3) ART: nome procurado nos primeiros dados do documento (tokens cruzados
  com o texto) + atividade "licenciamento ambiental" atribui a responsabilidade
  técnica à ART da seção 8; (4) REMOVIDA a mensagem "Nenhum Termo de
  Referência reconhecido" (usuário não envia TR); (5) agente de conformidade
  ganhou checks `CONF-*`.
- **Por quê:** 5 correções pedidas pelo licenciador (turno anterior).
- **Como reverter:** `git revert 75fbf5d`.
- **Estado:** 117 testes OK · auditor completo: 0 achados.

## [2026-09-17] Dupla checagem dos TRs + roteamento por título — commit 93afb6a
- **Arquivos:** `licenciamento/auditor_tecnico.py` (`auditar_com_dupla_checagem`,
  `TR_ESPERADO_PELO_TITULO`, roteamento geológico por "infiltração"), `app.py`
  (selo "🔎 Dupla checagem"), `tests/test_pipeline.py`.
- **Por quê:** confronto dos laudos (geológico/LCV/fauna) com os TRs do banco
  SEMPRE em dupla checagem, citando ponto a ponto.
- **Como reverter:** `git revert 93afb6a`.

## [2026-09-17] Redesign frontend (skill frontend-design) — commit 17d74f5
- **Arquivos:** `app.py` (tokens CSS `CSS_TOKENS`, `cabecalho_institucional`,
  `trilha_etapas`), `.claude/skills/frontend-design/SKILL.md` (skill instalada
  localmente, sem credenciais), `tests/test_pipeline.py`.
- **Como reverter:** `git revert 17d74f5` (o tema escuro continua funcionando
  pois é override de tokens).

## [2026-09-17] Agente Auditor do Sistema — commit a3ba3db
- **Arquivos:** `licenciamento/auditor_sistema.py` (NOVO), `app.py` (botão
  "🧭 Auditar sistema"), `.gitignore` (auditoria/), `tests/test_pipeline.py`.
- **Como reverter:** `git revert a3ba3db`.
