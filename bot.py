import os
import re
import json
import time
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

# Whisper
from faster_whisper import WhisperModel
from pydub import AudioSegment

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

ALLOWED_USER_ID = 1042036959
PENDING_TIMEOUT = 10

# Whisper model
whisper_model = WhisperModel("small", device="cpu", compute_type="int8")


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
# AI: INTENT + PARSING MULTI-ALIMENTO (VERSIONE MIGLIORATA)
# ---------------------------------------------------------

def ai_parse_intent(testo: str) -> dict:
    prompt = f"""
Sei un parser rigoroso per un diario alimentare.
Devi analizzare il messaggio dell'utente e restituire SOLO un JSON valido.

### FORMATO OBBLIGATORIO
{{
  "intento": "aggiungi | chiedi_calorie | riepilogo_giorno | riepilogo_pasto | somma_giorno | somma_pasto | cancella | altro",
  "pasto": "colazione | pranzo | cena | null",
  "data": "oggi | ieri | null",
  "alimenti": [
    {{
      "alimento": "string o null",
      "quantita": "string o null"
    }}
  ]
}}

### REGOLE IMPORTANTI

1. Se il messaggio contiene parole come:
   - "oggi", "di oggi", "oggi?" → data = "oggi"
   - "ieri", "di ieri" → data = "ieri"

2. Se il messaggio contiene:
   - "colazione" → pasto = "colazione"
   - "pranzo" → pasto = "pranzo"
   - "cena" → pasto = "cena"

3. Se il messaggio contiene:
   - "riepilogo", "recap", "resoconto", "mostrami" → intento = riepilogo_giorno o riepilogo_pasto
     *Se NON è specificato un pasto → riepilogo_giorno*
     *Se è specificato un pasto → riepilogo_pasto*

4. Se il messaggio contiene:
   - "totale", "quante calorie oggi", "somma" → intento = somma_giorno o somma_pasto

5. Se il messaggio contiene:
   - "aggiungi", "metti", "registra", "ho mangiato" → intento = aggiungi

6. Se il messaggio contiene:
   - "cancella", "rimuovi", "elimina" → intento = cancella

7. Se il messaggio NON contiene alimenti → "alimenti": []

8. NON inventare alimenti. NON dividere alimenti composti.

### MESSAGGIO UTENTE
"{testo}"

### RISPOSTA
Rispondi SOLO con il JSON.
"""

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.choices[0].message.content.strip()

        try:
            return json.loads(raw)
        except:
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                return json.loads(m.group(0))

    except:
        pass

    return {
        "intento": "altro",
        "pasto": None,
        "data": None,
        "alimenti": []
    }


# ---------------------------------------------------------
# PARSING QUANTITÀ
# ---------------------------------------------------------

def parse_quantita_string(q: str | None):
    if not q:
        return None
    t = q.lower().strip()

    m = re.match(r"(\d+)\s*(g|grammi|grammo)", t)
    if m:
        return {"tipo": "grammi", "valore": float(m.group(1))}

    m = re.match(r"(\d+)\s*(ml|millilitri|cl|l|litri)", t)
    if m:
        val = float(m.group(1))
        unit = m.group(2)
        if unit == "cl":
            val *= 10
        if unit in ["l", "litri"]:
            val *= 1000
        return {"tipo": "ml", "valore": val}

    m = re.match(r"(\d+)\s*(pezzi|biscotti|uova|fette)", t)
    if m:
        return {"tipo": "pezzi", "valore": float(m.group(1)), "unita": m.group(2)}

    if "cucchiaio" in t:
        return {"tipo": "grammi", "valore": 15}
    if "cucchiaino" in t:
        return {"tipo": "grammi", "valore": 5}
    if "bicchiere" in t:
        return {"tipo": "ml", "valore": 150}

    return None


# ---------------------------------------------------------
# WHISPER: TRASCRIZIONE AUDIO
# ---------------------------------------------------------

def trascrivi_audio(percorso):
    audio = AudioSegment.from_file(percorso)
    wav_path = percorso + ".wav"
    audio.export(wav_path, format="wav")

    segments, info = whisper_model.transcribe(wav_path, beam_size=1)
    testo = "".join([seg.text for seg in segments]).strip()
    return testo
    
    # ---------------------------------------------------------
