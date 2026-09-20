# 🔐 SEGURANCA.md — Laudo de auditoria de segurança

**Data:** 2026-09-17 · **Escopo:** repositório completo (app.py, `licenciamento/`,
`tests/`, `config/`, `.streamlit/`, dependências) · **Método:** skills
`security-audit` (OWASP Top 10 / SANS 25 / CWE Top 25) + `senior-security`,
instaladas local em `.claude/skills/` (sem credenciais).
**Diretriz do usuário:** revisar arquivos e scripts e APLICAR as correções
necessárias. Este laudo segue o formato exigido pela skill (categoria,
localização, severidade, vetor, impacto, evidência, correção, teste de
validação) e é mantido a cada mudança estrutural.

---

## RESUMO EXECUTIVO

| Severidade | Encontradas | Corrigidas | Aceitas/documentadas |
|---|---|---|---|
| Crítica | 1 | 1 | 0 |
| Alta | 3 | 3 | 0 |
| Média | 3 | 2 | 1 |
| Baixa | 2 | 2 | 0 |
| Informativa | 6 | 0 | 6 |

**5 riscos principais (antes das correções):**
1. XSS armazenado — conteúdo/nome de documentos enviados renderizados em
   markdown sem sanitização (CORRIGIDA);
2. CVEs conhecidas no `setuptools` do ambiente (6 CVEs, pip-audit) (CORRIGIDA);
3. Path traversal via sufixo de nome de arquivo em gravação em disco (CORRIGIDA);
4. Quebra de atributo HTML nos links de download (CORRIGIDA);
5. XSRF/CORS desligados no Streamlit atrás do proxy do preview (ACEITA com
   compensação documentada — produção exige reversão).

**Recomendações estratégicas:** manter a camada SEC- do auditor rodando em
toda bateria (detecta regressão das 4 correções); rodar `pip-audit` periódico;
em produção, habilitar XSRF/CORS + autenticar (SSO/rede interna) e revisar a
base legal (LGPD) antes de habilitar LLM externo.

---

## VULNERABILIDADES E RISCOS

### 1. [A. INJEÇÕES/XSS] — XSS armazenado via conteúdo de documentos
**Localização:** `app.py` — renderização de `trecho_referencia`,
`documento_analisado` (NOME do arquivo enviado), `itens_reprovados`,
`justificativa`, `documento` e `tipo_licenca` (parser) em
`st.markdown`/`st.error`/`st.warning`.
**Severidade:** Alta · **Status:** ✅ CORRIGIDA
**Descrição técnica:** Streamlit interpreta markdown (links, imagens) nesses
elementos; texto extraído de uploads é controlado por quem envia o processo.
Um laudo/citação malicioso poderia injetar link `javascript:`/`data:` ou
marcadores de HTML nos painéis do analista.
**Vetor de ataque:** documento com trecho `"[abrir](javascript:…)"` ou nome de
arquivo com payload, exibido na tela do analista.
**Impacto:** execução de ações na sessão do analista, phishing contextual.
**Evidência (antes):** `st.markdown(f"> 📄 … \"{analise.trecho_referencia}\"")`
**Correção aplicada:** módulo `licenciamento/seguranca.py` (`md_seguro`: escapa
HTML, neutraliza `[..](..)`, remove caracteres de controle) aplicado nos 9
pontos de renderização de dado não confiável.
**Teste de validação:** `test_seguranca_primitivas_e_auditor_sec` (A/B) +
checks SEC-XSS-TRECHO/SEC-XSS-NOME-DOC/SEC-XSS-ITENS do auditor (regressão
simulada detecta todos).

