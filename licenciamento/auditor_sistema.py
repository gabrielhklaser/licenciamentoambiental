"""AGENTE INDEPENDENTE DE AUDITORIA DO SISTEMA.

Agente que audita o PRÓPRIO sistema de licenciamento em 4 movimentos:

  1) DETECÇÃO - bateria de verificações independentes:
       COMP  compilação de todos os .py do projeto;
       TEST  bateria completa de testes (pytest);
       CFG   integridade/consistência dos JSON de configuração;
       PAR   parser nas fixtures (sem crash, campos saneados);
       PLE   pleitos por tipo (LP/AUTORIZACAO/PRAD/Renovação) + taxas;
       AMB   ambiente: pacotes do requirements.txt + OCR operante;
       APP   integração do frontend (tema, métricas, config do preview);
       HIG   higiene de arquivos (BOM, newline final).

  2) DUPLA CHECAGEM - todo erro detectado é RE-VERIFICADO numa 2ª passada
     isolada (re-execução do check de origem); só segue CONFIRMADO o que
     persiste nas duas passadas (detecções instáveis viram FALSO_POSITIVO).

  3) CORREÇÃO - ataca DIRETAMENTE cada erro confirmado que tenha correção
     automática SEGURA (pacotes ausentes, conflito do OpenCV, config.toml do
     preview, BOM/newline). Após corrigir, RE-EXECUTA o check para validar a
     correção. Erros que exigem mudança de código viram PENDÊNCIA documentada.

  4) RELATÓRIO - compila tudo em auditoria/relatorio_auditoria.{json,md}.

Uso:
    .venv/bin/python -m licenciamento.auditor_sistema            # completa
    .venv/bin/python -m licenciamento.auditor_sistema --rapida   # sem pytest
"""
from __future__ import annotations

import ast
import json
import logging
import py_compile
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)
RAIZ = Path(__file__).resolve().parents[1]


@dataclass
class Erro:
    """Um achado do auditor (erro real, dupla-checado, com correção)."""
    id: str                     # ex.: 'AMB-OCR', 'CFG-TIPOS', 'PAR-189'
    severidade: str             # 'alta' | 'media' | 'baixa'
    componente: str             # módulo/arquivo afetado
    descricao: str
    evidencia: str
    correcao: str               # o que será feito / foi feito
    corrigivel: bool = False    # possui correção automática SEGURA?
    confirmado: bool = False    # só após a DUPLA CHECAGEM
    corrigido: bool = False
    status: str = "DETECTADO"   # DETECTADO>CONFIRMADO>CORRIGIDO|PENDENTE|FALSO_POSITIVO


# config.toml canônico do preview (uploads passando pelo proxy exigem
# CORS/XSRF liberados - ver histórico 'AxiosError 403')
CONFIG_TOML_CANONICO = """\
# Configuração do Streamlit para o dashboard de licenciamento ambiental
#
# [server] enableCORS/enableXSRFProtection = false:
#   NECESSÁRIO atrás do proxy do preview (https://{porta}-{sandbox}.e2b.app).
[server]
headless = true
enableCORS = false
enableXsrfProtection = false

[browser]
gatherUsageStats = false

[theme]
primaryColor = "#2e7d32"
base = "light"
"""

# pacote requirements.txt -> nome de import (subconjunto auditado)
IMPORTES = {
    "beautifulsoup4": "bs4", "lxml": "lxml", "SQLAlchemy": "sqlalchemy",
    "geoalchemy2": "geoalchemy2", "pydantic": "pydantic", "pypdf": "pypdf",
    "streamlit": "streamlit", "pandas": "pandas", "python-docx": "docx",
    "openpyxl": "openpyxl", "PyMuPDF": "pymupdf",
    "rapidocr-onnxruntime": "rapidocr_onnxruntime",
    "opencv-python-headless": "cv2",
}


