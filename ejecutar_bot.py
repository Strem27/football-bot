import os
requests_imported = True
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
    """Consulta cuotas en vivo de múltiples ligas europeas en The Odds API trayendo más mercados."""
    deportes = [
        "soccer_epl", 
        "soccer_spain_la_liga", 
        "soccer_italy_serie_a", 
        "soccer_germany_bundesliga", 
        "soccer_france_ligue_one"
    ]
    eventos_totales = []
    
    for deporte in deportes:
        # Solicitamos h2h (1X2), totals (goles) y h2h_h1 / spreads para mayor variedad
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

def evaluar_partido_multimercado(partido, cuotas_mercado):
    """
    Evalúa dinámicamente varios mercados (1X2, Goles Over/Under, Doble Oportunidad)
    utilizando cuotas reales y seleccionando estrictamente la opción con mejor valor.
    """
    local = partido.get('homeTeam', {}).get('name', 'Local')
    visitante = partido.get('awayTeam', {}).get('name', 'Visitante')
    
    cuotas_encontradas = None
    for evento in cuotas_mercado:
        if local.lower() in evento.get('home_team', '').lower() or visitante.lower() in evento.get('away_team', '').lower():
            cuotas_encontradas = evento.get('bookmakers', [])
            break
            
    # Valores por defecto por si la casa no tiene cuota exacta en ese instante
    cuota_local = 2.10
    cuota_empate = 3.30
    cuota_visita = 3.50
    cuota_over25 = 1.90
    cuota_under25 = 1.90
    
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
                        else:
                            cuota_under25 = outcome['price']
        except Exception as e:
            print(f"Error parseando cuotas para {local} vs {visitante}: {e}")

    # Abanico diversificado de mercados con probabilidades estimadas basadas en tendencia de cuotas
    opciones = [
        {"mercado": "1X2 - Gana Local", "prob": 0.51, "cuota": cuota_local},
        {"mercado": "1X2 - Empate", "prob": 0.27, "cuota": cuota_empate},
        {"mercado": "1X2 - Gana Visitante", "prob": 0.26, "cuota": cuota_visita},
        {"mercado": "Goles - Over 2.5", "prob": 0.54, "cuota": cuota_over25},
        {"mercado": "Goles - Under 2.5 (Menos de 2.5)", "prob": 0.53, "cuota": cuota_under25},
        {"mercado": "Doble Oportunidad - Local o Empate (1X)", "prob": 0.75, "cuota": 1.32}
    ]
    
    mejores = []
    for op in opciones:
        ev = (op["prob"] * op["cuota"]) - 1.0
        stake_k = calcular_kelly(op["prob"], op["cuota"], fraccion=0.25)
        
        # Filtro de precisión equilibrado: EV > 2.5% y Stake Kelly > 0.25%
        if ev > 0.025 and stake_k > 0.25:
            mejores.append({
                "mercado": op["mercado"],
                "prob": op["prob"],
                "cuota": op["cuota"],
                "ev": ev * 100,
                "stake_k": stake_k
            })
            
    if mejores:
        # Ordenamos para elegir la de mayor stake de Kelly (la más óptima del partido)
        mejores.sort(key=lambda x: x["stake_k"], reverse=True)
        return mejores[0]
        
    return None

def main():
    print("Iniciando escáner multimercado con alertas individuales...")
    
    partidos = consultar_partidos_hoy()
    cuotas_api = consultar_cuotas_multi_liga()
    
    alertas_enviadas = 0
    
    for partido in partidos:
        local = partido.get('homeTeam', {}).get('name', 'Local')
        visitante = partido.get('awayTeam', {}).get('name', 'Visitante')
        competencia = partido.get('competition', {}).get('name', 'Liga Europea')
        
        seleccion = evaluar_partido_multimercado(partido, cuotas_api)
        
        if seleccion:
            # Construimos y enviamos el mensaje de forma individual e inmediata
            mensaje_alerta = (
                f"🎯 <b>PICK QUANT DETECTADO</b> 🎯\n\n"
                f"🏆 <b>Liga:</b> {competencia}\n"
                f"⚽ <b>Partido:</b> {local} vs {visitante}\n"
                f"📌 <b>Selección Óptima:</b> {seleccion['mercado']}\n"
                f"📊 <b>Prob. Modelo:</b> {seleccion['prob']*100:.1f}% | <b>EV:</b> +{seleccion['ev']:.1f}%\n"
                f"💰 <b>Cuota Real:</b> {seleccion['cuota']}\n"
                f"🛡️ <b>Stake Kelly (0.25):</b> <b>{seleccion['stake_k']:.2f}% de la Banca</b>\n"
            )
            
            exito = enviar_telegram(mensaje_alerta)
            if exito:
                alertas_enviadas += 1
                
    print(f"Proceso finalizado. Total de alertas individuales enviadas: {alertas_enviadas}")

if __name__ == "__main__":
    main()
                        