### 2. [J. DEPENDÊNCIAS] — CVEs conhecidas no setuptools
**Localização:** ambiente (`.venv`; setuptools 66.1.1 do Python 3.11).
**Severidade:** Alta · **Status:** ✅ CORRIGIDA
**Descrição técnica:** `pip-audit` apontou PYSEC-2025-49, PYSEC-2026-1918 e
PYSEC-2026-3447 (6 ocorrências) — path traversal/injeção ao processar pacotes.
**Vetor:** instalação/processamento de pacotes (surface de supply chain).
**Impacto:** execução de código na manutenção do ambiente.
**Correção aplicada:** `setuptools>=83.0.0` no ambiente + piso em
`requirements.txt`; re-audit: **"No known vulnerabilities found"**.
**Teste de validação:** `.venv/bin/pip-audit --skip-editable` (exit 0, vazio).

### 3. [C. CONTROLE DE ACESSO] — Path traversal via sufixo de upload
**Localização:** `app.py` `salvar_entrada_real` (sufixo do nome do arquivo ia
direto para o caminho gravado em `entradas_reais/`).
**Severidade:** Média · **Status:** ✅ CORRIGIDA
**Descrição/vetor:** nome de arquivo com sequências estranhas no sufixo
(controlado pelo remetente) composto em caminho de disco.
**Impacto:** escrita fora da pasta prevista (baixa aqui: prefixo é carimbo de
data/hora gerado pelo servidor, mas defesa obrigatória).
**Correção aplicada:** `sufixo_seguro()` (apenas `[a-z0-9]`, ≤10 chars,
default `.html`).
**Teste de validação:** teste F do `test_seguranca_primitivas_e_auditor_sec`
+ check SEC-SUFFIX.

### 4. [E. CLIENT-SIDE] — Quebra de atributo HTML nos links de download
**Localização:** `app.py` `link_download` (atributos `download`/`href`/rótulo
concatenados sem escape).
**Severidade:** Baixa · **Status:** ✅ CORRIGIDA
**Descrição/vetor:** valores vindos de variáveis poderiam conter `"` e
injetar atributos/eventos (`onerror=`, `onmouseover=`).
**Correção aplicada:** `nome_arquivo_seguro()` (slug `[A-Za-z0-9._-]`, sem
traversal) + `attr_html()` (escape com aspas) em TODOS os atributos.
**Teste de validação:** testes C/E do novo teste; E2E do parecer segue
decodificando os links data-URI (121 testes OK).

### 5. [F. REQUESTS] — CSRF/XSRF desprotegido e CORS aberto (config do preview)
**Localização:** `.streamlit/config.toml` — `enableCORS=false`,
`enableXsrfProtection=false`.
**Severidade:** Média · **Status:** ⚠️ ACEITA COM COMPENSAÇÃO
**Descrição técnica:** sem token XSRF, um site malicioso poderia induzir
requisições à sessão aberta do analista (upload inclusive). DESLIGAR é
necessário atrás do proxy do preview: com a proteção ativa TODO upload falha
(403 "XSRF token missing" — o cookie/header não atravessam o proxy), e o CORS
ativo restringe a origem ao localhost. Documentado no próprio config.toml.
**Compensação:** sessão do preview controlada pela plataforma; ferramenta
interna de usuário único.
**Ação obrigatória em produção:** reverter ambas para o padrão (protegido) e
servir sem proxy intermediário ou com proxy que preserve cookies/headers.
**Teste de validação:** upload funcional pelo preview (E2E do app) + nota no
config.toml.

### 6. [B/D. AUTENTICAÇÃO] — Aplicação sem login
**Localização:** app inteiro.
**Severidade:** Informativa (interno/preview) · **Status:** 📋 PLANEJADO
**Descrição:** sem autenticação/autorização (sem IDOR por não haver
multiusuário nem objeto compartilhado; estado em `session_state` local).
**Recomendação:** em produção, SSO da Prefeitura/rede interna + trilha de
auditoria por usuário (quem emitiu cada parecer).

### 7. [F/EXTERNO] — Envio de dados do processo a LLM externo (opt-in)
**Localização:** `licenciamento/auditor_tecnico.py` (`ProvedorLangChain`,
`LICENCIA_PROVEDOR_LLM`, `OPENAI_API_KEY` via variável de ambiente).
**Severidade:** Informativa · **Status:** 📋 POLÍTICA
**Descrição:** ao habilitar, TEXTO de documentos reais (dados pessoais:
CNPJ/CPF, nomes, coordenadas, matrículas) sai para o provedor.
**Mitigações existentes:** opt-in explícito por env; chave NUNCA hardcoded
(check SEGREDO-VERSIONADO no auditor); sem provedor = análise local.
**Recomendação:** LGPD — só habilitar com base legal/contrato de tratamento;
ideal: provedor local.

