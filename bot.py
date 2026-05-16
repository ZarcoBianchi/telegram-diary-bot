import os
import re
from datetime import datetime, date, timedelta
from dateutil import parser as dateparser

from groq import Groq
from supabase import create_client
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

# ---------------------------------------------------------
# CONFIGURAZIONE
# ---------------------------------------------------------

TOKEN = os.getenv("BOT_TOKEN")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
client = Groq(api_key=GROQ_API_KEY)

MODEL_NAME = "llama-3.3-70b-versatile"

# SOLO TU PUOI USARE IL BOT
ALLOWED_USER_ID = 1042036959


# ---------------------------------------------------------
# UTILS: PARSING DATE NATURALI
# ---------------------------------------------------------

def parse_natural_date(testo: str) -> date:
    testo = testo.lower()

    oggi = date.today()

    if "oggi" in testo:
        return oggi
    if "ieri" in testo:
        return oggi - timedelta(days=1)
    if "l'altro ieri" in testo or "l’altro ieri" in testo:
        return oggi - timedelta(days=2)
    if "due giorni fa" in testo:
        return oggi - timedelta(days=2)
    if "tre giorni fa" in testo:
        return oggi - timedelta(days=3)

    # Giorni della settimana
    giorni = {
        "lunedì": 0, "lunedi": 0,
        "martedì": 1, "martedi": 1,
        "mercoledì": 2,
        "giovedì": 3, "giovedi": 3,
        "venerdì": 4, "venerdi": 4,
        "sabato": 5,
        "domenica": 6
    }

    for g, idx in giorni.items():
        if g in testo:
            oggi_idx = oggi.weekday()
            delta = oggi_idx - idx
            if delta < 0:
                delta += 7
            return oggi - timedelta(days=delta)

    # Date esplicite (12 maggio, 12/05, 12-05-2026)
    try:
        d = dateparser.parse(testo, dayfirst=True)
        if d:
            return d.date()
    except:
        pass

    # Default: oggi
    return oggi


# ---------------------------------------------------------
# STIMA CALORIE (intelligente con quantità e bevande)
# ---------------------------------------------------------

def stima_calorie(cibo: str) -> int:
    prompt = f"""
Sei un nutrizionista italiano. Stima le calorie totali del seguente alimento o piatto:

\"{cibo}\"

Linee guida:
- Considera una porzione standard italiana.
- Se è un piatto completo (pizza, pasta, panino), considera la porzione intera.
- Se è un alimento singolo (mela, yogurt), usa valori realistici.
- Se è una bevanda, interpreta la quantità:
  * bicchiere di vino = 100 kcal
  * lattina di birra = 150 kcal
  * tazza di cappuccino = 80 kcal
  * bottiglia d'acqua = 0 kcal
  * bicchiere di succo = 90 kcal
- Se ci sono più ingredienti, somma le calorie.
- Rispondi SOLO con un numero intero (le kcal stimate), senza testo aggiuntivo.
"""

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}]
        )

        testo = response.choices[0].message.content.strip()
        match = re.search(r"\d+", testo)
        if match:
            return int(match.group(0))
        return 300
    except:
        return 300


# ---------------------------------------------------------
# RICONOSCIMENTO DEL PASTO
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
# ESTRAZIONE DEL CIBO
# ---------------------------------------------------------

def estrai_cibo(testo: str) -> str:
    testo = testo.lower()
    frasi_da_togliere = [
        "ho mangiato", "oggi", "stamattina", "stasera",
        "a pranzo", "a cena", "per", "ho preso"
    ]
    for f in frasi_da_togliere:
        testo = testo.replace(f, "")
    return testo.strip()


# ---------------------------------------------------------
# SALVATAGGIO SU SUPABASE
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
# CALCOLO SOMMA CALORIE
# ---------------------------------------------------------

def somma_calorie(pasto_richiesto: str = None):
    oggi = date.today().isoformat()
    dati = supabase.table("pasti").select("*").execute()
    totale = 0

    for r in dati.data:
        if r["ora"].startswith(oggi):
            if pasto_richiesto:
                if r["pasto"] == pasto_richiesto:
                    totale += r["kcal"]
            else:
                totale += r["kcal"]
    return totale


# ---------------------------------------------------------
# CANCELLAZIONE: LOGICA PRINCIPALE
# ---------------------------------------------------------

