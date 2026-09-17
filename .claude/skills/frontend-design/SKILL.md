---
name: frontend-design
description: >-
  Create distinctive, production-grade frontend interfaces with high design
  quality. Use when the user asks to build web components, pages, or
  applications and the visual direction matters as much as the code quality.
version: 1.0.2
source: https://lobehub.com/skills/affaan-m-everything-claude-code-frontend-design/skill.md
author: affaan-m (github.com/affaan-m/everything-claude-code)
licenca: conteúdo público do marketplace; instalação LOCAL sem credenciais
  (confirmado pelo usuário em 2026-09-17 — sem registro externo)
instalado_em: 2026-09-17
---

# Frontend Design

Crie interfaces frontend distintas e de nível de produção, com qualidade de
design alta. Use quando o pedido envolver construir telas/componentes e a
direção visual importar tanto quanto a qualidade do código.

## Fluxo de trabalho: FRAME → SYSTEM → COMPOSE → MOTION

### 1) FRAME (enquadramento)
Antes de escrever qualquer CSS, defina e registre:
- **Propósito** da tela (o que o usuário precisa conseguir fazer em 10 s);
- **Audiência** (quem usa, com que frequência, em que estado de atenção);
- **Tom** (institucional? acolhedor? técnico?);
- **UMA ideia memorável** — um único elemento-assinatura que dá identidade à
  interface. NÃO são dez enfeites: é um.

### 2) SYSTEM (sistema visual)
Traduza a direção em **tokens** reais do projeto (variáveis CSS), nunca em
valores espalhados:
- Hierarquia tipográfica (escala de tamanhos definida; 2 pesos, no máximo 3);
- Variáveis de cor: fundo, superfície, borda, texto, texto-secundário, marca,
  e cores FUNCIONAIS de status (ok/atenção/erro/neutro) com contraste AA;
- Ritmo de espaçamento (base 8px) e raio de borda consistente (2 valores);
- Tratamento de superfícies (quando usar elevação/sombra e quando usar borda);
- Regras de movimento (o que transiciona, duração única ~0,15-0,2s ease).

### 3) COMPOSE (composição)
- Hierarquia intencional: a informação MAIS importante domina a tela;
- Agrupamento por proximidade; whitespace é estrutura, não sobra;
- Simetria quebrada de propósito quando ajuda a hierarquia (nunca ao acaso);
- Densidade adequada à audiência (uso diário e técnico => densa, mas alinhada);
- Estados vazios orientam (instrua o próximo passo, não apenas "nada aqui").

### 4) MOTION (movimento)
- Movimento SIGNIFICA algo: feedback de interação, continuidade, foco;
- Duração única curta; nada de entrada teatral; respeitar prefers-reduced-motion.

## ANTI-PADRÕES (proibido)
- "Cara de IA/SaaS genérico": gradientes decorativos, cards idênticos
  empilhados sem hierarquia, emojis como decoração (emoji só FUNCIONAL,
  ex.: semáforo de status);
- Cores sem variável/token; mais de uma fonte display; sombras pesadas;
- Textos de interface que descrevem o sistema em vez de orientar o usuário.

## QUALITY GATE (checar antes de entregar)
1. A ideia-assinatura está visível na primeira dobra?
2. Toda cor vem de token? Contraste texto/fundo AA (≥ 4.5:1)?
3. Os 3 níveis de título seguem a mesma escala?
4. Estados vazios e de erro orientam ação?
5. Testes/pontos funcionais da interface continuam passando?
6. Funciona nos dois temas (claro/escuro)?
