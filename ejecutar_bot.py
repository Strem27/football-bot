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
    competencias = "PL,PD,SA,BL1,FL1"
    
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
    deportes = [
        "soccer_epl", 
        "soccer_spain_la_liga", 
        "soccer_italy_serie_a", 
        "soccer_germany_bundesliga", 
        "soccer_france_ligue_one"
    ]
    eventos_totales = []
    
    for deporte in deportes:
        url = f"https://api.the-odds-api.com/v4/sports/{deporte}/odds/"
        params = {
            "apiKey": ODDS_API_KEY,
            "regions": "eu",
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
            print(f"[ERROR] Cuotas {deporte}: {e}")
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

    prob_1x = prob_local + prob_empate
    prob_x2 = prob_visitante + prob_empate
    
    cuota_1x = round(1.0 / max(0.05, prob_1x * 0.96), 2)
    cuota_x2 = round(1.0 / max(0.05, prob_x2 * 0.96), 2)
    cuota_btts = 1.85 if prob_btts > 0.5 else 2.10

    mercados = [
        {"mercado": "1X2 - Gana Local", "prob": prob_local, "cuota": cuota_local},
        {"mercado": "1X2 - Empate", "prob": prob_empate, "cuota": cuota_empate},
        {"mercado": "1X2 - Gana Visitante", "prob": prob_visitante, "cuota": cuota_visita},
        {"mercado": "Doble Oportunidad - 1X (Local o Empate)", "prob": prob_1x, "cuota": cuota_1x},
        {"mercado": "Doble Oportunidad - X2 (Visitante o Empate)", "prob": prob_x2, "cuota": cuota_x2},
        {"mercado": "Goles - Over 2.5", "prob": prob_over_25, "cuota": cuota_over25},
        {"mercado": "Goles - Under 2.5", "prob": prob_under_25, "cuota": cuota_under25},
        {"
                    
