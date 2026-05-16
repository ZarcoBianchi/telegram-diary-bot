import os
import re
import json
from datetime import datetime, date, timedelta
from dateutil import parser as dateparser

from groq import Groq
from supabase import create_client
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
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
# UTILS: DATE NATURALI
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

    giorni = {
        "lunedì": 0, "lunedi": 0,
        "martedì": 1, "martedi": 1,
        "mercoledì": 2,
        "giovedì": 3, "giovedi": 3,
        "venerdì": 4, "venerdi": 4,
        "sabato": 5,
        "domenica": 6,
    }

    for g, idx in giorni.items():
        if g in testo:
            oggi_idx = oggi.weekday()
            delta = oggi_idx - idx
            if delta < 0:
                delta += 7
            return oggi - timedelta(days=delta)

    try:
        d = dateparser.parse(testo, dayfirst=True)
        if d:
            return d.date()
    except:
        pass

    return oggi


def date_to_iso(d: date) -> str:
    return d.isoformat()


# ---------------------------------------------------------
# AI: CLASSIFICAZIONE INTENTO
# ---------------------------------------------------------

def classify_intent(testo: str) -> dict:
    prompt = f"""
Sei un assistente che interpreta messaggi per un diario alimentare.

Devi classificare il seguente messaggio dell'utente e restituire un JSON.

Messaggio: "{testo}"

Possibili intenti:
- "aggiungi"           -> l'utente sta aggiungendo un alimento/pasto
- "cancella"           -> l'utente vuole cancellare qualcosa
- "riepilogo_pasto"    -> l'utente chiede cosa ha mangiato in un pasto (es. pranzo di oggi)
- "riepilogo_giorno"   -> l'utente chiede cosa ha mangiato in un giorno (es. cosa ho mangiato oggi)
- "somma_pasto"        -> l'utente chiede quante calorie per un pasto
- "somma_giorno"       -> l'utente chiede quante calorie in un giorno
- "domanda_generica"   -> domanda che non richiede modifica al diario
- "non_chiaro"         -> non è chiaro cosa vuole

Campi possibili nel JSON:
- "intento": uno degli intenti sopra
- "alimento": stringa, se l'utente parla di un alimento
- "pasto": "colazione", "pranzo", "cena" o "non_specificato"
- "data": "oggi", "ieri", "altro" (se l'utente indica una data diversa)
- "testo_data": testo originale della data se non è oggi/ieri (es. "12 maggio")

Esempi di output:

Per "ho mangiato una banana":
{
  "intento": "aggiungi",
  "alimento": "una banana",
  "pasto": "non_specificato",
  "data": "oggi"
}

Per "cosa ho mangiato per pranzo?":
{
  "intento": "riepilogo_pasto",
  "pasto": "pranzo",
  "data": "oggi"
}

Per "cosa ho mangiato ieri?":
{
  "intento": "riepilogo_giorno",
  "data": "ieri"
}

Per "quante calorie ho mangiato oggi?":
{
  "intento": "somma_giorno",
  "data": "oggi"
}

Per "cancella il vino rosso di ieri":
{
  "intento": "cancella",
  "alimento": "vino rosso",
  "data": "ieri"
}

Rispondi SOLO con un JSON valido, senza testo aggiuntivo.
"""

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.choices[0].message.content.strip()
        # prova a estrarre il JSON
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            raw_json = match.group(0)
            data = json.loads(raw_json)
            if isinstance(data, dict) and "intento" in data:
                return data
    except:
        pass

    # fallback molto semplice
    t = testo.lower()
    if any(x in t for x in ["cancella", "elimina", "rimuovi", "annulla"]):
        return {"intento": "cancella"}
    if "cosa ho mangiato" in t and "pranzo" in t:
        return {"intento": "riepilogo_pasto", "pasto": "pranzo", "data": "oggi"}
    if "cosa ho mangiato" in t and "cena" in t:
        return {"intento": "riepilogo_pasto", "pasto": "cena", "data": "oggi"}
    if "cosa ho mangiato" in t and "colazione" in t:
        return {"intento": "riepilogo_pasto", "pasto": "colazione", "data": "oggi"}
    if "cosa ho mangiato" in t:
        return {"intento": "riepilogo_giorno", "data": "oggi"}
    if "quante calorie" in t or "totale" in t or "somma" in t:
        return {"intento": "somma_giorno", "data": "oggi"}
    if any(x in t for x in ["ho mangiato", "mi sono mangiato", "ho preso"]):
        return {"intento": "aggiungi", "alimento": testo, "pasto": "non_specificato", "data": "oggi"}

    return {"intento": "non_chiaro"}


# ---------------------------------------------------------
# AI: MATCH PER CANCELLAZIONE
# ---------------------------------------------------------

