#!/usr/bin/env python3
"""
Bot de value betting en Over/Under de goles (fútbol), líneas 1.5 / 2.5 / 3.5 / 4.5.

Idea (la única que es defendible con solo cuotas):
  1) Se quita el margen (vig) de CADA casa con el método de potencia.
  2) La probabilidad "justa" sale de Pinnacle (casa de referencia) o, si no está,
     de la mediana de al menos MIN_CASAS casas.
  3) Se analiza UNA línea por partido: la que tenga más casas (empate: la más cercana a 2.5).
     Se busca la MEJOR cuota disponible entre las casas de esa línea.
  4) Hay valor solo si prob_justa * mejor_cuota - 1 cae en un rango razonable.
  5) Cada pick se guarda, se liquida solo con el marcador real y se mide
     ROI y Brier para saber con datos si el filtro sirve.
  6) Además se guarda TODO partido analizado (con o sin pick) en
     partidos_analizados.csv, para medir calibración con muchos más datos.

Uso:
  python bot_goles.py            -> liquida pendientes y busca picks nuevos
  python bot_goles.py resumen    -> ROI, aciertos y Brier (y lo envía a Telegram)

Variables de entorno: ODDS_API_KEY, TELEGRAM_TOKEN, CHAT_ID
"""
import csv
import html
import math
import os
import statistics
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

import requests

# ----------------------------- CONFIGURACIÓN ---------------------------------
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

BASE = "https://api.the-odds-api.com/v4"
ARCHIVO_CSV = "historial_picks.csv"
ARCHIVO_SOMBRA = "partidos_analizados.csv"

LIGAS = [
    "soccer_epl", "soccer_spain_la_liga", "soccer_italy_serie_a", "soccer_germany_bundesliga",
    "soccer_france_ligue_one", "soccer_uefa_champs_league", "soccer_uefa_nations_league",
    "soccer_efl_champ", "soccer_spain_copa_del_rey",
]
REGIONES = "eu,uk"          # Pinnacle viene en "eu". Costo = regiones x mercados por llamada
CASA_REFERENCIA = "pinnacle"
LINEA = 2.5                 # línea de referencia (desempate y valor por defecto)
LINEAS_VALIDAS = (1.5, 2.5, 3.5, 4.5)   # líneas .5 (sin devolución de apuesta)
VENTANA_HORAS = 24
REANALISIS_HORAS = 12       # no se vuelve a consultar un partido analizado hace menos de estas horas (ahorra créditos)

MIN_CASAS = 5               # mínimo de casas en UNA misma línea para confiar en la prob. justa
MAX_DISPERSION = 0.035      # si las casas no se ponen de acuerdo (desv. est. de P(Over)), no se juega
MAX_SOBRE_MEDIANA = 1.12    # cuota que supera 12% a la mediana = probable error/línea vieja
CUOTA_MIN = 1.50              # cuota mínima en la línea 2.5
CUOTA_MIN_OTRAS_LINEAS = 1.20   # en 1.5 / 3.5 las cuotas son más bajas por naturaleza
EV_MIN = 0.04
EV_MAX = 0.12               # un EV mayor casi siempre es error de datos, no una oportunidad
KELLY_FRACCION = 0.25
STAKE_MIN = 0.25            # % de banca
STAKE_MAX = 1.50            # % de banca

CREDITOS_MINIMOS = 10      # si quedan menos créditos, el bot no consulta más

CASA_USUARIO = "Rushbet"    # casa donde apuestas (se muestra en la cuota mínima)
# Ligas con prórroga/penales: el marcador de la API puede no ser el de los 90 minutos,
# así que NO se liquidan solas. Quedan en "REVISAR" y tú confirmas los goles en
# resultados_manuales.csv (una línea por partido: texto del partido,goles en 90 min).
LIGAS_REVISAR = {"soccer_spain_copa_del_rey"}
ARCHIVO_MANUAL = "resultados_manuales.csv"

# Referencias de cuotas justas para las líneas 1.5 y 3.5 (ESTIMADAS con un modelo Poisson
# a partir de la probabilidad justa de la línea 2.5; no son cuotas de mercado).
ENVIAR_REFERENCIAS = True   # manda a Telegram un resumen con las cuotas justas del día
MARGEN_ESTIMACION = 0.08    # colchón provisional para líneas estimadas (hasta validar con datos)
MAX_REFERENCIAS = 25        # máximo de partidos en el resumen

ZONA = timezone(timedelta(hours=-5))   # hora que se muestra en Telegram (Colombia)

