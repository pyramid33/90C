# Guia Completa: Configurar y Usar 90cent Bot

## Que es 90cent?

Es un bot de trading automatizado para **Polymarket** (mercado de predicciones crypto). Opera en mercados de 15 minutos de BTC, ETH, SOL y XRP. La estrategia principal ("Buy Once") compra cuando el precio cae a 98-99 centavos y vende cuando sube a ~99.9 centavos, ganando pequenos margenes repetidamente.

---

## Paso 1: Requisitos Previos

- **Python** instalado
- Una **cuenta en Polymarket** con fondos (USDC en Polygon)
- Tu **private key** de la wallet que usas en Polymarket

---

## Paso 2: Instalar Dependencias

Abre una terminal en la carpeta del proyecto y ejecuta:

```bash
cd 90cent
pip install -r requirements.txt
```

> **Nota:** Si `ta-lib` falla al instalar, en Windows necesitas descargar el binario precompilado desde la [pagina de Gohlke](https://www.lfd.uci.edu/~gohlke/pythonlibs/#ta-lib).

---

## Paso 3: Configurar las Variables de Entorno (.env)

El archivo `.env.example` es tu plantilla. Rellenalo, una vez rellenado puedes renombrarlo a `.env`:

```bash
# Windows
copy .env.example .env

# Linux/Mac
cp .env.example .env
```

Luego edita `.env` y llena lo siguiente:

| Variable | Que es | Obligatorio? |
|---|---|---|
| `POLYMARKET_PRIVATE_KEY` | La private key de tu wallet 
| `POLYMARKET_API_KEY` | Credencial API 
| `POLYMARKET_API_SECRET` | Secreto API 
| `POLYMARKET_API_PASSPHRASE` | Passphrase API  |
| `POLYMARKET_WALLET_ADDRESS` | Tu direccion de wallet 
| `LEADERBOARD_USERNAME` | Tu nombre en el leaderboard 
| `LOG_LEVEL` | Nivel de logs (INFO/DEBUG) 



---

## Paso 4: Configurar el Bot (config.py)

El archivo `config.example.py` muestra la configuracion por defecto. Copia y renombra el archivo a config.py

```bash
# Windows
copy config.example.py config.py

# Linux/Mac
cp config.example.py config.py
```

Las secciones clave son:

### 4a. Tipo de Wallet

```python
POLYMARKET_SIGNATURE_TYPE = 2  # 0=EOA normal, 1=Proxy, 2=Safe
```

- Si usas una wallet normal (MetaMask exportada): usa `0`
- Si usas Polymarket con Proxy wallet: usa `1`
- Si usas Polymarket con Safe wallet: usa `2`
Lo normal es usar `2`

### 4b. Mercados (MARKETS)

Define en que mercados opera el bot. Cada mercado tiene:

- `condition_id`: ID del mercado en Polymarket (se auto-descubre, no hay que tocar nada)
- `min_order_size`: Minimo por orden (1.01 USDC)
- `max_order_size`: Maximo de shares por orden (140)

### 4c. Estrategia Buy Once (BUY_ONCE_CONFIG)

La estrategia principal. Los valores clave:

| Parametro | Default | Que hace |
|---|---|---|
| `enabled` | `True` | Activa/desactiva la estrategia |
| `min_price` | `0.98` | Compra si el precio baja a 98c |
| `max_price` | `0.99` | No compra si esta por encima de 99c |
| `order_size` | `140.0` | Shares por orden |
| `stop_loss_price` | `0.92` | Vende si el precio cae a 92c |
| `trailing_stop_distance` | `0.05` | Trailing stop de 5c |
| `max_time_before_resolution` | `180` | Solo compra si faltan <3 min para resolver |

### 4d. Riesgo (RISK_CONFIG)

| Parametro | Default | Que hace |
|---|---|---|
| `max_position_size` | `0.50` | Maximo 50% del balance en una posicion |
| `stop_loss_percentage` | `1.0` | Stop loss (1.0 = desactivado) |
| `max_leverage` | `1.0` | Sin apalancamiento |

### 4e. Auto-Claim

```python
AUTO_CLAIM_ENABLED = True       # Cobra ganancias automaticamente
AUTO_CLAIM_INTERVAL = 900       # Cada 15 minutos
```

---

## Paso 5: Iniciar el Bot

```bash
# Opcion 1: Directamente
python trading_bot.py

# Opcion 2: Usando el batch script (Windows)
start_bot.bat
```

El bot:

1. Se conecta a Polymarket via WebSocket
2. Auto-descubre los mercados activos (BTC, ETH, SOL, XRP de 15 min)
3. Monitorea precios en tiempo real
4. Compra cuando detecta oportunidad (precio <= 98c)
5. Holdea hasta la resolución o vende por stop-loss
6. Cobra ganancias cada 15 minutos

Los logs se guardan en la terminal y en el archivo `trading_bot.log`.

---

## Paso 6: Usar el Dashboard

El dashboard es una interfaz web para monitorear el bot en tiempo real.
Se inicia automaticamente al ejecutar el bot.
Luego abre tu navegador en: **http://localhost:5052**

### Que puedes ver en el Dashboard

| Seccion | Que muestra |
|---|---|
| **P&L Total** | Ganancia/perdida total en USDC |
| **Win Rate** | Porcentaje de trades ganadores |
| **Trades** | Historial completo de compras y ventas |
| **Posiciones Abiertas** | Posiciones que el bot tiene actualmente |
| **Balance** | Tu balance disponible en USDC |
| **Estado del Mercado** | Mercados activos y sus precios |

### Acciones del Dashboard

| Boton | Que hace |
|---|---|
| **Claim** | Cobra manualmente las posiciones ganadoras |
| **Refresh** | Actualiza los datos del dashboard |

### API del Dashboard (para integraciones)

```
GET  /api/stats       → Estadisticas en JSON
GET  /api/positions   → Posiciones abiertas
GET  /api/trades      → Historial de trades
POST /api/claim       → Cobrar ganancias
POST /api/reset-pnl   → Reiniciar P&L
```

---

## Paso 7: Leaderboard (Opcional)

Si `LEADERBOARD_ENABLED = True` en config, el bot reporta tus estadisticas cada 5 minutos al leaderboard central en:

**https://nine0cent-leaderboard.onrender.com**

Puedes cambiar tu nombre con `LEADERBOARD_USERNAME` en config.py

---

## Resumen del Flujo Completo

```
1. Configuras .env (private key)
2. Ajustas config.py (mercados, tamano de ordenes, riesgo)
3. Inicias el bot → python trading_bot.py
4. El bot detecta mercados y compra automaticamente5. El bot opera automaticamente 24/7
6. Monitoreas desde http://localhost:5052
7. Las ganancias se cobran automaticamente cada 15 min
```

---

## Tips Importantes

- **No toques los `condition_id`** — se auto-descubren. Solo necesitas tocarlos si el auto-descubrimiento falla.
- **Empieza con ordenes pequenas** — ajusta `order_size` a algo como 5-10 USDC mientras aprendes.
- **Revisa los logs** — si algo falla, `trading_bot.log` tiene toda la info.
- **El bot necesita fondos** — asegurate de tener USDC en tu wallet de Polygon.
- **Las estrategias avanzadas** (momentum, technical, AI) estan desactivadas por defecto. La estrategia "Buy Once" es la principal y la mas probada.

---

## Estructura del Proyecto

```
90cent/
├── trading_bot.py           # Bot principal
├── dashboard.py             # Dashboard web (Flask)
├── polymarket_client.py     # Cliente API de Polymarket
├── order_manager.py         # Ejecucion de ordenes y riesgo
├── position_tracker.py      # Tracking de posiciones y P&L
├── claim_utils.py           # Cobro automatico de ganancias
├── config.py                # Configuracion activa
├── config.example.py        # Plantilla de configuracion
├── .env                     # Variables de entorno (privado)
├── .env.example             # Plantilla de variables
├── requirements.txt         # Dependencias Python
├── start_bot.bat            # Script de inicio (Windows)
├── strategies/              # Estrategias de trading
│   ├── momentum_strategy.py
│   ├── technical_indicators.py
│   └── ai_predictor.py
├── positions.json           # Posiciones persistidas
├── trading_bot.log          # Log de transacciones
└── Documentation/           # Documentacion adicional
```

---

## Diagrama de Flujo del Bot

```
WebSocket (precios en tiempo real)
         │
         ▼
  Analisis de senales:
  • Momentum
  • Indicadores tecnicos
  • Flujo de ordenes
  • Perfil de volumen
  • Volatilidad
  • Correlacion cross-market
         │
         ▼
  Estrategia Buy Once:
  1. Monitorea precio <= 98c
  2. Coloca orden limite de compra
  3. Si se llena: activa stop-loss (92c) y trailing stop
  4. Si llega a 99.9c: vende
         │
         ▼
  Tracking de posiciones y P&L
         │
         ▼
  Resolucion del mercado
         │
         ▼
  Auto-Claim (cada 15 min)
         │
         ▼
  Reporta al Leaderboard (cada 5 min)
```
