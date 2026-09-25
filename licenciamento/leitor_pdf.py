# -*- coding: utf-8 -*-
"""Leitura de PDFs com métodos calibrados do GabeBrain (extração por página, detecção
de texto corrompido, OCR com binarização adaptativa e mapa estrutural de documentos).
====================================================================================

Implementa as diretrizes das skills do GabeBrain:
1. `biblioteca-pesquisavel` (02_extrair_texto.py):
   - Extração página a página com marcadores [[pag N]];
   - Fatiamento e leitura seletiva por intervalo de páginas (extrair_paginas);
   - Localização precisa de evidências e páginas (localizar_pagina).
2. `07_ocr_escaneados.py`:
   - Detecção de camada de texto corrompida (mapeamento quebrado de fontes/mojibake);
   - Renderização a 300 DPI em tons de cinza (PyMuPDF);
   - Binarização adaptativa calibrada: limiar = max(1, int(img.mean()) - 90);
   - OCR via RapidOCR/ONNX em CPU (com singleton lazy).
3. `biblioteca-mapa-documento` (17_estrutura_documento.py):
   - Extração estrutural gratuita de sumário/ToC via PyMuPDF (doc.get_toc());
   - Validação da utilidade do outline (evita sumários fakes apontando para página 1);
   - Identificação de documentos extensos (> 50k tokens) e seções de alta densidade técnica.
"""
from __future__ import annotations

import io
import logging
import re
from typing import Any, Optional

logger = logging.getLogger("licenciamento.leitor_pdf")


