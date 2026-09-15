# -*- coding: utf-8 -*-
"""
FASE 2 - Modelagem do Banco de Dados (PostgreSQL + PostGIS via SQLAlchemy)
===========================================================================

Modelos ORM para estruturar as informações do licenciamento ambiental:

    - Empreendimento: dados gerais, CNPJ/CPF, coordenadas (SIRGAS 2000);
    - ProcessoLicenciamento: tipo de licença, CODRAM, porte, potencial poluidor;
    - DocumentoAnexado: checklist do processo (exigido/anexado/status);
    - ResponsavelTecnico: nome, CREA e ART (constraint administrativa);
    - TabelaURM: matriz de valores de taxas (porte x potencial x fase).

PostGIS: quando o banco de destino é PostgreSQL com a extensão PostGIS ativa,
a coluna `geometria` é criada como Geometry(POINT, 4674) - SIRGAS 2000 (graus
decimais). Em dialetos diferentes (ex.: SQLite do protótipo), a mesma coluna
armazena o WKT textual, mantendo o código portátil sem exigir PostGIS local.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer,
                        String, Text, UniqueConstraint, create_engine)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# GeoAlchemy2 é opcional no protótipo (necessário apenas com PostGIS real)
try:
    from geoalchemy2 import Geometry
    from sqlalchemy.types import TypeDecorator, Text as _Text

    class GeometriaPonto(TypeDecorator):
        """Coluna híbrida: Geometry(POINT, 4674) no PostgreSQL; WKT (texto) fora dele."""

        impl = _Text
        cache_ok = True

        def load_dialect_impl(self, dialect):
            if dialect.name == "postgresql":
                return dialect.type_descriptor(Geometry(geometry_type="POINT", srid=4674))
            return dialect.type_descriptor(_Text())

    POSTGIS_DISPONIVEL = True
except ImportError:  # ambiente sem geoalchemy2 - só armazenamos lat/long e WKT
    from sqlalchemy.types import TypeDecorator, Text as _Text

    class GeometriaPonto(TypeDecorator):
        impl = _Text
        cache_ok = True

    POSTGIS_DISPONIVEL = False


class Base(DeclarativeBase):
    """Base declarativa dos modelos ORM."""
    pass


class Empreendimento(Base):
    """Tabela `empreendimentos` - dados gerais do empreendimento/empreendedor."""
    __tablename__ = "empreendimentos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nome_razao_social: Mapped[str | None] = mapped_column(String(200))
    cpf_cnpj: Mapped[str | None] = mapped_column(String(20), index=True)
    nome_empreendimento: Mapped[str | None] = mapped_column(String(200))
    ramo_atividade: Mapped[str | None] = mapped_column(String(200))
    codram: Mapped[str | None] = mapped_column(String(30), index=True)
    porte: Mapped[str | None] = mapped_column(String(20))
    potencial_poluidor: Mapped[str | None] = mapped_column(String(20))
    area_total_ha: Mapped[float | None] = mapped_column(Float)
    area_util_ha: Mapped[float | None] = mapped_column(Float)
    area_intervencao_ha: Mapped[float | None] = mapped_column(Float)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    # Ponto em SIRGAS 2000 (SRID 4674). Em PostGIS: Geometry de verdade.
    geometria: Mapped[str | None] = mapped_column(GeometriaPonto, nullable=True)
    matricula_imovel: Mapped[str | None] = mapped_column(String(80))
    endereco: Mapped[str | None] = mapped_column(String(255))
    municipio: Mapped[str | None] = mapped_column(String(120), default="Campo Bom")
    uf: Mapped[str | None] = mapped_column(String(2), default="RS")
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    processos: Mapped[list["ProcessoLicenciamento"]] = relationship(
        back_populates="empreendimento", cascade="all, delete-orphan")


class ProcessoLicenciamento(Base):
    """Tabela `processos_licenciamento` - o processo administrativo (pleito)."""
    __tablename__ = "processos_licenciamento"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    empreendimento_id: Mapped[int] = mapped_column(ForeignKey("empreendimentos.id"), index=True)
    numero_processo: Mapped[str | None] = mapped_column(String(40), index=True)
    tipo_licenca: Mapped[str | None] = mapped_column(String(20))          # LP/LI/LO/LIR/LOR/...
    fases_componentes: Mapped[list | None] = mapped_column(JSON)          # ex.: ["LP","LI","LO"]
    status_triagem: Mapped[str | None] = mapped_column(String(40))        # parser (bloqueado_sem_art...)
    status_administrativo: Mapped[str | None] = mapped_column(String(40)) # agente administrativo
    valor_taxa_urm: Mapped[float | None] = mapped_column(Float)           # agente financeiro
    dados_parser: Mapped[dict | None] = mapped_column(JSON)               # JSON bruto da Fase 1
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    empreendimento: Mapped["Empreendimento"] = relationship(back_populates="processos")
    documentos: Mapped[list["DocumentoAnexado"]] = relationship(
        back_populates="processo", cascade="all, delete-orphan")
    responsaveis: Mapped[list["ResponsavelTecnico"]] = relationship(
        back_populates="processo", cascade="all, delete-orphan")


class DocumentoAnexado(Base):
    """Tabela `documentos_anexados` - checklist cruzado exigido x anexado."""
    __tablename__ = "documentos_anexados"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    processo_id: Mapped[int] = mapped_column(ForeignKey("processos_licenciamento.id"), index=True)
    nome_documento: Mapped[str] = mapped_column(String(300))
    exigido: Mapped[bool] = mapped_column(Boolean, default=True)
    anexado: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str | None] = mapped_column(String(20))   # "OK" | "PENDENTE"
    justificativa: Mapped[str | None] = mapped_column(Text)

    processo: Mapped["ProcessoLicenciamento"] = relationship(back_populates="documentos")


class ResponsavelTecnico(Base):
    """Tabela `responsaveis_tecnicos` - ART/CREA vinculados ao processo."""
    __tablename__ = "responsaveis_tecnicos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    processo_id: Mapped[int] = mapped_column(ForeignKey("processos_licenciamento.id"), index=True)
    nome: Mapped[str | None] = mapped_column(String(200))
    registro_crea: Mapped[str | None] = mapped_column(String(60))
    numero_art: Mapped[str | None] = mapped_column(String(60), index=True)
    empresa: Mapped[str | None] = mapped_column(String(200))

    processo: Mapped["ProcessoLicenciamento"] = relationship(back_populates="responsaveis")


class TabelaURM(Base):
    """Tabela `tabela_urm` - matriz de taxas (grupo x porte/faixa x potencial x fase).

    Espelha o dicionário MATRIZ_URM do AgenteFinanceiro, permitindo atualizar os
    valores por legislação sem alterar código (basta semear/atualizar esta tabela).
    """
    __tablename__ = "tabela_urm"
    __table_args__ = (
        UniqueConstraint("grupo_atividade", "porte_ou_faixa", "potencial_poluidor",
                         "fase_licenca", name="uq_urm_composicao"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    grupo_atividade: Mapped[str] = mapped_column(String(40), default="GERAL")
    porte_ou_faixa: Mapped[str] = mapped_column(String(40))   # 'MEDIO' ou '0 a 5 ha'
    potencial_poluidor: Mapped[str | None] = mapped_column(String(20))  # nulo p/ faixas/ERB
    fase_licenca: Mapped[str] = mapped_column(String(12))     # LP, LI, LO, AUTORIZACAO...
    valor_urm: Mapped[float] = mapped_column(Float)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# Alias em português sem underscore para leitura das especificações
EmpreendimentoModelo = Empreendimento
ProcessoModelo = ProcessoLicenciamento
DocumentosAnexados = DocumentoAnexado
TabelaURMModelo = TabelaURM
