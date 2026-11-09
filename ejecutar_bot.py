import os
import math
import csv
import time
import requests
from datetime import datetime, timedelta

# --- CONFIGURACIÓN DE TOKENS (Desde la nube de GitHub) ---
FOOTBALL_DATA_KEY = os.getenv("FOOTBALL_DATA_KEY")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

ARCHIVO_CSV = "historial_picks.csv"

def registrar_en_csv(datos_pick):
    archivo_existe = os.path.exists(ARCHIVO_CSV)
    try:
        with open(ARCHIVO_CSV, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not archivo_existe:
                writer.writerow([
                    "Fecha_Ejecucion", "Liga", "Partido", "Mercado", 
                    "Cuota", "Prob_Modelo_%", "EV_%", "Stake_Kelly_%", "Estado"
                ])
            writer.writerow([
                datos_pick["fecha"],
                datos_pick["liga"],
                datos_pick["partido"],
                datos_pick["mercado"],
                datos_pick["cuota"],
                f"{datos_pick['prob']*100:.1f}%",
                f"+{datos_pick['ev']:.1f}%",
                f"{datos_pick['stake_k']:.2f}%",
                "PENDIENTE"
            ])
    except Exception as e:
        print(f"Error escribiendo en el CSV: {e}")

def enviar_telegram(mensaje):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Error: Faltan las credenciales de Telegram en los Secrets.")
        return False
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": mensaje,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"Error enviando mensaje a Telegram: {e}")
        return False

def consultar_partidos_hoy():
    hoy = datetime.now().strftime('%Y-%m-%d')
    mañana = (datetime.now() + timedelta(days=2)).strftime('%Y-%m-%d')
    
    url = "https://api.football-data.org/v4/matches"
    headers = {"X-Auth-Token": FOOTBALL_DATA_KEY}
    
    # Ampliamos las competencias en Football-Data (España 1 y 2, Inglaterra FA Cup, etc.)
    competencias = "PL,PD,SD,SA,BL1,FL1,CLI,ELC" 
    
    params = {
        "dateFrom": hoy,
        "dateTo": mañana,
        "competitions": competencias
    }
    
    for intento in range(3):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=12)
            if response.status_code == 200:
                matches = response.json().get("matches", [])
                print(f"[DEBUG] Partidos encontrados en Football-Data: {len(matches)}")
                return matches
        except Exception as e:
            print(f"[ERROR] Football-Data (Intento {intento+1}): {e}")
        time.sleep(2)
    return []

def consultar_cuotas_multi_liga():
    # Ampliamos el radar de The Odds API con las nuevas ligas solicitadas
    deportes = [
        "soccer_epl", 
        "soccer_spain_la_liga", 
        "soccer_spain_segunda_division",
        "soccer_italy_serie_a", 
        "soccer_germany_bundesliga", 
        "soccer_france_ligue_one",
        "soccer_colombia_primera_a",
        "soccer_norway_eliteserien",
        "soccer_romania_liga_i"
    ]
    eventos_totales = []
    
    for deporte in deportes:
        url = f"https://api.the-odds-api.com/v4/sports/{deporte}/odds/"
        params = {
            "apiKey": ODDS_API_KEY,
            "regions": "eu,us,uk",
            "markets": "h2h,totals",
            "oddsFormat": "decimal"
        }
        try:
            response = requests.get(url, params=params, timeout=15)
            if response.status_code == 200:
                datos = response.json()
                eventos_totales.extend(datos)
                print(f"[DEBUG] Cuotas obtenidas para {deporte}: {len(datos)} eventos")
        except Exception as e:
            print(f"[ERROR] Cuotas {deporte} (posible inactividad temporal o sin cuotas activas): {e}")
        time.sleep(1)
        
    return eventos_totales

def calcular_kelly(probabilidad_modelo, cuota_decimal, fraccion=0.25):
    if cuota_decimal <= 1.0 or probabilidad_modelo <= 0:
        return 0.0
    q = 1.0 - probabilidad_modelo
    b = cuota_decimal - 1.0
    kelly_full = (b * probabilidad_modelo - q) / b
    return max(0.0, kelly_full * fraccion * 100)

