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
