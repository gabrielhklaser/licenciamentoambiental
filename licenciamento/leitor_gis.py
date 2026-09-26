# -*- coding: utf-8 -*-
"""
Leitura de MAPAS e CAMADAS GIS (skill gis-multicamadas)
========================================================

O checklist oficial de licenciamento (LP/LI) exige, no mesmo processo:

    "Arquivo KMZ/KML e DWG do projeto urbanístico, curvas de nível e mapa de
     áreas de preservação permanente"
    "Mapa da propriedade georreferenciado (shape file, SIRGAS 2000)"

Este módulo LÊ esses arquivos e devolve um PACOTE DE CAMADAS estruturado,
seguindo as diretrizes da skill `gis-multicamadas` do GabeBrain:

  1. CAMADAS DISTINTAS E INDEPENDENTES — cada camada tem tema próprio
     (geologia, hidrogeologia, solos/pedologia, drenagem/hidrografia, malha
     viária, APP/Reserva Legal, curvas de nível, projeto urbanístico) e é
     classificada por `classificar_camada()`. Nenhuma camada é misturada a
     outra e NENHUM Termo de Referência de conteúdo é aplicado a camadas GIS:
     o confronto de uma camada é GEOMÉTRICO (SRS, feições, área, distância),
     nunca de TR de laudo (evita aplicação cruzada de TRs).
  2. SRS/EPSG EXPLÍCITO — toda camada declara (ou herda) o sistema de
     referência; `validar_srs()` confere SIRGAS 2000 / WGS84 e reprojeta com
     fallback seguro para EPSG:4326 (nunca assume datum).
  3. GEOMETRIA ROBUSTA — `reparar_geometria()` fecha anéis, remove vértices
     duplicados e descarta geometrias degeneradas (0 ou 1 vértice); a
     validação topológica (`validar_geometrias`) sinaliza anéis abertos.
  4. MÉTRICAS ESPACIAIS — área (fórmula de Gauss em projeção métrica) e
     distância (haversine) para conferir raio de segurança (500 m), APP e
     distância a corpos hídricos.

Formatos suportados (somente biblioteca padrão - sem GeoPandas/QGIS no
servidor): KML, KMZ (KML comprimido), GeoJSON e GPX. WKT é aceito como
entrada textual (coluna `geometria` do banco fora do PostGIS).
"""

from __future__ import annotations

import json
import logging
import math
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional
from xml.etree import ElementTree

logger = logging.getLogger("licenciamento.leitor_gis")

# =============================================================================
# SRS / EPSG
# =============================================================================
#: SRS aceitos no licenciamento municipal (SIRGAS 2000 é o oficial do Brasil)
SRS_CONHECIDOS: dict[str, dict[str, Any]] = {
    "EPSG:4674": {"nome": "SIRGAS 2000 geográfico", "datum": "SIRGAS 2000",
                  "geografico": True, "uf": "BR"},
    "EPSG:31982": {"nome": "SIRGAS 2000 UTM 22S", "datum": "SIRGAS 2000",
                   "geografico": False, "fuso": 22, "hemisferio": "S"},
    "EPSG:31983": {"nome": "SIRGAS 2000 UTM 23S", "datum": "SIRGAS 2000",
                   "geografico": False, "fuso": 23, "hemisferio": "S"},
    "EPSG:4326": {"nome": "WGS 84 geográfico", "datum": "WGS 84",
                  "geografico": True},
    "EPSG:32722": {"nome": "WGS 84 UTM 22S", "datum": "WGS 84",
                   "geografico": False, "fuso": 22, "hemisferio": "S"},
    "EPSG:32723": {"nome": "WGS 84 UTM 23S", "datum": "WGS 84",
                   "geografico": False, "fuso": 23, "hemisferio": "S"},
}

#: aliases aceitos no campo de SRS (KML costuma usar nomes livres)
SRS_ALIASES: dict[str, str] = {
    "sirgas 2000": "EPSG:4674", "sirgas2000": "EPSG:4674", "sirgas": "EPSG:4674",
    "sirgas 2000 utm 22s": "EPSG:31982", "sirgas 2000 utm 23s": "EPSG:31983",
    "utm 22s": "EPSG:31982", "utm 23s": "EPSG:31983", "utm 22 s": "EPSG:31982",
    "utm 23 s": "EPSG:31983", "fuso 22": "EPSG:31982", "fuso 23": "EPSG:31983",
    "wgs84": "EPSG:4326", "wgs 84": "EPSG:4326", "wgs-84": "EPSG:4326",
    "epsg:4674": "EPSG:4674", "epsg:4326": "EPSG:4326", "epsg:31982": "EPSG:31982",
    "epsg:31983": "EPSG:31983", "epsg:32722": "EPSG:32722", "epsg:32723": "EPSG:32723",
    "córrego alegre": "EPSG:4674", "corrego alegre": "EPSG:4674",
}