CAMPOS = [
    "ID_Evento", "Sport", "Fecha_Ejecucion", "Inicio_UTC", "Liga", "Partido", "Mercado",
    "Casa", "Cuota", "Cuota_Justa", "Prob_Justa", "EV_%", "Stake_%", "Referencia",
    "N_Casas", "Estado", "Goles_Totales", "Profit_%Banca",
]
CAMPOS_SOMBRA = [
    "ID_Evento", "Sport", "Inicio_UTC", "Liga", "Partido", "Ultima_Consulta", "N_Casas",
    "Prob_Over_Justa", "Referencia", "Mejor_Lado", "Mejor_Cuota", "Mejor_EV_%", "Motivo",
    "Es_Pick", "Goles_Totales", "Over_2.5", "P_Over15_Est", "P_Over35_Est",
    "Linea", "Over_Linea",
]


# ------------------------------ MATEMÁTICA -----------------------------------
def quitar_margen_potencia(cuotas):
    """Probabilidades sin margen (método de potencia): busca k>=1 tal que sum((1/c)^k)=1."""
    if any(c <= 1.0 for c in cuotas):
        raise ValueError("Cuota inválida")
    raw = [1.0 / c for c in cuotas]
    s = sum(raw)
    if s <= 1.0:
        return [r / s for r in raw]
    lo, hi = 1.0, 50.0
    for _ in range(100):
        k = (lo + hi) / 2
        if sum(r ** k for r in raw) > 1.0:
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2
    p = [r ** k for r in raw]
    t = sum(p)
    return [x / t for x in p]


def calcular_kelly(prob, cuota, fraccion=KELLY_FRACCION):
    """Kelly fraccional en % de banca (0 si no hay ventaja)."""
    if cuota <= 1.0 or not 0.0 < prob < 1.0:
        return 0.0
    b = cuota - 1.0
    full = (b * prob - (1.0 - prob)) / b
    return max(0.0, full * fraccion * 100.0)


RESTANTES = None            # créditos restantes según la API
DESCARTES = Counter()       # por qué se descartan los partidos
DETALLE = []                # una línea por partido descartado


# ------------------------- LÍNEAS 1.5 / 3.5 (ESTIMADAS) ----------------------
def _poisson_ge(lmbda, k):
    """P(N >= k) con N ~ Poisson(lmbda)."""
    p, acum = math.exp(-lmbda), 0.0
    for i in range(k):
        acum += p
        p *= lmbda / (i + 1)
    return max(0.0, 1.0 - acum)


def probs_otras_lineas(p_over, linea=LINEA):
    """Dada P(Over linea) justa, estima P(Over 1.5), P(Over 2.5) y P(Over 3.5) suponiendo que los
    goles totales siguen una Poisson calibrada para reproducir esa probabilidad. Aproximación:
    en partidos muy desiguales las colas reales suelen ser algo más gruesas. Se valida con el CSV."""
    p = min(0.98, max(0.02, p_over))
    k_base = int(linea) + 1                      # Over 2.5 <=> al menos 3 goles
    lo, hi = 0.05, 12.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if _poisson_ge(mid, k_base) < p:
            lo = mid
        else:
            hi = mid
    lam = (lo + hi) / 2
    return {"over15": _poisson_ge(lam, 2), "over25": _poisson_ge(lam, 3),
            "over35": _poisson_ge(lam, 4), "lambda": lam}


# ------------------------------ DATOS (API) ----------------------------------
def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_dt(txt):
    return datetime.fromisoformat(txt.replace("Z", "+00:00"))


def _cuota_restante(resp):
    global RESTANTES
    txt = resp.headers.get("x-requests-remaining", "")
    if txt.strip().isdigit():
        RESTANTES = int(txt)
    print(f"[API] créditos usados: {resp.headers.get('x-requests-used', '?')} | "
          f"restantes: {resp.headers.get('x-requests-remaining', '?')}")


def deportes_activos():
    """Lista de ligas activas (esta llamada no consume créditos)."""
    try:
        r = requests.get(f"{BASE}/sports/", params={"apiKey": ODDS_API_KEY}, timeout=15)
        r.raise_for_status()
        activos = {s["key"] for s in r.json() if s.get("active")}
    except Exception as e:
        print(f"[ERROR] No pude listar deportes: {e}")
        return LIGAS
    faltan = [l for l in LIGAS if l not in activos]
    if faltan:
        print(f"[INFO] Ligas sin partidos/inactivas o con clave inválida: {faltan}")
    return [l for l in LIGAS if l in activos]