def ai_match_cancel(comando: str, descrizione: str) -> bool:
    prompt = f"""
Sei un assistente che interpreta comandi per un diario alimentare.

L’utente vuole cancellare qualcosa.
Comando dell’utente: "{comando}"
Riga registrata: "{descrizione}"

Devi capire se l’utente si riferisce a questa riga.

Regole:
- Se l’utente dice "vino", considera solo bevande, non piatti con vino.
- Se l’utente dice "vino rosso", considera "bicchiere di vino rosso" come corrispondente.
- Se non è chiaramente lo stesso alimento, rispondi NO.

Rispondi SOLO con: SI o NO.
"""

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
        )
        testo = response.choices[0].message.content.strip().upper()
        return "SI" in testo
    except:
        return False


# ---------------------------------------------------------
# STIMA CALORIE
# ---------------------------------------------------------

def stima_calorie(cibo: str) -> int:
    prompt = f"""
Stima le calorie totali del seguente alimento o piatto:

\"{cibo}\"

Rispondi solo con un numero intero (kcal), senza testo aggiuntivo.
"""

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
        )
        testo = response.choices[0].message.content.strip()
        match = re.search(r"\d+", testo)
        if match:
            return int(match.group(0))
        return 300
    except:
        return 300


# ---------------------------------------------------------
# RICONOSCIMENTO PASTO DA TESTO
# ---------------------------------------------------------

def riconosci_pasto_da_testo(testo: str) -> str:
    t = testo.lower()
    if "colazione" in t or "stamattina" in t or "mattina" in t:
        return "colazione"
    if "pranzo" in t or "mezzogiorno" in t:
        return "pranzo"
    if "cena" in t or "stasera" in t or "sera" in t:
        return "cena"
    return "non_specificato"


def suggerisci_pasto_da_orario(now: datetime) -> str:
    h = now.hour
    if 5 <= h < 11:
        return "colazione"
    if 11 <= h < 16:
        return "pranzo"
    if 16 <= h < 23:
        return "cena"
    return "non_specificato"


# ---------------------------------------------------------
# SUPABASE: SALVATAGGIO E LETTURA
# ---------------------------------------------------------

def salva_pasto(pasto: str, descrizione: str, kcal: int, d: date):
    dt = datetime.combine(d, datetime.now().time())
    supabase.table("pasti").insert({
        "ora": dt.isoformat(),
        "pasto": pasto,
        "descrizione": descrizione,
        "kcal": kcal,
    }).execute()


def get_pasti_by_date(d: date):
    iso = date_to_iso(d)
    res = supabase.table("pasti").select("*").execute()
    return [r for r in res.data if r["ora"].startswith(iso)]


def somma_calorie_giorno(d: date) -> int:
    pasti = get_pasti_by_date(d)
    return sum(r["kcal"] for r in pasti)


def somma_calorie_pasto(d: date, pasto: str) -> int:
    pasti = get_pasti_by_date(d)
    return sum(r["kcal"] for r in pasti if r["pasto"] == pasto)


def format_riepilogo_pasto(d: date, pasto: str) -> str:
    pasti = get_pasti_by_date(d)
    selezionati = [r for r in pasti if r["pasto"] == pasto]
    if not selezionati:
        return f"Nessun {pasto} trovato per la data richiesta."

    lines = [f"{pasto.capitalize()} di {d.isoformat()}:"]
    tot = 0
    for r in selezionati:
        lines.append(f"- {r['descrizione']} ({r['kcal']} kcal)")
        tot += r["kcal"]
    lines.append("")
    lines.append(f"Totale: {tot} kcal")
    return "\n".join(lines)


def format_riepilogo_giorno(d: date) -> str:
    pasti = get_pasti_by_date(d)
    if not pasti:
        return f"Nessun pasto registrato per {d.isoformat()}."

    by_pasto = {"colazione": [], "pranzo": [], "cena": [], "non_specificato": []}
    for r in pasti:
        by_pasto.get(r["pasto"], by_pasto["non_specificato"]).append(r)

    lines = [f"Giorno: {d.isoformat()}"]
    tot = 0
    for p in ["colazione", "pranzo", "cena", "non_specificato"]:
        if by_pasto[p]:
            lines.append("")
            lines.append(f"{p.capitalize()}:")
            for r in by_pasto[p]:
                lines.append(f"- {r['descrizione']} ({r['kcal']} kcal)")
                tot += r["kcal"]
    lines.append("")
    lines.append(f"Totale: {tot} kcal")
    return "\n".join(lines)


# ---------------------------------------------------------
# CANCELLAZIONE AI
# ---------------------------------------------------------