# AI: STIMA CALORIE
# ---------------------------------------------------------

def ai_kcal_per_100(alimento: str, tipo_quantita: str) -> int:
    if tipo_quantita == "grammi":
        domanda = f"Quante kcal per 100g ha {alimento}? Rispondi solo con un numero."
    else:
        domanda = f"Quante kcal per 100ml ha {alimento}? Rispondi solo con un numero."

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": domanda}],
        )
        testo = response.choices[0].message.content.strip()
        m = re.search(r"\d+", testo)
        if m:
            return int(m.group(0))
        return 300
    except:
        return 300


def ai_kcal_totali(alimento_o_descrizione: str) -> int:
    domanda = f"Quante kcal totali ha {alimento_o_descrizione}? Rispondi solo con un numero."
    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": domanda}],
        )
        testo = response.choices[0].message.content.strip()
        m = re.search(r"\d+", testo)
        if m:
            return int(m.group(0))
        return 300
    except:
        return 300


def ai_risposta_calorie_testuale(testo: str) -> str:
    prompt = f"""
L'utente chiede informazioni sulle calorie di un alimento.

Domanda: "{testo}"

Rispondi in italiano, in modo breve, indicando chiaramente:
- il valore in kcal
- l'unità di riferimento (100g, 100ml, porzione, ecc.)

Esempi:
- "Il cioccolato ha circa 575 kcal per 100g."
- "Il latte intero ha circa 60 kcal per 100ml."
- "Una fetta di torta ha circa 240 kcal."

Rispondi con UNA sola frase.
"""
    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content.strip()
    except:
        return "Direi circa 300 kcal, ma prendilo come una stima molto approssimativa."


# ---------------------------------------------------------
# RICONOSCIMENTO PASTO E DATA
# ---------------------------------------------------------

def riconosci_pasto_da_intent(intent_pasto: str | None, testo: str) -> str:
    if intent_pasto in ["colazione", "pranzo", "cena"]:
        return intent_pasto

    t = testo.lower()
    if "colazione" in t:
        return "colazione"
    if "pranzo" in t:
        return "pranzo"
    if "cena" in t:
        return "cena"

    h = datetime.now().hour
    if 5 <= h < 11:
        return "colazione"
    if 11 <= h < 16:
        return "pranzo"
    if 16 <= h < 23:
        return "cena"

    return "non_specificato"


def riconosci_data_da_intent(intent_data: str | None, testo: str) -> date:
    if intent_data == "oggi":
        return date.today()
    if intent_data == "ieri":
        return date.today() - timedelta(days=1)

    t = testo.lower()
    if "ieri" in t:
        return date.today() - timedelta(days=1)
    if "oggi" in t:
        return date.today()

    return parse_natural_date(testo)


# ---------------------------------------------------------
# SUPABASE: PASTI
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
    return sum(r["kcal"] for r in get_pasti_by_date(d))


def somma_calorie_pasto(d: date, pasto: str) -> int:
    return sum(r["kcal"] for r in get_pasti_by_date(d) if r["pasto"] == pasto)


def format_riepilogo_pasto(d: date, pasto: str) -> str:
    pasti = [r for r in get_pasti_by_date(d) if r["pasto"] == pasto]
    if not pasti:
        return f"Nessun {pasto} trovato per {d.isoformat()}."

    lines = [f"{pasto.capitalize()} di {d.isoformat()}:"]
    tot = 0
    for r in pasti:
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
# CANCELLAZIONE
# ---------------------------------------------------------

async def cancella_ai(update: Update, testo: str, intent: dict):
    t = testo.lower()
    intent_alimento = intent.get("alimento")
    intent_pasto = intent.get("pasto")
    intent_data = intent.get("data")

    d = riconosci_data_da_intent(intent_data, testo)
    data_str = date_to_iso(d)

    res = supabase.table("pasti").select("*").execute()
    candidati = [r for r in res.data if r["ora"].startswith(data_str)]

    if intent_pasto in ["colazione", "pranzo", "cena"]:
        candidati = [r for r in candidati if r["pasto"] == intent_pasto]

    if intent_alimento:
        a = intent_alimento.lower()
        candidati = [r for r in candidati if a in r["descrizione"].lower()]

    # Se l'utente ha specificato un alimento, filtriamo
