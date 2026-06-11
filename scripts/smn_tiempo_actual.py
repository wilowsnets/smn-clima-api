import requests
import zipfile
import io
import json
import re
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

URL_TIEMPO = "https://ssl.smn.gob.ar/dpd/zipopendata.php?dato=tiepre"

OUT_DIR = Path("docs/data")
OUT_DIR.mkdir(parents=True, exist_ok=True)

MESES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

TZ_AR = ZoneInfo("America/Argentina/Buenos_Aires")


def parse_float(valor):
    valor = valor.strip().replace(",", ".")
    if not valor or "No se calcula" in valor or "Sin información" in valor:
        return None
    return float(valor)


def parse_int(valor):
    valor = valor.strip()
    if not valor:
        return None
    return int(valor)


def parse_fecha_hora(fecha_txt, hora_txt):
    dia, mes, anio = fecha_txt.strip().lower().split("-")
    hora, minuto = hora_txt.strip().split(":")
    return datetime(
        int(anio),
        MESES[mes],
        int(dia),
        int(hora),
        int(minuto),
        tzinfo=TZ_AR,
    )


def parse_viento(viento_txt):
    viento_txt = viento_txt.strip()

    if viento_txt == "Calma":
        return "Calma", 0

    m = re.match(r"(.+?)\s+(\d+)$", viento_txt)
    if not m:
        return viento_txt, None

    direccion = m.group(1).strip()
    velocidad = int(m.group(2))
    return direccion, velocidad


def descargar_tiepre():
    r = requests.get(URL_TIEMPO, timeout=30)
    r.raise_for_status()

    z = zipfile.ZipFile(io.BytesIO(r.content))
    nombre = z.namelist()[0]

    with z.open(nombre) as f:
        texto = f.read().decode("latin-1")

    return texto


def parsear_linea(linea):
    linea = linea.strip()

    if not linea:
        return None

    linea = linea.rstrip("/").strip()
    partes = [p.strip() for p in linea.split(";")]

    if len(partes) < 10:
        return None

    estacion = partes[0]
    fecha_hora = parse_fecha_hora(partes[1], partes[2])
    estado = partes[3]
    visibilidad = partes[4]
    temperatura = parse_float(partes[5])
    termica = parse_float(partes[6])
    humedad = parse_int(partes[7])
    viento_dir, viento_vel = parse_viento(partes[8])
    presion = parse_float(partes[9])

    return {
        "estacion": estacion,
        "fecha_hora": fecha_hora.isoformat(),
        "estado": estado,
        "visibilidad": visibilidad,
        "temperatura": temperatura,
        "termica": termica,
        "humedad": humedad,
        "viento_dir": viento_dir,
        "viento_vel": viento_vel,
        "presion": presion,
    }


def cargar_json_anterior(path):
    if not path.exists():
        return None

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def calcular_top(estaciones):
    validas_temp = [
        e for e in estaciones.values()
        if e.get("temperatura") is not None
    ]
    validas_viento = [
        e for e in estaciones.values()
        if e.get("viento_vel") is not None
    ]
    validas_humedad = [
        e for e in estaciones.values()
        if e.get("humedad") is not None
    ]

    return {
        "mas_calor": max(validas_temp, key=lambda e: e["temperatura"]) if validas_temp else None,
        "mas_frio": min(validas_temp, key=lambda e: e["temperatura"]) if validas_temp else None,
        "mayor_viento": max(validas_viento, key=lambda e: e["viento_vel"]) if validas_viento else None,
        "mayor_humedad": max(validas_humedad, key=lambda e: e["humedad"]) if validas_humedad else None,
    }


def marcar_estado(data, ok=True, mensaje=None):
    ahora_utc = datetime.now(timezone.utc)
    ahora_ar = datetime.now(TZ_AR)

    fechas = []
    for e in data.get("estaciones", {}).values():
        try:
            fechas.append(datetime.fromisoformat(e["fecha_hora"]))
        except Exception:
            pass

    if fechas:
        ultima_smn = max(fechas)
        edad_minutos = int((ahora_ar - ultima_smn).total_seconds() / 60)
    else:
        ultima_smn = None
        edad_minutos = None

    desactualizado = edad_minutos is None or edad_minutos > 180

    data["actualizado_github"] = ahora_utc.isoformat().replace("+00:00", "Z")
    data["ultima_actualizacion_smn"] = ultima_smn.isoformat() if ultima_smn else None
    data["edad_minutos"] = edad_minutos
    data["estado"] = "ok" if ok and not desactualizado else "desactualizado"
    data["desactualizado"] = data["estado"] != "ok"

    if mensaje:
        data["mensaje"] = mensaje
    elif data["estado"] == "ok":
        data["mensaje"] = "Datos actualizados."
    else:
        data["mensaje"] = "No se pudo actualizar desde SMN o los datos son antiguos. Se muestran los últimos datos disponibles."

    return data


def main():
    tiempo_path = OUT_DIR / "tiempo_actual.json"
    top_path = OUT_DIR / "top_nacional.json"

    anterior = cargar_json_anterior(tiempo_path)

    try:
        texto = descargar_tiepre()
        estaciones_lista = []

        for linea in texto.splitlines():
            item = parsear_linea(linea)
            if item:
                estaciones_lista.append(item)

        estaciones = {
            item["estacion"].upper(): item
            for item in estaciones_lista
        }

        data = {
            "fuente": "Servicio Meteorológico Nacional",
            "producto": "tiepre",
            "url_fuente": URL_TIEMPO,
            "estaciones": estaciones,
        }

        data = marcar_estado(data, ok=True)

    except Exception as e:
        if anterior:
            data = marcar_estado(
                anterior,
                ok=False,
                mensaje=f"No se pudo actualizar desde SMN. Se muestran los últimos datos disponibles. Error: {e}",
            )
        else:
            data = {
                "fuente": "Servicio Meteorológico Nacional",
                "producto": "tiepre",
                "url_fuente": URL_TIEMPO,
                "estado": "sin_datos",
                "desactualizado": True,
                "mensaje": f"No hay datos disponibles. Error: {e}",
                "estaciones": {},
                "actualizado_github": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }

    tiempo_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    top = {
        "fuente": data["fuente"],
        "producto": "top_nacional",
        "actualizado_github": data.get("actualizado_github"),
        "ultima_actualizacion_smn": data.get("ultima_actualizacion_smn"),
        "estado": data.get("estado"),
        "desactualizado": data.get("desactualizado"),
        "mensaje": data.get("mensaje"),
        **calcular_top(data.get("estaciones", {})),
    }

    top_path.write_text(
        json.dumps(top, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("Generado:", tiempo_path)
    print("Generado:", top_path)


if __name__ == "__main__":
    main()
