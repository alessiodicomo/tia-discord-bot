import discord
from discord.ext import commands
from discord import Embed
import aiohttp
import asyncio
import os
import sys
import json
from typing import Optional
from keep_alive import keep_alive

# Configurazioni dal vecchio bot
TOKEN = os.environ.get("DISCORD_TOKEN")
TARGET_CHANNEL_ID = 1253532240923852840
ONLINE_CHANNEL_ID = 1253513418401386556
ADMIN_ID = 563133935677079554

# API URLs & Denoms
STRIDE_API_URL = "https://stride-api.polkachu.com/Stride-Labs/stride/stakeibc/host_zone"
TIA_IBC = "ibc/D79E7D83AB399BFFF93433E54FAA480C191248FC556924A2A8351AE2638B3877"
STTIA_IBC = "ibc/698350B8A61D575025F3ED13E9AC9C0F45C89DEFE92F76D5838F1D3C1A7FF7C9"

intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

STATE_FILE = "bot_state.json"

class BotState:
    current_task: Optional[asyncio.Task] = None
    usd_amount: Optional[float] = None
    alert_threshold: Optional[float] = None
    check_interval: int = 60  # secondi
    channel_id: Optional[int] = None

def save_state():
    data = {
        "usd_amount": BotState.usd_amount,
        "alert_threshold": BotState.alert_threshold,
        "check_interval": BotState.check_interval,
        "channel_id": BotState.channel_id
    }
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"Errore salvataggio stato: {e}")

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                BotState.usd_amount = data.get("usd_amount")
                BotState.alert_threshold = data.get("alert_threshold")
                BotState.check_interval = data.get("check_interval", 60)
                BotState.channel_id = data.get("channel_id")
        except Exception as e:
            print(f"Errore caricamento stato: {e}")

# Async API functions
async def get_stride_redemption_rate(session: aiohttp.ClientSession) -> Optional[float]:
    try:
        async with session.get(STRIDE_API_URL, timeout=10) as response:
            if response.status == 200:
                data = await response.json()
                for zone in data.get("host_zone", []):
                    if zone.get("chain_id") == "celestia":
                        return float(zone.get("redemption_rate"))
    except Exception as e:
        print(f"Errore Stride API: {e}")
    return None

async def get_tia_usd_price(session: aiohttp.ClientSession) -> Optional[float]:
    url = f"https://sqs.osmosis.zone/tokens/prices?base={TIA_IBC}"
    try:
        async with session.get(url, timeout=10) as response:
            if response.status == 200:
                data = await response.json()
                base_prices = data.get(TIA_IBC, {})
                if base_prices:
                    return float(list(base_prices.values())[0])
    except Exception as e:
        print(f"Errore USD API: {e}")
    return None

async def get_osmosis_swap_rate(session: aiohttp.ClientSession, amount_tia: float) -> Optional[float]:
    amount_in_micro = int(amount_tia * 1_000_000)
    url = f"https://sqs.osmosis.zone/router/quote?tokenIn={amount_in_micro}{TIA_IBC}&tokenOutDenom={STTIA_IBC}"
    try:
        async with session.get(url, timeout=10) as response:
            if response.status == 200:
                data = await response.json()
                amount_out_micro = int(data.get("amount_out", 0))
                return amount_out_micro / 1_000_000.0
    except Exception as e:
        print(f"Errore Osmosis API: {e}")
    return None

