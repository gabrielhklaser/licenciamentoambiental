# -*- coding: utf-8 -*-
"""
Ecossistema multiagentes para triagem de licenciamento ambiental municipal.
Prefeitura de Campo Bom - Secretaria Municipal do Meio Ambiente.

Submódulos:
    - parser_formulario      (Fase 1): Motor de Ingestão e Estruturação de Dados
    - models / banco         (Fase 2): Persistência PostgreSQL/PostGIS (SQLAlchemy)
    - agente_administrativo  (Fase 2): Auditoria administrativa (checklist + bloqueios)
    - agente_financeiro      (Fase 2): Cálculo de taxas em URMs
    - auditor_tecnico        (Fase 3): Auditoria técnica híbrida (determinística + LLM)
    - esquemas_tecnicos      (Fase 3): Contratos de dados Pydantic
    - gerador_oficios        (Fase 4): Geração de minutas de ofício (.docx)
"""

__version__ = "1.0.0"