def listar_eventos(sport, desde, hasta):
    """Partidos de la liga en la ventana. Este endpoint no debería gastar créditos
    (verifícalo en el log: los créditos restantes no deben bajar tras esta llamada)."""
    params = {"apiKey": ODDS_API_KEY, "dateFormat": "iso",
              "commenceTimeFrom": _iso(desde), "commenceTimeTo": _iso(hasta)}
    try:
        r = requests.get(f"{BASE}/sports/{sport}/events/", params=params, timeout=15)
        if r.status_code != 200:
            print(f"[AVISO] eventos {sport}: HTTP {r.status_code}")
            return None          # None = no se pudo saber; se consulta igual
        _cuota_restante(r)
        return r.json()
    except Exception as e:
        print(f"[ERROR] eventos {sport}: {e}")
        return None


def consultar_cuotas(sport, desde, hasta):
    params = {
        "apiKey": ODDS_API_KEY, "regions": REGIONES, "markets": "totals",
        "oddsFormat": "decimal", "dateFormat": "iso",
        "commenceTimeFrom": _iso(desde), "commenceTimeTo": _iso(hasta),
    }
    try:
        r = requests.get(f"{BASE}/sports/{sport}/odds/", params=params, timeout=20)
    except Exception as e:
        print(f"[ERROR] {sport}: {e}")
        return []
    if r.status_code == 429:
        global RESTANTES
        RESTANTES = 0
        print(f"[AVISO] {sport}: sin créditos de The Odds API")
        return []
    if r.status_code == 401:
        print("[ERROR] ODDS_API_KEY inválida")
        raise SystemExit(1)
    if r.status_code != 200:
        print(f"[ERROR] {sport}: HTTP {r.status_code}")
        return []
    _cuota_restante(r)
    return r.json()


def enviar_telegram(mensaje):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Faltan TELEGRAM_TOKEN / CHAT_ID")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": mensaje, "parse_mode": "HTML"}, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"Error Telegram: {e}")
        return False


# ------------------------------ ANÁLISIS -------------------------------------
def extraer_totales(evento):
    """{linea: {casa: {'titulo', 'over', 'under'}}} para las líneas de LINEAS_VALIDAS,
    con ambos lados de la MISMA casa."""
    out = {}
    for b in evento.get("bookmakers", []):
        for m in b.get("markets", []):
            if m.get("key") != "totals":
                continue
            por_linea = {}
            for o in m.get("outcomes", []):
                pt = o.get("point")
                if pt is None:
                    continue
                pt = float(pt)
                if not any(abs(pt - l) < 1e-9 for l in LINEAS_VALIDAS):
                    continue
                n = str(o.get("name", "")).lower()
                if n in ("over", "under"):
                    por_linea.setdefault(pt, {})[n] = o.get("price")
            for pt, d in por_linea.items():
                over, under = d.get("over"), d.get("under")
                if over and under and over > 1.0 and under > 1.0:
                    out.setdefault(pt, {})[b["key"]] = {
                        "titulo": b.get("title", b["key"]), "over": float(over), "under": float(under)}
    return out


def _resumen_lineas(totales):
    if not totales:
        return "sin datos de totales"
    return ", ".join(f"{l}x{len(c)}" for l, c in sorted(totales.items()))


def elegir_linea(totales):
    """La línea con más casas (mínimo MIN_CASAS). Empate: la más cercana a LINEA."""
    validas = [(l, len(c)) for l, c in totales.items() if len(c) >= MIN_CASAS]
    if not validas:
        return None
    return min(validas, key=lambda x: (-x[1], abs(x[0] - LINEA)))[0]


def _motivo_descarte(cuota, ev, stake, mediana, linea=LINEA):
    cuota_min = CUOTA_MIN if abs(linea - LINEA) < 1e-9 else CUOTA_MIN_OTRAS_LINEAS
    m = []
    if cuota > mediana * MAX_SOBRE_MEDIANA:
        m.append("cuota atípica vs mediana")
    if cuota < cuota_min:
        m.append(f"cuota < {cuota_min}")
    if ev < EV_MIN:
        m.append(f"EV < {EV_MIN * 100:.0f}%")
    if ev > EV_MAX:
        m.append(f"EV > {EV_MAX * 100:.0f}% (posible error)")
    if ev >= EV_MIN and stake < STAKE_MIN:
        m.append("stake muy bajo")
    return ", ".join(m)


