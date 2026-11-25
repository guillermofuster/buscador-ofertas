"""Buscador de ofertas de arte 3D para videojuegos (JobSpy + Adzuna + Jooble + portales de videojuegos).

Uso: python buscador.py
Claves (variables de entorno, opcionales):
  ADZUNA_APP_ID, ADZUNA_APP_KEY, JOOBLE_KEY
Salida:
  datos/ofertas.json   todas las ofertas vistas (histórico, sin repetir)
  datos/nuevas.json    las que aparecieron en esta ejecución
  OFERTAS.md           tabla legible de las ofertas vigentes
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml

RAIZ = Path(__file__).parent
DATOS = RAIZ / "datos"
HOY = datetime.now(timezone.utc).date()
UA = {"User-Agent": "buscador-ofertas/1.0"}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def sha_id(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:20]


def norm_empresa(n: str) -> str:
    n = re.sub(r"\(.*?\)", " ", (n or "").lower())
    n = re.sub(r"\b(studios?|games?|entertainment|interactive|s\.?a\.?|s\.?l\.?|sp\.? z o\.?o\.?|ltd|inc|gmbh|ab|bv)\b", " ", n)
    return re.sub(r"[^a-z0-9]+", "", n)


def fecha_iso(v) -> str | None:
    if v is None or v != v:  # None o NaN
        return None
    try:
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v / (1000 if v > 1e11 else 1), timezone.utc).date().isoformat()
        if isinstance(v, (date, datetime)):
            return v.isoformat()[:10]
        s = str(v)
        m = re.match(r"\d{4}-\d{2}-\d{2}", s)
        return m.group(0) if m else None
    except Exception:
        return None


def oferta(**k) -> dict:
    base = dict(empresa=None, puesto=None, url=None, fuente=None, pais=None,
                ubicacion=None, remoto=False, publicada=None, descripcion="", salario=None)
    base.update(k)
    base["descripcion"] = (base["descripcion"] or "")[:4000]
    return base


# ---------------------------------------------------------------- fuentes
def buscar_jobspy(cfg) -> list[dict]:
    try:
        from jobspy import scrape_jobs
    except ImportError:
        log("JobSpy no instalado; se omite")
        return []
    c = cfg["jobspy"]
    horas = cfg["dias_max"] * 24
    out = []
    for ub in c["ubicaciones"]:
        for termino in cfg["busquedas"]:
            try:
                # Indeed no admite hours_old junto con is_remote: en las búsquedas en remoto
                # no se pasa hours_old y la antigüedad se filtra después en evaluar().
                extra = {"is_remote": True} if ub.get("remoto") else {"hours_old": horas}
                df = scrape_jobs(
                    site_name=c["sitios"], search_term=termino, location=ub["location"],
                    country_indeed=ub.get("country", "spain"),
                    results_wanted=c["resultados_por_busqueda"],
                    linkedin_fetch_description=True, description_format="markdown", verbose=0,
                    **extra,
                )
            except Exception as e:
                log(f"JobSpy error {termino}/{ub['location']}: {e}")
                continue
            df = df.astype(object).where(df.notna(), None)
            for r in df.to_dict("records"):
                url = r.get("job_url") or r.get("job_url_direct")
                if not url:
                    continue
                out.append(oferta(
                    empresa=r.get("company"), puesto=r.get("title"), url=r.get("job_url") or url,
                    candidatura_url=r.get("job_url_direct"),
                    fuente=str(r.get("site", "jobspy")).capitalize(), ubicacion=r.get("location"),
                    pais=ub["location"], remoto=bool(r.get("is_remote")) or ub.get("remoto", False),
                    publicada=fecha_iso(r.get("date_posted")), descripcion=r.get("description") or "",
                ))
            time.sleep(2)
    log(f"JobSpy: {len(out)}")
    return out


def buscar_adzuna(cfg) -> list[dict]:
    app_id, app_key = os.getenv("ADZUNA_APP_ID"), os.getenv("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        log("Adzuna sin claves; se omite")
        return []
    out = []
    for pais in cfg["adzuna"]["paises"]:
        for termino in cfg["busquedas"]:
            url = f"https://api.adzuna.com/v1/api/jobs/{pais}/search/1"
            params = dict(app_id=app_id, app_key=app_key, what_phrase=termino,
                          max_days_old=cfg["dias_max"], results_per_page=50, sort_by="date")
            try:
                r = requests.get(url, params=params, headers=UA, timeout=30)
                r.raise_for_status()
                res = r.json().get("results", [])
            except Exception as e:
                log(f"Adzuna error {pais}/{termino}: {e}")
                continue
            for j in res:
                out.append(oferta(
                    empresa=(j.get("company") or {}).get("display_name"), puesto=j.get("title"),
                    url=j.get("redirect_url"), fuente="Adzuna", pais=pais.upper(),
                    ubicacion=(j.get("location") or {}).get("display_name"),
                    publicada=fecha_iso(j.get("created")), descripcion=j.get("description", ""),
                    salario=j.get("salary_min"),
                ))
            time.sleep(1)
    log(f"Adzuna: {len(out)}")
    return out


def buscar_jooble(cfg) -> list[dict]:
    key = os.getenv("JOOBLE_KEY")
    if not key:
        log("Jooble sin clave; se omite")
        return []
    out = []
    for ub in cfg["jooble"]["ubicaciones"]:
        for termino in cfg["busquedas"]:
            try:
                r = requests.post(f"https://jooble.org/api/{key}", headers=UA, timeout=30,
                                  json={"keywords": termino, "location": ub, "page": "1"})
                r.raise_for_status()
                res = r.json().get("jobs", [])
            except Exception as e:
                log(f"Jooble error {ub}/{termino}: {e}")
                continue
            for j in res:
                out.append(oferta(
                    empresa=j.get("company"), puesto=re.sub("<[^>]+>", "", j.get("title") or ""),
                    url=j.get("link"), fuente="Jooble", pais=ub, ubicacion=j.get("location"),
                    remoto=ub.lower() == "remote" or "remote" in (j.get("type") or "").lower(),
                    publicada=fecha_iso(j.get("updated")),
                    descripcion=re.sub("<[^>]+>", "", j.get("snippet") or ""),
                    salario=j.get("salary") or None,
                ))
            time.sleep(1)
    log(f"Jooble: {len(out)}")
    return out


# ---------------------------------------------------------------- portales de empleo de videojuegos
UA_WEB = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def _get(url, **kw):
    r = requests.get(url, headers=UA_WEB, timeout=30, **kw)
    r.raise_for_status()
    return r.text


def _txt(h: str) -> str:
    h = re.sub(r"<[^>]+>", " ", h or "")
    for a, b in (("&amp;", "&"), ("&#43;", "+"), ("&#38;", "&"), ("&#039;", "'"), ("&quot;", '"'), ("&lt;", "<"), ("&gt;", ">"), ("&nbsp;", " ")):
        h = h.replace(a, b)
    return re.sub(r"\s+", " ", h).strip()


def _rss_fecha(s: str) -> str | None:
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%d %b %Y %H:%M:%S %z"):
        try:
            return datetime.strptime(s.strip(), fmt).date().isoformat()
        except Exception:
            pass
    return None


def _hace(s: str) -> str | None:
    """'15 days ago', '<1d', '3d', '2 weeks ago' -> fecha ISO aproximada."""
    s = (s or "").lower()
    if "<1d" in s or "today" in s or "hour" in s or "minute" in s:
        return HOY.isoformat()
    m = re.search(r"(\d+)\s*(d|day|w|week|mo|month)", s)
    if not m:
        return None
    n, u = int(m.group(1)), m.group(2)
    dias = n * (30 if u.startswith("mo") else 7 if u.startswith("w") else 1)
    return (HOY - timedelta(days=dias)).isoformat()


def buscar_portales(cfg) -> list[dict]:
    activos = cfg.get("portales") or {}
    out = []

    if activos.get("remotegamejobs", True):  # RSS con las últimas ofertas en remoto
        try:
            x = _get("https://remotegamejobs.com/feed.rss")
            for it in re.findall(r"<item>(.*?)</item>", x, re.S):
                t = _txt((re.search(r"<title>(.*?)</title>", it, re.S) or [None, ""])[1])
                m = re.match(r"(.+?) is hiring (.+?)(?: \(Remote Job\))?$", t)
                link = (re.search(r"<link>(.*?)</link>", it, re.S) or [None, ""])[1].strip()
                if not (m and link):
                    continue
                out.append(oferta(empresa=m.group(1), puesto=m.group(2), url=link, fuente="RemoteGameJobs",
                                  pais="Remoto", ubicacion="Remoto", remoto=True,
                                  publicada=_rss_fecha((re.search(r"<pubDate>(.*?)</pubDate>", it) or [None, ""])[1]),
                                  descripcion=_txt((re.search(r"<description>(.*?)</description>", it, re.S) or [None, ""])[1])))
        except Exception as e:
            log(f"RemoteGameJobs error: {e}")

    if activos.get("stratos", True):  # RSS de ofertas de Stratos (España)
        try:
            x = _get("https://stratos-ad.com/rss?source=jobs")
            for it in re.findall(r"<item>(.*?)</item>", x, re.S):
                t = _txt((re.search(r"<title>(.*?)</title>", it, re.S) or [None, ""])[1])
                m = re.match(r"(.+?) busca (.+)$", t)
                link = (re.search(r"<link>(.*?)</link>", it, re.S) or [None, ""])[1].strip()
                if not (m and link):
                    continue
                desc = _txt(re.sub(r"<!\[CDATA\[|\]\]>", "", (re.search(r"<description>(.*?)</description>", it, re.S) or [None, ""])[1]))
                loc = re.search(r"(?:Localizaci[oó]n|Ubicaci[oó]n|Ciudad)\s*:\s*([^.<]{2,60})", desc)
                out.append(oferta(empresa=m.group(1), puesto=m.group(2).strip(), url=link, fuente="Stratos",
                                  pais="España", ubicacion=loc.group(1).strip() if loc else "España",
                                  remoto="remoto" in desc.lower() or "teletrabajo" in desc.lower(),
                                  publicada=_rss_fecha((re.search(r"<pubDate>(.*?)</pubDate>", it) or [None, ""])[1]),
                                  descripcion=desc))
        except Exception as e:
            log(f"Stratos error: {e}")

    if activos.get("gamejobs_co", True):  # buscador de gamejobs.co
        vistos = set()
        for termino in cfg["busquedas"]:
            try:
                x = _get("https://gamejobs.co/search", params={"q": termino})
            except Exception as e:
                log(f"gamejobs.co error {termino}: {e}")
                continue
            for b in re.findall(r'<div class="job">(.*?)</form>', x, re.S):
                a = re.search(r'<a class="title" href="([^"]+)">(.*?)</a>', b, re.S)
                if not a or a.group(1) in vistos:
                    continue
                vistos.add(a.group(1))
                emp = re.search(r'class="c">(.*?)</a>', b, re.S)
                ubi = re.search(r'class="w">(.*?)</a>', b, re.S)
                cuando = re.search(r"·\s*([^·<]*ago)", b)
                out.append(oferta(empresa=_txt(emp.group(1)) if emp else None, puesto=_txt(a.group(2)),
                                  url="https://gamejobs.co" + a.group(1), fuente="GameJobs.co",
                                  ubicacion=_txt(ubi.group(1)) if ubi else None, pais=_txt(ubi.group(1)) if ubi else None,
                                  remoto="remote" in (_txt(ubi.group(1)) if ubi else "").lower(),
                                  publicada=_hace(cuando.group(1)) if cuando else None))
            time.sleep(1)

    if activos.get("gamesindustry", True):  # jobs.gamesindustry.biz
        vistos = set()
        for termino in cfg["busquedas"]:
            try:
                x = _get("https://jobs.gamesindustry.biz/jobs", params={"keywords": termino})
            except Exception as e:
                log(f"GamesIndustry error {termino}: {e}")
                continue
            for art in re.findall(r'<article id="node-\d+".*?</article>', x, re.S):
                a = re.search(r'<h2 class="node__title">\s*<a href="([^"]+)"[^>]*>(.*?)</a>', art, re.S)
                if not a or a.group(1) in vistos:
                    continue
                vistos.add(a.group(1))
                emp = re.search(r'job-organization"><a[^>]*>(.*?)</a>', art, re.S)
                ubi = re.search(r'<div class="location">\s*<span>(.*?)</span>', art, re.S)
                fec = re.search(r'<span class="date">\s*([^,<]+)', art)
                pub = None
                if fec:
                    try:
                        pub = datetime.strptime(fec.group(1).strip(), "%d %b %Y").date().isoformat()
                    except Exception:
                        pass
                u = _txt(ubi.group(1)) if ubi else None
                out.append(oferta(empresa=_txt(emp.group(1)) if emp else None, puesto=_txt(a.group(2)), url=a.group(1),
                                  fuente="GamesIndustry.biz", ubicacion=u, pais=u, remoto="remote" in (u or "").lower(),
                                  publicada=pub))
            time.sleep(1)

    if activos.get("grackle", True):  # últimas ofertas de Grackle (agrega webs de estudios)
        try:
            x = _get("https://gracklehq.com/jobs")
            for a, t, eu, cuando in re.findall(r'<a href="(/rd/\d+)"[^>]*>(.*?)</a>\s*<div>(.*?)</div>\s*<div class="bottomright">(.*?)</div>', x, re.S):
                eu = _txt(eu)
                emp, _, ubi = eu.partition(" - ")
                out.append(oferta(empresa=emp, puesto=_txt(t), url="https://gracklehq.com" + a, fuente="Grackle",
                                  ubicacion=ubi or None, pais=ubi or None, remoto="remote" in eu.lower(),
                                  publicada=_hace(_txt(cuando))))
        except Exception as e:
            log(f"Grackle error: {e}")

    log(f"Portales: {len(out)}")
    return out


# ---------------------------------------------------------------- filtros
def contiene(texto: str, palabras) -> str | None:
    t = f" {texto.lower()} "
    for p in palabras:
        if re.search(r"(?<![a-z0-9])" + re.escape(p.lower()), t):
            return p
    return None


def anios_pedidos(desc: str) -> int | None:
    m = re.findall(r"(\d{1,2})\s*\+?\s*(?:-\s*\d+\s*)?(?:years|yrs|year|años|lat)", desc.lower())
    nums = [int(x) for x in m if 0 < int(x) < 20]
    return min(nums) if nums else None


def evaluar(o: dict, cfg) -> tuple[bool, str]:
    f = cfg["filtros"]
    titulo = (o["puesto"] or "")
    if not contiene(titulo, f["titulo_incluye"]):
        return False, "título no es de arte 3D"
    if (p := contiene(titulo, f["titulo_excluye"])):
        return False, f"título excluido ({p.strip()})"
    texto = f"{o['empresa'] or ''} {o['descripcion']}"
    if (p := contiene(texto, f["sector_excluye"])):
        return False, f"sector excluido ({p})"
    lugar = f"{o.get('ubicacion') or ''} {o.get('pais') or ''}"
    for p in f.get("lugares_excluye") or []:
        if re.search(r"(?<![a-z])" + re.escape(p.lower()) + r"(?![a-z])", lugar.lower()):
            return False, f"lugar excluido ({p})"
    if re.search(r",\s*(ca|ny|tx|wa|fl|nc|oh|al|ks|ma|il|ga|az|ut|mi|pa|nj|va|md|mn|co|or)\s*$", (o.get("ubicacion") or "").lower()):
        return False, "lugar excluido (EEUU)"
    a = anios_pedidos(o["descripcion"])
    if a and a >= f["anios_max"]:
        return False, f"pide {a}+ años"
    if o["publicada"]:
        if date.fromisoformat(o["publicada"]) < HOY - timedelta(days=cfg["dias_max"]):
            return False, "antigua"
    return True, ""


def encaje(o: dict, cfg) -> int:
    t = (o["puesto"] or "").lower()
    d = o["descripcion"].lower()
    s = 3
    if any(k in t for k in ("hard surface", "hard-surface", "vehicle", "prop", "environment", "weapon")):
        s += 1
    if "junior" in t or "jr" in t:
        s += 1
    if any(k in d for k in ("unreal", "ue5", "substance", "realistic", "hard surface", "modular")):
        s += 1
    if contiene(d, cfg["filtros"]["idiomas_penalizan"]):
        s -= 1
    if any(k in d for k in ("stylized", "stylised", "cartoon")) and "realistic" not in d:
        s -= 1
    if (o["pais"] or "").lower() in ("poland", "pl"):
        s += 1
    return max(1, min(5, s))


# ---------------------------------------------------------------- principal
def main():
    cfg = yaml.safe_load((RAIZ / "config.yaml").read_text(encoding="utf-8"))
    DATOS.mkdir(exist_ok=True)
    ruta_hist = DATOS / "ofertas.json"
    historico: dict = json.loads(ruta_hist.read_text()) if ruta_hist.exists() else {}
    vistas_clave = {f"{norm_empresa(v['empresa'])}|{(v['puesto'] or '').lower().strip()}" for v in historico.values()}
    en_lista = {norm_empresa(e) for e in cfg.get("empresas_en_lista") or []}

    brutas = buscar_jobspy(cfg) + buscar_adzuna(cfg) + buscar_jooble(cfg) + buscar_portales(cfg)
    log(f"Total bruto: {len(brutas)}")

    nuevas, descartes = [], {}
    for o in brutas:
        if not o["url"] or not o["puesto"]:
            continue
        oid = sha_id(o["url"])
        clave = f"{norm_empresa(o['empresa'])}|{o['puesto'].lower().strip()}"
        if oid in historico or clave in vistas_clave:
            continue
        o["en_lista"] = norm_empresa(o["empresa"]) in en_lista
        if o["en_lista"] and cfg.get("ocultar_empresas_en_lista"):
            descartes["empresa ya en tu lista"] = descartes.get("empresa ya en tu lista", 0) + 1
            continue
        ok, motivo = evaluar(o, cfg)
        if not ok:
            descartes[motivo] = descartes.get(motivo, 0) + 1
            continue
        o["id"] = oid
        o["encaje"] = encaje(o, cfg)
        o["encontrada"] = HOY.isoformat()
        o["anios"] = anios_pedidos(o["descripcion"])
        vistas_clave.add(clave)
        historico[oid] = o
        nuevas.append(o)

    nuevas.sort(key=lambda x: (-x["encaje"], x["publicada"] or ""), reverse=False)
    ruta_hist.write_text(json.dumps(historico, ensure_ascii=False, indent=1))
    (DATOS / "nuevas.json").write_text(json.dumps(
        {"fecha": HOY.isoformat(), "brutas": len(brutas), "nuevas": nuevas, "descartes": descartes},
        ensure_ascii=False, indent=1))
    escribir_md(historico, cfg)
    log(f"Nuevas: {len(nuevas)} | Descartes: {descartes}")


def escribir_md(historico: dict, cfg):
    limite = (HOY - timedelta(days=cfg["dias_max"])).isoformat()
    vigentes = [o for o in historico.values() if (o["publicada"] or o["encontrada"]) >= limite]
    vigentes.sort(key=lambda o: (-o["encaje"], o["encontrada"]), reverse=False)
    filas = ["| Encaje | Puesto | Empresa | Lugar | Publicada | Encontrada | Fuente |",
             "|---|---|---|---|---|---|---|"]
    for o in vigentes:
        esc = lambda s: str(s or "").replace("|", "/")
        filas.append(f"| {'★' * o['encaje']} | [{esc(o['puesto'])}]({o['url']}) | {esc(o['empresa'])}{' (en tu lista)' if o.get('en_lista') else ''} | "
                     f"{esc(o['ubicacion'] or o['pais'])}{' (remoto)' if o['remoto'] else ''} | "
                     f"{o['publicada'] or '?'} | {o['encontrada']} | {o['fuente']} |")
    (RAIZ / "OFERTAS.md").write_text(
        f"# Ofertas vigentes\n\nActualizado: {HOY.isoformat()} · {len(vigentes)} ofertas de los últimos "
        f"{cfg['dias_max']} días.\n\n" + "\n".join(filas) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