async def cancella_ai(update: Update, testo: str, intent: dict):
    # data
    if intent.get("data") == "ieri":
        d = date.today() - timedelta(days=1)
    elif intent.get("data") == "oggi" or not intent.get("data"):
        d = date.today()
    else:
        td = intent.get("testo_data") or testo
        d = parse_natural_date(td)

    data_str = date_to_iso(d)
    res = supabase.table("pasti").select("*").execute()
    candidati = [r for r in res.data if r["ora"].startswith(data_str)]

    if not candidati:
        await update.message.reply_text("Non ho trovato nulla da cancellare per la data richiesta.")
        return

    alimento = intent.get("alimento", "").strip()
    if not alimento:
        # se non specifica alimento, cancella ultimo pasto del giorno
        candidati = sorted(candidati, key=lambda x: x["id"], reverse=True)
        r = candidati[0]
        supabase.table("pasti").delete().eq("id", r["id"]).execute()
        await update.message.reply_text(f"Ho cancellato: {r['descrizione']} ({r['kcal']} kcal)")
        return

    trovati = []
    for r in candidati:
        if ai_match_cancel(testo, r["descrizione"]):
            trovati.append(r)

    if not trovati:
        await update.message.reply_text("Non ho trovato nulla da cancellare.")
        return

    if len(trovati) == 1:
        r = trovati[0]
        supabase.table("pasti").delete().eq("id", r["id"]).execute()
        await update.message.reply_text(f"Ho cancellato: {r['descrizione']} ({r['kcal']} kcal)")
        return

    keyboard = []
    for r in trovati:
        label = f"{r['descrizione']} - {r['ora'][11:16]}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"del_{r['id']}")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Ho trovato più elementi simili. Quale vuoi cancellare?",
        reply_markup=reply_markup,
    )