def evaluar_evento(evento):
    """Devuelve (pick o None, info). 'info' describe el análisis aunque no haya pick."""
    partido = f"{evento.get('home_team', '?')} vs {evento.get('away_team', '?')}"
    totales = extraer_totales(evento)
    linea = elegir_linea(totales)
    info = {"partido": partido, "linea": linea,
            "n_casas": len(totales[linea]) if linea else max((len(c) for c in totales.values()), default=0),
            "p_over": None, "referencia": "", "lado": "", "cuota": None, "ev": None, "motivo": ""}
    if linea is None:
        DESCARTES["ninguna línea con suficientes casas"] += 1
        DETALLE.append(f"{partido}: ninguna línea con >= {MIN_CASAS} casas ({_resumen_lineas(totales)})")
        info["motivo"] = "pocas casas"
        return None, info

    casas = totales[linea]
    p_over_por_casa = {k: quitar_margen_potencia([v["over"], v["under"]])[0]
                       for k, v in casas.items()}
    valores = list(p_over_por_casa.values())
    if CASA_REFERENCIA in p_over_por_casa:
        p_over, ref = p_over_por_casa[CASA_REFERENCIA], "Pinnacle"
    else:
        p_over, ref = statistics.median(valores), f"mediana de {len(casas)} casas"
    info["p_over"], info["referencia"] = p_over, ref

    disp = statistics.pstdev(valores)
    if disp > MAX_DISPERSION:
        DESCARTES["casas en desacuerdo"] += 1
        DETALLE.append(f"{partido}: línea {linea}, casas en desacuerdo (desv. {disp:.3f} > {MAX_DISPERSION})")
        info["motivo"] = "casas en desacuerdo"
        return None, info

    candidatos, mejor_descartado = [], None
    for lado, prob in (("Over", p_over), ("Under", 1.0 - p_over)):
        k = lado.lower()
        precios = [(v[k], c) for c, v in casas.items()]
        mejor, casa_key = max(precios)
        mediana = statistics.median(p for p, _ in precios)
        ev = prob * mejor - 1.0
        stake = min(STAKE_MAX, calcular_kelly(prob, mejor))
        motivo = _motivo_descarte(mejor, ev, stake, mediana, linea)
        if not motivo:
            candidatos.append({
                "mercado": f"Goles - {lado} {linea}", "lado": lado, "linea": linea, "prob": prob,
                "cuota": mejor, "cuota_justa": 1.0 / prob, "ev": ev * 100.0, "stake": stake,
                "casa": casas[casa_key]["titulo"], "referencia": ref, "n_casas": len(casas),
            })
        elif mejor_descartado is None or ev > mejor_descartado[1]:
            mejor_descartado = (lado, ev, mejor, motivo)

    if not candidatos:
        DESCARTES["sin valor (EV fuera de 4%-12% / cuota / stake)"] += 1
        lado, ev, cuota, motivo = mejor_descartado
        DETALLE.append(f"{partido}: línea {linea}, {len(casas)} casas, mejor opción {lado} "
                       f"@{cuota:.2f} EV {ev * 100:+.1f}% -> {motivo}")
        info.update(lado=lado, cuota=cuota, ev=ev * 100.0, motivo=motivo)
        return None, info
    pick = max(candidatos, key=lambda c: c["ev"])
    info.update(lado=pick["lado"], cuota=pick["cuota"], ev=pick["ev"], motivo="")
    return pick, info


def analizar_evento(evento):
    """Compatibilidad: solo el pick (o None)."""
    return evaluar_evento(evento)[0]


# ------------------------------ CSV ------------------------------------------
def preparar_csv(archivo=None, campos=None):
    """Si el archivo tiene columnas de menos, las agrega vacías (sin perder filas).
    Si el formato es otro, lo guarda con otro nombre."""
    archivo, campos = archivo or ARCHIVO_CSV, campos or CAMPOS
    if not os.path.exists(archivo):
        return
    with open(archivo, newline="", encoding="utf-8") as f:
        lector = csv.DictReader(f)
        cab = lector.fieldnames or []
        filas = list(lector)
    if cab == campos:
        return
    if cab and set(cab) <= set(campos):
        for fila in filas:
            for c in campos:
                fila.setdefault(c, "")
        escribir_csv(filas, archivo, campos)
        print(f"[INFO] {archivo}: columnas nuevas agregadas, {len(filas)} filas conservadas")
        return
    nuevo = archivo.replace(".csv", f"_formato_antiguo_{datetime.now():%Y%m%d%H%M%S}.csv")
    os.replace(archivo, nuevo)
    print(f"[INFO] {archivo} tenía otro formato; guardado como {nuevo}")


