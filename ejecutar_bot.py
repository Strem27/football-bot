#!/usr/bin/env python3
"""
Bot de value betting en Over/Under 2.5 goles (fútbol).

Idea (la única que es defendible con solo cuotas):
  1) Se quita el margen (vig) de CADA casa con el método de potencia.
  2) La probabilidad "justa" sale de Pinnacle (casa de referencia) o, si no está,
     de la mediana de al menos MIN_CASAS casas.
  3) Se busca la MEJOR cuota disponible entre todas las casas.
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
LINEA = 2.5
VENTANA_HORAS = 24

MIN_CASAS = 5               # mínimo de casas con la línea 2.5 para confiar en la prob. justa
MAX_DISPERSION = 0.035      # si las casas no se ponen de acuerdo (desv. est. de P(Over)), no se juega
MAX_SOBRE_MEDIANA = 1.12    # cuota que supera 12% a la mediana = probable error/línea vieja
CUOTA_MIN = 1.50
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

ZONA = timezone(timedelta(hours=-5))   # hora que se muestra en Telegram (Colombia)

CAMPOS = [
    "ID_Evento", "Sport", "Fecha_Ejecucion", "Inicio_UTC", "Liga", "Partido", "Mercado",
    "Casa", "Cuota", "Cuota_Justa", "Prob_Justa", "EV_%", "Stake_%", "Referencia",
    "N_Casas", "Estado", "Goles_Totales", "Profit_%Banca",
]
CAMPOS_SOMBRA = [
    "ID_Evento", "Sport", "Inicio_UTC", "Liga", "Partido", "Ultima_Consulta", "N_Casas",
    "Prob_Over_Justa", "Referencia", "Mejor_Lado", "Mejor_Cuota", "Mejor_EV_%", "Motivo",
    "Es_Pick", "Goles_Totales", "Over_2.5",
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
    """{casa: {'titulo', 'over', 'under'}} solo con la línea LINEA y ambos lados de la MISMA casa."""
    out = {}
    for b in evento.get("bookmakers", []):
        for m in b.get("markets", []):
            if m.get("key") != "totals":
                continue
            over = under = None
            for o in m.get("outcomes", []):
                pt = o.get("point")
                if pt is None or abs(float(pt) - LINEA) > 1e-9:
                    continue
                n = str(o.get("name", "")).lower()
                if n == "over":
                    over = o.get("price")
                elif n == "under":
                    under = o.get("price")
            if over and under and over > 1.0 and under > 1.0:
                out[b["key"]] = {"titulo": b.get("title", b["key"]),
                                 "over": float(over), "under": float(under)}
    return out


def _motivo_descarte(cuota, ev, stake, mediana):
    m = []
    if cuota > mediana * MAX_SOBRE_MEDIANA:
        m.append("cuota atípica vs mediana")
    if cuota < CUOTA_MIN:
        m.append(f"cuota < {CUOTA_MIN}")
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
    casas = extraer_totales(evento)
    info = {"partido": partido, "n_casas": len(casas), "p_over": None, "referencia": "",
            "lado": "", "cuota": None, "ev": None, "motivo": ""}
    if len(casas) < MIN_CASAS:
        DESCARTES["pocas casas con línea 2.5"] += 1
        DETALLE.append(f"{partido}: solo {len(casas)} casas con línea {LINEA} (mín. {MIN_CASAS})")
        info["motivo"] = "pocas casas"
        return None, info

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
        DETALLE.append(f"{partido}: casas en desacuerdo (desv. {disp:.3f} > {MAX_DISPERSION})")
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
        motivo = _motivo_descarte(mejor, ev, stake, mediana)
        if not motivo:
            candidatos.append({
                "mercado": f"Goles - {lado} {LINEA}", "lado": lado, "prob": prob, "cuota": mejor,
                "cuota_justa": 1.0 / prob, "ev": ev * 100.0, "stake": stake,
                "casa": casas[casa_key]["titulo"], "referencia": ref, "n_casas": len(casas),
            })
        elif mejor_descartado is None or ev > mejor_descartado[1]:
            mejor_descartado = (lado, ev, mejor, motivo)

    if not candidatos:
        DESCARTES["sin valor (EV fuera de 4%-12% / cuota / stake)"] += 1
        lado, ev, cuota, motivo = mejor_descartado
        DETALLE.append(f"{partido}: {len(casas)} casas, mejor opción {lado} @{cuota:.2f} "
                       f"EV {ev * 100:+.1f}% -> {motivo}")
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
    archivo, campos = archivo or ARCHIVO_CSV, campos or CAMPOS
    if not os.path.exists(archivo):
        return
    with open(archivo, newline="", encoding="utf-8") as f:
        cab = next(csv.reader(f), [])
    if cab != campos:
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


def _cerrar_pick(f, goles):
    over = "Over" in f["Mercado"]
    gana = (goles > LINEA) if over else (goles < LINEA)
    stake, cuota = float(f["Stake_%"]), float(f["Cuota"])
    f["Goles_Totales"] = goles
    f["Estado"] = "GANADA" if gana else "PERDIDA"
    f["Profit_%Banca"] = round(stake * (cuota - 1.0) if gana else -stake, 4)


def _cerrar_sombra(f, goles):
    f["Goles_Totales"] = goles
    f["Over_2.5"] = 1 if goles > LINEA else 0


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


def resumen_sombra():
    filas = [f for f in leer_csv(ARCHIVO_SOMBRA)
             if f["Over_2.5"] in ("0", "1") and f["Prob_Over_Justa"] != ""]
    if not filas:
        return "<b>PARTIDOS ANALIZADOS</b>\nAún no hay partidos liquidados."
    n = len(filas)
    p = [float(f["Prob_Over_Justa"]) for f in filas]
    y = [int(f["Over_2.5"]) for f in filas]
    brier = sum((pi - yi) ** 2 for pi, yi in zip(p, y)) / n
    tasa = sum(y) / n
    base = tasa * (1 - tasa)
    txt = [f"<b>PARTIDOS ANALIZADOS</b> (n={n})",
           f"P(Over) justa media: {sum(p) / n * 100:.1f}% | Over real: {tasa * 100:.1f}%",
           f"Brier: {brier:.3f} (referencia constante: {base:.3f})"]
    cubos = {"EV < 0%": [], "EV 0 a 2%": [], "EV 2 a 4%": [], "EV >= 4%": []}
    for f in filas:
        if not f["Mejor_Cuota"] or f["Mejor_EV_%"] == "":
            continue
        ev, cuota, over = float(f["Mejor_EV_%"]), float(f["Mejor_Cuota"]), int(f["Over_2.5"])
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
def escanear():
    ahora = datetime.now(timezone.utc)
    hasta = ahora + timedelta(hours=VENTANA_HORAS)
    ya = {f["ID_Evento"] for f in leer_csv()}
    sombra = {}
    enviados = 0
    vistos = 0
    for sport in deportes_activos():
        if RESTANTES is not None and RESTANTES <= CREDITOS_MINIMOS:
            print(f"[AVISO] Quedan {RESTANTES} créditos: me detengo para no agotarlos.")
            break
        eventos = listar_eventos(sport, ahora, hasta)
        if eventos is not None:
            nuevos = [e for e in eventos if e["id"] not in ya]
            if not nuevos:
                print(f"[SKIP] {sport}: sin partidos nuevos en {VENTANA_HORAS} h (0 créditos gastados)")
                continue
        for ev in consultar_cuotas(sport, ahora, hasta):
            vistos += 1
            if ev["id"] in ya:
                continue
            inicio = _parse_dt(ev["commence_time"])
            if inicio <= ahora:
                continue
            pick, info = evaluar_evento(ev)
            sombra[ev["id"]] = fila_sombra(ev, sport, info, ahora, pick is not None)
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
        resumen(enviar=True)
        return
    liquidar_pendientes()
    escanear()


if __name__ == "__main__":
    main()