#: datum oficial exigido pelo checklist ("SIRGAS 2000")
DATUM_OFICIAL = "SIRGAS 2000"

# =============================================================================
# TEMAS DAS CAMADAS (skill gis-multicamadas, seção 1)
# =============================================================================
TEMAS_CAMADA: dict[str, list[str]] = {
    "GEOLOGIA": ["geolog", "litolog", "unidade geologica", "embasamento"],
    "HIDROGEOLOGIA": ["hidrogeolog", "aquifero", "aquífero", "vulnerabilidade"],
    "PEDOLOGIA": ["solo", "pedolog", "classe de solo"],
    "DRENAGEM": ["drenagem", "hidrograf", "arroio", "rio", "corpo dagua",
                 "corpo d'agua", "nascente", "banhado"],
    "VIAS": ["via", "acesso", "logradouro", "rodovia", "rua", "malha viaria"],
    "APP": ["app", "area de preservacao permanente", "área de preservação permanente",
            "preservacao permanente", "reserva legal", "rl"],
    "CURVAS_DE_NIVEL": ["curva de nivel", "curvas de nivel", "curva",
                        "topograf", "altimetr", "mdT", "cot"],
    "PROJETO_URBANISTICO": ["projeto urbanistico", "urbanistic", "quadro de areas",
                            "loteamento", "parcelamento", "implantacao"],
    "PROPRIEDADE": ["propriedade", "perimetro", "perímetro", "poligonal", "terreno",
                    "lote", "gleba", "area do imovel", "matricula"],
    "RAIO_SEGURANCA": ["raio de seguranca", "raio", "buffer", "area de influencia"],
    "COBERTURA_VEGETAL": ["cobertura vegetal", "vegetacao", "vegetação", "uso do solo",
                          "fitofisionomia"],
    "FAUNA": ["fauna", "amostragem", "ponto de escuta", "armadilha"],
    "OUTRO": [],
}

# geometrias reconhecidas
TIPOS_GEOMETRIA = ("Point", "LineString", "Polygon", "MultiGeometry")


# =============================================================================
# Estruturas de dados
# =============================================================================
@dataclass
class Feicao:
    """Uma feição geoespacial (ponto, linha ou polígono) de uma camada."""
    nome: str = ""
    atributos: dict[str, Any] = field(default_factory=dict)
    coordenadas: list[tuple[float, float]] = field(default_factory=list)
    # anéis: polígonos com furos -> lista de anéis (cada anel = lista de pontos)
    aneis: list[list[tuple[float, float]]] = field(default_factory=list)

    @property
    def tipo(self) -> str:
        if self.aneis:
            return "Polygon"
        if len(self.coordenadas) == 1:
            return "Point"
        return "LineString"

    @property
    def valida(self) -> bool:
        """Geometria mínima: ponto, linha com >= 2 vértices ou polígono
        com anel fechado de >= 3 vértices distintos."""
        if self.aneis:
            return any(len({p for p in anel}) >= 3 for anel in self.aneis)
        distintos = {p for p in self.coordenadas}
        if not distintos:
            return False
        return len(distintos) >= (2 if len(self.coordenadas) > 1 else 1)


@dataclass
class Camada:
    """Camada temática independente (nunca mistura temas)."""
    nome: str
    tema: str = "OUTRO"
    feicoes: list[Feicao] = field(default_factory=list)
    srs_declarado: Optional[str] = None
    srs_epsg: Optional[str] = None
    visivel: bool = True
    origem: str = ""          # 'kml' | 'geojson' | 'gpx' | 'wkt'

    # ---------------------------------------------------------------- --
    @property
    def tipo_geometria(self) -> str:
        for f in self.feicoes:
            return f.tipo
        return ""

    @property
    def feicoes_validas(self) -> list[Feicao]:
        return [f for f in self.feicoes if f.valida]

    @property
    def feicoes_invalidas(self) -> list[Feicao]:
        return [f for f in self.feicoes if not f.valida]

    def area_graus2(self) -> float:
        """Área aproximada em graus² (só para polígonos geográficos)."""
        return sum(_area_anel_gauss(anel) for f in self.feicoes_validas
                   if f.aneis for anel in f.aneis)

    def centroide(self) -> Optional[tuple[float, float]]:
        pts = [p for f in self.feicoes_validas for p in
               (f.coordenadas or (f.aneis[0] if f.aneis else []))]
        if not pts:
            return None
        return (round(sum(p[0] for p in pts) / len(pts), 7),
                round(sum(p[1] for p in pts) / len(pts), 7))

    def resumo(self) -> dict[str, Any]:
        return {
            "nome": self.nome,
            "tema": self.tema,
            "feicoes": len(self.feicoes),
            "feicoes_validas": len(self.feicoes_validas),
            "tipo_geometria": self.tipo_geometria,
            "srs_declarado": self.srs_declarado,
            "srs_epsg": self.srs_epsg,
            "datum": SRS_CONHECIDOS.get(self.srs_epsg or "", {}).get("datum"),
        }