def leer_csv(archivo=None):
    archivo = archivo or ARCHIVO_CSV
    if not os.path.exists(archivo):
        return []
    with open(archivo, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def escribir_csv(filas, archivo=None, campos=None):
    archivo, campos = archivo or ARCHIVO_CSV, campos or CAMPOS
    with open(archivo, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        w.writerows(filas)


def registrar(fila):
    existe = os.path.exists(ARCHIVO_CSV)
    with open(ARCHIVO_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        if not existe:
            w.writeheader()
        w.writerow(fila)


# ------------------------- REGISTRO DE TODOS LOS PARTIDOS --------------------
def fila_sombra(ev, sport, info, ahora, es_pick):
    est = (probs_otras_lineas(info["p_over"], info["linea"] or LINEA)
           if info["p_over"] is not None else None)
    return {
        "ID_Evento": ev["id"], "Sport": sport, "Inicio_UTC": _iso(_parse_dt(ev["commence_time"])),
        "Liga": ev.get("sport_title", sport), "Partido": info["partido"],
        "Ultima_Consulta": f"{ahora:%Y-%m-%d %H:%M}", "N_Casas": info["n_casas"],
        "Prob_Over_Justa": f"{info['p_over']:.4f}" if info["p_over"] is not None else "",
        "Referencia": info["referencia"], "Mejor_Lado": info["lado"],
        "Mejor_Cuota": f"{info['cuota']:.2f}" if info["cuota"] else "",
        "Mejor_EV_%": f"{info['ev']:.2f}" if info["ev"] is not None else "",
        "Motivo": info["motivo"], "Es_Pick": "SI" if es_pick else "NO",
        "Goles_Totales": "", "Over_2.5": "",
        "P_Over15_Est": f"{est['over15']:.4f}" if est else "",
        "P_Over35_Est": f"{est['over35']:.4f}" if est else "",
        "Linea": info["linea"] if info.get("linea") else "", "Over_Linea": "",
    }


def guardar_sombra(nuevas):
    """Un registro por partido: se conserva la ÚLTIMA consulta antes del inicio
    (la más cercana al cierre). Los ya liquidados no se tocan."""
    if not nuevas:
        return
    filas = leer_csv(ARCHIVO_SOMBRA)
    idx = {f["ID_Evento"]: i for i, f in enumerate(filas)}
    for id_ev, fila in nuevas.items():
        if id_ev in idx:
            if filas[idx[id_ev]]["Goles_Totales"] != "":
                continue
            filas[idx[id_ev]] = fila
        else:
            filas.append(fila)
    escribir_csv(filas, ARCHIVO_SOMBRA, CAMPOS_SOMBRA)


# ------------------------------ LIQUIDACIÓN ----------------------------------
def _totales_por_evento(sports):
    totales = {}
    for sport in sports:
        try:
            r = requests.get(f"{BASE}/sports/{sport}/scores/",
                             params={"apiKey": ODDS_API_KEY, "daysFrom": 3, "dateFormat": "iso"},
                             timeout=15)
            if r.status_code != 200:
                continue
            for ev in r.json():
                if ev.get("completed") and ev.get("scores"):
                    totales[ev["id"]] = sum(int(s["score"]) for s in ev["scores"])
        except Exception as e:
            print(f"[ERROR] scores {sport}: {e}")
    return totales


def _linea_de_mercado(mercado):
    try:
        return float(str(mercado).split()[-1])
    except ValueError:
        return LINEA


def _cerrar_pick(f, goles):
    over = "Over" in f["Mercado"]
    linea = _linea_de_mercado(f["Mercado"])
    gana = (goles > linea) if over else (goles < linea)
    stake, cuota = float(f["Stake_%"]), float(f["Cuota"])
    f["Goles_Totales"] = goles
    f["Estado"] = "GANADA" if gana else "PERDIDA"
    f["Profit_%Banca"] = round(stake * (cuota - 1.0) if gana else -stake, 4)


def _cerrar_sombra(f, goles):
    f["Goles_Totales"] = goles
    f["Over_2.5"] = 1 if goles > LINEA else 0
    f["Over_Linea"] = 1 if goles > float(f.get("Linea") or LINEA) else 0


def aplicar_resultados_manuales(picks, sombra):
    """Lee resultados_manuales.csv (texto del partido,goles_90min) y cierra o corrige partidos de copa."""
    if not os.path.exists(ARCHIVO_MANUAL):
        return 0
    n = 0
    with open(ARCHIVO_MANUAL, newline="", encoding="utf-8") as fh:
        for fila in csv.reader(fh):
            if len(fila) < 2 or not fila[1].strip().isdigit():
                continue
            texto, goles = fila[0].strip().lower(), int(fila[1])
            if not texto:
                continue
            # Solo ligas de LIGAS_REVISAR; también corrige partidos ya liquidados con el
            # marcador de la API (idempotente: repetir la misma línea no cambia nada).
            for f in picks:
                if f["Sport"] in LIGAS_REVISAR and f["Estado"] != "PENDIENTE" \
                        and texto in f["Partido"].lower():
                    _cerrar_pick(f, goles)
                    n += 1
            for f in sombra:
                if f["Sport"] in LIGAS_REVISAR and f["Goles_Totales"] != "" \
                        and texto in f["Partido"].lower():
                    _cerrar_sombra(f, goles)
                    n += 1
    return n


def liquidar_pendientes():
    """Cierra picks y partidos analizados con el marcador real.
    Las ligas de LIGAS_REVISAR (copas con prórroga) quedan en REVISAR hasta que
    confirmes los goles de los 90 minutos en resultados_manuales.csv."""
    ahora = datetime.now(timezone.utc)
    espera = timedelta(hours=3)
    picks, sombra = leer_csv(), leer_csv(ARCHIVO_SOMBRA)

    manuales = aplicar_resultados_manuales(picks, sombra)

    pend_p = [f for f in picks if f["Estado"] == "PENDIENTE"
              and _parse_dt(f["Inicio_UTC"]) + espera < ahora]
    pend_s = [f for f in sombra if f["Goles_Totales"] == ""
              and _parse_dt(f["Inicio_UTC"]) + espera < ahora]
    totales = _totales_por_evento({f["Sport"] for f in pend_p + pend_s}) if (pend_p or pend_s) else {}

    cambios_p = cambios_s = 0
    revisar = []
    for f in pend_p:
        if f["ID_Evento"] not in totales:
            continue
        goles = totales[f["ID_Evento"]]
        if f["Sport"] in LIGAS_REVISAR:
            f["Estado"], f["Goles_Totales"] = "REVISAR", goles
            revisar.append((f["Partido"], goles))
        else:
            _cerrar_pick(f, goles)
            cambios_p += 1
    for f in pend_s:
        if f["ID_Evento"] not in totales:
            continue
        goles = totales[f["ID_Evento"]]
        if f["Sport"] in LIGAS_REVISAR:
            f["Goles_Totales"] = "REVISAR"
            f["Motivo"] = (f["Motivo"] + f" | API={goles} goles").strip(" |")
            if (f["Partido"], goles) not in revisar:
                revisar.append((f["Partido"], goles))
        else:
            _cerrar_sombra(f, goles)
            cambios_s += 1

    if cambios_p or manuales or revisar:
        escribir_csv(picks)
    if cambios_s or manuales or revisar:
        escribir_csv(sombra, ARCHIVO_SOMBRA, CAMPOS_SOMBRA)
    print(f"[INFO] liquidados: {cambios_p} picks, {cambios_s} partidos analizados, "
          f"{manuales} manuales, {len(revisar)} por revisar")
    if revisar:
        lineas = "\n".join(f"• {p}: la API dice {g} goles" for p, g in revisar)
        enviar_telegram("📝 <b>Copa: confirma los goles de los 90 minutos</b>\n" + lineas +
                        f"\nAgrega una línea por partido en {ARCHIVO_MANUAL}: "
                        "<code>texto del partido,goles</code>")


# ------------------------------ RESÚMENES ------------------------------------
def resumen_picks():
    cerradas = [f for f in leer_csv() if f["Estado"] in ("GANADA", "PERDIDA")]
    if not cerradas:
        return "<b>PICKS</b>\nAún no hay picks liquidados."
    n = len(cerradas)
    ganadas = sum(f["Estado"] == "GANADA" for f in cerradas)
    stake_tot = sum(float(f["Stake_%"]) for f in cerradas)
    profit = sum(float(f["Profit_%Banca"]) for f in cerradas)
    brier = sum((float(f["Prob_Justa"]) - (f["Estado"] == "GANADA")) ** 2 for f in cerradas) / n
    p_media = sum(float(f["Prob_Justa"]) for f in cerradas) / n
    return (f"<b>PICKS</b>\nLiquidados: {n} | aciertos: {ganadas} ({ganadas / n * 100:.1f}%) "
            f"| prob. media esperada: {p_media * 100:.1f}%\nProfit: {profit:+.2f}% de banca | "
            f"ROI sobre stake: {profit / stake_tot * 100:+.1f}%\nBrier: {brier:.3f} (0.25 = azar)")


def _over_linea(f):
    """1/0 si la línea analizada de ese partido terminó en Over/Under; None si no hay resultado.
    Filas antiguas (sin columna Linea) analizaban siempre la línea 2.5."""
    v = f.get("Over_Linea", "")
    if v in ("0", "1"):
        return int(v)
    if f.get("Linea", "") == "" and f.get("Over_2.5", "") in ("0", "1"):
        return int(f["Over_2.5"])
    return None


def resumen_sombra():
    filas = [f for f in leer_csv(ARCHIVO_SOMBRA)
             if _over_linea(f) is not None and f["Prob_Over_Justa"] != ""]
    if not filas:
        return "<b>PARTIDOS ANALIZADOS</b>\nAún no hay partidos liquidados."
    n = len(filas)
    p = [float(f["Prob_Over_Justa"]) for f in filas]
    y = [_over_linea(f) for f in filas]
    brier = sum((pi - yi) ** 2 for pi, yi in zip(p, y)) / n
    tasa = sum(y) / n
    base = tasa * (1 - tasa)
    txt = [f"<b>PARTIDOS ANALIZADOS</b> (n={n})",
           f"P(Over) justa media: {sum(p) / n * 100:.1f}% | Over real: {tasa * 100:.1f}%",
           f"Brier: {brier:.3f} (referencia constante: {base:.3f})"]
    lineas = Counter((f.get("Linea") or "2.5") for f in filas)
    txt.append("Líneas analizadas: " + ", ".join(f"{k}x{v}" for k, v in sorted(lineas.items())))
    for etiqueta, col, umbral in (("Over 1.5", "P_Over15_Est", 2), ("Over 3.5", "P_Over35_Est", 4)):
        con = [f for f in filas if f.get(col, "") != ""]
        if con:
            est_m = sum(float(f[col]) for f in con) / len(con)
            real = sum(int(f["Goles_Totales"]) >= umbral for f in con) / len(con)
            txt.append(f"Estimación {etiqueta}: {est_m * 100:.1f}% | real: {real * 100:.1f}% (n={len(con)})")
    cubos = {"EV < 0%": [], "EV 0 a 2%": [], "EV 2 a 4%": [], "EV >= 4%": []}
    for f in filas:
        if not f["Mejor_Cuota"] or f["Mejor_EV_%"] == "":
            continue
        ev, cuota, over = float(f["Mejor_EV_%"]), float(f["Mejor_Cuota"]), _over_linea(f)
        gana = (over == 1) if f["Mejor_Lado"] == "Over" else (over == 0)
        clave = "EV < 0%" if ev < 0 else "EV 0 a 2%" if ev < 2 else "EV 2 a 4%" if ev < 4 else "EV >= 4%"
        cubos[clave].append(cuota - 1.0 if gana else -1.0)
    txt.append("ROI hipotético de la mejor opción (1 unidad fija), por EV al analizar:")
    for k, v in cubos.items():
        if v:
            txt.append(f"  {k}: n={len(v)}, ROI {sum(v) / len(v) * 100:+.1f}%")
    txt.append("<i>Si el ROI no sube al subir el EV, el filtro no tiene señal. "
               "Con n < 200 todo esto es ruido.</i>")
    return "\n".join(txt)


def resumen(enviar=False):
    txt = resumen_picks() + "\n\n" + resumen_sombra()
    print(txt)
    if enviar:
        enviar_telegram(txt)


# ------------------------------ ESCANEO --------------------------------------
def _cuota(p):
    return f"{1.0 / p:.2f}" if 0.0 < p < 1.0 else "-"


def enviar_referencias(lista):
    """Resumen con las cuotas justas (sin margen) de cada partido analizado.
    Cada elemento: (inicio, partido, P(Over de la línea analizada), línea)."""
    if not ENVIAR_REFERENCIAS or not lista:
        return
    lista = sorted(lista, key=lambda x: x[0])[:MAX_REFERENCIAS]
    cab = (f"📋 <b>Cuotas justas de referencia</b>\n"
           f"✓ = línea con cuotas reales de varias casas. Las demás son ESTIMADAS (Poisson).\n"
           f"Mínima en {CASA_USUARIO} = justa × {1 + EV_MIN:.2f} (línea ✓) o × {1 + MARGEN_ESTIMACION:.2f} (estimadas).\n")
    bloques, actual = [], cab
    for item in lista:
        inicio, partido, p, *resto = item
        base = resto[0] if resto and resto[0] else LINEA
        e = probs_otras_lineas(p, base)
        est = {1.5: e["over15"], 2.5: e["over25"], 3.5: e["over35"]}
        est[base] = p
        partes = []
        for l in sorted(est):
            marca = " ✓" if abs(l - base) < 1e-9 else ""
            partes.append(f"{l}{marca}: O {_cuota(est[l])} / U {_cuota(1 - est[l])}")
        texto = (f"\n<b>{html.escape(partido)}</b> ({inicio.astimezone(ZONA):%d/%m %H:%M})\n"
                 + " · ".join(partes) + "\n")
        if len(actual) + len(texto) > 3500:
            bloques.append(actual)
            actual = ""
        actual += texto
    bloques.append(actual)
    for b in bloques:
        enviar_telegram(b)


def _analizados_recientes(ahora):
    """IDs de partidos (sin resultado todavía) analizados hace menos de REANALISIS_HORAS."""
    limite = ahora - timedelta(hours=REANALISIS_HORAS)
    out = set()
    for f in leer_csv(ARCHIVO_SOMBRA):
        try:
            t = datetime.strptime(f["Ultima_Consulta"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except (ValueError, KeyError, TypeError):
            continue
        if t >= limite and f.get("Goles_Totales", "") == "":
            out.add(f["ID_Evento"])
    return out


def escanear():
    ahora = datetime.now(timezone.utc)
    hasta = ahora + timedelta(hours=VENTANA_HORAS)
    ya = {f["ID_Evento"] for f in leer_csv()}
    omitir = ya | _analizados_recientes(ahora)
    sombra = {}
    referencias = []
    enviados = 0
    vistos = 0
    for sport in deportes_activos():
        if RESTANTES is not None and RESTANTES <= CREDITOS_MINIMOS:
            print(f"[AVISO] Quedan {RESTANTES} créditos: me detengo para no agotarlos.")
            break
        eventos = listar_eventos(sport, ahora, hasta)
        if eventos is not None:
            nuevos = [e for e in eventos if e["id"] not in omitir]
            if not nuevos:
                print(f"[SKIP] {sport}: sin partidos nuevos en {VENTANA_HORAS} h "
                      f"(o analizados hace < {REANALISIS_HORAS} h) (0 créditos gastados)")
                continue
        for ev in consultar_cuotas(sport, ahora, hasta):
            if ev["id"] in omitir:
                continue
            vistos += 1
            inicio = _parse_dt(ev["commence_time"])
            if inicio <= ahora:
                continue
            pick, info = evaluar_evento(ev)
            sombra[ev["id"]] = fila_sombra(ev, sport, info, ahora, pick is not None)
            if info["p_over"] is not None and info["motivo"] != "casas en desacuerdo":
                referencias.append((inicio, info["partido"], info["p_over"], info["linea"]))
            if not pick:
                continue
            partido = f"{ev['home_team']} vs {ev['away_team']}"
            registrar({
                "ID_Evento": ev["id"], "Sport": sport, "Fecha_Ejecucion": f"{ahora:%Y-%m-%d %H:%M}",
                "Inicio_UTC": _iso(inicio), "Liga": ev.get("sport_title", sport), "Partido": partido,
                "Mercado": pick["mercado"], "Casa": pick["casa"], "Cuota": f"{pick['cuota']:.2f}",
                "Cuota_Justa": f"{pick['cuota_justa']:.3f}", "Prob_Justa": f"{pick['prob']:.4f}",
                "EV_%": f"{pick['ev']:.2f}", "Stake_%": f"{pick['stake']:.2f}",
                "Referencia": pick["referencia"], "N_Casas": pick["n_casas"],
                "Estado": "PENDIENTE", "Goles_Totales": "", "Profit_%Banca": "",
            })
            ya.add(ev["id"])
            msg = (
                f"⚡ <b>PICK GOLES</b>\n\n"
                f"🏆 {html.escape(ev.get('sport_title', sport))}\n"
                f"🏟️ {html.escape(partido)}\n"
                f"🕒 {inicio.astimezone(ZONA):%d/%m %H:%M} (hora Colombia)\n"
                f"📌 <b>{pick['mercado']}</b> @ <b>{pick['cuota']:.2f}</b> ({html.escape(pick['casa'])})\n"
                f"📊 Prob. justa: {pick['prob'] * 100:.1f}% (cuota justa {pick['cuota_justa']:.2f}) "
                f"| EV: +{pick['ev']:.1f}%\n"
                f"🧮 Referencia: {pick['referencia']}\n"
                f"🎯 Cuota mínima en {CASA_USUARIO} (EV ≥ {EV_MIN * 100:.0f}%): "
                f"<b>{(1 + EV_MIN) / pick['prob']:.2f}</b> (si paga menos, no hay valor)\n"
                f"🛡️ Stake (Kelly {KELLY_FRACCION}): <b>{pick['stake']:.2f}% de la banca</b>"
            )
            if enviar_telegram(msg):
                enviados += 1
    guardar_sombra(sombra)
    enviar_referencias(referencias)
    for linea in DETALLE[:60]:
        print(f"  - {linea}")
    print(f"[DIAGNÓSTICO] partidos analizados: {vistos} | descartes: {dict(DESCARTES)}")
    print(f"=== FIN: {enviados} alertas enviadas ===")


def main():
    if not ODDS_API_KEY:
        print("Falta ODDS_API_KEY")
        sys.exit(1)
    preparar_csv(ARCHIVO_CSV, CAMPOS)
    preparar_csv(ARCHIVO_SOMBRA, CAMPOS_SOMBRA)
    if len(sys.argv) > 1 and sys.argv[1] == "resumen":
        liquidar_pendientes()       # cierra lo que ya terminó (gasta créditos solo si hay pendientes)
        resumen(enviar=True)
        return
    liquidar_pendientes()
    escanear()


if __name__ == "__main__":
    main()