class LeitorPDF:
    """Extração de texto de PDF com OCR adaptativo e análise estrutural do GabeBrain."""

    # Abaixo disso por página considera-se "sem camada de texto" (escaneado)
    MINIMO_CHARS_POR_PAGINA = 60
    # Limite padrão de páginas para OCR em documentos comuns
    MAX_PAGINAS_OCR = 25
    # Renderização OCR em 300 DPI (conforme receita GabeBrain 07)
    DPI_OCR = 300
    AJUSTE_LIMIAR_BINARIZACAO = 90
    ZOOM_OCR_FALLBACK = 4.0
    LIMIAR_TOKENS_EXTENSO = 50000

    TERMOS_QUENTES_AMBIENTAIS = [
        "sondagem", "lencol freatico", "fauna", "vegetacao", "cobertura vegetal",
        "residuos", "ruido", "vibracao", "mitigacao", "monitoramento", "prad",
        "pca", "rfo", "eiv", "conclusao", "geotecnic", "permeabilidade", "app",
        "esgotamento", "drenagem", "hidrologi", "infiltracao"
    ]

    _ocr_instancia = None  # singleton lazy (modelos pesados)

    @classmethod
    def ocr_disponivel(cls) -> bool:
        """True se a pilha de OCR (rapidocr/onnx) pôde ser carregada."""
        return cls._obter_ocr() is not None

    @classmethod
    def _obter_ocr(cls):
        if cls._ocr_instancia is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
                cls._ocr_instancia = RapidOCR()
            except Exception as exc:  # noqa: BLE001
                logger.info("OCR indisponível (rapidocr/onnxruntime): %s", exc)
                cls._ocr_instancia = False
        return cls._ocr_instancia or None

    @staticmethod
    def _texto_corrompido(texto: str) -> bool:
        """Detecta camada de texto com fontes quebradas/mojibake (GabeBrain 07).

        PDFs gerados com mapeamento incorreto de glifos produzem caracteres sem
        significado (códigos nulos, caracteres de substituição, proporção quase nula
        de caracteres alfanuméricos). Mesmo com comprimento > 60 chars, o texto
        é ilegível e exige OCR forçado.
        """
        if not texto:
            return False
        # Remove marcadores de página antes de analisar a integridade do texto
        texto_limpo = re.sub(r"\[\[pag\s+\d+\]\]", "", texto).strip()
        if len(texto_limpo) < 30:
            return False

        # 1. Caracteres nulos ou de substituição Unicode (\ufffd)
        quebrados = texto_limpo.count("\x00") + texto_limpo.count("\ufffd")
        if (quebrados / len(texto_limpo)) > 0.08:
            return True

        # 2. Proporção anormalmente baixa de caracteres alfanuméricos
        alfanum = sum(1 for c in texto_limpo if c.isalnum())
        if len(texto_limpo) >= 60 and (alfanum / len(texto_limpo)) < 0.25:
            return True

        return False

    @classmethod
    def extrair(cls, conteudo: bytes) -> tuple[str, dict]:
        """Extrai texto do PDF com marcadores de página [[pag N]] e fallback de OCR.

        Retorna (texto, info).
        info: {metodo: texto_nativo|ocr, paginas: int, aviso: str|None, tokens_estimados: int}
        """
        info: dict[str, Any] = {
            "metodo": "texto_nativo",
            "paginas": 0,
            "aviso": None,
            "tokens_estimados": 0,
            "documento_extenso": False,
        }
        if not conteudo:
            return "", info

        nativo, paginas = cls._texto_nativo(conteudo)
        info["paginas"] = paginas
        limite_minimo = cls.MINIMO_CHARS_POR_PAGINA * max(min(paginas, cls.MAX_PAGINAS_OCR), 1)

        texto_limpo = re.sub(r"\[\[pag\s+\d+\]\]", "", nativo).strip()
        corrompido = cls._texto_corrompido(nativo)

        # Dispara OCR se o texto for escasso OU se a camada de texto estiver corrompida
        if (paginas and len(texto_limpo) < limite_minimo) or corrompido:
            texto_ocr, aviso = cls._ocr_paginas(conteudo)
            if texto_ocr.strip():
                info["metodo"] = "ocr"
                info["motivo_ocr"] = "corrompido" if corrompido else "pouco_texto"
                info["tokens_estimados"] = int(len(texto_ocr) / 4)
                info["documento_extenso"] = info["tokens_estimados"] > cls.LIMIAR_TOKENS_EXTENSO
                logger.info("PDF lido via OCR GabeBrain (%s páginas, motivo: %s)",
                            min(paginas, cls.MAX_PAGINAS_OCR), info["motivo_ocr"])
                return texto_ocr, info
            if aviso:
                info["aviso"] = aviso

        info["tokens_estimados"] = int(len(nativo) / 4)
        info["documento_extenso"] = info["tokens_estimados"] > cls.LIMIAR_TOKENS_EXTENSO
        return nativo, info

    @classmethod
    def _texto_nativo(cls, conteudo: bytes) -> tuple[str, int]:
        """Extrai texto nativo página a página com marcadores [[pag N]] (GabeBrain 02)."""
        # 1. PyMuPDF (método preferencial GabeBrain)
        try:
            import pymupdf
            doc = pymupdf.open(stream=conteudo, filetype="pdf")
            total = doc.page_count
            partes = []
            for n in range(total):
                txt = doc.load_page(n).get_text()
                if txt.strip():
                    partes.append(f"\n[[pag {n + 1}]]\n{txt}")
            doc.close()
            return "".join(partes).strip(), total
        except Exception as exc:  # noqa: BLE001
            logger.debug("PyMuPDF não disponível ou falhou para texto nativo: %s", exc)

        # 2. Fallback para pypdf
        try:
            from pypdf import PdfReader
            leitor = PdfReader(io.BytesIO(conteudo))
            total = len(leitor.pages)
            partes = []
            for n, p in enumerate(leitor.pages):
                txt = p.extract_text() or ""
                if txt.strip():
                    partes.append(f"\n[[pag {n + 1}]]\n{txt}")
            return "".join(partes).strip(), total
        except Exception as exc:  # noqa: BLE001
            logger.warning("Falha no texto nativo do PDF via pypdf: %s", exc)
            return "", 0

    @classmethod
    def _ocr_paginas(cls, conteudo: bytes) -> tuple[str, str | None]:
        """Executa OCR usando a receita calibrada do GabeBrain (07_ocr_escaneados.py)."""
        ocr = cls._obter_ocr()
        if ocr is None:
            return "", ("PDF sem camada de texto legível e OCR indisponível - "
                        "instale 'rapidocr-onnxruntime' para ler documentos escaneados")
        try:
            import pymupdf
            doc = pymupdf.open(stream=conteudo, filetype="pdf")
            partes: list[str] = []

            # Tenta binarização adaptativa via numpy e Pillow
            usar_binarizacao = False
            try:
                import numpy as np
                from PIL import Image
                usar_binarizacao = True
            except ImportError:
                pass

            for n in range(min(doc.page_count, cls.MAX_PAGINAS_OCR)):
                pagina = doc.load_page(n)
                if usar_binarizacao:
                    # Receita GabeBrain: 300 DPI, tons de cinza, limiar = max(1, media - 90)
                    pix = pagina.get_pixmap(dpi=cls.DPI_OCR, colorspace=pymupdf.csGRAY)
                    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
                    limiar = max(1, int(img.mean()) - cls.AJUSTE_LIMIAR_BINARIZACAO)
                    binaria = ((img > limiar) * 255).astype(np.uint8)

                    buf = io.BytesIO()
                    Image.fromarray(binaria).save(buf, format="PNG")
                    img_bytes = buf.getvalue()
                else:
                    pix = pagina.get_pixmap(matrix=pymupdf.Matrix(cls.ZOOM_OCR_FALLBACK, cls.ZOOM_OCR_FALLBACK))
                    img_bytes = pix.tobytes("png")

                resultado, _ = ocr(img_bytes)
                if resultado:
                    texto_pag = " ".join(linha[1] for linha in resultado if len(linha) > 1 and linha[1])
                    if texto_pag.strip():
                        partes.append(f"\n[[pag {n + 1}]]\n{texto_pag}")

            doc.close()
            return "\n".join(partes).strip(), None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Falha no OCR do PDF: %s", exc)
            return "", f"Falha no OCR do PDF: {exc}"

    # =========================================================================
    # UTILITÁRIOS GABEBRAIN: Fatiamento, localização e limpeza de páginas
    # =========================================================================
    @staticmethod
    def extrair_paginas(texto: str, inicio: int, fim: int) -> str:
        """Lê cirurgicamente o intervalo de páginas solicitado (GabeBrain biblioteca.py ler COTA X-Y)."""
        if not texto:
            return ""
        padrao = re.compile(r"\[\[pag\s+(\d+)\]\]")
        blocos = padrao.split(texto)
        if len(blocos) <= 1:
            return texto

        selecionados: list[str] = []
        if blocos[0].strip() and inicio == 1:
            selecionados.append(blocos[0].strip())

        for i in range(1, len(blocos), 2):
            num_pag = int(blocos[i])
            conteudo_pag = blocos[i + 1] if i + 1 < len(blocos) else ""
            if inicio <= num_pag <= fim:
                selecionados.append(f"[[pag {num_pag}]]\n{conteudo_pag.strip()}")

        return "\n\n".join(selecionados).strip()

    @staticmethod
    def localizar_pagina(texto: str, alvo: str | int) -> Optional[int]:
        """Localiza em qual página [[pag N]] está determinado trecho ou offset."""
        if not texto:
            return None

        if isinstance(alvo, int):
            posicao = alvo
        else:
            if not alvo:
                return None
            posicao = texto.find(alvo)
            if posicao < 0:
                posicao = texto.lower().find(alvo.lower())
                if posicao < 0:
                    return None

        trecho_anterior = texto[:posicao]
        matches = list(re.finditer(r"\[\[pag\s+(\d+)\]\]", trecho_anterior))
        if matches:
            return int(matches[-1].group(1))

        if re.search(r"\[\[pag\s+(\d+)\]\]", texto):
            return 1
        return None

    @staticmethod
    def remover_marcadores_pagina(texto: str) -> str:
        """Remove os marcadores [[pag N]] para texto plano puro."""
        if not texto:
            return ""
        return re.sub(r"\[\[pag\s+\d+\]\]\n?", "", texto).strip()

    # =========================================================================
    # MAPA ESTRUTURAL GRATUITO DE DOCUMENTOS (GabeBrain 17_estrutura_documento.py)
    # =========================================================================
    @staticmethod
    def outline_util(toc: list, total_paginas: int) -> tuple[bool, str]:
        """Valida se o sumário embutido no PDF é consistente e utilizável."""
        if len(toc) < 3:
            return False, f"só {len(toc)} itens"
        pags = [p for _, _, p in toc]
        distintas = set(pags)
        if len(distintas) < 3:
            return False, f"só {len(distintas)} páginas distintas"
        concentracao = max(pags.count(p) for p in distintas)
        if concentracao > 0.3 * len(toc):
            return False, f"{round(100.0 * concentracao / len(toc))}% dos itens caem na mesma página"
        if total_paginas:
            alcance = (max(pags) - min(pags)) / float(total_paginas)
            if alcance < 0.3:
                return False, f"cobre só {round(100 * alcance)}% do documento"
        return True, ""

    @classmethod
    def extrair_estrutura(cls, conteudo: bytes) -> dict[str, Any]:
        """Extrai o esqueleto estrutural (ToC) sem consumo de tokens de LLM."""
        estrutura: dict[str, Any] = {
            "rota": "sem_estrutura",
            "total_paginas": 0,
            "tokens_estimados": 0,
            "documento_extenso": False,
            "secoes": [],
            "motivo": None
        }
        try:
            import pymupdf
            doc = pymupdf.open(stream=conteudo, filetype="pdf")
            total_paginas = doc.page_count
            estrutura["total_paginas"] = total_paginas

            raw_toc = doc.get_toc()
            toc = [(n, t.strip(), p) for n, t, p in raw_toc if n <= 2 and t.strip() and p >= 1]

            serve, motivo = cls.outline_util(toc, total_paginas)
            if serve:
                estrutura["rota"] = "sumario_embutido"
                secoes = []
                for i, (nivel, titulo, inicio) in enumerate(toc):
                    fim = toc[i + 1][2] - 1 if i + 1 < len(toc) else total_paginas
                    if fim < inicio:
                        fim = inicio
                    titulo_norm = titulo.lower()
                    quente = any(t in titulo_norm for t in cls.TERMOS_QUENTES_AMBIENTAIS)
                    secoes.append({
                        "nivel": nivel,
                        "titulo": titulo,
                        "inicio": inicio,
                        "fim": fim,
                        "quente": quente
                    })
                estrutura["secoes"] = secoes
            else:
                estrutura["motivo"] = motivo
                # Rota 2: fatia fixa se o documento for volumoso (> 20 páginas)
                if total_paginas > 20:
                    estrutura["rota"] = "fatia_fixa"
                    secoes = []
                    por_fatia = 25
                    for p in range(1, total_paginas + 1, por_fatia):
                        fim = min(p + por_fatia - 1, total_paginas)
                        secoes.append({
                            "nivel": 1,
                            "titulo": f"Páginas {p}-{fim}",
                            "inicio": p,
                            "fim": fim,
                            "quente": False
                        })
                    estrutura["secoes"] = secoes

            texto_amostra = "".join(doc.load_page(n).get_text() for n in range(min(total_paginas, 10)))
            if total_paginas > 0 and texto_amostra:
                tokens_estimados = int((len(texto_amostra) / min(total_paginas, 10) * total_paginas) / 4)
                estrutura["tokens_estimados"] = tokens_estimados
                estrutura["documento_extenso"] = tokens_estimados > cls.LIMIAR_TOKENS_EXTENSO

            doc.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Falha ao extrair estrutura do PDF: %s", exc)
            estrutura["motivo"] = str(exc)

        return estrutura