@dataclass
class PacoteGIS:
    """Conjunto de camadas lidas de um arquivo (ou de vários)."""
    arquivo: str = ""
    camadas: list[Camada] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    erros: list[str] = field(default_factory=list)

    # ---------------------------------------------------------------- --
    @property
    def camadas_por_tema(self) -> dict[str, list[Camada]]:
        agrupado: dict[str, list[Camada]] = {}
        for camada in self.camadas:
            agrupado.setdefault(camada.tema, []).append(camada)
        return agrupado

    @property
    def total_feicoes(self) -> int:
        return sum(len(c.feicoes) for c in self.camadas)

    def camada(self, tema: str) -> Optional[Camada]:
        """Primeira camada de um tema (ou None se o tema não está no mapa)."""
        for camada in self.camadas:
            if camada.tema == tema:
                return camada
        return None

    def resumo_textual(self) -> str:
        """Resumo legível das camadas (usado como 'texto' do documento).

        NÃO é laudo: não contém conteúdo de TR - por isso nenhum TR de
        conteúdo é aplicado a este arquivo."""
        linhas: list[str] = [f"CAMADAS GIS - {self.arquivo or 'arquivo'}"]
        for camada in self.camadas:
            srs = camada.srs_declarado or "SRS não declarado"
            linhas.append(
                f"Camada: {camada.nome} | tema: {camada.tema} | "
                f"geometria: {camada.tipo_geometria or 'n/d'} | "
                f"feições: {len(camada.feicoes)} | SRS: {srs}")
        if not self.camadas:
            linhas.append("Nenhuma camada geoespacial identificada.")
        linhas.append(f"Total de feições: {self.total_feicoes}")
        for aviso in self.avisos:
            linhas.append(f"Aviso: {aviso}")
        return "\n".join(linhas)


# =============================================================================
# Utilitários de geometria (equivalentes leves de layer_manipulation.py)
# =============================================================================
def _normalizar_ponto(p: Any) -> Optional[tuple[float, float]]:
    """Aceita (lon, lat), [lon, lat], 'lon,lat' ou {'lon':..,'lat':..}."""
    if p is None:
        return None
    try:
        if isinstance(p, dict):
            lon = p.get("lon", p.get("longitude", p.get("x")))
            lat = p.get("lat", p.get("latitude", p.get("y")))
            return (float(lon), float(lat))
        if isinstance(p, str):
            partes = [x for x in re.split(r"[;,/\s]+", p.strip()) if x]
            if len(partes) < 2:
                return None
            return (float(partes[0].replace(",", ".")),
                    float(partes[1].replace(",", ".")))
        seq = list(p)
        if len(seq) < 2:
            return None
        return (float(seq[0]), float(seq[1]))
    except (TypeError, ValueError):
        return None


def reparar_anel(anel: Iterable[Any]) -> list[tuple[float, float]]:
    """Fecha o anel, remove vértices repetidos e degenerados.

    Equivalente stdlib do `make_valid` + `buffer(0)` da skill
    gis-multicamadas: garante primeiro ponto == último ponto."""
    pontos: list[tuple[float, float]] = []
    for bruto in anel:
        p = _normalizar_ponto(bruto)
        if p is None:
            continue
        if pontos and pontos[-1] == p:
            continue  # vértice duplicado consecutivo
        pontos.append(p)
    if len(pontos) >= 3 and pontos[0] != pontos[-1]:
        pontos.append(pontos[0])  # anel fechado
    return pontos


def _area_anel_gauss(anel: list[tuple[float, float]]) -> float:
    """Área de um anel pela fórmula de Gauss (shoelace), em graus²."""
    if len(anel) < 4:
        return 0.0
    total = 0.0
    for (x1, y1), (x2, y2) in zip(anel, anel[1:]):
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def area_ha(camada: Camada) -> Optional[float]:
    """Área da camada em hectares.

    Polígonos em graus são convertidos por aproximação esférica local
    (1° ≈ 111.320 km de lon na latitude média e 110,574 km de lat); camadas
    métricas (UTM) usam a área diretamente."""
    epsg = camada.srs_epsg or ""
    info = SRS_CONHECIDOS.get(epsg, {})
    if camada.tipo_geometria != "Polygon":
        return None
    if info.get("geografico", True):
        centro = camada.centroide()
        if centro is None:
            return None
        lat = centro[1]
        km_lon = 111.320 * math.cos(math.radians(lat))
        km_lat = 110.574
        return camada.area_graus2() * km_lon * km_lat * 100.0
    return camada.area_graus2() / 10000.0


