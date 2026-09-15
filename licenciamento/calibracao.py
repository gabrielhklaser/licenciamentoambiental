# -*- coding: utf-8 -*-
"""
Calibração dirigida por configuração (valores oficiais x padrões de código)
============================================================================

Os agentes do sistema possuem valores PADRÃO no código (marcados onde precisam
de conferência). Quando os documentos oficiais são processados pela ferramenta
`ferramentas/ingestar_pdfs.py`, os valores EXTRAÍDOS dos PDFs são gravados em
`config/*.json` e PASSAM A TER PRECEDÊNCIA sobre os padrões — sem alterar código.

Arquivos de configuração (todos opcionais; presença => calibração ativa):

    config/taxas_urm.json            -> matriz de taxas (AgenteFinanceiro)
    config/gabarito_trs.json         -> parâmetros dos Termos de Referência (AuditorTecnico)
    config/checklists_oficiais.json  -> checklists oficiais por fase (FormularioParser)
    config/rotulos_formulario.json   -> rótulos extras dos formulários (FormularioParser)

Cada arquivo carrega a chave "fonte" (documento de origem) e "revisado" (bool)
para rastreabilidade: valores extraídos automaticamente nascem com
"revisado": false até que o licenciador confira.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("licenciamento.calibracao")

PASTA_CONFIG_PADRAO = "config"

DESCRICOES_CONFIG = {
    "taxas_urm": "Matriz de taxas em URMs (Manual de Legislação e Taxas Ambientais)",
    "gabarito_trs": "Parâmetros dos Termos de Referência (validações técnicas)",
    "checklists_oficiais": "Checklists oficiais de documentos por fase (formulários)",
    "rotulos_formulario": "Rótulos adicionais dos formulários oficiais",
}


class Calibracao:
    """Carrega e valida os arquivos de configuração oficiais (quando existirem)."""

    def __init__(self, pasta_config: Optional[str] = None):
        self.pasta = Path(pasta_config or PASTA_CONFIG_PADRAO)
        self.taxas_urm: Optional[dict] = None
        self.gabarito_trs: Optional[dict] = None
        self.checklists_oficiais: Optional[dict] = None
        self.rotulos_formulario: Optional[dict] = None
        self._carregar()

    # ------------------------------------------------------------------
    def _ler(self, nome: str) -> Optional[dict]:
        caminho = self.pasta / f"{nome}.json"
        if not caminho.exists():
            return None
        try:
            dados = json.loads(caminho.read_text(encoding="utf-8"))
            if not isinstance(dados, dict):
                raise ValueError("raiz do JSON deve ser um objeto")
            logger.info("Calibração carregada: %s (fonte: %s, revisado: %s)",
                        caminho, dados.get("fonte", "?"), dados.get("revisado", False))
            return dados
        except Exception as exc:  # noqa: BLE001 - config inválida não derruba o sistema
            logger.error("Falha ao ler %s: %s (usando padrões do código)", caminho, exc)
            return None

    def _carregar(self) -> None:
        self.taxas_urm = self._ler("taxas_urm")
        self.gabarito_trs = self._ler("gabarito_trs")
        self.checklists_oficiais = self._ler("checklists_oficiais")
        self.rotulos_formulario = self._ler("rotulos_formulario")

    # ------------------------------------------------------------------
    @property
    def ativa(self) -> bool:
        """Indica se ao menos um arquivo de calibração está presente."""
        return any(x is not None for x in
                   (self.taxas_urm, self.gabarito_trs, self.checklists_oficiais,
                    self.rotulos_formulario))

    def resumo(self) -> dict[str, Any]:
        """Resumo legível do estado de calibração (exibido no dashboard)."""
        itens = {}
        for chave, valor in (("taxas_urm", self.taxas_urm),
                             ("gabarito_trs", self.gabarito_trs),
                             ("checklists_oficiais", self.checklists_oficiais),
                             ("rotulos_formulario", self.rotulos_formulario)):
            itens[chave] = {
                "descricao": DESCRICOES_CONFIG[chave],
                "ativa": valor is not None,
                "fonte": (valor or {}).get("fonte"),
                "revisado": bool((valor or {}).get("revisado", False)),
            }
        return {"pasta": str(self.pasta), "ativa": self.ativa, "itens": itens}

    # ------------------------------------------------------------------
    @staticmethod
    def salvar_config(nome: str, dados: dict, pasta_config: Optional[str] = None) -> Path:
        """Grava um arquivo de configuração (usado pelo ingestar_pdfs e testes)."""
        pasta = Path(pasta_config or PASTA_CONFIG_PADRAO)
        pasta.mkdir(parents=True, exist_ok=True)
        caminho = pasta / f"{nome}.json"
        caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=2),
                           encoding="utf-8")
        logger.info("Configuração gravada: %s", caminho)
        return caminho
