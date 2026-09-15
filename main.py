# -*- coding: utf-8 -*-
"""
main.py - Demonstração do pipeline completo (Fases 1 + 2 + 3 + 4)
==================================================================

Roteiro do script (conforme especificação do projeto):

    1. FASE 1 - FormularioParser: lê o formulário HTML fictício de uma
       "Licença de Operação e Regularização (LOR)" de porte MÉDIO e potencial
       poluidor ALTO e gera o JSON estruturado (com desduplicação LP+LI+LO);
    2. FASE 2 - AgenteAdministrativo: audita hard constraints (CPF/CNPJ,
       matrícula, ART) e cruza o checklist exigido x anexados;
       AgenteFinanceiro: calcula a taxa final em URMs (LOR = LP + LI + LO);
    3. FASE 3 - AuditorTecnico: valida os laudos (RFO com densidade de
       2.000 mudas/ha deve gerar status "PENDENTE" com justificativa matemática);
    4. FASE 4 - GeradorOficios: consolida as pendências em uma minuta .docx
       e persiste o processo no banco (SQLite do protótipo).

Uso:  python main.py
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from licenciamento.agente_administrativo import AgenteAdministrativo
from licenciamento.agente_financeiro import AgenteFinanceiro
from licenciamento.auditor_tecnico import AuditorTecnico
from licenciamento.banco import obter_engine, salvar_processo, semear_tabela_urm
from licenciamento.gerador_oficios import GeradorOficios
from licenciamento.parser_formulario import FormularioParser
from sqlalchemy.orm import Session

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
logging.getLogger("licenciamento").setLevel(logging.WARNING)

LINHA = "=" * 78


def titulo(texto: str) -> None:
    print(f"\n{LINHA}\n  {texto}\n{LINHA}")


def main() -> None:
    raiz = Path(__file__).parent
    formulario = raiz / "exemplos" / "formulario_LOR_medio_alto.htm"

    # =====================================================================
    titulo("FASE 1 - MOTOR DE INGESTÃO E ESTRUTURAÇÃO (FormularioParser)")
    # =====================================================================
    parser = FormularioParser(caminho_arquivo=str(formulario))
    dados = parser.gerar_json()

    print(f"Arquivo origem .......: {Path(dados['arquivo_origem']).name}")
    print(f"Status triagem .......: {dados['status_triagem']}")
    print(f"Empreendedor .........: {dados['empreendedor']['nome_razao_social']} "
          f"({dados['empreendedor']['cpf_cnpj']})")
    emp = dados["empreendimento"]
    print(f"Empreendimento .......: {emp['nome_empreendimento']}")
    print(f"Ramo/CODRAM ..........: {emp['ramo_atividade']} | CODRAM {emp['codram']}")
    print(f"Porte/Potencial ......: {emp['porte']} / {emp['potencial_poluidor']}")
    print(f"Coordenadas (SIRGAS).: {emp['coordenadas']}")
    print(f"Pleito ...............: {dados['pleito']['tipo_licenca']} "
          f"(fases: {' + '.join(dados['pleito']['fases_componentes'])})")
    print(f"Responsável/ART ......: {dados['responsavel_tecnico']['nome']} | "
          f"ART {dados['responsavel_tecnico']['registro_art']}")
    estat = dados["documentos_exigidos"]["estatisticas"]
    print(f"Checklist deduplicado : {estat['total_bruto']} itens brutos -> "
          f"{estat['total_deduplicado']} itens (removidos {len(estat['removidos'])} "
          f"redundâncias entre LP/LI/LO)")
    for removido in estat["removidos"]:
        print(f"    [desduplicado] '{removido['documento']}' ~ '{removido['similar_a']}' "
              f"(similaridade {removido['similaridade']})")

    # =====================================================================
    titulo("FASE 2 - AGENTE ADMINISTRATIVO (hard constraints + checklist)")
    # =====================================================================
    # Prototipagem: simulação da submissão de arquivos com uma lista
    # (deliberadamente SEM dois documentos - certificado do PCA e alvará dos
    # Bombeiros - para evidenciar pendências na auditoria administrativa)
    documentos_anexados = [
        "formulario_enquadramento_assinado.pdf",
        "copia_cpf_cnpj.pdf",
        "matricula_imovel_atualizada.pdf",
        "planta_localizacao.pdf",
        "eiv_estudo_impacto_vizinhanca.pdf",
        "art_responsavel_tecnico.pdf",
        "copia_licenca_previa.pdf",
        "pca_plano_controle_ambiental.pdf",
        "projeto_executivo_sistema_tratamento_efluentes.pdf",
        "pgrs.pdf",
        "licenca_supressao_vegetacao.pdf",
        "rca_relatorio_controle_ambiental.pdf",
        "laudo_sistema_tratamento_efluentes.pdf",
    ]
    agente_admin = AgenteAdministrativo()
    resultado_admin = agente_admin.auditar(dados, documentos_anexados)

    print(f"STATUS ADMINISTRATIVO: {resultado_admin['status_geral']}")
    for bloqueio in resultado_admin["bloqueios"]:
        print(f"  [BLOQUEIO] {bloqueio}")
    print(f"Checklist: {resultado_admin['resumo']['total_ok']}/"
          f"{resultado_admin['resumo']['total_exigidos']} documentos OK")
    for pendente in resultado_admin["documentos_pendentes"]:
        print(f"  [PENDENTE] {pendente['documento']}")

    # =====================================================================
    titulo("FASE 2 - AGENTE FINANCEIRO (cálculo da taxa em URMs)")
    # =====================================================================
    agente_fin = AgenteFinanceiro()
    resultado_fin = agente_fin.calcular_do_parser(dados)

    print(f"Tipo de licença ......: {resultado_fin['tipo_licenca']}")
    print(f"Grupo de atividade ...: {resultado_fin['grupo_atividade']}")
    print(f"Porte/Faixa ..........: {resultado_fin['porte_ou_faixa']}")
    for fase, valor in resultado_fin["composicao_fases"].items():
        print(f"  Taxa {fase}: {valor:>10,.2f} URMs")
    print(f">>> TAXA FINAL (LOR = LP+LI+LO): {resultado_fin['total_urm']:,.2f} URMs")
    selo_cal = "" if resultado_fin.get('tabela_revisada') else " [rascunho - conferir Manual]"
    print(f"Fonte da tabela .....: {resultado_fin.get('fonte_tabela')}{selo_cal}")
    print(f"(regra aplicada: {resultado_fin.get('regra_aplicada', '-')})")

    # =====================================================================
    titulo("FASE 3 - AUDITOR TÉCNICO (TRs: determinístico + semântico)")
    # =====================================================================
    auditor = AuditorTecnico()  # provedor padrão offline (heurístico local)

    laudos: dict[str, str] = {}
    pasta_laudos = raiz / "exemplos" / "laudos"
    for arquivo in sorted(pasta_laudos.glob("*")):
        if arquivo.suffix.lower() in (".pdf", ".txt"):
            laudos[arquivo.name] = AuditorTecnico.extrair_texto(
                arquivo.name, arquivo.read_bytes())

    resultados_tecnicos = auditor.auditar_lote(laudos)
    for resultado in resultados_tecnicos:
        print(f"\nDocumento: {resultado.documento_analisado} | TR: {resultado.norma_tr} "
              f"| Motor: {resultado.origem.value}")
        print(f"  STATUS: {resultado.status.value}")
        for item in resultado.itens_reprovados:
            print(f"  [REPROVADO] {item}")
        if resultado.metricas:
            print(f"  Métricas: {resultado.metricas}")

    # Cenário de teste exigido na especificação: RFO com densidade de 2.000 mudas/ha
    titulo("FASE 3 - TESTE ESPECÍFICO: RFO com densidade 2.000 mudas/ha (deve dar PENDENTE)")
    from licenciamento.esquemas_tecnicos import MetricasRFO
    rfo_teste = MetricasRFO(nativos_suprimidos=120, exoticos_suprimidos=30,
                            mudas_nativas_propostas=1900, mudas_exoticas_propostas=90,
                            densidade_proposta_mudas_ha=2000.0)
    resultado_rfo = auditor.validar_rfo("laudo_rfo_teste_mock.pdf", rfo_teste)
    print(json.dumps(json.loads(resultado_rfo.model_dump_json()), ensure_ascii=False, indent=2))

    # =====================================================================
    titulo("FASE 4 - GERADOR DE OFÍCIO + PERSISTÊNCIA NO BANCO")
    # =====================================================================
    gerador = GeradorOficios()
    caminho_oficio = raiz / "saida_oficio_exemplo.docx"
    gerador.gerar_oficio_complementacao(
        dados_processo=dados,
        resultado_admin=resultado_admin,
        resultados_tecnicos=resultados_tecnicos,
        numero_oficio="042/2026",
        prazo_dias=30,
        caminho_saida=str(caminho_oficio),
    )
    print(f"Minuta de ofício gerada: {caminho_oficio.name}")

    # Persistência (protótipo: SQLite; produção: DATABASE_URL -> PostgreSQL/PostGIS)
    engine = obter_engine()
    with Session(engine) as sessao:
        semear_tabela_urm(sessao, AgenteFinanceiro.MATRIZ_URM)
    id_processo = salvar_processo(
        dados, resultado_admin, resultado_fin, documentos_anexados,
        numero_processo="2026/001234", engine=engine)
    print(f"Processo persistido no banco de dados (id = {id_processo}).")

    print(f"\n{LINHA}\nPipeline concluído com sucesso.\n{LINHA}")


if __name__ == "__main__":
    main()
