# -*- coding: utf-8 -*-
"""
FASE 2 - Utilitários de persistência (engine, criação de esquema e salvamento).
================================================================================

Por padrão o protótipo usa SQLite (arquivo local) para funcionar em qualquer
máquina. Para produção com PostgreSQL/PostGIS basta definir a variável de
ambiente DATABASE_URL, por exemplo:

    DATABASE_URL=postgresql+psycopg2://usuario:senha@localhost:5432/licenciamento

E ativar a extensão espacial no banco (uma única vez):

    CREATE EXTENSION postgis;
"""

from __future__ import annotations

import json
import os
from typing import Any, Iterable, Optional

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import (Base, DocumentoAnexado, Empreendimento,
                     ProcessoLicenciamento, ResponsavelTecnico, TabelaURM)

DATABASE_URL_PADRAO = os.getenv("DATABASE_URL", "sqlite:///./licenciamento.db")


def obter_engine(url: Optional[str] = None, echo: bool = False) -> Engine:
    """Cria (ou reutiliza) a engine do banco, criando o esquema declarativo."""
    engine = create_engine(url or DATABASE_URL_PADRAO, echo=echo, future=True)
    Base.metadata.create_all(engine)
    return engine


def obter_sessao(engine: Optional[Engine] = None) -> Session:
    """Abre uma sessão ORM pronta para uso."""
    engine = engine or obter_engine()
    fabrica = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return fabrica()


def semear_tabela_urm(sessao: Session, matriz: dict) -> int:
    """Popula/_atualiza a TabelaURM a partir da matriz do AgenteFinanceiro.

    Retorna a quantidade de linhas inseridas/atualizadas.
    """
    alteracoes = 0
    for grupo, portes in matriz.items():
        for porte_ou_faixa, potenciais in portes.items():
            for potencial, fases in potenciais.items():
                for fase, valor in fases.items():
                    stmt = select(TabelaURM).where(
                        TabelaURM.grupo_atividade == grupo,
                        TabelaURM.porte_ou_faixa == porte_ou_faixa,
                        TabelaURM.potencial_poluidor == (potencial if potencial != "_" else None),
                        TabelaURM.fase_licenca == fase,
                    )
                    linha = sessao.scalars(stmt).first()
                    if linha is None:
                        sessao.add(TabelaURM(
                            grupo_atividade=grupo,
                            porte_ou_faixa=porte_ou_faixa,
                            potencial_poluidor=(potencial if potencial != "_" else None),
                            fase_licenca=fase,
                            valor_urm=float(valor),
                        ))
                        alteracoes += 1
                    elif abs(linha.valor_urm - float(valor)) > 1e-9:
                        linha.valor_urm = float(valor)
                        alteracoes += 1
    sessao.commit()
    return alteracoes


def salvar_processo(dados_parser: dict,
                    resultado_admin: Optional[dict] = None,
                    resultado_financeiro: Optional[dict] = None,
                    documentos_anexados: Optional[Iterable[str]] = None,
                    numero_processo: Optional[str] = None,
                    engine: Optional[Engine] = None) -> int:
    """Persiste o fluxo completo (Fase 1 + 2) e devolve o id do processo.

    Args:
        dados_parser: JSON consolidado do FormularioParser (Fase 1).
        resultado_admin: saída do AgenteAdministrativo.auditar().
        resultado_financeiro: saída do AgenteFinanceiro.calcular_taxa().
        documentos_anexados: nomes dos arquivos simulados como anexados.
        numero_processo: número administrativo opcional.
        engine: engine SQLAlchemy (padrão: DATABASE_URL / SQLite local).
    """
    engine = engine or obter_engine()
    with Session(engine, expire_on_commit=False) as sessao:
        emp = dados_parser.get("empreendimento", {})
        requerente = dados_parser.get("empreendedor", {})
        coords = emp.get("coordenadas", {}) or {}

        empreendimento = Empreendimento(
            nome_razao_social=requerente.get("nome_razao_social"),
            cpf_cnpj=requerente.get("cpf_cnpj"),
            nome_empreendimento=emp.get("nome_empreendimento"),
            ramo_atividade=emp.get("ramo_atividade"),
            codram=emp.get("codram"),
            porte=emp.get("porte"),
            potencial_poluidor=emp.get("potencial_poluidor"),
            area_total_ha=emp.get("area_total_ha"),
            area_util_ha=emp.get("area_util_ha"),
            area_intervencao_ha=emp.get("area_intervencao_ha"),
            latitude=coords.get("latitude"),
            longitude=coords.get("longitude"),
            matricula_imovel=emp.get("matricula_imovel"),
            endereco=emp.get("endereco"),
            municipio=emp.get("municipio") or "Campo Bom",
            uf="RS",
        )
        # Ponto SIRGAS 2000: WKT 'POINT(long lat)'; PostGIS converte para Geometry(4674)
        if coords.get("latitude") is not None and coords.get("longitude") is not None:
            empreendimento.geometria = f"POINT({coords['longitude']} {coords['latitude']})"

        pleito = dados_parser.get("pleito", {})
        processo = ProcessoLicenciamento(
            empreendimento=empreendimento,
            numero_processo=numero_processo,
            tipo_licenca=pleito.get("tipo_licenca"),
            fases_componentes=pleito.get("fases_componentes"),
            status_triagem=dados_parser.get("status_triagem"),
            status_administrativo=(resultado_admin or {}).get("status_geral"),
            valor_taxa_urm=(resultado_financeiro or {}).get("total_urm"),
            dados_parser=json.loads(json.dumps(dados_parser, ensure_ascii=False)),
        )

        rt = dados_parser.get("responsavel_tecnico", {}) or {}
        processo.responsaveis.append(ResponsavelTecnico(
            nome=rt.get("nome"),
            registro_crea=rt.get("registro_crea"),
            numero_art=rt.get("registro_art"),
            empresa=rt.get("empresa"),
        ))

        exigidos = (dados_parser.get("documentos_exigidos", {}) or {}).get("lista_deduplicada", [])
        anexados = [a for a in (documentos_anexados or [])]
        ok_set = set((resultado_admin or {}).get("documentos_ok", []))

        for documento in exigidos:
            estah_ok = documento in ok_set
            processo.documentos.append(DocumentoAnexado(
                nome_documento=documento,
                exigido=True,
                anexado=estah_ok,
                status=("OK" if estah_ok else "PENDENTE"),
                justificativa=(None if estah_ok
                               else "Documento exigido não localizado no processo."),
            ))

        sessao.add(processo)
        sessao.commit()
        return processo.id
