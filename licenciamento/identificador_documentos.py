# -*- coding: utf-8 -*-
"""
Reconhecimento de documentos anexados com APRENDIZADO persistente
=================================================================
Pipeline definido pelo licenciador (instrução de 16/09/2026):

  1) reconhecer pelo NOME do arquivo — aceitando nomes alterados
     (ex.: '°Cópia da matrícula atualizada');
  2) se o nome não bastar, ABRIR o documento e identificar pelo CONTEÚDO
     (assinaturas textuais internas);
  3) quando o conteúdo confirmar o tipo e o nome não tiver casado direto,
     APRENDER a associação nome -> tipo para os próximos processos
     (persistido em config/aprendido_documentos.json).

O aprendizado é consultado ANTES do casamento por nome, então um nome
aprendido passa a ser reconhecido imediatamente nos próximos uploads.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Optional

# Tipos e palavras-chave do NOME (importadas do validador para não duplicar;
# entradas extras cobrem documentos comuns do checklist de Campo Bom)
from licenciamento.validador_documentos import PADROES_TIPO

# =============================================================================
# CONSELHOS PROFISSIONAIS E REGISTROS DE RESPONSABILIDADE TÉCNICA
# -----------------------------------------------------------------------------
# Regra do licenciador (instrução de 26/09/2026): RTT/RRT pertencem SEMPRE ao
# CAU/BR (Conselho de Arquitetura e Urbanismo) e a Arquitetos e Urbanistas.
# A ART (CREA/CRBio) é o registro dos engenheiros, geólogos e biólogos. Os
# dois registros NUNCA se confundem: um arquivo RTT não é ART e vice-versa.
# =============================================================================
TIPO_RRT = "RRT"       # Registro de Responsabilidade Técnica  -> CAU/BR
TIPO_ART = "ART"       # Anotação de Responsabilidade Técnica  -> CREA / CRBio

RE_CAU = re.compile(r"\bcau\b|caubr|cau\s*/\s*br|conselho de arquitetura", re.I)
RE_CREA = re.compile(r"\bcrea\b|confea|conselho regional de engenharia", re.I)
RE_CRBIO = re.compile(r"crbio|crbi\b|conselho regional de biologia", re.I)
# nº de registro no CAU/BR: 'A67154-1', 'A 12345-9' (prefixo 'A' + dígitos)
RE_REGISTRO_CAU = re.compile(r"^\s*A[\s\-]?\d{4,8}(?:[\-/]\d{1,2})?\s*$", re.I)
# nº de registro no CREA: 'RS233891', 'RS 123.456' (UF + dígitos)
RE_REGISTRO_CREA = re.compile(r"^\s*[A-Z]{2}[\s\-]?\d{3,8}(?:[\-/]\d{1,3})?\s*$")
# nº de registro no CRBio: '110544/03-D'
RE_REGISTRO_CRBIO = re.compile(r"^\s*\d{5,7}\s*/\s*\d{2}\s*-\s*[A-Za-z]\s*$")


def normalizar_conselho(texto: Optional[str]) -> Optional[str]:
    """Acento/caixa neutros para o nome do conselho."""
    if not texto:
        return None
    return re.sub(r"\s+", " ", texto).strip().upper()


def conselho_do_texto(texto: Optional[str]) -> Optional[str]:
    """Conselho profissional citado no texto do documento.

    Retorna 'CAU/BR', 'CREA', 'CRBio' ou None. A ordem importa: o CAU é
    testado primeiro porque um RRT de arquiteto costuma citar 'CAU/BR' e o
    termo 'conselho' genérico.
    """
    if not texto:
        return None
    if RE_CAU.search(texto):
        return "CAU/BR"
    if RE_CRBIO.search(texto):
        return "CRBio"
    if RE_CREA.search(texto):
        return "CREA"
    return None


def conselho_do_registro(registro: Optional[str]) -> Optional[str]:
    """Consselho inferido do NÚMERO de registro profissional declarado.

    'A67154-1' -> CAU/BR (arquiteto e urbanista); 'RS233891' -> CREA;
    '110544/03-D' -> CRBio. Devolve None quando o formato não é reconhecível
    (melhor não afirmar do que afirmar errado).
    """
    if not registro:
        return None
    reg = str(registro).strip()
    if not reg:
        return None
    if RE_REGISTRO_CAU.match(reg):
        return "CAU/BR"
    if RE_REGISTRO_CRBIO.match(reg):
        return "CRBio"
    if RE_REGISTRO_CREA.match(reg):
        return "CREA"
    # formato livre ('CREA RS 233891', 'CAU A67154-1', 'Registro CAU: A67154-1')
    combinado = f"{reg}"
    if re.search(r"\bcau\b", combinado, re.I):
        return "CAU/BR"
    if re.search(r"\bcrbio\b|\bcrbi\b", combinado, re.I):
        return "CRBio"
    if re.search(r"\bcrea\b|\bconfea\b", combinado, re.I):
        return "CREA"
    return None


def tipo_registro_por_conselho(conselho: Optional[str]) -> Optional[str]:
    """'CAU/BR' -> RRT; 'CREA'/'CRBio' -> ART; None -> indefinido."""
    cons = normalizar_conselho(conselho)
    if cons == "CAU/BR":
        return TIPO_RRT
    if cons in ("CREA", "CRBIO"):
        return TIPO_ART
    return None

PADROES_TIPO_EXTRA: dict[str, list[str]] = {
    "CNPJ": ["cnpj", "cartao cnpj", "comprovante cnpj", "situacao cadastral"],
    "FAUNA": ["inventario de fauna", "laudo de fauna", "fauna silvestre", "fauna", "lfs"],
    "LCV": ["laudo de cobertura vegetal", "cobertura vegetal", "inventario florestal", "lcv"],
    "SONDAGEM": ["laudo geologico", "laudo geotecnico", "sondagem", "ensaios de infiltracao", "infiltracao"],
    "DIRETRIZES_URBANISTICAS": ["diretrizes urbanisticas", "departamento de planejamento"],
    "CERTIDAO_ZONEAMENTO": ["certidao zoneamento", "zoneamento afirmando", "zoneamento loteavel", "zoneamento"],
    "VIABILIDADE_RESIDUOS": ["coleta de residuos", "limpeza urbana", "viabilidade de coleta"],
    "VIABILIDADE_AGUA": ["abastecimento de agua", "viabilidade de agua", "corsan agua"],
    "VIABILIDADE_ESGOTO": ["esgotamento sanitario", "viabilidade de esgotamento", "corsan esgoto"],
    "VIABILIDADE_ENERGIA": ["energia eletrica", "viabilidade de energia", "rge"],
    "DECLARACAO_ALAGAMENTO": ["alagamento", "inundacao", "ocorrencia de alagamento"],
    "PROJETO_URBANISTICO": ["projeto urbanistico", "planta das vias de acesso", "projeto arquitetonico"],
    "RELATORIO_FOTOGRAFICO": ["relatorio fotografico", "relatorio tecnico fotografico"],
    "PLANTA_LOCALIZACAO": ["planta de localizacao", "planta de situacao",
                           "plano de localizacao", "planta localizacao", "croqui da area"],
    "LICENCA_PREVIA": ["licenca previa", "copia da licenca previa"],
    "LICENCA_INSTALACAO": ["licenca de instalacao", "copia da licenca de instalacao"],
    "LICENCA_OPERACAO": ["licenca de operacao", "copia da licenca de operacao"],
    "RCA": ["relatorio de controle ambiental", "rca"],
    "PROJETO_EXECUTIVO": ["projeto executivo", "projeto tecnico", "projeto aprovado"],
    "FORMULARIO_ENQUADRAMENTO": ["formulario de enquadramento", "formulario assinado",
                                 "formulario enquadramento"],
    # RTT/RRT: registro do CAU/BR para Arquitetos e Urbanistas. Pode haver
    # VÁRIAS RTTs no processo (projeto urbanístico, execução de obras...),
    # então o nome costuma trazer a etapa - o conselho é o que define o tipo.
    "RRT": ["rrt", "rtt", "registro de responsabilidade tecnica", "cau br", "cau/br",
            "conselho de arquitetura", "arquiteto", "urbanista", "caubr"],
    "CAMADA_GIS": ["kmz", "kml", "shapefile", "shp", "geojson", "gpkg", "curvas de nivel",
                   "mapa de app", "camada gis", "projecao utm", "sirgas"],
}
TODOS_PADROES: dict[str, list[str]] = {**PADROES_TIPO, **PADROES_TIPO_EXTRA}

# Assinaturas do CONTEÚDO (regex por tipo; >= 2 assinaturas confirmam)
ASSINATURAS_CONTEUDO: dict[str, list[str]] = {
    "MATRICULA_IMOVEL": [r"matr[ií]cula\s*n[ºo°.]?", r"registro\s+de\s+im[óo]veis",
                         r"serventia\s+e\s+registro", r"certid[ãa]o\s+de\s+inteiro\s+teor",
                         r"matr[íi]cula\s+do\s+im[óo]vel"],
    "CNPJ": [r"\bcnpj\b", r"receita\s+federal", r"comprovante\s+de\s+inscri[çc][ãa]o",
             r"situa[çc][ãa]o\s+cadastral", r"cart[ãa]o\s+cnpj", r"numero\s+de\s+inscricao"],
    "CONTRATO_SOCIAL": [r"contrato\s+social", r"estatuto\s+social", r"ata\s+de\s+nomea[çc][ãa]o",
                        r"junta\s+comercial"],
    "RRT": [r"\b(?:rrt|rtt)\b",
            r"registro\s+de\s+responsabilidade\s+t[ée]cnica",
            r"conselho\s+de\s+arquitetura\s+e\s+urbanismo|caubr\.gov\.br|\bcau\s*/\s*br\b|\bcau\b",
            r"arquitet[oa]\s*(?:\(?a?\)?)?\s*e\s+urbanista",
            r"\bA\d{4,8}(?:-\d)?\b"],
    "ART": [r"\bart\s*n?[ºo°.]?\s*[:\-]?\s*[\d./\-]{4,}",
            r"anota[çc][ãa]o\s+de\s+responsabilidade\s+t[ée]cnica",
            r"conselho\s+regional\s+de\s+engenharia|crea|crbio|conselho\s+regional\s+de\s+biologia"],
    "CAMADA_GIS": [r"<\s*kml\b|</\s*kml\s*>", r"<\s*Placemark\b", r"<\s*Polygon\b",
                   r"<\s*LineString\b", r"<\s*GroundOverlay\b", r"\bgeojson\b",
                   r'"type"\s*:\s*"(?:Feature|FeatureCollection|Polygon|LineString|Point)"'],
    "PGRS": [r"plano\s+de\s+gerenciamento", r"res[íi]duos?\s+s[óo]lidos",
             r"\bpgrs\b"],
    "ALVARA_BOMBEIROS": [r"corpo\s+de\s+bombeiros", r"alvar[áa]\s+(do\s+)?(corpo\s+de\s+)?bombeiros",
                         r"\bppci\b", r"\bcbmpa\b"],
    "LICENCA_PREVIA": [r"licen[çc]a\s+pr[ée]via", r"\bLP\b.*licen[çc]a|licen[çc]a.*\bLP\b"],
    "LICENCA_INSTALACAO": [r"licen[çc]a\s+de\s+instala[çc][ãa]o", r"\bLI\b.*licen[çc]a"],
    "LICENCA_OPERACAO": [r"licen[çc]a\s+de\s+opera[çc][ãa]o", r"\bLO\b.*licen[çc]a"],
    "PLANTA_LOCALIZACAO": [r"planta\s+de\s+localiza[çc][ãa]o", r"planta\s+de\s+situa[çc][ãa]o",
                           r"plano\s+de\s+localiza[çc][ãa]o", r"planta\s+de\s+implanta[çc][ãa]o"],
    "PCA": [r"plano\s+de\s+controle\s+ambiental", r"\bpca\b"],
    "RCA": [r"relat[óo]rio\s+de\s+controle\s+ambiental", r"\brca\b"],
    "EIV": [r"estudo\s+de\s+impacto\s+de\s+vizinhan[çc]a", r"\beiv\b"],
    "FORMULARIO_ENQUADRAMENTO": [r"formul[áa]rio\s+de\s+enquadramento",
                                 r"motivo\s+do\s+encaminhamento"],
}

# Palavras-chave que ligam o TEXTO da exigência (checklist) ao tipo
# NB: tipos específicos de laudos/estudos DEVEM vir ANTES de ART, pois a frase
# '... elaborado de acordo com o TR desta secretaria, com ART de responsável...'
# cita ART apenas como acessório, e não como tipo principal do documento!
# NB2: RRT/RTT vem ANTES de ART porque RTT/RRT é registro do CAU/BR (Arquitetos
# e Urbanistas) e NUNCA deve ser classificado como ART de engenheiro/geólogo.
RE_EXIGENCIA_TIPO: list[tuple[str, re.Pattern]] = [
    ("EIV", re.compile(r"\beiv\b|impacto\s+de\s+vizinhan[çc]a", re.I)),
    ("FAUNA", re.compile(r"invent[áa]rio\s+de\s+fauna|fauna\s+silvestre|\bfauna\b|\blfs\b", re.I)),
    ("LCV", re.compile(r"cobertura\s+vegetal|invent[áa]rio\s+florestal|laudo\s+vegetal|\blcv\b", re.I)),
    ("SONDAGEM", re.compile(r"laudo\s+geol[óo]gico|geot[ée]cnic|sondagem|ensaio.*infiltra[çc][ãa]o", re.I)),
    ("MATRICULA_IMOVEL", re.compile(r"matr[íi]cula\s+do\s+im[óo]vel|matr[íi]cula\s+atualizada|"
                                    r"c[óo]pia\s+da\s+matr[íi]cula|\bmatr[íi]cula\b", re.I)),
    ("CNPJ", re.compile(r"\bcnpj\b|cpf\s*e\s*cnpj|cart[ãa]o\s+cnpj|comprovante\s+de\s+inscri[çc][ãa]o", re.I)),
    ("CONTRATO_SOCIAL", re.compile(r"contrato\s+social|estatuto\s+social", re.I)),
    ("CERTIDAO_ZONEAMENTO", re.compile(r"certid[ãa]o\s+(?:de\s+)?zoneamento|zoneamento", re.I)),
    ("DIRETRIZES_URBANISTICAS", re.compile(r"diretrizes\s+urban[íi]sticas", re.I)),
    ("VIABILIDADE_RESIDUOS", re.compile(r"viabilidade.*(?:limpeza|res[íi]duos)", re.I)),
    ("VIABILIDADE_AGUA", re.compile(r"viabilidade.*[áa]gua|corsan.*[áa]gua", re.I)),
    ("VIABILIDADE_ESGOTO", re.compile(r"viabilidade.*esgoto|corsan.*esgoto", re.I)),
    ("VIABILIDADE_ENERGIA", re.compile(r"viabilidade.*energia|rge", re.I)),
    ("DECLARACAO_ALAGAMENTO", re.compile(r"alagamento|inunda[çc][ãa]o", re.I)),
    # CAMADA_GIS antes de PROJETO_URBANISTICO: a exigência oficial
    # '21. Arquivo KMZ/KML e DWG do projeto urbanístico e APPs' é um
    # entregável GEOESPACIAL (KMZ/KML), não um projeto em PDF - classificá-la
    # como projeto faria o TR de projeto urbanístico ser aplicado a um .kmz.
    ("CAMADA_GIS", re.compile(r"\bkmz\b|\bkml\b|shape\s*file|\bshp\b|georreferenc|\bcamada\b|"
                              r"curvas\s+de\s+n[íi]vel|mapa\s+de\s+areas\s+de\s+preserva", re.I)),
    ("PROJETO_URBANISTICO", re.compile(r"projeto\s+(?:arquitet[ôo]nico|urban[íi]stico)", re.I)),
    ("PLANTA_LOCALIZACAO", re.compile(r"planta\s+de\s+(localiza[çc][ãa]o|situa[çc][ãa]o)|"
                                      r"plano\s+de\s+localiza[çc][ãa]o", re.I)),
    ("PGRS", re.compile(r"\bpgrs\b|plano\s+de\s+gerenciamento", re.I)),
    ("ALVARA_BOMBEIROS", re.compile(r"bombeiro|\bppci\b", re.I)),
    ("LICENCA_PREVIA", re.compile(r"licen[çc]a\s+pr[ée]via", re.I)),
    ("LICENCA_INSTALACAO", re.compile(r"licen[çc]a\s+de\s+instala[çc][ãa]o", re.I)),
    ("LICENCA_OPERACAO", re.compile(r"licen[çc]a\s+de\s+opera[çc][ãa]o", re.I)),
    ("PCA", re.compile(r"\bpca\b|plano\s+de\s+controle\s+ambiental", re.I)),
    ("RCA", re.compile(r"\brca\b|relat[óo]rio\s+de\s+controle\s+ambiental", re.I)),
    ("PROJETO_EXECUTIVO", re.compile(r"projeto\s+(executivo|t[ée]cnic|construC?[çc][ãa]o|aprovado)", re.I)),
    ("RRT", re.compile(r"^\s*\d*\s*[.)]?\s*(?:c[óo]pia\s+da\s+)?(?:rrt|rtt)\b|"
                       r"registro\s+de\s+responsabilidade\s+t[ée]cnica|"
                       r"(?:rrt|rtt)\s+de\s+(?:profissional|arquiteto)|"
                       r"arquitet[oa]\s+e\s+urbanista", re.I)),
    ("ART", re.compile(r"^\s*\d*\s*[.)]?\s*(?:c[óo]pia\s+da\s+)?art\b|"
                       r"anota[çc][ãa]o\s+de\s+responsabilidade\s+t[ée]cnica\s*(?:\(art\))?$|"
                       r"art\s+de\s+profissional", re.I)),
]


def normalizar_nome(texto: Optional[str]) -> str:
    """Nome do arquivo em minúsculas, sem acentos, sem pontuação de enfeite
    (°, º, sublinhados, extensão) — para casamento tolerante."""
    if not texto:
        return ""
    texto = Path(texto).stem
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[_\-°º]+", " ", texto.lower())
    return re.sub(r"\s+", " ", texto).strip()


def _mesmo_nome(nome_a: str, nome_b: str) -> bool:
    """Nomes equivalentes p/ o aprendizado: iguais, muito parecidos
    (SequenceMatcher >= 0.80) ou com os tokens do nome aprendido TODOS
    presentes no novo (sufixos 'versao final', '(2)', 'atualizado'...)."""
    if not nome_a or not nome_b:
        return False
    if nome_a == nome_b:
        return True
    if SequenceMatcher(None, nome_a, nome_b).ratio() >= 0.80:
        return True
    tokens_a = set(nome_a.split())
    tokens_b = set(nome_b.split())
    return bool(tokens_a) and tokens_a.issubset(tokens_b)


class IdentificadorDocumentos:
    """Identifica o tipo do documento: NOME -> APRENDIDO -> CONTEÚDO, e grava
    no JSON o nome aprendido quando o conteúdo confirma a identificação."""

    LIMIAR_NOME = 0.60          # % de palavras-chave do tipo presentes no nome
    LIMIAR_APRENDIDO = 0.80     # similaridade do nome com um nome aprendido
    MIN_ASSINATURAS = 2         # assinaturas de conteúdo que confirmam o tipo

    # Tokos curtos que, apesar do tamanho, são decisivos no NOME do arquivo.
    # Sem esta lista 'rrt', 'rtt' e 'art' (3 letras) eram descartados pelo
    # filtro len(w) >= 4 e 'RTT - Projeto Urbanistico.pdf' virava PROJETO
    # URBANISTICO - o registro de responsabilidade técnica desaparecia.
    TOKENS_CURTOS = {"rrt", "rtt", "art", "rca", "eiv", "lfs", "lcv", "pca",
                     "prad", "rfo", "gis", "shp", "kmz", "kml", "app"}
    # Tipos que vencem a disputa quando o nome traz um registro explícito:
    # 'RTT - Projeto Urbanístico.pdf' é uma RTT (CAU/BR), não um projeto.
    TIPOS_REGISTRO = ("RRT", "ART")
    # token do nome -> tipo de registro ('rtt' e 'rrt' são a mesma coisa:
    # Registro de Responsabilidade Técnica do CAU/BR)
    TOKEN_REGISTRO = {"rrt": "RRT", "rtt": "RRT", "art": "ART"}

    def __init__(self, caminho: Optional[Path] = None):
        self.caminho = Path(caminho) if caminho else (
            Path(__file__).resolve().parents[1] / "config" / "aprendido_documentos.json")
        self.aprendidos: list[dict[str, Any]] = []
        try:
            if self.caminho.exists():
                dados = json.loads(self.caminho.read_text(encoding="utf-8"))
                self.aprendidos = list(dados.get("aprendidos", []))
        except Exception:  # noqa: BLE001 — aprendizado corrompido não derruba a análise
            self.aprendidos = []

    # -------------------------------------------------------------- NOME --
    def _por_nome(self, nome: str) -> tuple[Optional[str], float]:
        """Tipo pelo nome do arquivo (palavras-chave + similaridade)."""
        nome_n = normalizar_nome(nome)
        if not nome_n:
            return None, 0.0
        # a EXTENSÃO decide em arquivos geoespaciais ('projeto.kmz',
        # 'camadas.kml'): normalizar_nome usa o stem, então ela é recolocada
        nome_n = f"{nome_n} {Path(nome).suffix.lower().lstrip('.')}"
        tokens_nome = set(nome_n.split())
        # registro explícito no nome ('rrt', 'rtt', 'art' como palavra inteira)
        registro_no_nome = next(
            (self.TOKEN_REGISTRO[tok] for tok in tokens_nome
             if tok in self.TOKEN_REGISTRO), None)
        melhor: tuple[Optional[str], float] = (None, 0.0)
        for tipo, chaves in TODOS_PADROES.items():
            for chave in chaves:
                palavras = []
                for w in chave.split():
                    if len(w) >= 4:
                        palavras.append(w)
                    elif w in self.TOKENS_CURTOS:
                        palavras.append(w)   # curto, mas decisivo
                if not palavras:
                    continue
                presentes = sum(
                    1 for w in palavras
                    if (w in tokens_nome if w in self.TOKENS_CURTOS
                        else w in nome_n))
                fracao = presentes / len(palavras)
                if fracao >= self.LIMIAR_NOME:
                    confianca = 0.5 + 0.5 * fracao
                    # um registro declarado no nome nunca perde para
                    # 'projeto urbanístico', 'planta' ou 'laudo'
                    if registro_no_nome and tipo != registro_no_nome:
                        confianca = min(confianca, 0.55)
                    if confianca > melhor[1]:
                        melhor = (tipo, confianca)
        return melhor

    # -------------------------------------------------------- APRENDIDO --
    def _por_aprendizado(self, nome: str) -> Optional[str]:
        """Tipo aprendido em processo anterior para este (ou parecido) nome."""
        nome_n = normalizar_nome(nome)
        for item in self.aprendidos:
            registrado = item.get("nome", "")
            if _mesmo_nome(registrado, nome_n):
                return item.get("tipo")
        return None

    # -------------------------------------------------------- CONTEÚDO --
    def _por_conteudo(self, texto: Optional[str]) -> tuple[Optional[str], int]:
        """Tipo pelas assinaturas internas do documento (>= MIN_ASSINATURAS).

        Desempate pelo CONSELHO: RTT/RRT é do CAU/BR e ART é do CREA/CRBio —
        quando o documento cita o conselho, ele decide entre os dois."""
        if not texto:
            return None, 0
        trecho = texto[:6000]
        melhor: tuple[Optional[str], int] = (None, 0)
        for tipo, padroes in ASSINATURAS_CONTEUDO.items():
            pontos = sum(1 for padrao in padroes
                         if re.search(padrao, trecho, re.I))
            if pontos >= self.MIN_ASSINATURAS and pontos > melhor[1]:
                melhor = (tipo, pontos)
        if melhor[0] in ("ART", "RRT"):
            conselho = conselho_do_texto(trecho)
            tipo_conselho = tipo_registro_por_conselho(conselho)
            if tipo_conselho and tipo_conselho != melhor[0]:
                # o conselho contradiz a pontuação: exige sinais do tipo certo
                padroes = ASSINATURAS_CONTEUDO[tipo_conselho]
                pontos = sum(1 for p in padroes if re.search(p, trecho, re.I))
                if pontos >= 1:
                    return tipo_conselho, pontos
        return melhor

    # ------------------------------------------------------- PRINCIPAL --
    def identificar(self, nome_arquivo: str,
                    texto: Optional[str] = None) -> dict[str, Any]:
        """Pipeline completo. Retorna {tipo, via, confianca, evidencia}.

        via ∈ {'aprendido', 'nome', 'conteudo', None} — quando 'conteudo'
        confirma e o NOME não havia casado, o nome é APRENDIDO (persistente).
        """
        resultado: dict[str, Any] = {"tipo": None, "via": None,
                                     "confianca": 0.0, "evidencia": None}
        try:
            # 0) aprendido em processos anteriores
            tipo = self._por_aprendizado(nome_arquivo)
            if tipo:
                return {**resultado, "tipo": tipo, "via": "aprendido",
                        "confianca": 1.0}

            # 1) nome do arquivo (pode vir alterado: '°Cópia da matrícula...')
            tipo, confianca = self._por_nome(nome_arquivo)
            if tipo:
                return {**resultado, "tipo": tipo, "via": "nome",
                        "confianca": round(confianca, 2)}

            # 2) conteúdo do documento
            tipo, pontos = self._por_conteudo(texto)
            if tipo:
                resultado.update({"tipo": tipo, "via": "conteudo",
                                  "confianca": min(1.0, pontos / 4),
                                  "evidencia": f"{pontos} assinaturas no conteúdo"})
                # 3) APRENDE: nome confirmado pelo conteúdo deste tipo
                self.aprender(nome_arquivo, tipo, resultado["evidencia"],
                              texto=texto)
        except Exception:  # noqa: BLE001
            return resultado
        return resultado

    def aprender(self, nome_arquivo: str, tipo: str, evidencia: str,
                 texto: Optional[str] = None) -> bool:
        """Grava a associação nome -> tipo para reconhecimento imediato nos
        próximos processos (idempotente; nunca sobrescreve tipo diverso)."""
        try:
            nome_n = normalizar_nome(nome_arquivo)
            if not nome_n or not tipo:
                return False
            # NUNCA aprender ART ou RRT para arquivos de formulários, projetos, laudos ou estudos
            if tipo in ("ART", "RRT") and any(k in nome_n for k in [
                    "formulario", "projeto", "laudo", "estudo", "inventario",
                    "diretriz", "certidao", "declaracao", "planta", "croqui",
                    "matricula", "relatorio", "contrato"]):
                return False
            # RTT/RRT é registro do CAU/BR; ART é registro do CREA/CRBio.
            # O conselho do CONTEÚDO manda: não se aprende um tipo que
            # contradiz o conselho que emite o registro (evita 'rrt' -> ART).
            if tipo in ("ART", "RRT") and texto:
                conselho_doc = conselho_do_texto(texto)
                tipo_pelo_conselho = tipo_registro_por_conselho(conselho_doc)
                if tipo_pelo_conselho and tipo_pelo_conselho != tipo:
                    return False
            if any(nome_arquivo.lower().endswith(ext)
                   for ext in [".htm", ".html", ".dwg", ".kmz", ".kml", ".xlsx"]):
                return False
            if any(i.get("nome") == nome_n for i in self.aprendidos):
                return False
            self.aprendidos.append({"nome": nome_n, "tipo": tipo,
                                    "data": date.today().isoformat(),
                                    "evidencia": evidencia})
            self.caminho.parent.mkdir(parents=True, exist_ok=True)
            self.caminho.write_text(
                json.dumps({"descricao": "Associações nome->tipo aprendidas "
                                         "(documento identificado pelo CONTEÚDO)",
                            "aprendidos": self.aprendidos},
                           ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
            return True
        except Exception:  # noqa: BLE001
            return False

    # ----------------------------------------------------- EXIGÊNCIAS --
    @staticmethod
    def tipo_da_exigencia(exigido: str) -> Optional[str]:
        """Tipo esperado de uma exigência do checklist (texto do formulário)."""
        for tipo, padrao in RE_EXIGENCIA_TIPO:
            if padrao.search(exigido or ""):
                return tipo
        return None


# Consulta leve do aprendizado (usada pelo validador antes de classificar)
def tipo_aprendido(nome_arquivo: str,
                   caminho: Optional[Path] = None) -> Optional[str]:
    """Tipo aprendido para o nome (consulta barata, sem conteúdo)."""
    try:
        alvo = Path(caminho) if caminho else (
            Path(__file__).resolve().parents[1] / "config" / "aprendido_documentos.json")
        if not alvo.exists():
            return None
        dados = json.loads(alvo.read_text(encoding="utf-8"))
        nome_n = normalizar_nome(nome_arquivo)
        for item in dados.get("aprendidos", []):
            if _mesmo_nome(item.get("nome", ""), nome_n):
                return item.get("tipo")
    except Exception:  # noqa: BLE001
        return None
    return None