# ---------------------------------------------------------
# CALLBACK BOTTONI
# ---------------------------------------------------------

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    user_data = context.user_data

    # cancellazione
    if data.startswith("del_"):
        id_da_cancellare = int(data.replace("del_", ""))
        res = supabase.table("pasti").select("*").eq("id", id_da_cancellare).execute()
        if not res.data:
            await query.edit_message_text("Elemento già cancellato.")
            return
        r = res.data[0]
        supabase.table("pasti").delete().eq("id", id_da_cancellare).execute()
        await query.edit_message_text(
            f"Ho cancellato: {r['descrizione']} ({r['kcal']} kcal)"
        )
        return

    # conferma aggiunta pasto suggerito
    if data == "add_confirm_yes":
        pending = user_data.get("pending_add")
        if not pending:
            await query.edit_message_text("Nessun pasto in attesa di conferma.")
            return
        alimento = pending["alimento"]
        pasto = pending["pasto_suggerito"]
        d = pending["data"]
        kcal = stima_calorie(alimento)
        salva_pasto(pasto, alimento, kcal, d)
        user_data["pending_add"] = None
        await query.edit_message_text(
            f"Registrato {pasto}: {alimento} ({kcal} kcal)"
        )
        return

    if data == "add_confirm_no":
        pending = user_data.get("pending_add")
        if not pending:
            await query.edit_message_text("Nessun pasto in attesa di conferma.")
            return
        alimento = pending["alimento"]
        user_data["pending_add_ask_pasto"] = pending
        user_data["pending_add"] = None

        keyboard = [
            [
                InlineKeyboardButton("Colazione", callback_data="add_set_colazione"),
                InlineKeyboardButton("Pranzo", callback_data="add_set_pranzo"),
                InlineKeyboardButton("Cena", callback_data="add_set_cena"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"Ok, per quale pasto hai mangiato {alimento}?",
            reply_markup=reply_markup,
        )
        return

    if data in ["add_set_colazione", "add_set_pranzo", "add_set_cena"]:
        pending = user_data.get("pending_add_ask_pasto")
        if not pending:
            await query.edit_message_text("Nessun pasto in attesa di conferma.")
            return
        alimento = pending["alimento"]
        d = pending["data"]
        if data == "add_set_colazione":
            pasto = "colazione"
        elif data == "add_set_pranzo":
            pasto = "pranzo"
        else:
            pasto = "cena"
        kcal = stima_calorie(alimento)
        salva_pasto(pasto, alimento, kcal, d)
        user_data["pending_add_ask_pasto"] = None
        await query.edit_message_text(
            f"Registrato {pasto}: {alimento} ({kcal} kcal)"
        )
        return


# ---------------------------------------------------------
# COMANDI
# ---------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ALLOWED_USER_ID:
        await update.message.reply_text("Non sei autorizzato a usare questo bot.")
        return

    await update.message.reply_text(
        "Diario alimentare attivo. Scrivimi cosa hai mangiato o chiedimi un riepilogo."
    )


async def debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ALLOWED_USER_ID:
        await update.message.reply_text("Non sei autorizzato a usare questo bot.")
        return

    risposta = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[{"role": "user", "content": "Dimmi un numero a caso tra 1 e 1000."}],
    )
    testo = risposta.choices[0].message.content.strip()
    await update.message.reply_text("Risposta LLaMA: " + testo)


# ---------------------------------------------------------
# LOGICA PRINCIPALE
# ---------------------------------------------------------

async def log_food(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ALLOWED_USER_ID:
        await update.message.reply_text("Non sei autorizzato a usare questo bot.")
        return

    testo = update.message.text.strip()
    intent = classify_intent(testo)
    now = datetime.now()

    # aggiungi
    if intent["intento"] == "aggiungi":
        alimento = intent.get("alimento", testo).strip()
        pasto_txt = riconosci_pasto_da_testo(testo)
        # data
        if intent.get("data") == "ieri":
            d = date.today() - timedelta(days=1)
        elif intent.get("data") == "oggi" or not intent.get("data"):
            d = date.today()
        else:
            td = intent.get("testo_data") or testo
            d = parse_natural_date(td)

        # se il pasto è già chiaro dal testo, registra subito
        if pasto_txt != "non_specificato":
            kcal = stima_calorie(alimento)
            salva_pasto(pasto_txt, alimento, kcal, d)
            await update.message.reply_text(
                f"Registrato {pasto_txt}: {alimento} ({kcal} kcal)"
            )
            return

        # deduci dal tempo e chiedi conferma
        pasto_suggerito = suggerisci_pasto_da_orario(now)
        if pasto_suggerito == "non_specificato":
            # nessun suggerimento sensato, chiedi direttamente il pasto
            keyboard = [
                [
                    InlineKeyboardButton("Colazione", callback_data="add_set_colazione"),
                    InlineKeyboardButton("Pranzo", callback_data="add_set_pranzo"),
                    InlineKeyboardButton("Cena", callback_data="add_set_cena"),
                ]
            ]
            context.user_data["pending_add_ask_pasto"] = {
                "alimento": alimento,
                "data": d,
            }
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                f"Per quale pasto hai mangiato {alimento}?",
                reply_markup=reply_markup,
            )
            return

        # suggerimento + conferma
        context.user_data["pending_add"] = {
            "alimento": alimento,
            "pasto_suggerito": pasto_suggerito,
            "data": d,
        }
        keyboard = [
            [
                InlineKeyboardButton("Sì", callback_data="add_confirm_yes"),
                InlineKeyboardButton("No", callback_data="add_confirm_no"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"Hai mangiato {alimento} per {pasto_suggerito}?",
            reply_markup=reply_markup,
        )
        return

    # cancella
    if intent["intento"] == "cancella":
        await cancella_ai(update, testo, intent)
        return

    # riepilogo pasto
    if intent["intento"] == "riepilogo_pasto":
        pasto = intent.get("pasto", "pranzo")
        if intent.get("data") == "ieri":
            d = date.today() - timedelta(days=1)
        elif intent.get("data") == "oggi" or not intent.get("data"):
            d = date.today()
        else:
            td = intent.get("testo_data") or testo
            d = parse_natural_date(td)
        msg = format_riepilogo_pasto(d, pasto)
        await update.message.reply_text(msg)
        return

    # riepilogo giorno
    if intent["intento"] == "riepilogo_giorno":
        if intent.get("data") == "ieri":
            d = date.today() - timedelta(days=1)
        elif intent.get("data") == "oggi" or not intent.get("data"):
            d = date.today()
        else:
            td = intent.get("testo_data") or testo
            d = parse_natural_date(td)
        msg = format_riepilogo_giorno(d)
        await update.message.reply_text(msg)
        return

    # somma giorno
    if intent["intento"] == "somma_giorno":
        if intent.get("data") == "ieri":
            d = date.today() - timedelta(days=1)
        elif intent.get("data") == "oggi" or not intent.get("data"):
            d = date.today()
        else:
            td = intent.get("testo_data") or testo
            d = parse_natural_date(td)
        tot = somma_calorie_giorno(d)
        await update.message.reply_text(f"Totale giornaliero: {tot} kcal")
        return

    # somma pasto (se mai la useremo in futuro)
    if intent["intento"] == "somma_pasto":
        pasto = intent.get("pasto", "pranzo")
        if intent.get("data") == "ieri":
            d = date.today() - timedelta(days=1)
        elif intent.get("data") == "oggi" or not intent.get("data"):
            d = date.today()
        else:
            td = intent.get("testo_data") or testo
            d = parse_natural_date(td)
        tot = somma_calorie_pasto(d, pasto)
        await update.message.reply_text(f"Totale {pasto}: {tot} kcal")
        return

    # domanda generica o non chiaro
    await update.message.reply_text(
        "Non ho capito bene cosa vuoi fare. Prova a chiedere un riepilogo, una somma o a dirmi cosa hai mangiato."
    )


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