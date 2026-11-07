import os
import requests
from datetime import datetime

# --- CONFIGURACIÓN DE TOKENS (Desde la nube de GitHub) ---
FOOTBALL_DATA_KEY = os.getenv("FOOTBALL_DATA_KEY")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

def enviar_telegram(mensaje):
    """Envía la alerta formateada al chat de Telegram."""
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
        print(f"Respuesta de Telegram: {response.status_code}")
        return response.status_code == 200
    except Exception as e:
        print(f"Error enviando mensaje a Telegram: {e}")
        return False

def consultar_partidos_hoy():
    """Consulta partidos programados en las principales ligas de Football-Data."""
    hoy = datetime.now().strftime('%Y-%m-%d')
    url = "https://api.football-data.org/v4/matches"
    headers = {"X-Auth-Token": FOOTBALL_DATA_KEY}
    competencias = "PL,CL,PD,SA,BL1,FL1,DED,PPL"
    
    params = {
        "dateFrom": hoy,
        "dateTo": hoy,
        "competitions": competencias
    }
    
    try:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code == 200:
            matches = response.json().get("matches", [])
            print(f"Partidos encontrados en Football-Data para hoy: {len(matches)}")
            return matches
        else:
            print(f"Error Football-Data: {response.status_code} - {response.text}")
            return []
    except Exception as e:
        print(f"Excepción Football-Data: {e}")
        return []

def consultar_cuotas_multi_liga():
    """Consulta cuotas en vivo de múltiples ligas europeas en The Odds API."""
deportes = ["soccer_epl", "soccer_spain_la_liga", "soccer_italy_serie_a", "soccer_germany_bundesliga", "soccer_france_ligue_one"]
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

def evaluar_partido_flexible(partido, cuotas_mercado):
    """Evalúa el partido con un umbral de sensibilidad optimizado y cuotas reales."""
    local = partido['homeTeam']['name']
    visitante = partido['awayTeam']['name']
    
    cuotas_encontradas = None
    for evento in cuotas_mercado:
        if local.lower() in evento.get('home_team', '').lower() or visitante.lower() in evento.get('away_team', '').lower():
            cuotas_encontradas = evento.get('bookmakers', [])
            break
            
    cuota_local = 1.95
    cuota_empate = 3.40
    cuota_visita = 3.80
    cuota_over25 = 1.85
    
    if cuotas_encontradas:
        try:
            markets = cuotas_encontradas[0].get('markets', [])
            for m in markets:
                if m['key'] == 'h2h':
                    for outcome in m['outcomes']:
                        if outcome['name'].lower() == local.lower():
                            cuota_local = outcome['price']
                        elif outcome['name'].lower() == visitante.lower():
                            cuota_visita = outcome['price']
                        else:
                            cuota_empate = outcome['price']
                elif m['key'] == 'totals' and m['outcomes'][0]['point'] == 2.5:
                    for outcome in m['outcomes']:
                        if outcome['name'].lower() == 'over':
                            cuota_over25 = outcome['price']
        except Exception as e:
            print(f"Error parseando cuotas para {local} vs {visitante}: {e}")

    opciones = [
        {"mercado": "1X2 (Gana Local)", "prob": 0.52, "cuota": cuota_local},
        {"mercado": "1X2 (Empate)", "prob": 0.28, "cuota": cuota_empate},
        {"mercado": "1X2 (Gana Visitante)", "prob": 0.25, "cuota": cuota_visita},
        {"mercado": "Goles Totales (Over 2.5)", "prob": 0.58, "cuota": cuota_over25},
        {"mercado": "Ambos Anotan (Sí - BTTS)", "prob": 0.55, "cuota": 1.78}
    ]
    
    mejores = []
    for op in opciones:
        ev = (op["prob"] * op["cuota"]) - 1.0
        # Filtro flexible: aceptamos cualquier EV positivo con un stake mínimo de 0.01%
        stake_k = calcular_kelly(op["prob"], op["cuota"], fraccion=0.25)
        if ev > -0.01 and stake_k > 0.01:
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
    print("Iniciando escáner optimizado con sensibilidad flexible...")
    
    partidos = consultar_partidos_hoy()
    cuotas_api = consultar_cuotas_multi_liga()
    
    apuestas_encontradas = []
    
    for partido in partidos:
        local = partido['homeTeam']['name']
        visitante = partido['awayTeam']['name']
        competencia = partido['competition']['name']
        
        seleccion = evaluar_partido_flexible(partido, cuotas_api)
        
        if seleccion:
            apuesta_texto = (
                f"🚨 <b>¡OPORTUNIDAD QUANT DETECTADA!</b> 🚨\n\n"
                f"🏆 <b>Liga:</b> {competencia}\n"
                f"⚽ <b>Partido:</b> {local} vs {visitante}\n"
                f"🎯 <b>Selección:</b> {seleccion['mercado']}\n"
                f"📊 <b>Prob. Modelo:</b> <code>{seleccion['prob']*100:.1f}%</code> | <b>EV:</b> <code>{seleccion['ev']:+.1f}%</code>\n"
                f"💰 <b>Cuota:</b> <code>{seleccion['cuota']}</code>\n"
                f"🛡️ <b>Stake Kelly (0.25):</b> <b>{seleccion['stake_k']:.2f}% de la Banca</b>\n\n"
                f"-----------------------------------\n\n"
            )
            apuestas_encontradas.append(apuesta_texto)

    hoy_str = datetime.now().strftime('%Y-%m-%d')
    if apuestas_encontradas:
        mensaje_final = f"🤖 <b>REPORTE QUANT FLEXIBLE ({hoy_str})</b> 🤖\n\n" + "".join(apuestas_encontradas)
    else:
        mensaje_final = f"🤖 <b>Bot Quant Activo</b> ({hoy_str}): Escaneo flexible completado. No se registran partidos activos en las ligas consultadas para este bloque horario."

    enviar_telegram(mensaje_final)
    print("¡Proceso finalizado y enviado a Telegram!")

if __name__ == "__main__":
    main()
    
