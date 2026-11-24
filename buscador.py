"""Buscador de ofertas de arte 3D para videojuegos (JobSpy + Adzuna + Jooble).

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

    brutas = buscar_jobspy(cfg) + buscar_adzuna(cfg) + buscar_jooble(cfg)
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
