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

PADROES_TIPO_EXTRA: dict[str, list[str]] = {
    "PLANTA_LOCALIZACAO": ["planta de localizacao", "planta de situacao",
                           "plano de localizacao", "planta localizacao"],
    "LICENCA_PREVIA": ["licenca previa", "copia da licenca previa"],
    "LICENCA_INSTALACAO": ["licenca de instalacao", "copia da licenca de instalacao"],
    "LICENCA_OPERACAO": ["licenca de operacao", "copia da licenca de operacao"],
    "RCA": ["relatorio de controle ambiental", "rca"],
    "PROJETO_EXECUTIVO": ["projeto executivo", "projeto tecnico", "projeto aprovado"],
    "FORMULARIO_ENQUADRAMENTO": ["formulario de enquadramento", "formulario assinado",
                                 "formulario enquadramento"],
}
TODOS_PADROES: dict[str, list[str]] = {**PADROES_TIPO, **PADROES_TIPO_EXTRA}

# Assinaturas do CONTEÚDO (regex por tipo; >= 2 assinaturas confirmam)
ASSINATURAS_CONTEUDO: dict[str, list[str]] = {
    "MATRICULA_IMOVEL": [r"matr[ií]cula\s*n[ºo°.]?", r"registro\s+de\s+im[óo]veis",
                         r"serventia\s+e\s+registro", r"certid[ãa]o\s+de\s+inteiro\s+teor",
                         r"matr[íi]cula\s+do\s+im[óo]vel"],
    "CNPJ": [r"\bcnpj\b", r"receita\s+federal", r"comprovante\s+de\s+inscri[çc][ãa]o",
             r"situa[çc][ãa]o\s+cadastral", r"cart[ãa]o\s+cnpj"],
    "CONTRATO_SOCIAL": [r"contrato\s+social", r"estatuto\s+social", r"ata\s+de\s+nomea[çc][ãa]o",
                        r"junta\s+comercial"],
    "ART": [r"\bart\b", r"anota[çc][ãa]o\s+de\s+responsabilidade",
            r"\bcrea\b|\bcau\b", r"respons[áa]vel\s+t[ée]cnic"],
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
RE_EXIGENCIA_TIPO: list[tuple[str, str]] = [
    ("MATRICULA_IMOVEL", re.compile(r"matr[íi]cula\s+do\s+im[óo]vel|matr[íi]cula\s+atualizada|"
                                    r"c[óo]pia\s+da\s+matr[íi]cula|\bmatr[íi]cula\b", re.I)),
    ("ART", re.compile(r"anota[çc][ãa]o\s+de\s+responsabilidade|\bART\b", re.I)),
    ("CNPJ", re.compile(r"\bcnpj\b|cpf\s*e\s*cnpj|cart[ãa]o\s+cnpj|comprovante\s+de\s+inscri[çc][ãa]o", re.I)),
    ("PGRS", re.compile(r"\bpgrs\b|plano\s+de\s+gerenciamento", re.I)),
    ("ALVARA_BOMBEIROS", re.compile(r"bombeiro|\bppci\b", re.I)),
    ("PLANTA_LOCALIZACAO", re.compile(r"planta\s+de\s+(localiza[çc][ãa]o|situa[çc][ãa]o)|"
                                      r"plano\s+de\s+localiza[çc][ãa]o", re.I)),
    ("LICENCA_PREVIA", re.compile(r"licen[çc]a\s+pr[ée]via", re.I)),
    ("LICENCA_INSTALACAO", re.compile(r"licen[çc]a\s+de\s+instala[çc][ãa]o", re.I)),
    ("LICENCA_OPERACAO", re.compile(r"licen[çc]a\s+de\s+opera[çc][ãa]o", re.I)),
    ("PCA", re.compile(r"\bpca\b|plano\s+de\s+controle\s+ambiental", re.I)),
    ("RCA", re.compile(r"\brca\b|relat[óo]rio\s+de\s+controle\s+ambiental", re.I)),
    ("EIV", re.compile(r"\beiv\b|impacto\s+de\s+vizinhan[çc]a", re.I)),
    ("CONTRATO_SOCIAL", re.compile(r"contrato\s+social|estatuto\s+social", re.I)),
    ("PROJETO_EXECUTIVO", re.compile(r"projeto\s+(executivo|t[ée]cnic|construC?[çc][ãa]o|aprovado)", re.I)),
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
        melhor: tuple[Optional[str], float] = (None, 0.0)
        for tipo, chaves in TODOS_PADROES.items():
            for chave in chaves:
                palavras = [w for w in chave.split() if len(w) >= 4]
                if not palavras:
                    continue
                presentes = sum(1 for w in palavras if w in nome_n)
                fracao = presentes / len(palavras)
                if fracao >= self.LIMIAR_NOME:
                    confianca = 0.5 + 0.5 * fracao
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
        """Tipo pelas assinaturas internas do documento (>= MIN_ASSINATURAS)."""
        if not texto:
            return None, 0
        trecho = texto[:6000]
        melhor: tuple[Optional[str], int] = (None, 0)
        for tipo, padroes in ASSINATURAS_CONTEUDO.items():
            pontos = sum(1 for padrao in padroes
                         if re.search(padrao, trecho, re.I))
            if pontos >= self.MIN_ASSINATURAS and pontos > melhor[1]:
                melhor = (tipo, pontos)
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
                self.aprender(nome_arquivo, tipo, resultado["evidencia"])
        except Exception:  # noqa: BLE001
            return resultado
        return resultado

    def aprender(self, nome_arquivo: str, tipo: str, evidencia: str) -> bool:
        """Grava a associação nome -> tipo para reconhecimento imediato nos
        próximos processos (idempotente; nunca sobrescreve tipo diverso)."""
        try:
            nome_n = normalizar_nome(nome_arquivo)
            if not nome_n or not tipo:
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
