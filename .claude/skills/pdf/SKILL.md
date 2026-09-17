---
name: pdf
description: >-
  Toolkit programável para PDFs: criar (reportlab), ler/extrair texto e
  tabelas (pdfplumber/pypdf), dividir/juntar, formulários, OCR. Use quando o
  pedido envolver manipular ou GERAR PDFs.
version: 1.0.5
source: https://lobehub.com/skills/anthropics-skills-pdf/skill.md
author: anthropics (github.com/anthropics/skills)
licenca: conteúdo público do marketplace; instalação LOCAL sem credenciais
  (padrão confirmado pelo usuário em 2026-09-17)
instalado_em: 2026-09-17
---

# PDF

Use sempre que a tarefa envolver manipulação ou CRIAÇÃO de PDFs.

## Bibliotecas
- **reportlab** — CRIAR PDFs (canvas de baixo nível; controle total de layout);
- **pypdf** — ler/mesclar/dividir/rotacionar, metadados, validar PDFs gerados;
- **pdfplumber** — extrair TEXTO e TABELAS com leiaute (superior ao pypdf nisso).

## Quick start
```python
# CRIAR um PDF simples com reportlab
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
import io

buffer = io.BytesIO()
doc = SimpleDocTemplate(buffer, pagesize=A4,
                        topMargin=46, bottomMargin=46,
                        leftMargin=56, rightMargin=56)
estilos = getSampleStyleSheet()
fluxo = [Paragraph("Título", estilos["Title"]),
         Spacer(1, 8),
         Paragraph("Parágrafo de texto...", estilos["BodyText"])]
doc.build(fluxo)
pdf_bytes = buffer.getvalue()

# VALIDAR/reler o PDF gerado (pypdf)
from pypdf import PdfReader
leitor = PdfReader(io.BytesIO(pdf_bytes))
texto = "\n".join(p.extract_text() or "" for p in leitor.pages)

# EXTRAIR texto/tabelas preservando leiaute (pdfplumber)
# import pdfplumber
# with pdfplumber.open("arquivo.pdf") as pdf:
#     texto = "\n".join((p.extract_text() or "") for p in pdf.pages)
```

## Regras do projeto
- Gerar SEMPRE em memória (BytesIO) e devolver bytes para download;
- Validar o PDF gerado com pypdf antes de entregar (abre? contém o texto-chave?);
- Texto em pt-BR: manter acentuação (fontes padrão Helvetica/Times suportam
  Latin-1; para símbolos fora, registrar TTF).