if intent_alimento:
    a = intent_alimento.lower()
    candidati = [r for r in candidati if a in r["descrizione"].lower()]

# Se dopo il filtro non c'è nulla → non trovato
if not candidati:
    await update.message.reply_text(f"Non ho trovato '{intent_alimento}' da cancellare.")
    return

# Se c'è un solo match → cancellalo subito
if len(candidati) == 1:
    r = candidati[0]
    supabase.table("pasti").delete().eq("id", r["id"]).execute()
    await update.message.reply_text(f"Ho cancellato: {r['descrizione']} ({r['kcal']} kcal)")
    return

# Se ci sono più match → chiedi quale
keyboard = []
for r in candidati:
    label = f"{r['descrizione']} - {r['ora'][11:16]}"
    keyboard.append([InlineKeyboardButton(label, callback_data=f"del_{r['id']}")])

await update.message.reply_text(
    f"Ho trovato più elementi che contengono '{intent_alimento}'. Quale vuoi cancellare?",
    reply_markup=InlineKeyboardMarkup(keyboard),
)

    keyboard = []
    for r in candidati:
        label = f"{r['descrizione']} - {r['ora'][11:16]}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"del_{r['id']}")])

    await update.message.reply_text(
        "Ho trovato più elementi. Quale vuoi cancellare?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    
    # ---------------------------------------------------------
# PENDING / CALLBACK
# ---------------------------------------------------------

def check_pending_timeout(context: ContextTypes.DEFAULT_TYPE):
    ud = context.user_data
    ts = ud.get("pending_timestamp")
    if not ts:
        return False
    return (time.time() - ts) > PENDING_TIMEOUT


async def reset_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ud = context.user_data
    ud.clear()
    if update.message:
        await update.message.reply_text(
            "Ho annullato la richiesta precedente perché non hai risposto."
        )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    await query.answer()

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


# ---------------------------------------------------------
# COMANDI
# ---------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ALLOWED_USER_ID:
        await update.message.reply_text("Non sei autorizzato a usare questo bot.")
        return

    await update.message.reply_text(
        "Diario alimentare attivo.\n"
        "- Scrivimi o mandami un vocale per registrare cosa hai mangiato.\n"
        "- Puoi elencare più alimenti insieme.\n"
        "- Chiedimi \"quante kcal ha ...\" per sapere le calorie.\n"
        "- Chiedimi \"riepilogo di oggi\" o \"totale di oggi\".\n"
        "- Puoi dire \"cancella il latte dalla colazione\"."
    )


async def debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    risposta = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[{"role": "user", "content": "Dimmi un numero a caso tra 1 e 1000."}],
    )
    testo = risposta.choices[0].message.content.strip()
    await update.message.reply_text("Risposta LLaMA: " + testo)


# ---------------------------------------------------------
# HANDLER VOCALE
# ---------------------------------------------------------

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ALLOWED_USER_ID:
        await update.message.reply_text("Non sei autorizzato a usare questo bot.")
        return

    # Scarica il file vocale
    file = await update.message.voice.get_file()
    percorso = f"/tmp/{file.file_id}.ogg"
    await file.download_to_drive(percorso)

    # Trascrivi
    testo = trascrivi_audio(percorso)

    # Mostra cosa hai detto (debug)
    await update.message.reply_text(f"🎤 Hai detto:\n{testo}")

    # Normalizza
    testo_norm = testo.lower().strip().replace(".", "")

    # Salviamo il testo trascritto per log_food()
    context.user_data["voice_text"] = testo_norm

    # Eseguiamo la logica principale
    await log_food(update, context)


# ---------------------------------------------------------
# LOGICA PRINCIPALE
# ---------------------------------------------------------