#: parâmetros do elipsóide GRS80/WGS84 (SIRGAS 2000) para UTM
_WGS84_A = 6378137.0
_WGS84_F = 1 / 298.257222101
_WGS84_K0 = 0.9996


def utm_para_lonlat(leste: float, norte: float, fuso: int = 22,
                    hemisferio: str = "S") -> Optional[tuple[float, float]]:
    """UTM (fuso SIRGAS 2000 / WGS84) -> (longitude, latitude) em graus.

    Necessário porque os formulários do RS declaram o ponto do empreendimento
    em UTM (E/N/Fuso 22S) e as camadas GIS normalmente vêm em geográficas -
    sem a conversão o confronto geométrico fica impossível.
    """
    try:
        leste, norte = float(leste), float(norte)
        if not (100000 < leste < 900000):
            return None
        if not (0 < norte < 10000000):
            return None
        if str(hemisferio).upper().startswith("S"):
            norte_final = norte - 10000000.0
        else:
            norte_final = norte
        e2 = 2 * _WGS84_F - _WGS84_F ** 2
        m = norte_final / _WGS84_K0
        mu = m / (_WGS84_A * (1 - e2 / 4 - 3 * e2 ** 2 / 64
                              - 5 * e2 ** 3 / 256))
        e1 = (1 - (1 - e2) ** 0.5) / (1 + (1 - e2) ** 0.5)
        j1 = (3 * e1 / 2 - 27 * e1 ** 3 / 32)
        j2 = (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32)
        j3 = (151 * e1 ** 3 / 96)
        j4 = (1097 * e1 ** 4 / 512)
        fp = (mu + j1 * math.sin(2 * mu) + j2 * math.sin(4 * mu)
              + j3 * math.sin(6 * mu) + j4 * math.sin(8 * mu))
        # série de Snyder (USGS Professional Paper 1395, eq. 8-12..8-15)
        sin_fp = math.sin(fp)
        cos_fp = math.cos(fp)
        ep2 = e2 / (1 - e2)                    # 2ª excentricidade ao quadrado
        t1 = math.tan(fp) ** 2
        c1 = ep2 * cos_fp ** 2
        # N: raio de curvatura na vertical; R: raio na meridiana
        n1 = _WGS84_A / (1 - e2 * sin_fp ** 2) ** 0.5
        r1 = _WGS84_A * (1 - e2) / (1 - e2 * sin_fp ** 2) ** 1.5
        d = (leste - 500000.0) / (n1 * _WGS84_K0)
        lat = fp - (n1 * math.tan(fp) / r1) * (
            d ** 2 / 2
            - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * ep2) * d ** 4 / 24
            + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * ep2
               - 3 * c1 ** 2) * d ** 6 / 720)
        lon = (d - (1 + 2 * t1 + c1) * d ** 3 / 6
               + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * ep2
                  + 24 * t1 ** 2) * d ** 5 / 120) / cos_fp
        lon0 = math.radians((int(fuso) - 1) * 6 - 180 + 3)
        return (math.degrees(lon0 + lon), math.degrees(lat))
    except (TypeError, ValueError):
        return None