def poisson_prob(lmbda, k):
    try:
        return (math.exp(-lmbda) * (lmbda ** k)) / math.factorial(k)
    except:
        return 0.0

def modelo_bivariado_normalizado(cuota_local, cuota_empate, cuota_visita, cuota_over25, cuota_under25):
    p_l = 1.0 / cuota_local
    p_e = 1.0 / cuota_empate
    p_v = 1.0 / cuota_visita
    
    total_h2h = p_l + p_e + p_v
    p_l, p_e, p_v = p_l/total_h2h, p_e/total_h2h, p_v/total_h2h
    
    implied_over = 1.0 / cuota_over25
    lambda_global = 2.4 + (implied_over * 0.8)
    lambda_global = max(1.8, min(lambda_global, 3.6))
    
    suma_p = p_l + p_e + p_v
    lambda_local = lambda_global * (p_l / suma_p) * 1.03
    lambda_visita = lambda_global * (p_v / suma_p) * 0.97
    
    matriz = [[0.0 for _ in range(10)] for _ in range(10)]
    suma_total_prob = 0.0
    rho = -0.08
    
    for x in range(10):
        for y in range(10):
            p_xy = poisson_prob(lambda_local, x) * poisson_prob(lambda_visita, y)
            
            if x == 0 and y == 0:
                p_xy *= max(0.1, (1.0 - (lambda_local * lambda_visita * rho)))
            elif x == 1 and y == 0:
                p_xy *= (1.0 + (lambda_local * rho))
            elif x == 0 and y == 1:
                p_xy *= (1.0 + (lambda_visita * rho))
            elif x == 1 and y == 1:
                p_xy *= max(0.1, (1.0 - rho))
                
            p_xy = max(0.0, p_xy)
            matriz[x][y] = p_xy
            suma_total_prob += p_xy

    if suma_total_prob > 0:
        for x in range(10):
            for y in range(10):
                matriz[x][y] /= suma_total_prob

    prob_local = 0.0
    prob_empate = 0.0
    prob_visitante = 0.0
    prob_over_25 = 0.0
    prob_under_25 = 0.0
    prob_btts = 0.0

    for x in range(10):
        for y in range(10):
            p_xy = matriz[x][y]
            
            if x > y:
                prob_local += p_xy
            elif x == y:
                prob_empate += p_xy
            else:
                prob_visitante += p_xy
                
            if (x + y) > 2.5:
                prob_over_25 += p_xy
            else:
                prob_under_25 += p_xy
                
            if x > 0 and y > 0:
                prob_btts += p_xy

    mercados = [
        {"mercado": "1X2 - Gana Local", "prob": prob_local, "cuota": cuota_local},
        {"mercado": "1X2 - Empate", "prob": prob_empate, "cuota": cuota_empate},
        {"mercado": "1X2 - Gana Visitante", "prob": prob_visitante, "cuota": cuota_visita},
        {"mercado": "Goles - Over 2.5", "prob": prob_over_25, "cuota": cuota_over25},
        {"mercado": "Goles - Under 2.5", "prob": prob_under_25, "cuota": cuota_under25},
        {"mercado": "Ambos Anotan (BTTS - Sí)", "prob": prob_btts, "cuota": 1.90 if prob_btts > 0.5 else 2.05}
    ]
    return mercados

