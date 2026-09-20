"""PRIMITIVAS DE SEGURANÇA (skills security-audit + senior-security).

Tudo que vem de DOCUMENTOS ENVIADOS pelo licenciador (nomes de arquivo,
trechos citados, itens de não conformidade, justificativas, conteúdo
parseado) é DADO NÃO CONFIÁVEL: antes de renderizar em markdown, entrar
em caminhos de disco ou em atributos HTML, passa por estas funções.
"""
from __future__ import annotations

import html
import re

# caracteres de controle (exceto \n) — anti header/terminal injection
_RE_CONTROLE = re.compile(r"[\x00-\x09\x0b\x0c\x0e-\x1f\x7f]")


def md_seguro(texto: object) -> str:
    """Sanitiza texto NÃO CONFIÁVEL para renderização em st.markdown /
    st.error / st.warning (markdown interpreta HTML e sintaxe de links).

    - escapa HTML (< > &) — inibe XSS armazenado via conteúdo do documento;
    - neutraliza links/imagens markdown [..](..) — inibe injeção de links
      `javascript:`/`data:` e fetch de URLs externas na tela do analista;
    - remove caracteres de controle (exceto quebra de linha).
    """
    texto_limpo = _RE_CONTROLE.sub("", str(texto or ""))
    texto_limpo = html.escape(texto_limpo, quote=False)
    return texto_limpo.replace("[", "\\[").replace("]", "\\]")


def nome_arquivo_seguro(nome: str, maximo: int = 80) -> str:
    """Slug seguro para NOMES DE ARQUIVO em atributos HTML/caminhos:
    mantém apenas [A-Za-z0-9._-], sem traversal (sem `/` `..`), tamanho
    limitado. Usado no atributo `download` dos links data-URI."""
    nome_limpo = re.sub(r"[^A-Za-z0-9._-]", "_", str(nome or ""))
    nome_limpo = re.sub(r"_+", "_", nome_limpo).strip("._") or "arquivo"
    return nome_limpo[:maximo]


def sufixo_seguro(nome: str, maximo: int = 10) -> str:
    """Sufixo de extensão seguro a partir de nome NÃO CONFIÁVEL:
    apenas [a-z0-9], sem pontos duplos nem caracteres de controle —
    anti path traversal na gravação de arquivos."""
    sufixo = re.sub(r"[^a-z0-9]", "", str(nome or "").lower())[:maximo]
    return "." + sufixo if sufixo else ".html"


def attr_html(valor: object) -> str:
    """Escapa valor para atributo HTML (aspas incluídas) — anti quebra de
    atributo/injeção de evento (onerror=, onmouseover=...)."""
    return html.escape(str(valor or ""), quote=True)
