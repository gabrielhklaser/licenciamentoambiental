# -*- coding: utf-8 -*-
"""
FASE 2 - Agente Administrativo (auditoria de bloqueios e checklist)
====================================================================

Agente DETERMINÍSTICO baseado em regras. Recebe o JSON do parser (Fase 1) e a
lista de documentos efetivamente anexados ao processo (na prototipagem a
submissão é simulada com uma lista de nomes) e realiza uma auditoria rígida:

Hard Constraints (bloqueio imediato do processo):
    - CPF/CNPJ do empreendedor ausente;
    - Matrícula do imóvel ausente;
    - Anotação de Responsabilidade Técnica (ART) ausente.

Conferência de checklist:
    - Cruza a lista de documentos exigidos (já desduplicada para LIR/LOR na
      Fase 1) com os arquivos anexados, usando normalização + similaridade de
      strings para tolerar pequenas variações de nomenclatura;
    - Retorna, para cada documento exigido, o status "ok" ou "pendente".
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Optional

import logging

from licenciamento.identificador_documentos import IdentificadorDocumentos

logger = logging.getLogger("licenciamento.agente_administrativo")


class AgenteAdministrativo:
    """Auditor administrativo determinístico (regras de bloqueio + checklist)."""

    LIMIAR_SIMILARIDADE = 0.82       # tolerância de nomenclatura entre exigido x anexado
    LIMIAR_PALAVRAS_CHAVE = 0.5      # % mínima de palavras-chave do exigido presentes no anexo

    # Valor extraído do CONTEÚDO de cada tipo de documento para completar
    # campos críticos ausentes no formulário (o anexo é a fonte da verdade)
    VALOR_NO_DOCUMENTO: dict[str, tuple[list[str], re.Pattern, str]] = {
        "MATRICULA_IMOVEL": (
            ["empreendimento", "matricula_imovel"],
            re.compile(r"MATR[IÍ]CULA\s*N[ºo°.]?\s*([\d.\-]{4,})", re.I),
            "Matrícula do imóvel"),
        "CNPJ": (
            ["empreendedor", "cpf_cnpj"],
            re.compile(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}"),
            "CPF/CNPJ do empreendedor"),
        "ART": (
            ["responsavel_tecnico", "registro_art"],
            re.compile(r"ART\s*N?[ºo°.]?\s*([A-Z0-9/\-]{6,})", re.I),
            "Anotação de Responsabilidade Técnica (ART)"),
    }

    # Hard constraints: campo no JSON -> rótulo humano para o relatório
    CAMPOS_CRITICOS: list[tuple[list[str], str]] = [
        (["empreendedor", "cpf_cnpj"], "CPF/CNPJ do empreendedor"),
        (["empreendimento", "matricula_imovel"], "Matrícula do imóvel"),
        (["responsavel_tecnico", "registro_art"],
         "Anotação de Responsabilidade Técnica (ART)"),
    ]

    # ------------------------------------------------------------------
    @staticmethod
    def _normalizar(texto: Optional[str]) -> str:
        """Minúsculas, sem acentos e sem pontuação (comparação tolerante)."""
        if not texto:
            return ""
        texto = unicodedata.normalize("NFKD", texto)
        texto = "".join(c for c in texto if not unicodedata.combining(c))
        texto = re.sub(r"[^\w\s]", " ", texto.lower())
        return re.sub(r"\s+", " ", texto).strip()

    # CNPJ no texto OCR: tolera espaços quebrados e tokens colados
    # ("CNPJ12.345.678/0001-95MATRICULA", "N. INSCRICAO 12 345 678 0001 95")
    RE_CNPJ_TEXTO = re.compile(
        r"\d{2}\s*[.,]?\s*\d{3}\s*[.,]?\s*\d{3}\s*[/.]?\s*\d{4}\s*-?\s*\d{2}")

    @classmethod
    def _conferir_cnpj_matricula(cls, dados_processo: dict,
                                 anexados: list[str],
                                 textos: dict[str, str]) -> Optional[dict]:
        """Confere o CNPJ do empreendedor (formulário HTML) com o NÚMERO DE
        INSCRIÇÃO da matrícula anexada (primeira linha/coluna do documento;
        PDFs escaneados chegam aqui já com o texto extraído via OCR).
        Status: CONFERE | DIVERGENTE | NAO_ENCONTRADO | ANEXO_NAO_LEGIVEL."""
        try:
            exigidos = (dados_processo.get("documentos_exigidos")
                        .get("lista_deduplicada") or [])
            cnpj_form = re.sub(r"\D", "",
                               (dados_processo.get("empreendedor")
                                .get("cpf_cnpj") or ""))
            if len(cnpj_form) != 14:  # só CNPJ (CPF não consta na matrícula)
                return None
            # localiza o anexo da matrícula: 1º pelo checklist, senão pelo nome
            anexo_mat = None
            for exigido in exigidos:
                if "matricula" in cls._normalizar(exigido):
                    anexo_mat = cls._documento_estah_anexado(exigido, anexados)
                    if anexo_mat:
                        break
            if not anexo_mat:
                anexo_mat = next((a for a in anexados
                                  if "matricul" in cls._normalizar(a)), None)
            if not anexo_mat:
                return None
            texto = textos.get(anexo_mat) or ""
            if not texto.strip():
                return {"anexo": anexo_mat, "cnpj_formulario": cnpj_form,
                        "cnpj_encontrado": None, "status": "ANEXO_NAO_LEGIVEL",
                        "detalhe": ("matrícula sem texto legível (escaneada e "
                                    "OCR indisponível) - conferir manualmente")}
            # o CNPJ da matrícula vem sob o rótulo 'Número de Inscrição'
            # ou no corpo (ex.: PROPRIETÁRIA: ... inscrita no CNPJ sob nº ...)
            priorizados: set[str] = set()
            m_rot = re.search(r"numero\s+de\s+inscric[aã]o(.{0,120})", texto,
                              re.I | re.S)
            if m_rot:
                priorizados = {re.sub(r"\D", "", m.group(0)) for m in
                               cls.RE_CNPJ_TEXTO.finditer(m_rot.group(1))}
                priorizados.discard("")
            encontrados = {re.sub(r"\D", "", m.group(0))
                           for m in cls.RE_CNPJ_TEXTO.finditer(texto)}
            encontrados.discard("")
            # Também confere o documento específico de CNPJ anexado (se houver)
            anexo_cnpj = next((a for a in anexados
                               if re.search(r"\bcnpj\b|cart[aã]o.*cnpj|comprovante.*cnpj",
                                            cls._normalizar(a))), None)
            confere_doc_cnpj = False
            if anexo_cnpj and textos.get(anexo_cnpj):
                t_cnpj = textos.get(anexo_cnpj) or ""
                cnpjs_doc = {re.sub(r"\D", "", m.group(0))
                             for m in cls.RE_CNPJ_TEXTO.finditer(t_cnpj)}
                if cnpj_form in cnpjs_doc:
                    confere_doc_cnpj = True

            # Se o CNPJ do formulário está nos priorizados, nos encontrados ou no doc CNPJ
            if cnpj_form in priorizados or cnpj_form in encontrados:
                status = "CONFERE"
                cnpj_enc = cnpj_form
                detalhe = "número de inscrição / CNPJ confere na matrícula"
                if confere_doc_cnpj:
                    detalhe += " e no Comprovante de Inscrição (Cartão CNPJ)"
            elif confere_doc_cnpj:
                status = "CONFERE"
                cnpj_enc = cnpj_form
                detalhe = f"CNPJ confere com o Comprovante de Inscrição (Cartão CNPJ: `{anexo_cnpj}`)"
            elif priorizados:
                status = "DIVERGENTE"
                cnpj_enc = sorted(priorizados)[0]
                detalhe = "CNPJ da matrícula DIFERENTE do formulário"
            elif encontrados:
                status = "DIVERGENTE"
                cnpj_enc = sorted(encontrados)[0]
                detalhe = "CNPJ da matrícula DIFERENTE do formulário"
            else:
                status = "NAO_ENCONTRADO"
                cnpj_enc = None
                detalhe = "CNPJ não localizado no texto da matrícula"

            return {"anexo": anexo_mat, "cnpj_formulario": cnpj_form,
                    "cnpj_encontrado": cnpj_enc,
                    "status": status,
                    "detalhe": detalhe}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Falha na conferência de CNPJ: %s", exc)
            return None

    def _conferir_responsaveis_etapas(self, dados_processo: dict,
                                      anexados: list[str],
                                      textos: dict[str, str]) -> list[dict[str, Any]]:
        """Seção 4.3 do formulário ('demais responsáveis técnicos de diferentes
        etapas'): cada ART/RTT listada deve ser encontrada nos documentos
        apresentados, com o NOME ou o REGISTRO do profissional batendo."""
        resultado: list[dict[str, Any]] = []
        try:
            responsaveis = list(dados_processo.get("responsaveis_etapas") or [])
            # TAMBÉM o responsável técnico PRINCIPAL (item 14 do formulário):
            # toda ART/RTT declarada deve ser conferida (nº + nome)
            rt_principal = dados_processo.get("responsavel_tecnico") or {}
            art_principal = (rt_principal.get("registro_art") or "").strip()
            if art_principal:
                arts_ja_listadas = {
                    re.sub(r"\D", "", p.get("art_rtt") or "")
                    for p in responsaveis}
                if re.sub(r"\D", "", art_principal) not in arts_ja_listadas:
                    responsaveis.append({
                        "nome": rt_principal.get("nome"),
                        "registro": rt_principal.get("registro_crea"),
                        "art_rtt": art_principal,
                        "etapa": "Responsável Técnico principal (item 14)",
                    })
            for prof in responsaveis:
                art = (prof.get("art_rtt") or "").strip()
                m_num = re.search(r"([\w./\-]{5,})\s*$", art)
                numero = re.sub(r"\D", "", m_num.group(1)) if m_num else ""
                nome = (prof.get("nome") or "").strip()
                registro = (prof.get("registro") or "").strip()
                tokens = [t for t in self._normalizar(nome).split() if len(t) >= 4]
                reg_digitos = re.sub(r"\D", "", registro)
                achou_anexo, achou_nivel = None, None
                for anexo in anexados:
                    texto = textos.get(anexo) or ""
                    if not texto:
                        continue
                    tem_art = bool(numero) and numero in re.sub(r"\D", "", texto)
                    if not tem_art:
                        continue
                    texto_n = self._normalizar(texto)
                    tem_nome = bool(tokens) and all(t in texto_n
                                                    for t in tokens[:3])
                    tem_reg = (bool(reg_digitos)
                               and reg_digitos in re.sub(r"\D", "", texto))
                    if tem_nome or tem_reg:
                        achou_anexo = anexo
                        achou_nivel = ("ART/RTT + nome e registro conferidos"
                                       if (tem_nome and tem_reg)
                                       else ("ART/RTT + nome conferido" if tem_nome
                                             else "ART/RTT + registro conferido"))
                        break
                    if achou_nivel is None:
                        achou_anexo, achou_nivel = anexo, \
                            "somente o nº da ART/RTT (nome/registro não batem)"
                resultado.append({
                    "profissional": nome or "(sem nome no formulário)",
                    "registro": registro or None,
                    "art_rtt": art or None,
                    "etapa": prof.get("etapa") or "",
                    "encontrado": achou_nivel is not None
                    and "somente" not in (achou_nivel or ""),
                    "anexo": achou_anexo,
                    "nivel": achou_nivel,
                })
        except Exception as exc:  # noqa: BLE001
            logger.warning("Falha ao conferir responsáveis das etapas: %s", exc)
        return resultado

    def _completar_campos_criticos(self, dados_processo: dict,
                                   anexados: list[str],
                                   textos: dict[str, str],
                                   identificador: IdentificadorDocumentos) -> list[str]:
        """Recupera campos críticos ausentes no formulário a partir dos anexos:
        reconhece o documento (nome -> aprendido -> conteúdo) e extrai o valor
        do próprio documento. Devolve os avisos de completamento."""
        completados: list[str] = []
        for tipo, (caminho, padrao, rotulo) in self.VALOR_NO_DOCUMENTO.items():
            valor: Any = dados_processo
            try:
                for chave in caminho:
                    valor = (valor or {}).get(chave)
            except AttributeError:
                valor = None
            if valor and str(valor).strip():
                continue  # o formulário já trouxe o dado
            for anexo in anexados:
                idf = identificador.identificar(anexo, textos.get(anexo))
                if idf.get("tipo") != tipo:
                    continue
                m = padrao.search(textos.get(anexo) or "")
                if not m:
                    continue
                extraido = m.group(0) if tipo == "CNPJ" else m.group(1)
                destino = dados_processo
                for chave in caminho[:-1]:
                    destino = destino.setdefault(chave, {})
                destino[caminho[-1]] = extraido
                completados.append(
                    f"{rotulo} não lido no formulário - COMPLETADO a partir do "
                    f"anexo '{anexo}' (documento reconhecido pelo {idf.get('via')}).")
                if tipo == "ART" and \
                        dados_processo.get("status_triagem") == "bloqueado_sem_art":
                    dados_processo["status_triagem"] = "liberado_triagem"
                break
        return completados

    @classmethod
    def _documento_estah_anexado(cls, documento_exigido: str, anexados: list[str]) -> Optional[str]:
        """Verifica se o documento exigido corresponde a algum arquivo anexado.

        Retorna o nome do arquivo correspondente ou None. Combina:
            1) contenção de sub-cadeia normalizada (com e sem prefixos/numeração);
            2) similaridade de strings (SequenceMatcher >= LIMIAR_SIMILARIDADE);
            3) siglas específicas do documento (PGRS, PCA, EIV...) presentes no anexo;
            4) casamento por palavras-chave relevantes (>= LIMIAR_PALAVRAS_CHAVE).
        """
        exigido_n = cls._normalizar(documento_exigido)
        # o FORMULÁRIO .htm/.html só atende à exigência do próprio formulário
        exigencia_de_formulario = "formulario" in exigido_n
        exigido_limpo = re.sub(r"^\d+\s*[.)]\s*", "", exigido_n).strip()

        # Palavras-chave que definem o documento (ignora artigos/preposições e boilerplate)
        stopwords = {"desta", "deste", "dessas", "desses", "elaborado", "acordo",
                     "secretaria", "responsavel", "tecnico", "habilitado", "acompanhado",
                     "apresentar", "copia", "para", "como", "pelo", "pela"}
        palavras = {p for p in exigido_limpo.split() if len(p) >= 4 and p not in stopwords}

        # siglas no texto original (ex.: 'Plano de Gerenciamento ... (PGRS)')
        siglas = {s.lower() for s in re.findall(r"\b[A-Z]{2,6}\b", documento_exigido or "")}
        eh_exigencia_art = bool(re.search(
            r"^\s*\d*\s*[.)]?\s*(?:c[óo]pia\s+da\s+)?(?:art|rrt)\b|"
            r"anota[çc][ãa]o\s+de\s+responsabilidade\s+t[ée]cnica\s*(?:\(art\))?$|"
            r"art\s+de\s+profissional", exigido_n))
        if not eh_exigencia_art:
            siglas.discard("art")
            siglas.discard("rrt")
            siglas.discard("tr")

        melhor_anexo = None
        melhor_score = 0.0

        for anexo in anexados:
            anexo_n = cls._normalizar(anexo)
            if not anexo_n:
                continue
            if (not exigencia_de_formulario
                    and anexo.lower().endswith((".htm", ".html"))):
                continue  # o formulário nao e documento apresentado

            anexo_limpo = re.sub(r"^[_\-°º\d\s.]+", "", anexo_n).strip()

            # 1) Contenção direta exata
            if (exigido_n in anexo_n or anexo_n in exigido_n
                    or (exigido_limpo and anexo_limpo and (exigido_limpo in anexo_limpo or anexo_limpo in exigido_limpo))):
                return anexo

            # 2) Similaridade e palavras-chave
            ratio = max(SequenceMatcher(None, exigido_n, anexo_n).ratio(),
                        SequenceMatcher(None, exigido_limpo, anexo_limpo).ratio())
            sigla_presente = bool(siglas and any(s in anexo_n for s in siglas))
            comuns = sum(1 for p in palavras if p in anexo_n or p in anexo_limpo) if palavras else 0
            frac = comuns / len(palavras) if palavras else 0.0

            score = ratio * 0.4 + frac * 0.6 + (0.35 if sigla_presente else 0.0)
            if (ratio >= cls.LIMIAR_SIMILARIDADE or frac >= cls.LIMIAR_PALAVRAS_CHAVE
                    or (sigla_presente and frac >= 0.25)):
                if score > melhor_score:
                    melhor_score = score
                    melhor_anexo = anexo

        return melhor_anexo

    # ------------------------------------------------------------------
    def auditar(self, dados_processo: dict,
                documentos_anexados: Optional[list[str]] = None,
                textos_anexados: Optional[dict[str, str]] = None,
                identificador: Optional[IdentificadorDocumentos] = None) -> dict[str, Any]:
        """Executa a auditoria administrativa completa.

        Args:
            dados_processo: JSON estruturado gerado pelo FormularioParser (Fase 1).
            documentos_anexados: lista dos arquivos submetidos ao processo.
            textos_anexados: {nome_arquivo: texto_extraído} para o
                reconhecimento pelo CONTEÚDO quando o nome não basta.
            identificador: IdentificadorDocumentos (default: instância própria
                com o aprendizado persistente em config/).

        Returns:
            Dicionário com:
                status_geral: "APROVADO" | "PENDENTE" | "BLOQUEADO"
                bloqueios: hard constraints violadas (impossibilitam a análise)
                documentos_ok / documentos_pendentes: cruzamento do checklist
                origem_ok: como cada documento foi reconhecido
                    (nome/conteúdo/aprendido) - transparência ao licenciador
                avisos: anexos não correspondidos + campos críticos completados
                    a partir de documentos anexados
        """
        documentos_anexados = list(documentos_anexados or [])
        textos = textos_anexados or {}
        identificador = identificador or IdentificadorDocumentos()
        bloqueios: list[str] = []
        avisos: list[str] = []

        # 0) Campos críticos ausentes no FORMULÁRIO são recuperados dos
        #    ANEXOS (nome -> aprendido -> conteúdo) - ex.: matrícula cujo
        #    rótulo no formulário não foi lido, mas o documento está anexado
        avisos.extend(self._completar_campos_criticos(
            dados_processo, documentos_anexados, textos, identificador))

        # 1) Hard constraints --------------------------------------------
        for caminho, rotulo in self.CAMPOS_CRITICOS:
            valor: Any = dados_processo
            try:
                for chave in caminho:
                    valor = (valor or {}).get(chave)
            except AttributeError:
                valor = None
            if not valor or not str(valor).strip():
                bloqueios.append(f"Falta {rotulo} (dado crítico ausente no processo).")
                logger.warning("Hard constraint violada: %s", rotulo)

        # Status de triagem vindo do parser (ART ausente já marca o bloqueio)
        if dados_processo.get("status_triagem") == "bloqueado_sem_art":
            msg = "Processo sem ART (Anotação de Responsabilidade Técnica) válida."
            if not any("ART" in b for b in bloqueios):
                bloqueios.append(msg)

        # 2) Conferência de checklist ------------------------------------
        exigidos: list[str] = (dados_processo.get("documentos_exigidos", {}) or {}) \
            .get("lista_deduplicada", [])

        documentos_ok: list[str] = []
        documentos_pendentes: list[dict[str, str]] = []
        anexados_reconhecidos: set[str] = set()
        anexados_disponiveis = [a for a in documentos_anexados]

        # Casamento greedy 1-para-1: cada anexo atende apenas UMA exigência
        # (evita que um mesmo arquivo, ex. 'pca_...pdf', cubra exigências distintas
        # que apenas mencionam a mesma sigla).
        origem_ok: dict[str, dict[str, str]] = {}
        for exigido in exigidos:
            anexo_correspondente = None
            via_reconhecimento = "nome"
            candidatos = [a for a in anexados_disponiveis
                          if a not in anexados_reconhecidos]
            anexo_correspondente = self._documento_estah_anexado(exigido, candidatos)
            if not anexo_correspondente:
                # FALLBACK: o nome não casou - reconhece pelo CONTEÚDO ou pelo
                # APRENDIZADO (nomes alterados, ex.: '°Cópia da matrícula...')
                tipo_exigido = IdentificadorDocumentos.tipo_da_exigencia(exigido)
                if tipo_exigido:
                    for anexo in candidatos:
                        idf = identificador.identificar(anexo, textos.get(anexo))
                        if idf.get("tipo") == tipo_exigido:
                            anexo_correspondente = anexo
                            via_reconhecimento = idf.get("via") or "conteudo"
                            break
            if anexo_correspondente:
                documentos_ok.append(exigido)
                anexados_reconhecidos.add(anexo_correspondente)
                if via_reconhecimento != "nome":
                    origem_ok[exigido] = {"anexo": anexo_correspondente,
                                          "via": via_reconhecimento}
            else:
                documentos_pendentes.append({
                    "documento": exigido,
                    "justificativa": "Documento exigido não localizado entre os anexos do processo.",
                })

        for anexo in documentos_anexados:
            if anexo not in anexados_reconhecidos:
                avisos.append(f"Anexo '{anexo}' não corresponde a nenhuma exigência do checklist.")

        # 2.4) Conferência do CNPJ: nº de inscrição no anexo da MATRÍCULA
        # (frequentemente PDF escaneado - lido via OCR) x formulário HTML
        conferencia_cnpj = self._conferir_cnpj_matricula(
            dados_processo, documentos_anexados, textos)

        # 2.5) Conferência dos profissionais das etapas (seção 4.3):
        # ART/RTT de cada responsável procurada nos documentos apresentados
        conferencia_responsaveis = self._conferir_responsaveis_etapas(
            dados_processo, documentos_anexados, textos)
        for conf in conferencia_responsaveis:
            if not conf["encontrado"]:
                avisos.append(
                    f"ART/RTT {conf['art_rtt']} ({conf['profissional']}) NÃO "
                    f"confirmada nos documentos apresentados - conferir o "
                    f"responsável da etapa "
                    f"'{conf.get('etapa') or 'não informada'}'.")

        # 3) Status consolidado -------------------------------------------
        if bloqueios:
            status_geral = "BLOQUEADO"
        elif documentos_pendentes:
            status_geral = "PENDENTE"
        else:
            status_geral = "APROVADO"

        resultado = {
            "agente": "AgenteAdministrativo",
            "status_geral": status_geral,
            "bloqueios": bloqueios,
            "documentos_ok": documentos_ok,
            "documentos_pendentes": documentos_pendentes,
            "origem_ok": origem_ok,
            "conferencia_responsaveis": conferencia_responsaveis,
            "conferencia_cnpj": conferencia_cnpj,
            "avisos": avisos,
            "resumo": {
                "total_exigidos": len(exigidos),
                "total_ok": len(documentos_ok),
                "total_pendentes": len(documentos_pendentes),
                "total_anexados_informados": len(documentos_anexados),
            },
        }
        logger.info("Auditoria administrativa concluída: %s", status_geral)
        return resultado