async def log_food(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ALLOWED_USER_ID:
        await update.message.reply_text("Non sei autorizzato a usare questo bot.")
        return

    # Se arriva da vocale, usa il testo trascritto
    testo = context.user_data.get("voice_text") or update.message.text
    testo = testo.strip()
    ud = context.user_data

    if check_pending_timeout(context):
        await reset_pending(update, context)

    # Parsing AI
    intent = ai_parse_intent(testo)
    intento = intent.get("intento", "altro")
    alimenti_ai = intent.get("alimenti", [])

    # ----------------- CHIEDI CALORIE -----------------
    if intento == "chiedi_calorie":
        risposta = ai_risposta_calorie_testuale(testo)
        await update.message.reply_text(risposta)
        return

    # ----------------- CANCELLA -----------------
    if intento == "cancella":
        await cancella_ai(update, testo, intent)
        return

    # ----------------- RIEPILOGO PASTO -----------------
    if intento == "riepilogo_pasto":
        pasto = riconosci_pasto_da_intent(intent.get("pasto"), testo)
        d = riconosci_data_da_intent(intent.get("data"), testo)
        msg = format_riepilogo_pasto(d, pasto)
        await update.message.reply_text(msg)
        return

    # ----------------- RIEPILOGO GIORNO -----------------
    if intento == "riepilogo_giorno":
        d = riconosci_data_da_intent(intent.get("data"), testo)
        msg = format_riepilogo_giorno(d)
        await update.message.reply_text(msg)
        return

    # ----------------- SOMMA GIORNO -----------------
    if intento == "somma_giorno":
        d = riconosci_data_da_intent(intent.get("data"), testo)
        tot = somma_calorie_giorno(d)
        await update.message.reply_text(f"Totale giornaliero: {tot} kcal")
        return

    # ----------------- SOMMA PASTO -----------------
    if intento == "somma_pasto":
        pasto = riconosci_pasto_da_intent(intent.get("pasto"), testo)
        d = riconosci_data_da_intent(intent.get("data"), testo)
        tot = somma_calorie_pasto(d, pasto)
        await update.message.reply_text(f"Totale {pasto}: {tot} kcal")
        return

    # ----------------- AGGIUNGI (MULTI-ALIMENTO) -----------------
    if intento == "aggiungi":
        if not alimenti_ai:
            await update.message.reply_text(
                "Non ho capito bene cosa hai mangiato. Puoi ripetere?"
            )
            return

        pasto = riconosci_pasto_da_intent(intent.get("pasto"), testo)
        d = riconosci_data_da_intent(intent.get("data"), testo)

        risposte = []
        totale_kcal = 0

        for item in alimenti_ai:
            alimento = item.get("alimento")
            quantita_str = item.get("quantita")
            quantita = parse_quantita_string(quantita_str)

            if not alimento:
                continue

            # Calcolo kcal
            if quantita and quantita["tipo"] in ["grammi", "ml"]:
                tipo_q = "grammi" if quantita["tipo"] == "grammi" else "ml"
                base = ai_kcal_per_100(alimento, tipo_q)
                kcal = int((base * quantita["valore"]) / 100)
            elif quantita and quantita["tipo"] in ["pezzi", "porzione"]:
                descr = f"{quantita_str} {alimento}"
                kcal = ai_kcal_totali(descr)
            else:
                kcal = ai_kcal_totali(alimento)

            # Descrizione fedele (mostra quantità)
            if quantita_str:
                descrizione = f"{quantita_str} {alimento}"
            else:
                descrizione = alimento

            # Salva riga
            salva_pasto(pasto, descrizione, kcal, d)

            # Risposta
            risposte.append(f"- {descrizione} ({kcal} kcal)")
            totale_kcal += kcal

        msg = f"Registrato {pasto}:\n" + "\n".join(risposte) + f"\nTotale: {totale_kcal} kcal"
        await update.message.reply_text(msg)
        return

    # ----------------- ALTRO -----------------
    await update.message.reply_text(
        "Ho capito il messaggio, ma non sembra un pasto o una richiesta del diario.\n"
        "Puoi dirmi cosa hai mangiato, chiedermi le calorie o chiedere un riepilogo."
    )


# ---------------------------------------------------------
# AVVIO BOT
# ---------------------------------------------------------

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("debug", debug))

    app.add_handler(CallbackQueryHandler(button_callback))

    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, log_food))

    app.run_polling()