import os
import requests
from datetime import datetime
from supabase import create_client
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# --- TOKEN TELEGRAM ---
TOKEN = os.getenv("BOT_TOKEN")

# --- SUPABASE ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ---------------------------------------------------------
# 🔥 STIMA CALORIE CON OPENFOODFACTS (GRATIS)
# ---------------------------------------------------------
def stima_calorie(cibo):
    url = "https://world.openfoodfacts.org/cgi/search.pl"

    params = {
        "search_terms": cibo,
        "search_simple": 1,
        "action": "process",
        "json": 1
    }

    try:
        r = requests.get(url, params=params)
        data = r.json()

        if "products" in data and len(data["products"]) > 0:
            prodotto = data["products"][0]
            nutr = prodotto.get("nutriments", {})

            kcal = nutr.get("energy-kcal_100g")
            if kcal:
                return int(kcal)

        return 100  # fallback se non trova nulla
    except:
        return 100  # fallback in caso di errore
        

# ---------------------------------------------------------
# 🧠 RICONOSCIMENTO DEL PASTO (linguaggio naturale)
# ---------------------------------------------------------
def riconosci_pasto(testo):
    testo = testo.lower()

    if "colazione" in testo or "stamattina" in testo or "mattina" in testo:
        return "colazione"
    if "pranzo" in testo or "mezzogiorno" in testo or "oggi a pranzo" in testo:
        return "pranzo"
    if "cena" in testo or "stasera" in testo or "sera" in testo:
        return "cena"

    return "non specificato"


# ---------------------------------------------------------
# 🍽️ ESTRAZIONE DEL CIBO DAL TESTO
# ---------------------------------------------------------
def estrai_cibo(testo):
    testo = testo.lower()

    parole_da_togliere = [
        "ho mangiato", "oggi", "stamattina", "stasera", "a pranzo",
        "a cena", "per", "la", "il", "una", "un", "ho preso"
    ]

    for p in parole_da_togliere:
        testo = testo.replace(p, "")

    return testo.strip()


# ---------------------------------------------------------
# 💾 SALVATAGGIO SU SUPABASE
# ---------------------------------------------------------
def salva_pasto(tipo, descrizione, kcal):
    now = datetime.now()

    supabase.table("pasti").insert({
        # 'data' viene gestita da Supabase (default now())
        "ora": now.isoformat(),
        "pasto": tipo,
        "descrizione": descrizione,
        "kcal": kcal
    }).execute()


# ---------------------------------------------------------
# /start
# ---------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Ciao! Scrivimi cosa hai mangiato e lo registro nel diario 🍎")


# ---------------------------------------------------------
# /test
# ---------------------------------------------------------
async def test_sheet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        now = datetime.now()

        supabase.table("pasti").insert({
            "ora": now.isoformat(),
            "pasto": "test",
            "descrizione": "test di connessione",
            "kcal": 0
        }).execute()

        await update.message.reply_text("Test riuscito! Controlla Supabase 👍")
    except Exception as e:
        await update.message.reply_text(f"Errore: {e}")


# ---------------------------------------------------------
# LOGICA PRINCIPALE DEL BOT
# ---------------------------------------------------------
async def log_food(update: Update, context: ContextTypes.DEFAULT_TYPE):
    testo = update.message.text

    tipo = riconosci_pasto(testo)
    cibo = estrai_cibo(testo)
    kcal = stima_calorie(cibo)

    salva_pasto(tipo, cibo, kcal)

    await update.message.reply_text(
        f"Registrato {tipo}: {cibo} (~{kcal} kcal)"
    )


# ---------------------------------------------------------
# AVVIO BOT
# ---------------------------------------------------------
if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("test", test_sheet))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, log_food))
    app.run_polling()