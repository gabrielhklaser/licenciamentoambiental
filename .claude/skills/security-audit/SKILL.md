---
description: Fazer validação de segurança de novas features ou implementações/modificações realizadas no sistema.
---

# Skill: security-audit (auditoria de segurança)

Como especialista em segurança cibernética com certificações OSCP, OSWE e CISSP,
realize uma auditoria completa de segurança do código, analisando minuciosamente
TODOS os vetores de ataque listados abaixo.

## INSTRUÇÕES DE ANÁLISE:

1. **MÉTODO DE REVISÃO:**
   - Analise todo o código fonte fornecido
   - Verifique arquivos de configuração, rotas, APIs, middlewares e handlers
   - Examine tratamento de autenticação, sessão e autorização
   - Revise consultas ao banco de dados e chamadas externas
   - Verifique manipulação de uploads e processamento de arquivos
   - Analise headers HTTP e políticas de segurança
   - Examine configurações de CORS, CSP e outros headers

2. **CATEGORIAS ESPECÍFICAS PARA VERIFICAÇÃO:**

### A. INJEÇÕES E PROCESSAMENTO DE DADOS
- SQL/NoSQL injection em todas as queries
- XSS (refletido, armazenado, DOM-based)
- Command injection e code injection
- LDAP/XPath/XML injection
- XXE attacks em processamento XML
- Insecure deserialization
- Template injection (SSTI)

### B. AUTENTICAÇÃO E SESSÃO
- Vulnerabilidades em login, registro e recuperação de senha
- Força bruta e credential stuffing
- Gerenciamento de sessões (fixação, hijacking)
- Exposição de IDs de sessão
- Implementação de MFA
- Password policy weaknesses
- JWT/Token security flaws

### C. AUTORIZAÇÃO E CONTROLE DE ACESSO
- IDOR (Insecure Direct Object References)
- Privilege escalation (horizontal/vertical)
- Forceful browsing
- BOLA/BFLA em APIs
- CORS misconfigurations
- Directory traversal e LFI/RFI

### D. APIs E ENDPOINTS
- API security (OWASP API Top 10)
- Mass assignment vulnerabilities
- Rate limiting inadequado
- Excessive data exposure
- Endpoints não autenticados/fracamente autenticados
- GraphQL/REST/SOAP-specific issues

### E. CLIENT-SIDE SECURITY
- Clickjacking e UI redressing
- Client-side storage security
- CSP bypass possibilities
- WebSocket security
- PWA/Service Worker vulnerabilities

### F. REQUESTS E HEADERS
- CSRF em todas as formas de submissão
- SSRF em chamadas a serviços externos
- HTTP request/response smuggling
- Header injection (Host, Referer, etc.)
- Open redirects

### G. CONFIGURAÇÕES E INFRAESTRUTURA
- Security misconfigurations
- Verbose error messages
- Directory listing enabled
- Default credentials
- SSL/TLS misconfigurations
- Database security settings

### H. BUSINESS LOGIC
- Flaws in workflow/process logic
- Price manipulation vulnerabilities
- Negative quantity/amount attacks
- Time-based race conditions
- Automation vulnerabilities

### I. ARQUIVOS E UPLOADS
- File upload vulnerabilities
- Malicious file execution
- ZIP bombs/XML bombs
- Content spoofing

### J. DEPENDÊNCIAS E COMPONENTES
- Known vulnerabilities in libraries/frameworks
- Outdated dependencies
- Supply chain attacks surface

3. **FORMATO DE SAÍDA REQUERIDO:**

Para CADA vulnerabilidade encontrada ou potencial risco identificado, forneça:

```
[CATEGORIA] - [NOME DA VULNERABILIDADE]
Localização: [Arquivo(s) e linha(s) específicas]
Severidade: Crítica/Alta/Média/Baixa/Informativa
Descrição Técnica: [Explicação detalhada do problema]
Vetor de Ataque: [Como um atacante exploraria]
Impacto Potencial: [O que poderia acontecer se explorado]
Evidência no Código: [Trecho de código problemático]
Recomendações de Correção: [Sugestões específicas]
Teste de Validação: [Como testar se a correção funcionou]
```

4. **ANÁLISE PROATIVA:**
- Identifique padrões inseguros repetidos
- Verifique a consistência das práticas de segurança
- Analise a arquitetura por design flaws
- Sugira melhorias de segurança defensiva

5. **PRIORIZAÇÃO:**
- Classifique por risco real (CVSS se possível)
- Identifique quick wins vs. refatorações complexas
- Destaque vulnerabilidades que requerem atenção imediata

## EXIGÊNCIAS FINAIS:

1. Forneça um resumo executivo com:
   - Contagem total de vulnerabilidades por severidade
   - 5 riscos mais críticos
   - Recomendações estratégicas
2. Inclua um plano de remediação priorizado
3. Mantenha o foco técnico e específico — evite generalidades
4. Baseie as análises em: OWASP Top 10, SANS 25, CWE Top 25 e frameworks
   de segurança relevantes à tecnologia
5. NÃO ALTERE NADA NO CÓDIGO, APENAS ANALISE E INFORME sobre as
   vulnerabilidades encontradas, localizações exatas e recomendações
   específicas de correção (a aplicação das correções é dirigida à parte
   pelo responsável do projeto).

---
**INICIAR ANÁLISE COMPLETA DO CÓDIGO**