async def monitoring_loop(channel) -> None:
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                # 1. Ottieni prezzo USD e Redemption rate
                usd_price = await get_tia_usd_price(session)
                stride_rate = await get_stride_redemption_rate(session)
                
                if usd_price is None or stride_rate is None:
                    await channel.send("⚠️ Errore nel recupero dati dalle API (prezzo USD o Stride)")
                    await asyncio.sleep(BotState.check_interval)
                    continue
                
                # 2. Calcola i TIA in ingresso basati sull'importo USD scelto dall'utente
                tia_in = BotState.usd_amount / usd_price
                
                # 3. Ottieni quanti stTIA riceve scambiando 'tia_in' su Osmosis
                sttia_out = await get_osmosis_swap_rate(session, tia_in)
                if sttia_out is None:
                    await channel.send("⚠️ Errore nel calcolo preventivo su Osmosis (SQS API in down?)")
                    await asyncio.sleep(BotState.check_interval)
                    continue
                
                # 4. Calcola i TIA finali dopo il redeem su Stride e il profitto
                tia_final = sttia_out * stride_rate
                profit_tia = tia_final - tia_in
                profit_percentage = (profit_tia / tia_in) * 100
                profit_usd = profit_tia * usd_price
                
                # Formattazione
                profit_str = f"+{profit_percentage:.2f}%" if profit_percentage > 0 else f"{profit_percentage:.2f}%"
                profit_usd_str = f"+${profit_usd:.2f}" if profit_usd > 0 else f"-${abs(profit_usd):.2f}"
                
                # Genera Report
                report = Embed(title="📊 Arbitraggio TIA/stTIA", color=0x00ff00 if profit_percentage > 0 else 0xff0000)
                report.add_field(name="Capitale Investito", value=f"${BotState.usd_amount:.2f} ({tia_in:.2f} TIA)", inline=False)
                report.add_field(name="stTIA Ricevuti", value=f"{sttia_out:.4f} stTIA", inline=True)
                report.add_field(name="TIA Riscattabili", value=f"{tia_final:.4f} TIA", inline=True)
                report.add_field(name="Profitto %", value=profit_str, inline=True)
                report.add_field(name="Profitto USD", value=profit_usd_str, inline=True)
                report.add_field(name="Soglia Allarme", value=f"{BotState.alert_threshold}%", inline=False)
                
                await channel.send(embed=report)
                
                print(f"[LOG] {discord.utils.utcnow().strftime('%Y-%m-%d %H:%M:%S')} | TIA Price: ${usd_price:.4f} | Stride Rate: {stride_rate:.4f} | Profit: {profit_percentage:.2f}% | USD Profit: ${profit_usd:.2f}")
                
                # Se supera la soglia, allarme sul canale target
                if profit_percentage >= BotState.alert_threshold:
                    print(f"[ALERT] Soglia superata! ({profit_percentage:.2f}% >= {BotState.alert_threshold}%)")
                    target_channel = bot.get_channel(TARGET_CHANNEL_ID)
                    if target_channel:
                        alert = Embed(title="🚨 ALLARME ARBITRAGGIO TIA 🚨", color=0xff0000)
                        alert.description = f"Profitto potenziale sopra la soglia del {BotState.alert_threshold}%!"
                        alert.add_field(name="Profitto Calcolato", value=profit_str)
                        alert.add_field(name="Guadagno Netto", value=profit_usd_str)
                        alert.add_field(name="Investimento", value=f"${BotState.usd_amount}")
                        await target_channel.send(content="@here", embed=alert)

                await asyncio.sleep(BotState.check_interval)
                
            except Exception as e:
                print(f"[ERROR] Errore nel loop: {e}")
                await channel.send(f"❌ Errore interno al loop di monitoraggio: {e}")
                break

@bot.event
async def on_ready() -> None:
    print(f'✅ Bot online come {bot.user.name}')
    online_channel = bot.get_channel(ONLINE_CHANNEL_ID)
    if online_channel:
        await online_channel.send("🟢 Bot Arbitraggio TIA/stTIA online e operativo!")
    
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="TIA Arbitrage"))

    load_state()
    if BotState.usd_amount is not None and BotState.channel_id is not None:
        channel = bot.get_channel(BotState.channel_id)
        if channel and BotState.current_task is None:
            BotState.current_task = bot.loop.create_task(monitoring_loop(channel))
            await channel.send("🔄 Monitoraggio ripreso automaticamente dopo il riavvio del server!")

@bot.command()
async def tiastart(ctx: commands.Context) -> None:
    """Avvia il monitoraggio dell'arbitraggio TIA/stTIA"""
    if BotState.current_task:
        await ctx.send("⚠️ Monitoraggio già attivo! Usa prima !tiastop")
        return

    await ctx.send("💰 Inserisci l'importo in **USD** che vuoi investire (es. 100):")
    
    try:
        msg = await bot.wait_for('message', check=lambda m: m.author == ctx.author and m.channel == ctx.channel, timeout=30)
        BotState.usd_amount = float(msg.content.replace(',','.'))

        await ctx.send("🔔 Inserisci la **soglia percentuale** minima per l'allarme (es. 2.0):")
        msg_threshold = await bot.wait_for('message', check=lambda m: m.author == ctx.author and m.channel == ctx.channel, timeout=30)
        BotState.alert_threshold = float(msg_threshold.content.replace(',','.'))
        
        BotState.channel_id = ctx.channel.id
        save_state()
        BotState.current_task = bot.loop.create_task(monitoring_loop(ctx.channel))
        await ctx.send(
            f"✅ Monitoraggio TIA avviato:\n"
            f"- Investimento base: ${BotState.usd_amount}\n"
            f"- Soglia allarme: {BotState.alert_threshold}%"
        )
        
    except asyncio.TimeoutError:
        await ctx.send("⏰ Tempo scaduto per l'inserimento. Riprova con !tiastart")
    except ValueError:
        await ctx.send("❌ Valore inserito non valido (usa il punto per i decimali, es. 2.5).")

