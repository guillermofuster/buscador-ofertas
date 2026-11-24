# Buscador de ofertas

Busca cada día ofertas de arte 3D para videojuegos (prop, environment, hard surface, vehículos, 3D generalist) en Europa con **JobSpy** (LinkedIn e Indeed), la **API de Adzuna** y la **API de Jooble**. Filtra seniors/leads, otros puestos, iGaming/arquitectura y ofertas que pidan 4+ años, y guarda el resultado en el repo.

- `OFERTAS.md`: tabla de las ofertas vigentes (últimos 21 días), ordenadas por encaje.
- `datos/nuevas.json`: lo que apareció en la última ejecución y por qué se descartó el resto.
- `datos/ofertas.json`: histórico, para no repetir ofertas.

## Puesta en marcha (10 minutos)

1. **Crear el repo**: en GitHub → *New repository* → nombre `buscador-ofertas`, público → *Create*. Luego *Add file → Upload files* y arrastra todo el contenido de esta carpeta (incluida la carpeta `.github`; si el navegador la oculta, créala a mano con *Add file → Create new file* y el nombre `.github/workflows/buscar.yml`).
2. **Claves de Adzuna**: regístrate en https://developer.adzuna.com → *Dashboard* → copia `Application ID` y `Application Key`.
3. **Clave de Jooble**: pide la clave en https://jooble.org/api/about (llega por email).
4. **Guardar las claves como secretos** (nunca en el código, el repo es público): repo → *Settings → Secrets and variables → Actions → New repository secret*, crea `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` y `JOOBLE_KEY`.
5. **Probar**: pestaña *Actions* → *Buscar ofertas* → *Run workflow*. Al acabar (5–15 min) aparece `OFERTAS.md` actualizado. Después se ejecuta solo cada día.

## Ajustes

Todo está en `config.yaml`: términos de búsqueda, países, palabras que excluyen títulos o sectores, años máximos y la lista de estudios que ya sigues (sus ofertas salen marcadas “en tu lista”; con `ocultar_empresas_en_lista: true` se ocultan).

## Avisos

- LinkedIn bloquea a menudo las IPs de servidores (error 429). El buscador sigue con el resto de fuentes; Indeed, Adzuna y Jooble son las más estables.
- El repo es público: cualquiera puede ver qué ofertas guardas. Las claves no se ven porque van en *Secrets*.

## En local

```bash
pip install -r requirements.txt
export ADZUNA_APP_ID=... ADZUNA_APP_KEY=... JOOBLE_KEY=...
python buscador.py
```
