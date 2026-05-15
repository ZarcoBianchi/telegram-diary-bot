import os
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

# --- CALCOLO CALORIE (placeholder) ---
def stima_calorie(testo):
    return 100  # da sostituire con Nutritionix

# --- SALVATAGGIO SU SUPABASE ---
def salva_pasto(tipo, descrizione, kcal):
    now = datetime.now()

    supabase.table("pasti").insert({
        "data": now.isoformat(),   # timestamptz valido
        "ora": now.isoformat(),    # timestamptz valido
        "pasto": tipo,
        "descrizione": descrizione,
        "kcal": kcal
    }).execute()

# --- COMANDO /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Ciao! Scrivimi cosa hai mangiato e lo registro nel diario 🍎")

# --- COMANDO /test ---
async def test_sheet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        now = datetime.now()

        supabase.table("pasti").insert({
            "data": now.isoformat(),
            "ora": now.isoformat(),
            "pasto": "test",
            "descrizione": "test di connessione",
            "kcal": 0
        }).execute()

        await update.message.reply_text("Test riuscito! Controlla Supabase 👍")
    except Exception as e:
        await update.message.reply_text(f"Errore: {e}")

# --- REGISTRAZIONE PASTI ---
async def log_food(update: Update, context: ContextTypes.DEFAULT_TYPE):
    testo = update.message.text.lower()
    kcal = stima_calorie(testo)

    # riconoscimento tipo pasto
    if "colazione" in testo:
        tipo = "colazione"
    elif "pranzo" in testo:
        tipo = "pranzo"
    elif "cena" in testo:
        tipo = "cena"
    elif "merenda" in testo and "mattutina" in testo:
        tipo = "merenda mattutina"
    elif "merenda" in testo and "pomeridiana" in testo:
        tipo = "merenda pomeridiana"
    else:
        tipo = "non specificato"

    salva_pasto(tipo, testo, kcal)

    await update.message.reply_text(
        f"Registrato {tipo}: {testo} (~{kcal} kcal)"
    )

# --- AVVIO BOT ---
if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("test", test_sheet))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, log_food))
    app.run_polling()