@bot.command()
async def tiastop(ctx: commands.Context) -> None:
    """Ferma il monitoraggio"""
    if BotState.current_task:
        BotState.current_task.cancel()
        BotState.current_task = None
        BotState.usd_amount = None
        save_state()
        await ctx.send("🛑 Monitoraggio TIA fermato")
    else:
        await ctx.send("⚠️ Nessun monitoraggio attivo")

@bot.command()
async def status(ctx: commands.Context) -> None:
    """Mostra lo stato attuale del bot"""
    if BotState.current_task:
        await ctx.send(f"🟢 Monitoraggio **attivo** (ogni {BotState.check_interval}s)\nInvestimento test: ${BotState.usd_amount}\nSoglia allarme: {BotState.alert_threshold}%")
    else:
        await ctx.send(f"🔴 Monitoraggio **spento** (Intervallo: {BotState.check_interval}s)")

@bot.command()
async def setinterval(ctx: commands.Context, seconds: int) -> None:
    """Imposta l'intervallo di monitoraggio in secondi"""
    if seconds < 10:
        await ctx.send("⚠️ L'intervallo minimo è 10 secondi per non sovraccaricare le API.")
        return
    BotState.check_interval = seconds
    save_state()
    print(f"[CONFIG] Intervallo aggiornato a {seconds} secondi.")
    await ctx.send(f"⏱️ Intervallo di controllo aggiornato a **{seconds} secondi**.")

@bot.command()
async def price(ctx: commands.Context) -> None:
    """Mostra i prezzi attuali senza avviare un monitoraggio continuo"""
    async with aiohttp.ClientSession() as session:
        usd_price = await get_tia_usd_price(session)
        stride_rate = await get_stride_redemption_rate(session)
        if usd_price and stride_rate:
            embed = Embed(title="💸 Prezzi Attuali", color=0x00A2FF)
            embed.add_field(name="TIA Price (USD)", value=f"${usd_price:.4f}", inline=False)
            embed.add_field(name="Stride stTIA/TIA", value=f"{stride_rate:.6f}", inline=False)
            await ctx.send(embed=embed)
            print(f"[LOG] Prezzo richiesto: TIA=${usd_price:.4f}, Stride={stride_rate:.6f}")
        else:
            await ctx.send("⚠️ Impossibile recuperare i prezzi correnti.")

@bot.command()
async def restart(ctx: commands.Context) -> None:
    """Riavvia il bot (solo admin)"""
    if ctx.author.id != ADMIN_ID:
        await ctx.send("⛔ Permessi insufficienti")
        return

    await ctx.send("🔁 Riavvio in corso...")
    if BotState.current_task:
        BotState.current_task.cancel()
    
    await bot.close()
    os.execv(sys.executable, [sys.executable] + sys.argv)

@bot.command()
async def help(ctx: commands.Context) -> None:
    """Mostra i comandi disponibili"""
    embed = Embed(title="ℹ️ Lista Comandi - TIA Arbitrage Bot", color=0x7289DA, description="Ecco tutti i comandi disponibili per gestire il bot:")
    
    commands_info = {
        "🚀 !tiastart": "Avvia il monitoraggio interattivo (chiede USD e soglia %).",
        "🛑 !tiastop": "Ferma il monitoraggio corrente.",
        "📊 !status": "Mostra se il monitoraggio è attivo e le sue impostazioni.",
        "⏱️ !setinterval <secondi>": "Cambia la frequenza dei controlli (default 60s).",
        "💸 !price": "Mostra il prezzo istantaneo di TIA e Stride senza avviare loop.",
        "🔁 !restart": "Riavvia completamente il bot (solo per admin).",
        "❓ !help": "Mostra questo messaggio di aiuto."
    }
    for cmd, desc in commands_info.items():
        embed.add_field(name=cmd, value=desc, inline=False)
    embed.set_footer(text="Bot sviluppato per l'arbitraggio TIA/stTIA su Osmosis e Stride.")
    await ctx.send(embed=embed)

if __name__ == "__main__":
    keep_alive()
    bot.run(TOKEN)