class AuditorSistema:
    """Agente independente: detecta -> dupla-checa -> corrige -> relata."""

    def __init__(self, com_testes: bool = True, raiz: Path = RAIZ):
        self.com_testes = com_testes
        self.raiz = raiz
        self.erros: list[Erro] = []
        self.log: list[str] = []

    # ==================================================================
    # 1) DETECÇÃO (bateria de verificações)
    # ==================================================================
    def _checks(self) -> list[tuple[str, Callable[[], list[Erro]]]]:
        checks: list[tuple[str, Callable[[], list[Erro]]]] = [
            ("COMP", self.verificar_compilacao),
            ("CFG", self.verificar_configs_json),
            ("PAR", self.verificar_parser_fixtures),
            ("PLE", self.verificar_pleitos_e_taxas),
            ("AMB", self.verificar_ambiente),
            ("CONF", self.verificar_conferencias_documentais),
            ("SEG", self.verificar_seguranca),
            ("APP", self.verificar_integracao_app),
            ("HIG", self.verificar_higiene_arquivos),
        ]
        if self.com_testes:
            checks.insert(1, ("TEST", self.verificar_testes))
        return checks

    def auditar(self) -> list[Erro]:
        """Executa a bateria completa + DUPLA CHECAGEM de cada achado."""
        self.erros, self.log = [], []
        for _id, metodo in self._checks():          # 1ª passada
            self.erros.extend(metodo())
        self.dupla_checagem()                       # 2ª passada isolada
        return self.erros

    # ==================================================================
    # 2) DUPLA CHECAGEM
    # ==================================================================
    def dupla_checagem(self) -> None:
        """Re-executa de forma ISOLADA apenas os checks que acusaram erro.
        Persistindo: CONFIRMADO. Sumiu: FALSO_POSITIVO (registrado no log)."""
        ids_com_erro = {e.id.split("-")[0] for e in self.erros}
        primeira = list(self.erros)
        self.erros = []
        for prefixo, metodo in self._checks():
            if prefixo in ids_com_erro:
                reexec = metodo()
                if reexec:
                    for e in reexec:
                        e.confirmado = True
                        e.status = "CONFIRMADO"
                    self.erros.extend(reexec)
                else:
                    self.log.append(f"DUPLA CHECAGEM: {prefixo} não repetiu "
                                    "na 2ª passada -> FALSO_POSITIVO")
            else:
                self.erros.extend([])  # check limpo nas duas passadas
        for e in primeira:
            if not any(e.id == n.id and e.descricao == n.descricao
                       for n in self.erros):
                self.log.append(f"FALSO_POSITIVO descartado na dupla "
                                f"checagem: {e.id} ({e.descricao})")

    # ==================================================================
    # 3) CORREÇÃO DIRETA
    # ==================================================================
    def corrigir(self, instalar_pacotes: bool = True) -> list[Erro]:
        """Ataca cada erro CONFIRMADO com correção automática segura e
        RE-VALIDA executando novamente o check de origem."""
        for erro in [e for e in self.erros
                     if e.confirmado and e.corrigivel]:
            try:
                ok = self._aplicar_correcao(erro, instalar_pacotes)
            except Exception as exc:  # noqa: BLE001
                ok = False
                erro.correcao += f" | FALHA na correção automática: {exc}"
            if ok:
                # re-validação: o check de origem ainda acusa?
                prefixo = erro.id.split("-")[0]
                ainda = [e2 for m in self._checks() if m[0] == prefixo
                         for e2 in m[1]() if e2.id == erro.id]
                if not ainda:
                    erro.corrigido = True
                    erro.status = "CORRIGIDO"
                else:
                    erro.status = "PENDENTE"
            else:
                erro.status = "PENDENTE"
        return self.erros

    def _aplicar_correcao(self, erro: Erro, instalar_pacotes: bool) -> bool:
        if erro.id == "AMB-PACOTE" and instalar_pacotes:
            espec = erro.correcao.replace("pip install ", "", 1).strip()
            return bool(espec) and self._pip_install(espec)
        if erro.id == "AMB-OCR":
            self._pip(["uninstall", "-y", "opencv-python"])  # conflito libGL
            self._pip_install("rapidocr-onnxruntime PyMuPDF")
            self._pip(["install", "-q", "--force-reinstall", "--no-deps",
                       "opencv-python-headless"])
            # revalidação honesta: o singleton caches a falha da 1ª tentativa
            from licenciamento.leitor_pdf import LeitorPDF
            LeitorPDF._ocr_instancia = None
            return True
        if erro.id == "APP-CONFIG-TOML":
            destino = self.raiz / ".streamlit" / "config.toml"
            destino.parent.mkdir(exist_ok=True)
            destino.write_text(CONFIG_TOML_CANONICO, encoding="utf-8")
            return True
        if erro.id == "HIG-BOM":
            alvo = Path(erro.evidencia)
            alvo.write_bytes(alvo.read_bytes()
                             .replace(b"\xef\xbb\xbf", b"", 1))
            return True
        if erro.id == "HIG-NEWLINE":
            alvo = Path(erro.evidencia)
            dados = alvo.read_bytes()
            if dados:
                alvo.write_bytes(dados + b"\n")
            return True
        return False

    @staticmethod
    def _pip(args: list[str]) -> bool:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", *args, "-q"],
            capture_output=True, text=True, timeout=900)
        return proc.returncode == 0

    def _pip_install(self, espec: str) -> bool:
        return self._pip(["install", "-q", *espec.split()])

    # ==================================================================
    # VERIFICAÇÕES
    # ==================================================================
    def verificar_compilacao(self) -> list[Erro]:
        erros: list[Erro] = []
        for py in sorted(self.raiz.rglob("*.py")):
            if ".venv" in py.parts:
                continue
            try:
                py_compile.compile(str(py), doraise=True)
            except py_compile.PyCompileError as exc:
                erros.append(Erro(
                    id=f"COMP-{py.name}", severidade="alta",
                    componente=str(py.relative_to(self.raiz)),
                    descricao="Erro de sintaxe/compilação em arquivo Python.",
                    evidencia=str(exc)[:500],
                    correcao="Corrigir a sintaxe do arquivo (manual)."))
        return erros

    def verificar_testes(self) -> list[Erro]:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=no",
             "-p", "no:cacheprovider"],
            capture_output=True, text=True, timeout=900, cwd=self.raiz)
        m = re.search(r"(\d+) failed", proc.stdout)
        if m or proc.returncode != 0:
            falhas = m.group(1) if m else "?"
            falados = re.findall(r"^(FAILED [\w:.\[\]-]+)$", proc.stdout,
                                 re.M)[:20]
            return [Erro(
                id="TEST-FAILURES", severidade="alta", componente="tests/",
                descricao=f"Bateria de testes com {falhas} falha(s).",
                evidencia="\n".join(falados) or proc.stdout[-800:],
                correcao="Corrigir o(s) teste(s)/código apontado(s) (manual).")]
        return []

    def verificar_configs_json(self) -> list[Erro]:
        erros: list[Erro] = []
        cfg = self.raiz / "config"
        dados: dict[str, dict] = {}
        for arquivo in sorted(cfg.glob("*.json")):
            try:
                dados[arquivo.name] = json.loads(
                    arquivo.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                erros.append(Erro(
                    id=f"CFG-JSON-{arquivo.stem}", severidade="alta",
                    componente=str(arquivo.relative_to(self.raiz)),
                    descricao="JSON de configuração inválido/corrompido.",
                    evidencia=str(exc)[:300],
                    correcao="Restaurar/corrigir o JSON (manual)."))

        def contem_valor(obj, alvo: float) -> bool:
            if isinstance(obj, (int, float)) and abs(float(obj) - alvo) < 0.005:
                return True
            if isinstance(obj, dict):
                return any(contem_valor(v, alvo) for v in obj.values())
            if isinstance(obj, list):
                return any(contem_valor(v, alvo) for v in obj)
            return False

        taxas = dados.get("taxas_urm.json", {})
        if taxas and not contem_valor(taxas, 1290.70):
            erros.append(Erro(
                id="CFG-TAXA-D", severidade="media", componente="config/taxas_urm.json",
                descricao="Tabela D (parcelamento 0-5 ha / LP = 1290,70 URM) "
                          "ausente no arquivo de taxas.",
                evidencia="valor 1290.70 não encontrado", correcao="manual"))
        if taxas and not contem_valor(taxas, 1960.00):
            erros.append(Erro(
                id="CFG-TAXA-ERB", severidade="media", componente="config/taxas_urm.json",
                descricao="Taxa de ERB (1.960,00 URM por fase) ausente.",
                evidencia="valor 1960.00 não encontrado", correcao="manual"))

        tipos = dados.get("checklists_por_formulario_tipos.json", {})
        if tipos:
            mapa = tipos.get("tipos", {})
            if len(mapa) < 15:
                erros.append(Erro(
                    id="CFG-TIPOS-QUANT", severidade="media",
                    componente="config/checklists_por_formulario_tipos.json",
                    descricao=f"Banco de tipos com apenas {len(mapa)} tipos "
                              "(esperado >= 15 do documento de referência).",
                    evidencia=str(list(mapa)[:20]), correcao="manual"))
            for chave, entrada in mapa.items():
                if not (entrada.get("nome") and entrada.get("palavras")
                        and entrada.get("por_licenca")):
                    erros.append(Erro(
                        id=f"CFG-TIPOS-{chave}", severidade="media",
                        componente="config/checklists_por_formulario_tipos.json",
                        descricao=f"Tipo '{chave}' sem nome/palavras/por_licenca "
                                  "completos.",
                        evidencia=json.dumps(entrada)[:200], correcao="manual"))
                    break

        oficiais = dados.get("checklists_oficiais.json", {})
        if oficiais:
            por_fase = oficiais.get("documentos_por_fase", {})
            for fase in ("LP", "LI", "LO"):
                if len(por_fase.get(fase, [])) < 3:
                    erros.append(Erro(
                        id=f"CFG-OFICIAL-{fase}", severidade="media",
                        componente="config/checklists_oficiais.json",
                        descricao=f"Checklist oficial da fase {fase} vazio/"
                                  "insuficiente.",
                        evidencia=str(len(por_fase.get(fase, []))),
                        correcao="manual"))
        return erros

    def verificar_parser_fixtures(self) -> list[Erro]:
        from licenciamento.parser_formulario import FormularioParser
        erros: list[Erro] = []
        exemplos = self.raiz / "exemplos"
        re_codram = re.compile(r"^\d{1,4}(?:[.,]\d{1,3})+(?:-\d)?$|^\d{3,4}$")
        for htm in sorted(exemplos.glob("*.htm*")):
            try:
                parser = FormularioParser(str(htm))
                dados = parser.parse()
            except Exception as exc:  # noqa: BLE001
                erros.append(Erro(
                    id=f"PAR-CRASH-{htm.stem}", severidade="alta",
                    componente=str(htm.relative_to(self.raiz)),
                    descricao="Parser CRASHOU ao ler a fixture.",
                    evidencia=f"{type(exc).__name__}: {exc}"[:400],
                    correcao="manual"))
                continue
            emp = dados.get("empreendimento") or {}
            codram = emp.get("codram")
            if codram and (not re_codram.match(codram)
                           or re.search(r"[A-Za-zÀ-ÿ]", codram)):
                erros.append(Erro(
                    id=f"PAR-CODRAM-{htm.stem}", severidade="media",
                    componente=str(htm.relative_to(self.raiz)),
                    descricao="CODRAM extraído com ruído (run-on da tabela).",
                    evidencia=f"codram={codram!r}",
                    correcao="manual"))
            coord = emp.get("coordenadas") or {}
            lat, lon = coord.get("latitude"), coord.get("longitude")
            if (lat is not None and not -90 <= lat <= 90) or \
               (lon is not None and not -180 <= lon <= 180):
                erros.append(Erro(
                    id=f"PAR-COORD-{htm.stem}", severidade="media",
                    componente=str(htm.relative_to(self.raiz)),
                    descricao="Coordenada fora da faixa geográfica válida.",
                    evidencia=f"lat={lat} lon={lon}", correcao="manual"))
            brutos = str(coord.get("valores_brutos") or "")
            if len(brutos) > 200 or "<" in brutos:
                erros.append(Erro(
                    id=f"PAR-BRUTOS-{htm.stem}", severidade="baixa",
                    componente=str(htm.relative_to(self.raiz)),
                    descricao="valores_brutos das coordenadas grande demais "
                              "(pode ter despejado o HTML inteiro).",
                    evidencia=f"{len(brutos)} caracteres", correcao="manual"))
            endereco = emp.get("endereco") or ""
            # OBS.: ' - ' é legítimo dentro do endereço (ex.: 'Av. Júlio de
            # Castilhos, 455 - Centro'); o que denuncia mistura é RÓTULO
            # vizinho vazando na célula
            if any(tok in endereco for tok in ("Bairro:", "CEP:", "Número:",
                                               "Município:")):
                erros.append(Erro(
                    id=f"PAR-ENDERECO-{htm.stem}", severidade="media",
                    componente=str(htm.relative_to(self.raiz)),
                    descricao="Endereço misturado com outros campos da linha "
                              "(célula-a-célula falhou).",
                    evidencia=endereco[:120], correcao="manual"))
        return erros

    def verificar_pleitos_e_taxas(self) -> list[Erro]:
        from licenciamento.parser_formulario import FormularioParser
        from licenciamento.agente_financeiro import AgenteFinanceiro
        erros: list[Erro] = []
        fixture = self.raiz / "exemplos" / "formulario_MARIA_BELLE_PARCELAMENTO.htm"
        if not fixture.exists():
            return erros
        parser = FormularioParser(str(fixture))
        parser.parse()
        expectativa = {"LP": 14, "AUTORIZACAO": 7, "PRAD": 0}
        for tipo, minimo in expectativa.items():
            try:
                d = parser.aplicar_pleito_manual(tipo, "Primeira licença")
                total = len(d["documentos_exigidos"]["lista_deduplicada"])
                if total != minimo:
                    erros.append(Erro(
                        id=f"PLE-{tipo}", severidade="alta",
                        componente="parser/aplicar_pleito_manual",
                        descricao=f"Pleito {tipo} deveria listar {minimo} "
                                  f"documento(s) e listou {total}.",
                        evidencia=str(d["documentos_exigidos"]
                                      ["lista_deduplicada"][:20]),
                        correcao="manual"))
            except Exception as exc:  # noqa: BLE001
                erros.append(Erro(
                    id=f"PLE-{tipo}-CRASH", severidade="alta",
                    componente="parser/aplicar_pleito_manual",
                    descricao=f"Pleito {tipo} levantou exceção.",
                    evidencia=f"{type(exc).__name__}: {exc}"[:300],
                    correcao="manual"))
        try:
            parser2 = FormularioParser(str(fixture))
            parser2.parse()
            d_ren = parser2.aplicar_pleito_manual("LP", "Renovação")
            if len(d_ren["documentos_exigidos"]["lista_deduplicada"]) < 3:
                erros.append(Erro(
                    id="PLE-RENOVACAO", severidade="media",
                    componente="parser/aplicar_pleito_manual",
                    descricao="Renovação sem listagem própria (>= 3 itens).",
                    evidencia=str(d_ren["documentos_exigidos"]
                                  ["lista_deduplicada"]),
                    correcao="manual"))
        except Exception as exc:  # noqa: BLE001
            erros.append(Erro(id="PLE-RENOVACAO-CRASH", severidade="media",
                              componente="parser/aplicar_pleito_manual",
                              descricao="Renovação levantou exceção.",
                              evidencia=str(exc)[:200], correcao="manual"))
        # taxa LP do parcelamento: valor positivo (TABELA D)
        try:
            parser3 = FormularioParser(str(fixture))
            d3 = parser3.parse()
            d3 = parser3.aplicar_pleito_manual("LP", "Primeira licença")
            fin = AgenteFinanceiro().calcular_do_parser(d3)
            if not fin.get("total_urm"):
                erros.append(Erro(
                    id="TAX-LP", severidade="alta",
                    componente="agente_financeiro",
                    descricao="Taxa do pleito LP (parcelamento) calculou vazia.",
                    evidencia=json.dumps(fin, default=str)[:300],
                    correcao="manual"))
        except Exception as exc:  # noqa: BLE001
            erros.append(Erro(id="TAX-LP-CRASH", severidade="alta",
                              componente="agente_financeiro",
                              descricao="Cálculo de taxa levantou exceção.",
                              evidencia=str(exc)[:300], correcao="manual"))
        return erros

    def verificar_ambiente(self) -> list[Erro]:
        erros: list[Erro] = []
        req = self.raiz / "requirements.txt"
        if req.exists():
            for linha in req.read_text(encoding="utf-8").splitlines():
                linha = linha.split("#")[0].strip()
                if not linha:
                    continue
                pacote = re.split(r"[><=!~\s]+", linha)[0]
                importe = IMPORTES.get(pacote)
                if not importe:
                    continue
                try:
                    __import__(importe)
                except Exception as exc:  # noqa: BLE001
                    erros.append(Erro(
                        id="AMB-PACOTE", severidade="media",
                        componente="requirements.txt",
                        descricao=f"Pacote '{pacote}' citado mas NÃO importável "
                                  "no ambiente.",
                        evidencia=f"import '{importe}' falhou: "
                                  f"{type(exc).__name__}",
                        correcao=f"pip install {linha}",
                        corrigivel=True))
                    break  # um só já indica ambiente quebrado
        try:
            from licenciamento.leitor_pdf import LeitorPDF
            if not LeitorPDF.ocr_disponivel():
                erros.append(Erro(
                    id="AMB-OCR", severidade="alta",
                    componente="licenciamento/leitor_pdf.py",
                    descricao="OCR de PDFs escaneados INDISPONÍVEL "
                              "(rapidocr/onnx/cv2).",
                    evidencia="LeitorPDF.ocr_disponivel() == False",
                    correcao="reinstalar pilha OCR (headless)",
                    corrigivel=True))
        except ImportError as exc:
            erros.append(Erro(id="AMB-OCR", severidade="alta",
                              componente="licenciamento/leitor_pdf.py",
                              descricao="Módulo leitor_pdf não importável.",
                              evidencia=str(exc), correcao="manual"))
        return erros

    def verificar_integracao_app(self) -> list[Erro]:
        erros: list[Erro] = []
        app = self.raiz / "app.py"
        if app.exists():
            fonte = app.read_text(encoding="utf-8")
            for simbolo, rotulo in [("🌙 Tema escuro", "toggle de tema"),
                                    ("Tipo de empreendimento",
                                     "métrica CODRAM da Etapa 2"),
                                    ("Auditoria do sistema",
                                     "painel do agente auditor")]:
                if simbolo not in fonte:
                    erros.append(Erro(
                        id=f"APP-{rotulo.split()[0].upper()}",
                        severidade="media", componente="app.py",
                        descricao=f"Frontend sem o {rotulo} (regressão).",
                        evidencia=f"'{simbolo}' ausente em app.py",
                        correcao="manual"))
        toml = self.raiz / ".streamlit" / "config.toml"
        conteudo = toml.read_text(encoding="utf-8") if toml.exists() else ""
        if ("enableXsrfProtection" not in conteudo
                or not re.search(r"enableXsrfProtection\s*=\s*false", conteudo, re.I)):
            erros.append(Erro(
                id="APP-CONFIG-TOML", severidade="alta",
                componente=".streamlit/config.toml",
                descricao="config.toml sem XSRF/CORS liberados: UPLOADS quebram "
                          "atrás do proxy do preview (AxiosError 403).",
                evidencia=str(conteudo)[:200],
                correcao="regravar config.toml canônico", corrigivel=True))
        return erros

    def verificar_conferencias_documentais(self) -> list[Erro]:
        """CONFERÊNCIAS DOCUMENTAIS (lições dos erros reais do licenciador):
        ART com o nome nos primeiros dados deve ser CONFORME (com atribuição
        à responsabilidade pelo licenciamento quando a ART declara a
        atividade); projeto urbanístico tem dupla checagem profissional+áreas;
        CNPJ sob o rótulo 'Número de Inscrição' é reconhecido; a antiga
        mensagem 'Nenhum Termo de Referência...' está ABOLIDA."""
        erros: list[Erro] = []

        # (a) mensagem abolida não pode voltar ao código
        fonte_aud = (self.raiz / "licenciamento" / "auditor_tecnico.py")
        if fonte_aud.exists() and ("Nenhum Termo de Refer"
                                   "ência reconhecido" in fonte_aud.read_text(
                                       encoding="utf-8")):
            erros.append(Erro(
                id="CONF-MSG-TR", severidade="media",
                componente="licenciamento/auditor_tecnico.py",
                descricao="Mensagem 'Nenhum Termo de Referência reconhecido' "
                          "reintroduzida (usuário NÃO envia TR).",
                evidencia="texto presente no fonte", correcao="manual"))

        try:
            from licenciamento.auditor_tecnico import AuditorTecnico
            auditor = AuditorTecnico()
            # (b) ART: nome nos primeiros dados + atividade licenciamento
            art = ("ART Nº 202613404\nKeli Daiane Bernardes dos Santos\n"
                   "CREA-RS 110544/03-D\nDescrição da atividade/sumária: "
                   "LICENCIAMENTO AMBIENTAL do empreendimento.\n")
            res = auditor.auditar_com_dupla_checagem(
                "art_licenciamento.pdf", art,
                arts_formulario=[{"numero": "202613404",
                                  "nome": "Keli Daiane Bernardes dos Santos",
                                  "secao": "8"}])
            r = [x for x in res if "ART" in x.norma_tr]
            if not r or r[0].status.value != "CONFORME" or \
                    "RESPONSABILIDADE TÉCNICA" not in (r[0].metricas or {}) \
                    .get("papel", ""):
                erros.append(Erro(
                    id="CONF-ART-NOME", severidade="media",
                    componente="licenciamento/auditor_tecnico.py",
                    descricao="Conferência de ART falhou com o nome nos "
                              "primeiros dados do documento (deve ser CONFORME "
                              "e atribuir a responsabilidade técnica).",
                    evidencia=str([(x.norma_tr, x.status.value,
                                    x.itens_reprovados) for x in res])[:400],
                    correcao="manual"))
            # (c) projeto urbanístico: áreas + profissional
            proj = ("PROJETO URBANÍSTICO com plantas\nResponsável técnico: "
                    "Keli Daiane Bernardes dos Santos - ART 202613404\n"
                    "Área total: 32.450,00 m²\nÁrea útil: 32.450,00 m²\n")
            res_p = auditor.auditar_com_dupla_checagem(
                "projeto.pdf", proj,
                arts_formulario=[{"numero": "202613404",
                                  "nome": "Keli Daiane Bernardes dos Santos",
                                  "secao": "8"}],
                areas_formulario={"area_total_ha": 3.245,
                                  "area_util_ha": 3.245})
            rp = [x for x in res_p if "urbanístico" in x.norma_tr]
            if not rp or rp[0].status.value != "CONFORME":
                erros.append(Erro(
                    id="CONF-PROJETO-URB", severidade="media",
                    componente="licenciamento/auditor_tecnico.py",
                    descricao="Dupla checagem de projeto urbanístico "
                              "(profissional + áreas) falhou no caso correto.",
                    evidencia=str([(x.norma_tr, x.status.value,
                                    x.itens_reprovados)
                                   for x in res_p])[:400], correcao="manual"))
            # (d) CNPJ sob o rótulo 'Número de Inscrição' (formatado)
            from licenciamento.agente_administrativo import AgenteAdministrativo
            dados = {"empreendedor": {"cpf_cnpj": "12.345.678/0001-95"},
                     "documentos_exigidos": {"lista_deduplicada": [
                         "Cópia da matrícula atualizada do imóvel"]}}
            c = AgenteAdministrativo._conferir_cnpj_matricula(
                dados, ["matricula.pdf"],
                {"matricula.pdf": "Matrícula 33024 - Numero de Inscrição: "
                                  "12.345.678/0001-95 CNPJ da empresa"})
            if not c or c.get("status") != "CONFERE":
                erros.append(Erro(
                    id="CONF-CNPJ-ROTULO", severidade="media",
                    componente="licenciamento/agente_administrativo.py",
                    descricao="CNPJ sob o rótulo 'Número de Inscrição' "
                              "(xx.xxx.xxx/xxxx-xx) não reconhecido na "
                              "matrícula.",
                    evidencia=str(c)[:300], correcao="manual"))
        except Exception as exc:  # noqa: BLE001
            erros.append(Erro(id="CONF-FUNC", severidade="media",
                              componente="licenciamento/auditor_tecnico.py",
                              descricao="Falha ao executar as conferências "
                                        "funcionais de ART/projeto/CNPJ.",
                              evidencia=str(exc)[:300], correcao="manual"))
        return erros

    # ==================================================================
    # SEGURANÇA (skills security-audit + senior-security) — garantias
    # estáticas anti-regressão: sanitização anti-XSS, execução dinâmica
    # proibida e segredos versionados.
    # ==================================================================
    RE_SEGREDO = re.compile(
        r"(sk-[A-Za-z0-9_\-]{16,}|ghp_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,}"
        r"|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----)")

    def verificar_seguranca(self) -> list[Erro]:
        erros: list[Erro] = []
        app_py = self.raiz / "app.py"
        linhas = (app_py.read_text(encoding="utf-8", errors="replace")
                  .splitlines() if app_py.is_file() else [])

        def janela(i: int, n: int = 2) -> str:
            return "\n".join(linhas[i:i + n])

        # SEC-XSS: TODO texto derivado de documento renderizado em markdown
        # passa por md_seguro(...) (st.markdown/st.error/st.warning
        # interpretam markdown/HTML — injeção via conteúdo do upload)
        for i, ln in enumerate(linhas):
            if "st.markdown" in ln and ("> 📄" in ln or "> 📄" in ln):
                if "md_seguro(" not in janela(i, 2):
                    erros.append(Erro(
                        id="SEC-XSS-TRECHO", severidade="alta",
                        componente="app.py",
                        descricao="Trecho de documento renderizado em "
                                  "markdown SEM md_seguro (XSS armazenado).",
                        evidencia=ln.strip()[:200],
                        correcao="Envolver com md_seguro(...) — ver "
                                 "licenciamento/seguranca.py (manual)."))
            if "documento_analisado" in ln and "f\"" in ln:
                if "md_seguro(" not in janela(i, 2):
                    erros.append(Erro(
                        id="SEC-XSS-NOME-DOC", severidade="alta",
                        componente="app.py",
                        descricao="Nome de arquivo enviado pelo usuário "
                                  "renderizado em markdown sem sanitizar.",
                        evidencia=ln.strip()[:200],
                        correcao="Envolver com md_seguro(...) (manual)."))
            if "itens_reprovados" in ln and "st.error" in janela(i, 4):
                if "md_seguro(" not in janela(i, 4):
                    erros.append(Erro(
                        id="SEC-XSS-ITENS", severidade="alta",
                        componente="app.py",
                        descricao="Itens de não conformidade (texto do "
                                  "laudo) em st.error sem md_seguro.",
                        evidencia=ln.strip()[:200],
                        correcao="Envolver com md_seguro(...) (manual)."))
            if "suffix.lower() or" in ln:
                erros.append(Erro(
                    id="SEC-SUFFIX", severidade="media",
                    componente="app.py",
                    descricao="Sufixo de arquivo NÃO CONFIÁVEL usado direto "
                              "em caminho de disco (path traversal).",
                    evidencia=ln.strip()[:200],
                    correcao="Usar sufixo_seguro(...) — ver "
                             "licenciamento/seguranca.py (manual)."))

        # SEC-EXEC: execução dinâmica proibida no código de produção
        for alvo in sorted((self.raiz / "licenciamento").glob("*.py")) + [
                self.raiz / "app.py", self.raiz / "main.py"]:
            if not alvo.is_file():
                continue
            conteudo = alvo.read_text(encoding="utf-8", errors="replace")
            for padrao, rotulo in (
                    (r"\beval\s*\(", "chamada a eval dinâmico"),
                    (r"\bexec\s*\(", "chamada a exec dinâmico"),
                    (r"\bshell\b\s*=\s*True",
                     "subprocesso com shell ativo")):
                if re.search(padrao, conteudo):
                    erros.append(Erro(
                        id=f"SEC-EXEC-{alvo.stem}".upper(), severidade="alta",
                        componente=str(alvo.relative_to(self.raiz)),
                        descricao=f"Execução dinâmica insegura ({rotulo}).",
                        evidencia=padrao,
                        correcao="Remover a execução dinâmica (manual)."))

        # SEGREDO: nenhuma credencial versionada (py/toml/env do repositório)
        for alvo in sorted(self.raiz.rglob("*")):
            if not alvo.is_file() or ".venv" in alvo.parts or \
                    ".git" in alvo.parts:
                continue
            if alvo.suffix.lower() not in (".py", ".toml", ".env", ".cfg",
                                           ".ini", ".json", ".yml", ".yaml"):
                continue
            if self.RE_SEGREDO.search(
                    alvo.read_text(encoding="utf-8", errors="replace")):
                erros.append(Erro(
                    id="SEGREDO-VERSIONADO", severidade="critica",
                    componente=str(alvo.relative_to(self.raiz)),
                    descricao="Padrão de credencial/chave privada versionado.",
                    evidencia=alvo.name,
                    correcao="Remover o segredo do repositório e revogar a "
                             "chave (manual)."))
        return erros

    def verificar_higiene_arquivos(self) -> list[Erro]:
        erros: list[Erro] = []
        alvos = list((self.raiz / "config").glob("*.json")) + \
            list((self.raiz / "licenciamento").glob("*.py")) + \
            list((self.raiz / "tests").glob("*.py")) + \
            [self.raiz / "app.py", self.raiz / "main.py"]
        for arquivo in alvos:
            if not arquivo.is_file():
                continue
            bruto = arquivo.read_bytes()
            if bruto.startswith(b"\xef\xbb\xbf"):
                erros.append(Erro(
                    id="HIG-BOM", severidade="baixa",
                    componente=str(arquivo.relative_to(self.raiz)),
                    descricao="Arquivo com BOM UTF-8 (quebra parsers fracos).",
                    evidencia=str(arquivo), correcao="remover BOM",
                    corrigivel=True))
            if bruto and not bruto.endswith(b"\n"):
                erros.append(Erro(
                    id="HIG-NEWLINE", severidade="baixa",
                    componente=str(arquivo.relative_to(self.raiz)),
                    descricao="Arquivo sem newline no final.",
                    evidencia=str(arquivo), correcao="adicionar newline",
                    corrigivel=True))
        return erros

    # ==================================================================
    # 4) RELATÓRIO
    # ==================================================================
    def relatorio(self) -> dict:
        por_status: dict[str, int] = {}
        for e in self.erros:
            por_status[e.status] = por_status.get(e.status, 0) + 1
        return {
            "quando": datetime.now().isoformat(timespec="seconds"),
            "bateria_completa": self.com_testes,
            "resumo": {"total": len(self.erros),
                       "por_status": por_status,
                       "por_severidade": {
                           sev: len([e for e in self.erros
                                     if e.severidade == sev])
                           for sev in ("alta", "media", "baixa")}},
            "erros": [asdict(e) for e in self.erros],
            "log": self.log,
        }

    def salvar_relatorio(self) -> tuple[Path, Path]:
        pasta = self.raiz / "auditoria"
        pasta.mkdir(exist_ok=True)
        rel = self.relatorio()
        j = pasta / "relatorio_auditoria.json"
        j.write_text(json.dumps(rel, ensure_ascii=False, indent=2),
                     encoding="utf-8")
        m = pasta / "relatorio_auditoria.md"
        m.write_text(_relatorio_md(rel), encoding="utf-8")
        return j, m


def _relatorio_md(rel: dict) -> str:
    linhas = [f"# Relatório de Auditoria do Sistema",
              f"*Gerado em {rel['quando']} · bateria "
              f"{'COMPLETA' if rel['bateria_completa'] else 'RÁPIDA'}*", ""]
    r = rel["resumo"]
    linhas.append(f"**{r['total']}** achado(s) · "
                  + " · ".join(f"{k}: {v}" for k, v in
                               r["por_status"].items() or {"—": 0}.items()))
    linhas.append("")
    if not rel["erros"]:
        linhas.append("✅ **Nenhum erro confirmado. Sistema íntegro.**")
    for e in rel["erros"]:
        linhas += [f"## {e['id']} — {e['severidade'].upper()} · {e['status']}",
                   f"- **Componente:** {e['componente']}",
                   f"- **Descrição:** {e['descricao']}",
                   f"- **Evidência:** `{e['evidencia'][:300]}`",
                   f"- **Correção:** {e['correcao']}", ""]
    if rel["log"]:
        linhas.append("## Log da dupla checagem")
        linhas += [f"- {l}" for l in rel["log"]]
    return "\n".join(linhas)


def main(argv: Optional[list[str]] = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    rapida = "--rapida" in args
    auditor = AuditorSistema(com_testes=not rapida)
    print("🧭 AGENTE AUDITOR DO SISTEMA — bateria",
          "RÁPIDA" if rapida else "COMPLETA")
    auditor.auditar()
    auditor.corrigir()
    j, m = auditor.salvar_relatorio()
    rel = auditor.relatorio()
    r = rel["resumo"]
    print(f"\nRESUMO: {r['total']} achado(s) | por status: {r['por_status']}"
          f" | por severidade: {r['por_severidade']}")
    for e in rel["erros"]:
        marca = {"CORRIGIDO": "✅", "PENDENTE": "🟡",
                 "CONFIRMADO": "❌"}.get(e["status"], "·")
        print(f"{marca} {e['id']:24s} {e['severidade']:6s} {e['status']:11s} "
              f"{e['componente']}: {e['descricao'][:80]}")
    for linha in auditor.log:
        print("ℹ️ ", linha)
    print(f"\nRelatórios: {j}\n            {m}")
    return 0 if not [e for e in rel["erros"]
                     if e["status"] == "PENDENTE" and e["severidade"] == "alta"] \
        else 1


if __name__ == "__main__":
    raise SystemExit(main())