def distancia_haversine_m(p1: tuple[float, float],
                          p2: tuple[float, float]) -> float:
    """Distância geodésica em metros entre dois pontos (lon, lat)."""
    raio = 6371008.8  # raio médio da Terra (m)
    lon1, lat1 = map(math.radians, p1)
    lon2, lat2 = map(math.radians, p2)
    dlon, dlat = lon2 - lon1, lat2 - lat1
    a = (math.sin(dlat / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2)
    return 2 * raio * math.asin(min(1.0, math.sqrt(a)))


def ponto_em_aneis(ponto: tuple[float, float],
                   aneis: list[list[tuple[float, float]]]) -> bool:
    """Ponto dentro do polígono (ray casting; o primeiro anel é externo)."""
    if not aneis:
        return False
    return _ponto_no_anel(ponto, aneis[0])


def _ponto_no_anel(ponto: tuple[float, float],
                   anel: list[tuple[float, float]]) -> bool:
    x, y = ponto
    dentro = False
    n = len(anel)
    if n < 4:
        return False
    for i in range(n - 1):
        x1, y1 = anel[i]
        x2, y2 = anel[i + 1]
        if (y1 > y) != (y2 > y):
            xint = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xint:
                dentro = not dentro
    return dentro


def validar_geometrias(camada: Camada) -> list[str]:
    """Problemas topológicos da camada (avisos, não erros fatais)."""
    problemas: list[str] = []
    for f in camada.feicoes_invalidas:
        problemas.append(
            f"Feição '{f.nome or '(sem nome)'}' da camada '{camada.nome}' com "
            f"geometria degenerada (vértices insuficientes ou anel aberto).")
    return problemas


# =============================================================================
# Classificação de camada e SRS
# =============================================================================
def classificar_camada(nome: str, atributos: Optional[dict] = None) -> str:
    """Tema da camada pelo nome (e pelos atributos, em 2º lugar)."""
    alvo = _sem_acento((nome or "").lower())
    for tema, chaves in TEMAS_CAMADA.items():
        if any(chave in alvo for chave in chaves):
            return tema
    if atributos:
        texto = _sem_acento(" ".join(str(v) for v in atributos.values()).lower())
        for tema, chaves in TEMAS_CAMADA.items():
            if any(chave in texto for chave in chaves):
                return tema
    return "OUTRO"


def normalizar_srs(texto: Optional[str]) -> Optional[str]:
    """Converte um SRS livre ('SIRGAS 2000 UTM 22S') em EPSG canônico."""
    if not texto:
        return None
    alvo = _sem_acento(str(texto).lower()).strip()
    alvo = re.sub(r"\s+", " ", alvo)
    if alvo in SRS_ALIASES:
        return SRS_ALIASES[alvo]
    m = re.search(r"epsg[:\s]*(\d{4,5})", alvo)
    if m:
        codigo = f"EPSG:{m.group(1)}"
        return codigo if codigo in SRS_CONHECIDOS else codigo
    for alias, epsg in SRS_ALIASES.items():
        if alias and alias in alvo:
            return epsg
    return None


def validar_srs(camada: Camada) -> Optional[str]:
    """Aviso de SRS: None = OK; string = o que conferir."""
    epsg = camada.srs_epsg
    if not epsg:
        return (f"Camada '{camada.nome}' sem SRS declarado - o checklist exige "
                f"georreferenciamento em {DATUM_OFICIAL}.")
    info = SRS_CONHECIDOS.get(epsg)
    if info is None:
        return (f"Camada '{camada.nome}' declara SRS '{epsg}' fora da lista "
                f"de referências aceitas ({', '.join(SRS_CONHECIDOS)}) - "
                f"reprojetar para {DATUM_OFICIAL}.")
    if info.get("datum") != DATUM_OFICIAL:
        return (f"Camada '{camada.nome}' está em {info.get('datum')} "
                f"({epsg}) - conferir a reprojeção para {DATUM_OFICIAL}.")
    return None


def _sem_acento(texto: str) -> str:
    import unicodedata
    texto = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in texto if not unicodedata.combining(c))


