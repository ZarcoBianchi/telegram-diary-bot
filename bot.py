import os
import re
from datetime import datetime

import google.generativeai as genai
from supabase import create_client
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# --- TOKEN TELEGRAM ---
TOKEN = os.getenv("BOT_TOKEN")

# --- SUPABASE ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- GEMINI CONFIG ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.0-flash")

# ---------------------------------------------------------
# 🔥 STIMA CALORIE CON GEMINI FLASH
# ---------------------------------------------------------
def stima_calorie(cibo: str) -> int:
    prompt = f"""
Sei un nutrizionista. Stima le calorie totali del seguente alimento o piatto:

\"{cibo}\"

- Considera una porzione media realistica.
- Se ci sono più ingredienti, somma le calorie.
- Rispondi SOLO con un numero intero (le kcal stimate), senza testo aggiuntivo.
"""

    try:
        response = model.generate_content(prompt)
        testo = response.text.strip()

        match = re.search(r"\d+", testo)
        if match:
            return int(match.group(0))

        return 300
    except Exception as e:
        print("Errore Gemini:", e)
        return 300


# ---------------------------------------------------------
# 🧠 RICONOSCIMENTO DEL PASTO
# ---------------------------------------------------------
def riconosci_pasto(testo: str) -> str:
    testo = testo.lower()

    if "colazione" in testo or "stamattina" in testo or "mattina" in testo:
        return "colazione"
    if "pranzo" in testo or "mezzogiorno" in testo:
        return "pranzo"
    if "cena" in testo or "stasera" in testo or "sera" in testo:
        return "cena"

    return "non specificato"


# ---------------------------------------------------------
# 🍽️ ESTRAZIONE DEL CIBO (versione semplice, senza distruggere le parole)
# ---------------------------------------------------------
def estrai_cibo(testo: str) -> str:
    testo = testo.lower()

    parole_da_togliere = [
        "ho mangiato", "oggi", "stamattina", "stasera",
        "a pranzo", "a cena", "per", "ho preso"
    ]

    for p in parole_da_togliere:
        testo = testo.replace(p, "")

    return testo.strip()


# ---------------------------------------------------------
# 💾 SALVATAGGIO SU SUPABASE
# ---------------------------------------------------------
def salva_pasto(tipo: str, descrizione: str, kcal: int):
    now = datetime.now()

    supabase.table("pasti").insert({
        "ora": now.isoformat(),
        "pasto": tipo,
        "descrizione": descrizione,
        "kcal": kcal
    }).execute()


# ---------------------------------------------------------
# /start
# ---------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Ciao! Scrivimi cosa hai mangiato e lo registro nel diario con una stima delle calorie 🍎"
    )


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
# LOGICA PRINCIPALE
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