def evaluar_partido_multimercado(partido, cuotas_mercado):
    local = partido.get('homeTeam', {}).get('name', '')
    visitante = partido.get('awayTeam', {}).get('name', '')
    
    cuotas_encontradas = None
    for evento in cuotas_mercado:
        home_ev = evento.get('home_team', '').lower()
        away_ev = evento.get('away_team', '').lower()
        if (local.lower()[:4] in home_ev) or (visitante.lower()[:4] in away_ev):
            cuotas_encontradas = evento.get('bookmakers', [])
            break
            
    if not cuotas_encontradas:
        return None

    cuota_local = None
    cuota_empate = None
    cuota_visita = None
    cuota_over25 = None
    cuota_under25 = None
    
    try:
        for bookmaker in cuotas_encontradas:
            for m in bookmaker.get('markets', []):
                if m['key'] == 'h2h':
                    for outcome in m['outcomes']:
                        name = outcome['name'].lower()
                        if local.lower()[:4] in name:
                            cuota_local = outcome['price']
                        elif visitante.lower()[:4] in name:
                            cuota_visita = outcome['price']
                        else:
                            cuota_empate = outcome['price']
                elif m['key'] == 'totals':
                    for outcome in m['outcomes']:
                        if outcome.get('point') == 2.5:
                            if 'over' in outcome['name'].lower():
                                cuota_over25 = outcome['price']
                            else:
                                cuota_under25 = outcome['price']
    except Exception as e:
        print(f"[ERROR PARSEO] {local} vs {visitante}: {e}")

    if not all([cuota_local, cuota_empate, cuota_visita, cuota_over25, cuota_under25]):
        return None

    opciones = modelo_bivariado_normalizado(cuota_local, cuota_empate, cuota_visita, cuota_over25, cuota_under25)
    
    mejores = []
    for op in opciones:
        ev = (op["prob"] * op["cuota"]) - 1.0
        stake_k = calcular_kelly(op["prob"], op["cuota"], fraccion=0.25)
        
        if op["cuota"] >= 1.55 and 0.05 <= ev <= 0.18 and stake_k > 0.4:
            mejores.append({
                "mercado": op["mercado"],
                "prob": op["prob"],
                "cuota": op["cuota"],
                "ev": ev * 100,
                "stake_k": stake_k
            })
            
    if mejores:
        mejores.sort(key=lambda x: x["stake_k"], reverse=True)
        return mejores[0]
        
    return None

def main():
    print("=== INICIANDO MOTOR CUANTITATIVO MULTI-LIGA EXPANDIDO ===")
    
    partidos = consultar_partidos_hoy()
    if not partidos:
        print("No se obtuvieron partidos en las ligas seleccionadas para hoy.")
        return
        
    cuotas_api = consultar_cuotas_multi_liga()
    
    alertas_enviadas = 0
    hoy_str = datetime.now().strftime('%Y-%m-%d %H:%M')
    
    for partido in partidos:
        local = partido.get('homeTeam', {}).get('name', 'Local')
        visitante = partido.get('awayTeam', {}).get('name', 'Visitante')
        competencia = partido.get('competition', {}).get('name', 'Liga Global')
        
        seleccion = evaluar_partido_multimercado(partido, cuotas_api)
        
        if seleccion:
            prob_pct = seleccion['prob'] * 100
            ev_val = seleccion['ev']
            cuota_val = seleccion['cuota']
            stake_val = seleccion['stake_k']
            mercado_val = seleccion['mercado']
            
            datos_pick = {
                "fecha": hoy_str,
                "liga": competencia,
                "partido": f"{local} vs {visitante}",
                "mercado": mercado_val,
                "cuota": cuota_val,
                "prob": seleccion['prob'],
                "ev": ev_val,
                "stake_k": stake_val
            }
            
            registrar_en_csv(datos_pick)
            
            mensaje_alerta = (
                f"🌍 <b>PICK QUANT MULTI-LIGA</b> 🌍\n\n"
                f"🏆 <b>Liga:</b> {competencia}\n"
                f"⚽ <b>Partido:</b> {local} vs {visitante}\n"
                f"📌 <b>Selección Óptima:</b> {mercado_val}\n"
                f"📊 <b>Prob. Modelo:</b> {prob_pct:.1f}% | <b>EV:</b> +{ev_val:.1f}%\n"
                f"💰 <b>Cuota Real:</b> {cuota_val}\n"
                f"🛡️ <b>Stake Kelly (0.25):</b> <b>{stake_val:.2f}% de la Banca</b>\n"
            )
            
            exito = enviar_telegram(mensaje_alerta)
            if exito:
                alertas_enviadas += 1
                
    print(f"=== PROCESO MULTI-LIGA FINALIZADO: {alertas_enviadas} ALERTAS ENVIADAS ===")

if __name__ == "__main__":
    main()