async def cancella(update: Update, testo: str):
    data = parse_natural_date(testo)
    data_str = data.isoformat()

    # Cancella ultimo pasto
    if "ultimo" in testo or "ultima" in testo:
        dati = supabase.table("pasti").select("*").order("id", desc=True).limit(1).execute()
        if not dati.data:
            await update.message.reply_text("Non ci sono pasti da cancellare.")
            return

        r = dati.data[0]
        supabase.table("pasti").delete().eq("id", r["id"]).execute()
        await update.message.reply_text(f"Ho cancellato l'ultimo pasto: {r['descrizione']} (~{r['kcal']} kcal)")
        return

    # Cancella un intero pasto (colazione/pranzo/cena)
    for pasto in ["colazione", "pranzo", "cena"]:
        if pasto in testo:
            dati = supabase.table("pasti").select("*").execute()
            da_cancellare = [r for r in dati.data if r["pasto"] == pasto and r["ora"].startswith(data_str)]

            if not da_cancellare:
                await update.message.reply_text(f"Nessun {pasto} trovato per la data richiesta.")
                return

            for r in da_cancellare:
                supabase.table("pasti").delete().eq("id", r["id"]).execute()

            totale = somma_calorie()
            await update.message.reply_text(
                f"Ho cancellato tutto il {pasto}.\n\nTotale giornaliero aggiornato: {totale} kcal"
            )
            return

    # Cancella un alimento specifico
    # Estraggo la parola dopo "cancella"
    match = re.search(r"cancella (.*)", testo)
    if not match:
        await update.message.reply_text("Dimmi cosa vuoi cancellare.")
        return

    alimento = match.group(1).strip()

    dati = supabase.table("pasti").select("*").execute()
    candidati = [r for r in dati.data if alimento in r["descrizione"] and r["ora"].startswith(data_str)]

    if not candidati:
        await update.message.reply_text("Non ho trovato nulla da cancellare.")
        return

    # Se c'è una sola riga → cancella subito
    if len(candidati) == 1:
        r = candidati[0]
        supabase.table("pasti").delete().eq("id", r["id"]).execute()

        # Riepilogo pasto aggiornato
        totale = somma_calorie(r["pasto"])
        await update.message.reply_text(
            f"Ho cancellato: {r['descrizione']} (~{r['kcal']} kcal)\n\n"
            f"{r['pasto'].capitalize()} aggiornato: {totale} kcal"
        )
        return

    # Se ci sono più righe → bottoni inline
    keyboard = []
    for r in candidati:
        label = f"{r['descrizione']} - {r['ora'][11:16]}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"del_{r['id']}")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "Ho trovato più elementi simili. Quale vuoi cancellare?",
        reply_markup=reply_markup
    )


# ---------------------------------------------------------
# CALLBACK BOTTONI INLINE
# ---------------------------------------------------------

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data.startswith("del_"):
        id_da_cancellare = int(query.data.replace("del_", ""))

        # Recupero la riga prima di cancellarla
        dati = supabase.table("pasti").select("*").eq("id", id_da_cancellare).execute()
        if not dati.data:
            await query.edit_message_text("Elemento già cancellato.")
            return

        r = dati.data[0]

        # Cancello
        supabase.table("pasti").delete().eq("id", id_da_cancellare).execute()

        # Riepilogo pasto aggiornato
        totale = somma_calorie(r["pasto"])

        await query.edit_message_text(
            f"Ho cancellato: {r['descrizione']} (~{r['kcal']} kcal)\n\n"
            f"{r['pasto'].capitalize()} aggiornato: {totale} kcal"
        )


# ---------------------------------------------------------
# COMANDI TELEGRAM
# ---------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ALLOWED_USER_ID:
        await update.message.reply_text("❌ Non sei autorizzato a usare questo bot.")
        return

    await update.message.reply_text(
        "Ciao! Scrivimi cosa hai mangiato o chiedimi di cancellare qualcosa 🍎"
    )


async def debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ALLOWED_USER_ID:
        await update.message.reply_text("❌ Non sei autorizzato a usare questo bot.")
        return

    risposta = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[{"role": "user", "content": "Dimmi un numero a caso tra 1 e 1000."}]
    )
    testo = risposta.choices[0].message.content.strip()
    await update.message.reply_text("Risposta LLaMA: " + testo)


# ---------------------------------------------------------
# LOGICA PRINCIPALE
# ---------------------------------------------------------

async def log_food(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    if user_id != ALLOWED_USER_ID:
        await update.message.reply_text("❌ Non sei autorizzato a usare questo bot.")
        return

    testo = update.message.text.lower()

    # CANCELLAZIONE
    if "cancella" in testo or "elimina" in testo or "rimuovi" in testo or "annulla" in testo:
        await cancella(update, testo)
        return

    # SOMMA CALORIE
    if "conto" in testo or "totale" in testo or "somma" in testo or "quante calorie" in testo:
        if "colazione" in testo:
            totale = somma_calorie("colazione")
            await update.message.reply_text(f"Totale colazione: {totale} kcal 🍞")
            return
        elif "pranzo" in testo:
            totale = somma_calorie("pranzo")
            await update.message.reply_text(f"Totale pranzo: {totale} kcal 🍝")
            return
        elif "cena" in testo:
            totale = somma_calorie("cena")
            await update.message.reply_text(f"Totale cena: {totale} kcal 🍽️")
            return
        else:
            totale = somma_calorie()
            await update.message.reply_text(f"Totale giornaliero: {totale} kcal 🔥")
            return

    # REGISTRAZIONE PASTO
    tipo = riconosci_pasto(testo)
    cibo = estrai_cibo(testo)
    kcal = stima_calorie(cibo)

    salva_pasto(tipo, cibo, kcal)
    await update.message.reply_text(f"Registrato {tipo}: {cibo} (~{kcal} kcal)")


# ---------------------------------------------------------
# AVVIO BOT
# ---------------------------------------------------------

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("debug", debug))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, log_food))
    app.run_polling()