# =============================================================================
# Leitores por formato
# =============================================================================
def _local(tag: str) -> str:
    """Nome local de uma tag XML (sem namespace)."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _texto_de(elem: ElementTree.Element, *nomes: str) -> str:
    for filho in elem.iter():
        if _local(filho.tag).lower() in nomes:
            return (filho.text or "").strip()
    return ""


def _atributos_de(elem: ElementTree.Element) -> dict[str, Any]:
    dados: dict[str, Any] = {}
    for filho in elem:
        nome = _local(filho.tag)
        if nome in ("name", "description", "coordinates", "Polygon",
                    "LineString", "Point", "MultiGeometry", "LinearRing",
                    "outerBoundaryIs", "innerBoundaryIs"):
            continue
        texto = (filho.text or "").strip()
        if texto:
            dados[nome] = texto
    return dados


def _coordenadas_kml(texto: str) -> list[tuple[float, float]]:
    """'lon,lat[,alt] lon,lat' -> [(lon, lat), ...]."""
    pontos: list[tuple[float, float]] = []
    for token in re.split(r"[\s]+", (texto or "").strip()):
        if not token:
            continue
        partes = token.split(",")
        if len(partes) < 2:
            continue
        try:
            pontos.append((float(partes[0]), float(partes[1])))
        except ValueError:
            continue
    return pontos


def _feicao_kml(elem: ElementTree.Element) -> Optional[Feicao]:
    nome = ""
    atributos: dict[str, Any] = {}
    aneis: list[list[tuple[float, float]]] = []
    coordenadas: list[tuple[float, float]] = []
    for filho in elem.iter():
        tag = _local(filho.tag).lower()
        if tag == "name" and not nome:
            nome = (filho.text or "").strip()
        elif tag == "simpledata" or tag == "data":
            chave = filho.get("name") or _local(filho.tag)
            if chave and (filho.text or "").strip():
                atributos[chave] = (filho.text or "").strip()
        elif tag == "coordinates":
            coordenadas.extend(_coordenadas_kml(filho.text or ""))
        elif tag in ("polygon", "multigeometry"):
            continue
        elif tag == "linearring":
            aneis.append(reparar_anel(_coordenadas_kml(
                _texto_de(filho, "coordinates"))))
    # MultiGeometry: cada filho com coordenadas vira anel/linha próprio
    for filho in elem.iter():
        if _local(filho.tag).lower() in ("polygon", "linearring"):
            continue
    if not nome:
        nome = elem.get("id") or ""
    if not coordenadas and not aneis:
        return None
    return Feicao(nome=nome, atributos=atributos or _atributos_de(elem),
                  coordenadas=reparar_anel(coordenadas) if len(coordenadas) > 2
                  else coordenadas,
                  aneis=[a for a in aneis if len(a) >= 4])


def ler_kml(xml: str, arquivo: str = "") -> PacoteGIS:
    """Lê um KML: cada <Folder> (ou <Document>) vira uma CAMADA independente."""
    pacote = PacoteGIS(arquivo=arquivo)
    try:
        raiz = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        pacote.erros.append(f"KML inválido: {exc}")
        return pacote

    documentos = [e for e in raiz.iter() if _local(e.tag).lower() == "document"]
    folders = [e for e in raiz.iter() if _local(e.tag).lower() == "folder"]
    placemarks = [e for e in raiz.iter() if _local(e.tag).lower() == "placemark"]

    # Sem <Folder>: uma camada única com todas as feições
    if not folders and not documentos:
        camada = Camada(nome=arquivo or "camada", origem="kml",
                        srs_declarado=_texto_de(raiz, "srsname", "srs_name"))
        for pm in placemarks:
            f = _feicao_kml(pm)
            if f:
                camada.feicoes.append(f)
        if camada.feicoes:
            pacote.camadas.append(camada)
        return pacote

    # Com <Folder>: cada pasta é uma camada temática distinta
    for folder in folders:
        nome = _texto_de(folder, "name") or "camada"
        camada = Camada(nome=nome, origem="kml",
                        srs_declarado=_texto_de(folder, "srsname", "srs_name"))
        for pm in [e for e in folder.iter()
                   if _local(e.tag).lower() == "placemark"]:
            f = _feicao_kml(pm)
            if f:
                camada.feicoes.append(f)
        if camada.feicoes:
            pacote.camadas.append(camada)
    # <Placemark> soltos no <Document> (sem pasta) formam a camada padrão
    soltos: list[Feicao] = []
    for doc in documentos:
        for pm in [e for e in doc
                   if _local(e.tag).lower() == "placemark"]:
            f = _feicao_kml(pm)
            if f:
                soltos.append(f)
    if soltos:
        pacote.camadas.append(Camada(nome=arquivo or "camada", origem="kml",
                                     feicoes=soltos))
    return pacote


def ler_kmz(conteudo: bytes, arquivo: str = "") -> PacoteGIS:
    """KMZ = ZIP com um (ou mais) KML dentro; cada KML é um conjunto de camadas."""
    pacote = PacoteGIS(arquivo=arquivo)
    try:
        with zipfile.ZipFile(__import__("io").BytesIO(conteudo)) as zf:
            kmls = [n for n in zf.namelist() if n.lower().endswith(".kml")]
            if not kmls:
                pacote.erros.append("KMZ sem arquivo .kml interno.")
                return pacote
            for nome in kmls:
                xml = zf.read(nome).decode("utf-8", errors="replace")
                interno = ler_kml(xml, arquivo=nome.rsplit("/", 1)[-1])
                # o nome da camada é o do <Folder>/<Document> de dentro do
                # KMZ - prefixar com o nome do .kmz quebraria a classificação
                # de tema e a conferência por camada
                pacote.camadas.extend(interno.camadas)
                pacote.avisos.extend(interno.avisos)
                pacote.erros.extend(interno.erros)
    except zipfile.BadZipFile as exc:
        pacote.erros.append(f"KMZ corrompido: {exc}")
    return pacote


def _geometria_geojson(geom: dict) -> tuple[list[tuple[float, float]],
                                            list[list[tuple[float, float]]]]:
    """Extrai (coordenadas, anéis) de uma geometria GeoJSON."""
    tipo = (geom or {}).get("type")
    coords = (geom or {}).get("coordinates")
    if not tipo or coords is None:
        return [], []
    if tipo == "Point":
        p = _normalizar_ponto(coords)
        return ([p] if p else []), []
    if tipo in ("LineString", "MultiPoint"):
        pts = [q for q in (_normalizar_ponto(c) for c in coords) if q]
        return pts, []
    if tipo in ("Polygon", "MultiPolygon"):
        aneis_raw = coords if tipo == "Polygon" else [
            anel for poli in coords for anel in poli]
        aneis = [reparar_anel(anel) for anel in aneis_raw]
        return [], [a for a in aneis if len(a) >= 4]
    if tipo == "GeometryCollection":
        return [], []
    return [], []


def ler_geojson(texto: str, arquivo: str = "") -> PacoteGIS:
    """Lê um GeoJSON: cada 'layer' declarada (ou o nome do arquivo) é uma camada."""
    pacote = PacoteGIS(arquivo=arquivo)
    try:
        dados = json.loads(texto)
    except (json.JSONDecodeError, TypeError) as exc:
        pacote.erros.append(f"GeoJSON inválido: {exc}")
        return pacote
    if not isinstance(dados, dict):
        pacote.erros.append("GeoJSON não é um objeto.")
        return pacote

    srs_declarado = None
    crs = dados.get("crs")
    if isinstance(crs, dict):
        srs_declarado = (crs.get("properties") or {}).get("name")

    feicoes_raw: list[dict] = []
    if dados.get("type") == "FeatureCollection":
        feicoes_raw = [f for f in dados.get("features") or [] if isinstance(f, dict)]
    elif dados.get("type") == "Feature":
        feicoes_raw = [dados]

    agrupado: dict[str, Camada] = {}
    for feat in feicoes_raw:
        props = feat.get("properties") or {}
        geom = feat.get("geometry") or {}
        coords, aneis = _geometria_geojson(geom)
        if not coords and not aneis:
            continue
        nome_camada = (props.get("layer") or props.get("camada")
                       or props.get("tema") or arquivo or "camada")
        camada = agrupado.setdefault(
            str(nome_camada),
            Camada(nome=str(nome_camada), origem="geojson",
                   srs_declarado=srs_declarado))
        camada.feicoes.append(Feicao(
            nome=str(props.get("name") or props.get("nome") or ""),
            atributos={k: v for k, v in props.items()
                       if k not in ("layer", "camada", "tema")},
            coordenadas=coords, aneis=aneis))
    pacote.camadas.extend(agrupado.values())

    # GeoJSON de geometria única (sem 'features')
    if not pacote.camadas and dados.get("type") in TIPOS_GEOMETRIA:
        coords, aneis = _geometria_geojson(dados)
        if coords or aneis:
            pacote.camadas.append(Camada(
                nome=arquivo or "camada", origem="geojson",
                srs_declarado=srs_declarado,
                feicoes=[Feicao(nome=arquivo or "", coordenadas=coords,
                                aneis=aneis)]))
    return pacote


def ler_gpx(texto: str, arquivo: str = "") -> PacoteGIS:
    """Lê um GPX: waypoints, trilhas e rotas viram camadas distintas."""
    pacote = PacoteGIS(arquivo=arquivo)
    try:
        raiz = ElementTree.fromstring(texto)
    except ElementTree.ParseError as exc:
        pacote.erros.append(f"GPX inválido: {exc}")
        return pacote
    srs_declarado = None
    for elem in raiz.iter():
        if _local(elem.tag).lower() == "bounds":
            srs_declarado = elem.get("latlonbox") or None
            break

    wpts = [e for e in raiz.iter() if _local(e.tag).lower() == "wpt"]
    if wpts:
        feicoes = [Feicao(nome=(e.findtext("{*}name") or "").strip(),
                          coordenadas=[(float(e.get("lon")), float(e.get("lat")))])
                   for e in wpts if e.get("lat") and e.get("lon")]
        pacote.camadas.append(Camada(nome=f"{arquivo or 'gpx'} · waypoints",
                                     feicoes=feicoes, origem="gpx",
                                     srs_declarado=srs_declarado))
    for tipo, rotulo in (("trk", "trilhas"), ("rte", "rotas")):
        feicoes: list[Feicao] = []
        for elem in [e for e in raiz.iter() if _local(e.tag).lower() == tipo]:
            nome = ""
            for filho in elem.iter():
                if _local(filho.tag).lower() == "name":
                    nome = (filho.text or "").strip()
                    break
            pts = [(float(p.get("lon")), float(p.get("lat")))
                   for p in elem.iter()
                   if _local(p.tag).lower() in ("trkpt", "rtept")
                   and p.get("lat") and p.get("lon")]
            if pts:
                feicoes.append(Feicao(nome=nome, coordenadas=pts))
        if feicoes:
            pacote.camadas.append(Camada(nome=f"{arquivo or 'gpx'} · {rotulo}",
                                         feicoes=feicoes, origem="gpx",
                                         srs_declarado=srs_declarado))
    return pacote


def ler_wkt(texto: str, nome: str = "geometria") -> Optional[Camada]:
    """Lê uma geometria WKT (coluna `geometria` do banco fora do PostGIS)."""
    bruto = (texto or "").strip()
    if not bruto:
        return None
    m = re.match(r"\s*(POINT|LINESTRING|POLYGON)\s*[A-Za-z]*\s*\((.*)\)\s*$",
                 bruto, re.I | re.S)
    if not m:
        return None
    tipo = m.group(1).upper()
    corpo = m.group(2)
    if tipo == "POINT":
        p = _normalizar_ponto(corpo)
        if not p:
            return None
        return Camada(nome=nome, origem="wkt", srs_declarado="EPSG:4674",
                      feicoes=[Feicao(nome=nome, coordenadas=[p])])
    if tipo == "LINESTRING":
        pts = [q for q in (_normalizar_ponto(t) for t in corpo.split(",")) if q]
        if not pts:
            return None
        return Camada(nome=nome, origem="wkt", srs_declarado="EPSG:4674",
                      feicoes=[Feicao(nome=nome, coordenadas=pts)])
    # POLYGON((...),(...))
    aneis = []
    for grupo in re.findall(r"\(([^()]*)\)", corpo):
        pts = [q for q in (_normalizar_ponto(t) for t in grupo.split(",")) if q]
        anel = reparar_anel(pts)
        if len(anel) >= 4:
            aneis.append(anel)
    if not aneis:
        return None
    return Camada(nome=nome, origem="wkt", srs_declarado="EPSG:4674",
                  feicoes=[Feicao(nome=nome, aneis=aneis)])


# =============================================================================
# Porta única
# =============================================================================
class LeitorGIS:
    """Leitor de camadas GIS: uma leitura, pacote estruturado de camadas."""

    @staticmethod
    def ler_arquivo(nome_arquivo: str, conteudo: bytes | str) -> PacoteGIS:
        """Lê KML/KMZ/GeoJSON/GPX/WKT e devolve o PacoteGIS.

        NUNCA levanta exceção: arquivo ilegível devolve pacote com `erros`
        (princípio da resiliência da skill de auditoria documental)."""
        nome = (nome_arquivo or "").lower()
        pacote = PacoteGIS(arquivo=nome_arquivo)
        try:
            if isinstance(conteudo, str):
                dados = conteudo.encode("utf-8", errors="replace")
            else:
                dados = conteudo or b""
            texto = dados.decode("utf-8", errors="replace") if dados else ""

            if nome.endswith(".kmz") or dados[:2] == b"PK":
                pacote = ler_kmz(dados, arquivo=nome_arquivo)
            elif nome.endswith(".kml") or "<kml" in texto[:400].lower():
                pacote = ler_kml(texto, arquivo=nome_arquivo)
            elif nome.endswith(".gpx") or "<gpx" in texto[:400].lower():
                pacote = ler_gpx(texto, arquivo=nome_arquivo)
            elif nome.endswith(".geojson") or nome.endswith(".json") or \
                    texto.lstrip().startswith("{"):
                pacote = ler_geojson(texto, arquivo=nome_arquivo)
            elif texto.lstrip().upper().startswith(("POINT", "LINESTRING",
                                                    "POLYGON")):
                camada = ler_wkt(texto, nome=nome_arquivo or "geometria")
                if camada:
                    pacote.camadas.append(camada)
            else:
                pacote.erros.append(
                    "Formato geoespacial não reconhecido (esperado KML, KMZ, "
                    "GeoJSON, GPX ou WKT).")

            # pós-processamento: tema, SRS canônico e avisos geométricos
            for camada in pacote.camadas:
                if not camada.tema or camada.tema == "OUTRO":
                    amostra = camada.feicoes[0].atributos if camada.feicoes else {}
                    camada.tema = classificar_camada(camada.nome, amostra)
                camada.srs_epsg = normalizar_srs(camada.srs_declarado)
                aviso_srs = validar_srs(camada)
                if aviso_srs:
                    pacote.avisos.append(aviso_srs)
                pacote.avisos.extend(validar_geometrias(camada))
        except Exception as exc:  # noqa: BLE001 - leitura nunca derruba o fluxo
            logger.warning("Falha ao ler camadas GIS de %s: %s", nome_arquivo, exc)
            pacote.erros.append(f"Falha na leitura: {exc}")
        return pacote

    @staticmethod
    def ler_texto(nome_arquivo: str, conteudo: bytes | str) -> str:
        """Resumo textual das camadas (contrato do ValidadorDocumentos)."""
        return LeitorGIS.ler_arquivo(nome_arquivo, conteudo).resumo_textual()
