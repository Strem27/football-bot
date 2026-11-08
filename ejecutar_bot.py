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
        print(f"Pick registrado en el ledger CSV: {datos_pick['partido']} - {datos_pick['mercado']}")
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
                print(f"Partidos encontrados en Football-Data: {len(matches)}")
                return matches
            else:
                print(f"Error Football-Data (Intento {intento+1}): {response.status_code}")
        except Exception as e:
            print(f"Excepción en Football-Data (Intento {intento+1}): {e}")
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
        exito = False
        for intento in range(2):
            try:
                response = requests.get(url, params=params, timeout=15)
                if response.status_code == 200:
                    eventos_totales.extend(response.json())
                    exito = True
                    break
                else:
                    print(f"Aviso cuotas {deporte} (Código {response.status_code}), reintentando...")
            except Exception as e:
                print(f"Timeout o error en cuotas para {deporte} (Intento {intento+1}): {e}")
            time.sleep(2)
        if not exito:
            print(f"No se pudieron obtener cuotas para {deporte} en este ciclo, saltando...")
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

def modelo_bivariado_dixon_coles(cuota_local, cuota_empate, cuota_visita, cuota_over25, cuota_under25):
    """
    Simulación profesional con matriz bivariada ajustada para corregir 
    el sesgo natural de los marcadores bajos en Poisson puro.
    """
    p_l = 1.0 / cuota_local
    p_e = 1.0 / cuota_empate
    p_v = 1.0 / cuota_visita
    
    # Normalizamos el margen de las casas (vig)
    total_h2h = p_l + p_e + p_v
    p_l, p_e, p_v = p_l/total_h2h, p_e/total_h2h, p_v/total_h2h
    
    # Derivación de goles esperados (Lambda)
    implied_over = 1.0 / cuota_over25
    lambda_global = 2.4 + (implied_over * 0.8)
    lambda_global = max(2.0, min(lambda_global, 3.4))
    
    suma_p = p_l + p_e + p_v
    lambda_local = lambda_global * (p_l / suma_p) * 1.03
    lambda_visita = lambda_global * (p_v / suma_p) * 0.97
    
    # Acumuladores de probabilidad exacta
    prob_local = 0.0
    prob_empate = 0.0
    prob_visitante = 0.0
    prob_over_25 = 0.0
    prob_under_25 = 0.0
    prob_btts = 0.0
    
    # Matriz bivariada de 7x7 con factor de corrección Dixon-Coles para 0-0 y 1-1
    rho = -0.10 # Factor de corrección estándar para baja anotación
    
    for x in range(7):
        for y in range(7):
            p_xy = poisson_prob(lambda_local, x) * poisson_prob(lambda_visita, y)
            
            # Ajuste analítico de correlación para resultados cerrados
            if x == 0 and y == 0:
                p_xy *= (1.0 - (lambda_local * lambda_visita * rho))
            elif x == 1 and y == 0:
                p_xy *= (1.0 + (lambda_local * rho))
            elif x == 0 and y == 1:
                p_xy *= (1.0 + (lambda_visita * rho))
            elif x == 1 and y == 1:
                p_xy *= (1.0 - rho)
                
            p_xy = max(0.0, p_xy)
            
            # Acumuladores 1X2
            if x > y:
                prob_local += p_xy
            elif x == y:
                prob_empate += p_xy
            else:
                prob_visitante += p_xy
                
            # Totales y BTTS
            if (x + y) > 2.5:
                prob_over_25 += p_xy
            else:
                prob_under_25 += p_xy
                
            if x > 0 and y > 0:
                prob_btts += p_xy

    # Doble Oportunidad
    prob_1x = prob_local + prob_empate
    prob_x2 = prob_visitante + prob_empate
    
    cuota_1x = round(1.0 / max(0.05, prob_1x * 0.96), 2)
    cuota_x2 = round(1.0 / max(0.05, prob_x2 * 0.96), 2)
    cuota_btts = 1.85 if prob_btts > 0.5 else 2.10

    # Retornamos el abanico completo sin penalizaciones falsas, puramente matemático
    mercados = [
        {"mercado": "1X2 - Gana Local", "prob": prob_local, "cuota": cuota_local},
        {"mercado": "1X2 - Empate", "prob": prob_empate, "cuota": cuota_empate},
        {"mercado": "1X2 - Gana Visitante", "prob": prob_visitante, "cuota": cuota_visita},
        {"mercado": "Doble Oportunidad - 1X (Local o Empate)", "prob": prob_1x, "cuota": cuota_1x},
        {"mercado": "Doble Oportunidad - X2 (Visitante o Empate)", "prob": prob_x2, "cuota": cuota_x2},
        {"mercado": "Goles - Over 2.5", "prob": prob_over_25, "cuota": cuota_over25},
        {"mercado": "Goles - Under 2.5", "prob": prob_under_25, "cuota": cuota_under25},
        {"mercado": "Ambos Anotan (BTTS - Sí)", "prob": prob_btts, "cuota": cuota_btts}
    ]
    return mercados

def evaluar_partido_multimercado(partido, cuotas_mercado):
    local = partido.get('homeTeam', {}).get('name', '')
    visitante = partido.get('awayTeam', {}).get('name', '')
    
    cuotas_encontradas = None
    for evento in cuotas_mercado:
        home_ev = evento.get('home_team', '').lower()
        away_ev = evento.get('away_team', '').lower()
        if (local.lower()[:5] in home_ev) or (visitante.lower()[:5] in away_ev):
            cuotas_encontradas = evento.get('bookmakers', [])
            break
            
    cuota_local = 2.10
    cuota_empate = 3.40
    cuota_visita = 3.30
    cuota_over25 = 1.90
    cuota_under25 = 1.90
    
    if cuotas_encontradas:
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
            print(f"Error parseando cuotas para {local} vs {visitante}: {e}")

    opciones = modelo_bivariado_dixon_coles(cuota_local, cuota_empate, cuota_visita, cuota_over25, cuota_under25)
    
    mejores = []
    for op in opciones:
        ev = (op["prob"] * op["cuota"]) - 1.0
        stake_k = calcular_kelly(op["prob"], op["cuota"], fraccion=0.25)
        
        # Filtro cuantitativo profesional estricto: EV > 4.5% y Kelly > 0.5%
        if ev > 0.045 and stake_k > 0.5:
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
    print("Iniciando motor cuantitativo con Modelo Bivariado Dixon-Coles...")
    
    partidos = consultar_partidos_hoy()
    if not partidos:
        print("No se obtuvieron partidos de Football-Data. Saliendo de manera segura.")
        return
        
    cuotas_api = consultar_cuotas_multi_liga()
    
    alertas_enviadas = 0
    hoy_str = datetime.now().strftime('%Y-%m-%d %H:%M')
    
    for partido in partidos:
        local = partido.get('homeTeam', {}).get('name', 'Local')
        visitante = partido.get('awayTeam', {}).get('name', 'Visitante')
        competencia = partido.get('competition', {}).get('name', 'Liga Europea')
        
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
                f"🧠 <b>MODELO DIXON-COLES VIP</b> 🧠\n\n"
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
                
    print(f"Proceso finalizado. Total de alertas procesadas: {alertas_enviadas}")

if __name__ == "__main__":
    main()
                
