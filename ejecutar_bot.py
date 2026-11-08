import os
import math
import requests
from datetime import datetime, timedelta

# --- CONFIGURACIÓN DE TOKENS (Desde la nube de GitHub) ---
FOOTBALL_DATA_KEY = os.getenv("FOOTBALL_DATA_KEY")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

def enviar_telegram(mensaje):
    """Envía un mensaje individual y directo a Telegram."""
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
        response = requests.post(url, json=payload)
        return response.status_code == 200
    except Exception as e:
        print(f"Error enviando mensaje a Telegram: {e}")
        return False

def consultar_partidos_hoy():
    """Consulta partidos programados ampliando el rango al fin de semana completo."""
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
    
    try:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code == 200:
            matches = response.json().get("matches", [])
            print(f"Partidos encontrados en Football-Data: {len(matches)}")
            return matches
        else:
            print(f"Error Football-Data: {response.status_code}")
            return []
    except Exception as e:
        print(f"Excepción Football-Data: {e}")
        return []

def consultar_cuotas_multi_liga():
    """Consulta cuotas en vivo de múltiples ligas europeas en The Odds API."""
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
            response = requests.get(url, params=params)
            if response.status_code == 200:
                eventos_totales.extend(response.json())
        except Exception as e:
            print(f"Excepción obteniendo cuotas para {deporte}: {e}")
            
    return eventos_totales

def calcular_kelly(probabilidad_modelo, cuota_decimal, fraccion=0.25):
    """Calcula el porcentaje de bankroll usando Criterio de Kelly Fraccional."""
    if cuota_decimal <= 1.0 or probabilidad_modelo <= 0:
        return 0.0
    
    q = 1.0 - probabilidad_modelo
    b = cuota_decimal - 1.0
    
    kelly_full = (b * probabilidad_modelo - q) / b
    kelly_fraccional = kelly_full * fraccion
    
    return max(0.0, kelly_fraccional * 100)

def poisson_prob(lmbda, k):
    """Calcula la probabilidad de Poisson para k eventos dado un promedio lmbda."""
    try:
        return (math.exp(-lmbda) * (lmbda ** k)) / math.factorial(k)
    except:
        return 0.0

def simular_mercados_equilibrados(cuota_local, cuota_empate, cuota_visita, cuota_over25, cuota_under25, cuota_over15):
    """Modelo de Poisson con calibración simétrica para evitar sesgos hacia los goles bajo."""
    p_l = 1.0 / cuota_local
    p_e = 1.0 / cuota_empate
    p_v = 1.0 / cuota_visita
    
    implied_p_over = 1.0 / cuota_over25
    lambda_global = 2.3 + (implied_p_over * 1.2)
    lambda_global = max(1.9, min(lambda_global, 3.5))
    
    suma_p_h2h = p_l + p_e + p_v
    lambda_local = lambda_global * (p_l / suma_p_h2h) * 1.03
    lambda_visita = lambda_global * (p_v / suma_p_h2h) * 0.97
    
    prob_over_25 = 0.0
    prob_under_25 = 0.0
    prob_over_15 = 0.0
    prob_btts = 0.0
    
    for g_local in range(7):
        for g_visita in range(7):
            p_match = poisson_prob(lambda_local, g_local) * poisson_prob(lambda_visita, g_visita)
            total_goles = g_local + g_visita
            
            if total_goles > 2.5:
                prob_over_25 += p_match
            else:
                prob_under_25 += p_match
                
            if total_goles > 1.5:
                prob_over_15 += p_match
                
            if g_local > 0 and g_visita > 0:
                prob_btts += p_match

    prob_1x = p_l + p_e
    prob_x2 = p_v + p_e
    
    cuota_1x = round(1.0 / max(0.05, prob_1x * 0.96), 2)
    cuota_x2 = round(1.0 / max(0.05, prob_x2 * 0.96), 2)
    cuota_btts = 1.85 if prob_btts > 0.48 else 2.05

    mercados = [
        {"mercado": "1X2 - Gana Local", "prob": p_l, "cuota": cuota_local},
        {"mercado": "1X2 - Empate", "prob": p_e, "cuota": cuota_empate},
        {"mercado": "1X2 - Gana Visitante", "prob": p_v, "cuota": cuota_visita},
        {"mercado": "Doble Oportunidad - 1X (Local o Empate)", "prob": prob_1x, "cuota": cuota_1x},
        {"mercado": "Doble Oportunidad - X2 (Visitante o Empate)", "prob": prob_x2, "cuota": cuota_x2},
        {"mercado": "Goles - Over 2.5", "prob": prob_over_25, "cuota": cuota_over25},
        {"mercado": "Goles - Under 2.5", "prob": prob_under_25, "cuota": cuota_under25},
        {"mercado": "Goles - Over 1.5", "prob": prob_over_15, "cuota": cuota_over15},
        {"mercado": "Ambos Anotan (BTTS - Sí)", "prob": prob_btts, "cuota": cuota_btts}
    ]
    return mercados

def evaluar_partido_multimercado(partido, cuotas_mercado):
    """Busca el partido usando coincidencia parcial de nombres y evalúa todos los mercados."""
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
    cuota_over15 = 1.35
    
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
                            elif outcome.get('point') == 1.5:
                                if 'over' in outcome['name'].lower():
                                    cuota_over15 = outcome['price']
        except Exception as e:
            print(f"Error parseando cuotas para {local} vs {visitante}: {e}")

    opciones = simular_mercados_equilibrados(cuota_local, cuota_empate, cuota_visita, cuota_over25, cuota_under25, cuota_over15)
    
    mejores = []
    for op in opciones:
        ev = (op["prob"] * op["cuota"]) - 1.0
        stake_k = calcular_kelly(op["prob"], op["cuota"], fraccion=0.25)
        
        if ev > 0.025 and stake_k > 0.25:
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
    print("Iniciando motor cuantitativo multimercado calibrado...")
    
    partidos = consultar_partidos_hoy()
    cuotas_api = consultar_cuotas_multi_liga()
    
    alertas_enviadas = 0
    
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
            
            mensaje_alerta = (
                f"🎯 <b>PICK QUANT MULTIMERCADO</b> 🎯\n\n"
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
                
    print(f"Proceso finalizado. Total de alertas individuales enviadas: {alertas_enviadas}")

if __name__ == "__main__":
    main()