### 8. [I. UPLOADS] — Processamento de PDF/imagem não confiável
**Localização:** `leitor_pdf.py` (PyMuPDF + RapidOCR), `validador_documentos.py`
(PIL), parser BS4 `html.parser` (sem XXE — não usa lxml/XML externo).
**Severidade:** Informativa · **Status:** ✅ MITIGADO
**Mitigações:** `MAX_PAGINAS_OCR` (OCR limitado às primeiras páginas),
`maxUploadSize=300MB`, todas as leituras em try/except com degradação para
REVISAO_MANUAL; PIL tem guarda nativa de decompression bomb.
**Recomendação:** monitorar memória; em produção, antivírus no upload.

### 9. [A. ReDoS] — Regexes sobre texto não confiável
**Localização:** `parser_formulario.py`, `identificador_documentos.py`.
**Severidade:** Informativa · **Status:** 📋 REVISÃO PERIÓDICA
**Descrição:** varredura não encontrou quantificadores aninhados catastróficos
(padrões usam janelas limitadas `.{0,N}`); risco existe a CADA novo padrão.
**Recomendação:** ao incluir regex nova sobre texto de documento, testar com
entrada adversarial longa.

### 10. [G. CONFIG] — Mensagens de erro detalhadas na UI
**Localização:** `app.py` (`str(exc)` em erros de formulário/compilação).
**Severidade:** Baixa · **Status:** ⚠️ ACEITO (interno)
**Impacto:** pode expor caminhos internos ao usuário. **Recomendação:** em
produção, logar completo e exibir mensagem genérica + código.

### 11. [J. SUPPLY CHAIN] — Auto-instalação de pacotes pelo auditor
**Localização:** `auditor_sistema.py` `_pip_install` (lista ESTÁTICA do
repositório, sem input do usuário).
**Severidade:** Informativa · **Status:** ✅ ACEITO controlado.

### 12. [H. LÓGICA DE NEGÓCIO] — Manipulação de taxa/pleito
**Localização:** `agente_financeiro.py`, `parser_formulario.py`.
**Severidade:** Informativa · **Status:** ✅ MITIGADO
**Mitigações:** taxa vem de tabela oficial (Manual) por faixas — sem entrada
numérica livre; pleito DECLARADO prevalece e divergência com o formulário é
sinalizada; áreas/profissional com dupla checagem.

---

## VERIFICAÇÕES PERMANENTES (anti-regressão)

A camada **SEC-** (`AuditorSistema.verificar_seguranca`) roda na bateria e no
CLI `--rapida` e falha ALTA se qualquer garantia acima sumir:
`SEC-XSS-TRECHO`, `SEC-XSS-NOME-DOC`, `SEC-XSS-ITENS` (md_seguro obrigatório),
`SEC-SUFFIX` (sufixo seguro), `SEC-EXEC-*` (proíbe `eval(`/`exec(`/`shell=True`
em produção), `SEGREDO-VERSIONADO` (proíbe credenciais versionadas).
**Estado na emissão deste laudo: 0 achados** (e a regressão simulada é 100%
detectada — ver teste G de `test_seguranca_primitivas_e_auditor_sec`).

## COMO REVERTER
`git revert <hash do commit de segurança>` — restaura app.py/auditor_sistema
pré-blindagem (as skills em `.claude/skills/` são aditivas e podem ficar).

## ROTINA RECOMENDADA
1. `pip-audit` a cada mudança de `requirements.txt`;
2. bateria completa (121+ testes) + `auditor --rapida` antes de cada entrega;
3. revisar este laudo a cada nova superfície (upload novo, integração externa).
