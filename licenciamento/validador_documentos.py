# -*- coding: utf-8 -*-
"""
Validador de Documentos Comuns (análise documental do processo)
================================================================

Camada de análise que opera sobre os ARQUIVOS ANEXADOS ao processo
(PDF, Word, Excel, HTML, TXT) e alimenta o quadro resumo do frontend:

    1. EXTRAÇÃO DE TEXTO multi-formato (.pdf, .docx, .xlsx, .htm/.html, .txt);
    2. IDENTIFICAÇÃO do tipo de documento (matrícula, CNPJ, ART, PGRS,
       alvará do Corpo de Bombeiros, laudos de TR etc.);
    3. VALIDAÇÕES determinísticas por tipo - destaque para a MATRÍCULA DO
       IMÓVEL: a data de emissão fica no FINAL do documento (canto inferior
       esquerdo, fechamento do oficial de registro / certificação digital) e
       o prazo de validade é contado a partir dela (padrão: 90 dias, conforme
       instrução do licenciador; config/regras_documentos.json);
    4. QUADRO RESUMO: cruza o checklist da licença com os arquivos recebidos
       e as análises, classificando cada exigência como EM CONFORMIDADE /
       PENDENTE / NÃO APRESENTADO.
"""

from __future__ import annotations

import io
import logging
import re
import unicodedata
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Optional

from .calibracao import Calibracao
from .esquemas_tecnicos import OrigemAnalise, ResultadoValidacao, StatusValidacao

logger = logging.getLogger("licenciamento.validador_documentos")

# Palavras-chave por TIPO de documento (nome do arquivo + conteúdo)
PADROES_TIPO: dict[str, list[str]] = {
    "MATRICULA_IMOVEL": ["matricula do imovel", "matricula atualizada", "matricula n",
                         "matricula nº", "registro de imoveis", "serventia e registro",
                         "certidao de inteiro teor"],
    "CNPJ": ["comprovante de inscricao e de situacao cadastral", "cartao cnpj",
             "copia do cnpj", "comprovante cnpj", "cnpj"],
    "CONTRATO_SOCIAL": ["contrato social", "estatuto social", "ata de nomeacao"],
    "ART": ["anotacao de responsabilidade tecnica", "art nº", "art n", "rrt nº", "rrt n",
            "anotacao de responsabilidade"],
    "PGRS": ["plano de gerenciamento de residuos solidos", "pgrs"],
    "ALVARA_BOMBEIROS": ["alvara do corpo de bombeiros", "alvara de bombeiros",
                         "corpo de bombeiros militar", "cbmpa", "ppci"],
    "ALVARA_MUNICIPAL": ["alvara municipal", "alvara de funcionamento", "alvara de localizacao"],
    "RFO": ["reposicao florestal obrigatoria", "projeto de reposicao florestal", "rfo"],
    "PCA": ["plano de controle ambiental", "pca"],
    "PRAD": ["plano de recuperacao de area degradada", "prad", "recuperacao de area degradada"],
    "EIV": ["estudo de impacto de vizinhanca", "eiv"],
    "LCV": ["laudo de cobertura vegetal", "inventario florestal", "lcv"],
    "SONDAGEM": ["sondagem", "laudo geotecnico", "trincheira", "nivel do lencol"],
    "PROJETO_ARQUITETONICO": ["projeto arquitetonico", "projeto de construcao"],
    "RELATORIO_FOTOGRAFICO": ["relatorio tecnico-fotografico", "relatorio fotografico"],
    "CERTIDAO_ZONEAMENTO": ["certidao de zoneamento", "zoneamento urbano"],
}

# Extensões aceitas no upload (alinhadas à página inicial do frontend)
EXTENSOES_TEXTO = {".pdf", ".docx", ".xlsx", ".xls", ".htm", ".html", ".txt", ".rtf", ".csv"}

