---
name: document-image-analysis
description: >-
  Toolkit e metodologia para análise, extração e verificação de documentos
  técnicos em texto nativo e imagens (PNG, JPG, JPEG, TIFF, WebP e PDF escaneado).
  Inclui pré-processamento de imagens (contraste, escala de cinza, rotação),
  OCR via RapidOCR/ONNX em CPU (sem dependência de binários externos do sistema),
  extração de tokens estruturados (CNPJ, CPF, ART, Inscrição Imobiliária, Matrícula)
  e conferência com dupla checagem e consolidação de etapas.
version: 1.0.0
source: marketplace/awesome-agent-skills/document-image-analysis
author: sema-licenciamento (github.com/gabrielhklaser/licenciamentoambiental)
licenca: Apache-2.0 / Conteúdo do projeto
instalado_em: 2026-09-25
---

# Document & Image Analysis (Análise de Documentos e Imagens)

Use sempre que a tarefa envolver inspeção de documentos anexados, extração de texto de imagens escaneadas (PNG, JPG, TIFF, WebP), OCR em PDFs digitalizados ou verificação de conformidade de documentos técnicos e administrativos no licenciamento ambiental.

## Capacidades & Pilha Tecnológica

1. **Extração de Imagens e Documentos Digitalizados:**
   - **RapidOCR / ONNX Runtime:** OCR executado diretamente em CPU, sem necessidade de binário `tesseract` instalado no sistema operacional.
   - **Pillow (PIL):** Pré-processamento de imagem:
     - Conversão de canais (RGBA/Paleta para RGB).
     - Correção de orientação (EXIF auto-orient).
     - Equalização de contraste e nitidez para melhorar reconhecimento de caracteres em formulários e carimbos.
   - **PyMuPDF / Fitz:** Renderização de páginas de PDFs escaneados para mapas de bits com zoom calibrado (~288 DPI) para posterior OCR.

2. **Dupla Checagem em Camadas (Multi-Stage Verification):**
   - **Camada 1 (Determinística):** Identificação de padrões estruturados (CNPJ com regex `xx.xxx.xxx/xxxx-xx`, número de ART/RRT, datas de emissão de matrículas, coordenadas geográficas SIRGAS 2000).
   - **Camada 2 (Heurística & Contextual):** Cruzamento de dados entre documentos anexados e o formulário do requerimento (ex: responsável técnico da ART x seção 4.3/8; área do projeto urbanístico x área declarada).
   - **Camada 3 (Consolidação):** Unificação dos achados das etapas administrativa, financeira e técnica em relatório conciso, sem duplicação de alertas.

## Padrões de Implementação

```python
from PIL import Image, ImageEnhance, ImageOps
import io
from rapidocr_onnxruntime import RapidOCR

ocr = RapidOCR()

def extrair_texto_imagem(conteudo_bytes: bytes) -> str:
    # 1. Carrega imagem e normaliza orientação e canais
    img = Image.open(io.BytesIO(conteudo_bytes))
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    
    # 2. Otimização de contraste se necessário
    enhancer = ImageEnhance.Contrast(img)
    img_otimizada = enhancer.enhance(1.2)
    
    # 3. Buffer PNG para OCR
    buffer = io.BytesIO()
    img_otimizada.save(buffer, format="PNG")
    
    # 4. OCR via RapidOCR
    resultado, _ = ocr(buffer.getvalue())
    if not resultado:
        return ""
    return "\n".join(linha[1] for linha in resultado)
```

## Regras de Integração
- **Resiliência:** Falha de OCR ou ausência de camada de texto nunca interrompe a execução do pipeline; o documento é encaminhado para conferência visual com prévia preservada.
- **Transparência:** Indicar na auditoria se o texto foi obtido por camada nativa ou OCR.
- **Deduplicação:** Achados redundantes entre checagem primária e secundária são deduplicados na interface e no parecer.