# Documentações às vezes chegam como IMAGEM (fotos/escaneamentos): aceitas no
# upload e analisadas por OCR quando o servidor possui tesseract; sem OCR, o
# documento segue para CONFERÊNCIA MANUAL com preview no painel.
EXTENSOES_IMAGEM = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}


def normalizar(texto: Optional[str]) -> str:
    """Minúsculas, sem acentos e com espaços colapsados (para casamento)."""
    if not texto:
        return ""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto).lower()


class ValidadorDocumentos:
    """Análise documental multi-formato para o quadro resumo e o parecer."""

    # ------------------------------------------------------------------
    def __init__(self, caminho_regras: Optional[str] = None):
        """Carrega as regras gerais (padrão: config/regras_documentos.json)."""
        config = Calibracao().regras_documentos
        if caminho_regras:
            caminho = Path(caminho_regras)
            config = None
            if caminho.exists():
                import json
                config = json.loads(caminho.read_text(encoding="utf-8"))
        params = (config or {}).get("parametros", {})
        # prazo de validade da matrícula em dias (instrução do licenciador: 90)
        self.matricula_validade_dias: int = int(params.get("matricula_validade_dias", 90))
        # quantos caracteres finais do texto guardam a data de emissão (canto inferior esquerdo)
        self.matricula_cauda_caracteres: int = int(params.get("matricula_cauda_caracteres", 900))
        self.fonte_regras: str = (config or {}).get(
            "fonte", "regras internas do código")
        self.regras_revisadas: bool = bool((config or {}).get("revisado", False))
        self.notas_regras: list[str] = list((config or {}).get("nota_revisao", []))

    # ==================================================================
    # 1) EXTRAÇÃO DE TEXTO MULTI-FORMATO
    # ==================================================================
    def extrair_texto(self, nome_arquivo: str, conteudo: bytes) -> str:
        """Extrai o texto do anexo conforme a extensão (nunca levanta exceção)."""
        try:
            extensao = Path(nome_arquivo).suffix.lower()
            if extensao == ".pdf":
                return self._texto_pdf(conteudo)
            if extensao in (".docx",):
                return self._texto_docx(conteudo)
            if extensao in (".xlsx", ".xls"):
                return self._texto_xlsx(conteudo)
            if extensao in (".htm", ".html"):
                return self._texto_html(conteudo)
            if extensao in EXTENSOES_IMAGEM:
                return self._texto_ocr(conteudo)
            if extensao in (".txt", ".rtf", ".csv"):
                return conteudo.decode("utf-8", errors="replace")
            return ""
        except Exception as exc:  # noqa: BLE001 - extração não pode derrubar a análise
            logger.error("Falha ao extrair texto de %s: %s", nome_arquivo, exc)
            return ""

    @staticmethod
    def _texto_pdf(conteudo: bytes) -> str:
        # LeitorPDF: texto nativo + OCR quando o PDF é escaneado (matrículas,
        # ARTs e laudos digitalizados)
        from licenciamento.leitor_pdf import LeitorPDF
        texto, _info = LeitorPDF.extrair(conteudo)
        return texto

    @staticmethod
    def _texto_docx(conteudo: bytes) -> str:
        from docx import Document
        doc = Document(io.BytesIO(conteudo))
        partes = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
        for tabela in doc.tables:
            for linha in tabela.rows:
                celulas = [c.text.strip() for c in linha.cells if c.text and c.text.strip()]
                if celulas:
                    partes.append(" | ".join(celulas))
        return "\n".join(partes)

    @staticmethod
    def _texto_xlsx(conteudo: bytes) -> str:
        import openpyxl
        planilha = openpyxl.load_workbook(io.BytesIO(conteudo), read_only=True,
                                          data_only=True)
        partes: list[str] = []
        for aba in planilha.worksheets:
            partes.append(f"# Aba: {aba.title}")
            for linha in aba.iter_rows(values_only=True):
                celulas = [str(c).strip() for c in linha if c is not None and str(c).strip()]
                if celulas:
                    partes.append(" | ".join(celulas))
        return "\n".join(partes)

    @staticmethod
    def _texto_html(conteudo: bytes) -> str:
        from bs4 import BeautifulSoup
        return BeautifulSoup(conteudo.decode("utf-8", errors="replace"),
                             "html.parser").get_text(" ", strip=True)

    @staticmethod
    def _texto_ocr(conteudo: bytes) -> str:
        """OCR de imagens (fotos/escaneamentos).
        
        Utiliza LeitorImagem com RapidOCR/ONNX em CPU (sem necessidade de binários de sistema)
        e pré-processamento via Pillow, com fallback gracioso para pytesseract se presente.
        Em caso de OCR indisponível, devolve string vazia para conferência manual.
        """
        try:
            from licenciamento.leitor_imagem import LeitorImagem
            texto, _ = LeitorImagem.extrair_texto(conteudo)
            return texto
        except Exception as exc:  # noqa: BLE001 - OCR indisponível não derruba a análise
            logger.warning("Falha ao extrair OCR de imagem: %s", exc)
            return ""

    # ==================================================================
    # 2) IDENTIFICAÇÃO DO TIPO DO DOCUMENTO
    # ==================================================================
    def identificar_tipo(self, nome_arquivo: str, texto: str) -> Optional[str]:
        """Inferir o tipo do documento por palavras-chave (nome + conteúdo).

        O NOME do arquivo tem peso maior (padrão de nomeação exigido pela
        SEMA: 'documentos devidamente nomeados de acordo com seu conteúdo').
        Antes de classificar, consulta o APRENDIZADO persistente (nomes que o
        conteúdo já confirmou em processos anteriores).
        """
        from licenciamento.identificador_documentos import tipo_aprendido
        aprendido = tipo_aprendido(nome_arquivo)
        if aprendido:
            return aprendido
        nome_n = normalizar(Path(nome_arquivo).stem.replace("_", " ").replace("-", " "))
        texto_n = normalizar(texto[:4000])
        melhor: tuple[int, Optional[str]] = (0, None)
        for tipo, chaves in PADROES_TIPO.items():
            pontos = 0
            for chave in chaves:
                chave_n = normalizar(chave)
                # palavras significativas da chave (ignora conectores curtos);
                # chave sem palavras longas (ex.: "rfo") cai no casamento literal
                palavras = [w for w in chave_n.split() if len(w) >= 4]
                if palavras:
                    no_nome = all(w in nome_n for w in palavras) or chave_n in nome_n
                    no_texto = all(w in texto_n for w in palavras) or chave_n in texto_n
                else:
                    no_nome = chave_n in nome_n
                    no_texto = chave_n in texto_n
                if no_nome:
                    pontos += 3          # nome do arquivo é o sinal mais forte
                elif no_texto:
                    pontos += 1
            if pontos > melhor[0]:
                melhor = (pontos, tipo)
        return melhor[1]

    # ==================================================================
    # 3) VALIDAÇÕES POR TIPO
    # ==================================================================
    # formatos de data aceitos na cauda do documento
    RE_DATA_LONGA = re.compile(
        r"(\d{1,2})\s*de\s*(janeiro|fevereiro|mar[cç]o|abril|maio|junho|julho|"
        r"agosto|setembro|outubro|novembro|dezembro)\s*de\s*(\d{4})", re.I)
    RE_DATA_CURTA = re.compile(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})")
    RE_DATA_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

    MESES = {"janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4,
             "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
             "outubro": 10, "novembro": 11, "dezembro": 12}

    @classmethod
    def extrair_data_emissao(cls, texto: str, cauda_caracteres: int = 900) -> Optional[date]:
        """Localiza a DATA DE EMISSÃO no final do documento.

        Em matrículas do Registro de Imóveis o fechamento (local, data e
        assinatura do oficial) e a certificação digital ficam no canto
        inferior esquerdo - na extração de texto, isso aparece na CAUDA.
        Retorna a ÚLTIMA data encontrada nessa região.
        """
        if not texto:
            return None
        cauda = texto[-int(cauda_caracteres):]
        candidatas: list[date] = []
        for dia, mes, ano in cls.RE_DATA_LONGA.findall(cauda):
            if mes.lower() in cls.MESES:
                try:
                    candidatas.append(date(int(ano), cls.MESES[mes.lower()], int(dia)))
                except ValueError:
                    pass
        for dia, mes, ano in cls.RE_DATA_CURTA.findall(cauda):
            try:
                candidatas.append(date(int(ano), int(mes), int(dia)))
            except ValueError:
                pass
        for ano, mes, dia in cls.RE_DATA_ISO.findall(cauda):
            try:
                candidatas.append(date(int(ano), int(mes), int(dia)))
            except ValueError:
                pass
        return candidatas[-1] if candidatas else None

    def validar_matricula(self, nome_arquivo: str, texto: str,
                          data_referencia: Optional[date] = None) -> ResultadoValidacao:
        """Verifica o prazo de validade da matrícula do imóvel.

        Regra (instrução do licenciador, 15/09/2026): a matrícula deve estar
        dentro do prazo de validade de 90 DIAS CORRIDOS a partir da data de
        emissão, que fica no final do documento (canto inferior esquerdo).
        """
        ref = data_referencia or date.today()
        norma = (f"Conferência documental - matrícula do imóvel "
                 f"(validade de {self.matricula_validade_dias} dias corridos)")
        data_emissao = self.extrair_data_emissao(texto, self.matricula_cauda_caracteres)
        metricas: dict[str, Any] = {
            "prazo_validade_dias": self.matricula_validade_dias,
            "data_referencia": ref.isoformat(),
        }
        if data_emissao is None:
            return ResultadoValidacao(
                documento_analisado=nome_arquivo, norma_tr=norma,
                status=StatusValidacao.REVISAO_MANUAL,
                itens_reprovados=[
                    "Data de emissão da matrícula NÃO localizada no final do documento "
                    "(canto inferior esquerdo). Conferir manualmente a data do fechamento "
                    "do oficial de registro e se a matrícula está dentro do prazo de "
                    f"{self.matricula_validade_dias} dias corridos."],
                trecho_referencia=texto[-200:].replace("\n", " ").strip(),
                metricas=metricas, origem=OrigemAnalise.DETERMINISTICO)
        dias = (ref - data_emissao).days
        metricas["data_emissao"] = data_emissao.isoformat()
        metricas["dias_desde_emissao"] = dias
        metricas["dias_restantes"] = self.matricula_validade_dias - dias
        if dias < 0:
            return ResultadoValidacao(
                documento_analisado=nome_arquivo, norma_tr=norma,
                status=StatusValidacao.REVISAO_MANUAL,
                itens_reprovados=[f"Data de emissão ({data_emissao:%d/%m/%Y}) posterior à "
                                  f"data de referência - conferir o documento."],
                metricas=metricas, origem=OrigemAnalise.DETERMINISTICO)
        if dias <= self.matricula_validade_dias:
            return ResultadoValidacao(
                documento_analisado=nome_arquivo, norma_tr=norma,
                status=StatusValidacao.CONFORME,
                itens_reprovados=[],
                metricas=metricas, origem=OrigemAnalise.DETERMINISTICO)
        return ResultadoValidacao(
            documento_analisado=nome_arquivo, norma_tr=norma,
            status=StatusValidacao.PENDENTE,
            itens_reprovados=[
                f"Matrícula VENCIDA: emitida em {data_emissao:%d/%m/%Y} "
                f"({dias} dias corridos atrás) e o prazo de validade é de "
                f"{self.matricula_validade_dias} dias corridos a contar da emissão. "
                "Apresentar matrícula atualizada (emissão há menos de "
                f"{self.matricula_validade_dias} dias)."],
            trecho_referencia=texto[-200:].replace("\n", " ").strip(),
            metricas=metricas, origem=OrigemAnalise.DETERMINISTICO)

    # ------------------------------------------------------------------
    def analisar_documento(self, nome_arquivo: str, texto: str,
                           data_referencia: Optional[date] = None) -> ResultadoValidacao:
        """Analisa um anexo: identificação + validação específica do tipo."""
        tipo = self.identificar_tipo(nome_arquivo, texto)
        eh_imagem = Path(nome_arquivo).suffix.lower() in EXTENSOES_IMAGEM
        if not texto or len(texto.strip()) < 30:
            if eh_imagem:
                return ResultadoValidacao(
                    documento_analisado=nome_arquivo,
                    norma_tr=f"Documento recebido como imagem "
                             f"({tipo or 'tipo não identificado'})",
                    status=StatusValidacao.REVISAO_MANUAL,
                    itens_reprovados=[
                        "Documento enviado como IMAGEM (png/jpg) sem texto "
                        "extraível pelo OCR (recurso indisponível neste servidor "
                        "ou foto ilegível). Conferir o conteúdo no preview do "
                        "painel e, se possível, anexar também em PDF - a SEMA "
                        "não analisa documentos fotografados."],
                    metricas={"imagem": True, "ocr_disponivel": False},
                    origem=OrigemAnalise.DETERMINISTICO)
            return ResultadoValidacao(
                documento_analisado=nome_arquivo,
                norma_tr=f"Documento recebido ({tipo or 'tipo não identificado'})",
                status=StatusValidacao.REVISAO_MANUAL,
                itens_reprovados=["Documento sem texto extraível (arquivo vazio, "
                                  "escaneado/imagem ou formato não suportado). "
                                  "Conferência manual necessária."],
                origem=OrigemAnalise.DETERMINISTICO)
        if tipo == "MATRICULA_IMOVEL":
            return self.validar_matricula(nome_arquivo, texto, data_referencia)
        if tipo is None:
            return ResultadoValidacao(
                documento_analisado=nome_arquivo,
                norma_tr="Documento recebido (tipo não identificado)",
                status=StatusValidacao.REVISAO_MANUAL,
                itens_reprovados=[],
                metricas={"observacao": "Documento recebido e legível; sem exigência "
                                        "do checklist correspondente - conferir se é "
                                        "complemento de outra exigência."},
                origem=OrigemAnalise.DETERMINISTICO)
        # tipos com análise leve: presença de elemento essencial
        return self._checagem_leve(nome_arquivo, tipo, texto)

    @classmethod
    def _checagem_leve(cls, nome_arquivo: str, tipo: str, texto: str) -> ResultadoValidacao:
        """Checagens simples por tipo (elemento essencial presente no texto)."""
        texto_n = normalizar(texto)
        checagens = {
            "CNPJ": [
                r"cnpj\s*[:nºo.]*\s*\d{2}[.]\d{3}[.]\d{3}[/]\d{4}-?\d{2}",
                r"\b\d{2}[.]\d{3}[.]\d{3}[/]\d{4}-?\d{2}\b",
                r"numero\s+de\s+inscricao\s*[:.\s]*\d{2}[.]\d{3}[.]\d{3}[/]\d{4}-?\d{2}",
                r"cadastro\s+nacional\s+da\s+pessoa\s+juridica",
                r"comprovante\s+de\s+inscricao\s+e\s+de\s+situacao\s+cadastral",
            ],
            "ART": [r"\b(art|rrt)\s*n?[°ºo]?\s*\.?\s*[\w/\-]{4,}"],
            "ALVARA_BOMBEIROS": [r"(alvar[aá]|ppci|protocolo)"],
            "CONTRATO_SOCIAL": [r"(contrato social|estatuto|sociedade|quota)"],
        }
        padroes = checagens.get(tipo)
        if padroes and not any(re.search(p, texto_n) for p in padroes):
            return ResultadoValidacao(
                documento_analisado=nome_arquivo, norma_tr=f"Documento ({tipo})",
                status=StatusValidacao.PENDENTE,
                itens_reprovados=[
                    f"Documento identificado como {tipo}, mas o elemento essencial "
                    "(número/identificação) não foi localizado no texto - conferir."],
                origem=OrigemAnalise.DETERMINISTICO)
        return ResultadoValidacao(
            documento_analisado=nome_arquivo, norma_tr=f"Documento ({tipo})",
            status=StatusValidacao.CONFORME,
            itens_reprovados=[],
            metricas={"observacao": "Documento identificado e legível."},
            origem=OrigemAnalise.DETERMINISTICO)

    # ==================================================================
    # 4) QUADRO RESUMO (checklist x arquivos x análises)
    # ==================================================================
    @staticmethod
    def _similaridade(a: str, b: str) -> float:
        return SequenceMatcher(None, normalizar(a), normalizar(b)).ratio()

    # conectivos ignorados na extração do NÚCLEO discriminante da exigência
    STOPWORDS_NUCLEO = {"para", "desta", "deste", "dessas", "desses", "segundo",
                        "conforme", "sobre", "pelo", "pela", "quando", "caso",
                        "ser", "deve", "apresentar", "copia"}

    SIGLAS_RELEVANTES = {"cnpj", "art", "rrt", "eiv", "pca", "rfo", "prad", "pgrs", "lcv", "lfs"}

    def _nucleo_discriminante(self, exigencia: str) -> list[str]:
        """Primeiras palavras SIGNIFICATIVAS do nome do documento exigido
        (ex.: 'Diretrizes Urbanísticas Departamento Planejamento') - o que
        REALMENTE identifica o documento, e não frases genéricas do meio
        ('elaborado de acordo com o TR... com ART de responsável técnico')."""
        ex_n = normalizar(re.sub(r"^\d+\s*[.)]\s*", "", exigencia))
        palavras = [p.strip("(),;:") for p in ex_n.split()]
        nucleo = [p for p in palavras
                  if (len(p) >= 4 or p in self.SIGLAS_RELEVANTES)
                  and p not in self.STOPWORDS_NUCLEO]
        return nucleo[:4]

    def casar_exigencia(self, exigencia: str, arquivos: list[dict],
                        limiar: float = 0.42) -> Optional[str]:
        """Encontra o ARQUIVO mais aderente a uma exigência do checklist.

        Dupla blindagem (corrige conformidades cruzadas):
          1. o FORMULÁRIO .htm/.html só atende à exigência do próprio
             formulário - nunca serve de 'documento apresentado' para as demais;
          2. o NÚCLEO discriminante da exigência (2 primeiras palavras do
             nome do documento) precisa estar no NOME ou no TEXTO INICIAL do
             arquivo - palavras genéricas do meio da frase ('elaborado',
             'responsável', 'habilitado') não cruzam documentos.
        Entre os aderentes, vence o melhor score (similaridade + keywords).
        """
        ex_n = normalizar(exigencia)
        ex_limpo = re.sub(r"^\d+\s*[.)]\s*", "", ex_n).strip()
        exigencia_de_formulario = "formulario" in ex_n
        nucleo = self._nucleo_discriminante(exigencia)
        # nomes de formulário variam demais para núcleo rígido (o guard
        # específico de .htm/.html já cuida dessa família de exigência)
        exige_nucleo = len(nucleo) >= 2 and not exigencia_de_formulario
        candidatos: list[tuple[float, str]] = []
        for arq in arquivos:
            nome = arq.get("nome", "")
            sufixo = Path(nome).suffix.lower()
            if sufixo in (".htm", ".html") and not exigencia_de_formulario:
                continue  # formulário não é 'documento apresentado'
            nome_n = normalizar(nome)
            nome_limpo = re.sub(r"^[_\-°º\d\s.]+", "", nome_n)
            texto_n = normalizar((arq.get("texto") or "")[:1200])
            if exige_nucleo:
                # 1ª palavra do núcleo OBRIGATÓRIA + ao menos mais uma das
                # seguintes (tolera nomes de arquivo resumidos, ex.:
                # 'matricula_imovel.jpg' para 'Cópia da matrícula atualizada')
                tem_primeira = (nucleo[0] in nome_n or nucleo[0] in nome_limpo
                                or nucleo[0] in texto_n)
                tem_apoio = any(p in nome_n or p in nome_limpo or p in texto_n
                                for p in nucleo[1:4])
                if not (tem_primeira and tem_apoio):
                    continue  # núcleo ausente: não é este documento
            palavras = [p for p in ex_limpo.split()
                        if (len(p) >= 4 or p in self.SIGLAS_RELEVANTES)
                        and p not in self.STOPWORDS_NUCLEO]
            pontos = max(self._similaridade(exigencia, nome),
                         self._similaridade(ex_limpo, nome_limpo))
            for palavra in palavras:
                if palavra in nome_limpo or palavra in nome_n:
                    pontos += 0.20
                elif palavra in texto_n:
                    pontos += 0.08
            candidatos.append((pontos, nome))
        if not candidatos:
            return None
        pontos, nome = max(candidatos, key=lambda par: par[0])
        return nome if pontos >= limiar else None

    def montar_quadro(self, exigencias: list[str], arquivos: list[dict],
                      analises: dict[str, ResultadoValidacao],
                      data_referencia: Optional[date] = None) -> tuple[list[dict], list[dict]]:
        """Monta o quadro resumo do processo.

        Args:
            exigencias: documentos exigidos pela licença (checklist deduplicado).
            arquivos: lista [{"nome", "texto", "tipo"}] dos anexos recebidos.
            analises: {nome_arquivo: ResultadoValidacao} das análises por documento.
            data_referencia: data base para prazos (default: hoje).

        Returns:
            (linhas_do_quadro, arquivos_sem_correspondencia)
            Cada linha: {documento, situacao, arquivo, pendencias}
        """
        usados: set[str] = set()
        linhas: list[dict] = []
        for exigencia in exigencias:
            arquivo = self.casar_exigencia(exigencia, arquivos)
            if arquivo is None:
                linhas.append({"documento": exigencia, "situacao": "NAO_APRESENTADO",
                               "arquivo": None, "pendencias":
                                   ["Documento NÃO apresentado no processo."]})
                continue
            usados.add(arquivo)
            analise = analises.get(arquivo)
            if analise and analise.status == StatusValidacao.CONFORME:
                linhas.append({"documento": exigencia, "situacao": "CONFORME",
                               "arquivo": arquivo, "pendencias": []})
            elif analise and analise.itens_reprovados:
                linhas.append({"documento": exigencia, "situacao": "PENDENTE",
                               "arquivo": arquivo,
                               "pendencias": list(analise.itens_reprovados)})
            else:
                linhas.append({"documento": exigencia, "situacao": "CONFORME",
                               "arquivo": arquivo,
                               "pendencias": []})
        extras = [a for a in arquivos if a.get("nome") not in usados]
        return linhas, extras

    # ------------------------------------------------------------------
    def resumo_quadro(self, linhas: list[dict]) -> dict[str, int]:
        """Contadores por situação para exibição no cabeçalho do quadro."""
        resumo = {"CONFORME": 0, "PENDENTE": 0, "NAO_APRESENTADO": 0}
        for linha in linhas:
            resumo[linha["situacao"]] = resumo.get(linha["situacao"], 0) + 1
        return resumo


# Alias curto para o rotulador de rotas no app
Validador = ValidadorDocumentos
