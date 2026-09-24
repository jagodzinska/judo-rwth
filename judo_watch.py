#!/usr/bin/env python3
"""
judo_watch.py - Beobachtet die RWTH-Hochschulsport-Seiten rund um "Judo Level 1"
und meldet sich, sobald sich dort etwas aendert.

Beobachtet werden:
  * die Buchungsseite Judo Level 1 (Wintersemester)   -> neue Kurse/Zeitraeume/Buchungsstatus
  * die Buchungsseite Bedienstetensportkarte          -> wann die WS-Karte kaufbar wird
  * "Programmstart, Anmeldung und Termine"            -> Anmeldetermine
  * die Judo-Uebersichtsseite                         -> Kursbeschreibung/Hinweise
  * Teilnahmeinformationen                            -> Regeln fuer Bedienstete

Laeuft lokal (Desktop-Benachrichtigung per notify-send) und genauso in GitHub
Actions (dort: Job-Zusammenfassung, Issue, optional ntfy/Telegram).

Aufrufe:
  judo_watch.py                 einmal pruefen (das macht der stuendliche Timer)
  judo_watch.py --status        aktuellen Stand + Countdown anzeigen, nichts abrufen
  judo_watch.py --diff [key]    letzte gespeicherte Aenderung im Detail anzeigen
  judo_watch.py --daily         Tagesmeldung erzwingen
  judo_watch.py --test-notify   Benachrichtigungswege testen
  judo_watch.py --reset         Zustand loeschen (naechster Lauf legt neu an, meldet nichts)
"""

import argparse
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------- Konfiguration

TARGETS = [
    {
        "key": "judo_l1_ws",
        "name": "Judo Level 1 (Wintersemester)",
        "url": "https://buchung.hsz.rwth-aachen.de/angebote/Wintersemester/_Judo_Level_1.html",
        "kind": "booking",
    },
    {
        "key": "bedienstetenkarte",
        "name": "Bedienstetensportkarte",
        "url": "https://buchung.hsz.rwth-aachen.de/angebote/aktueller_zeitraum/_Bedienstetensportkarte.html",
        "kind": "booking",
    },
    {
        "key": "programmstart",
        "name": "Programmstart, Anmeldung und Termine",
        "url": "https://hochschulsport.rwth-aachen.de/go/id/bgxpbf",
        "kind": "cms",
    },
    {
        "key": "judo_uebersicht",
        "name": "Judo-Uebersichtsseite",
        "url": "https://hochschulsport.rwth-aachen.de/cms/hsz/sport/sportangebot/~ilywg/judo/",
        "kind": "cms",
    },
    {
        "key": "teilnahmeinfos",
        "name": "Teilnahmeinformationen",
        "url": "https://hochschulsport.rwth-aachen.de/cms/hsz/sport/~itoe/Teilnahmeinformationen/",
        "kind": "cms",
    },
]

# Termine fuer das Wintersemester 2026/2027 (Quelle: "Programmstart, Anmeldung und
# Termine", Stand 10.09.2026). Erinnerung jeweils X Tage vorher und am Tag selbst.
REMINDERS = [
    {
        "id": "bedienstetenkarte_fenster",
        "date": "2026-10-05",
        "title": "Bedienstetensportkarte WS 26/27",
        "text": ("Ab jetzt sollte die Bedienstetensportkarte fuers Wintersemester vorab "
                 "buchbar sein (eine Woche vor Semesterbeginn). Vorausbuchung geht nur bis "
                 "zum Tag VOR dem Anmeldestart, also spaetestens Mo 12.10.2026."),
        "lead_days": [7, 2],
    },
    {
        "id": "bedienstetenkarte_deadline",
        "date": "2026-10-12",
        "title": "LETZTER TAG: Bedienstetensportkarte kaufen",
        "text": ("Heute ist der letzte Tag, an dem die Bedienstetensportkarte vorab gebucht "
                 "werden kann. Ohne diese Karte ist morgen keine Kursbuchung moeglich."),
        "lead_days": [3, 1],
    },
    {
        "id": "anmeldestart",
        "date": "2026-10-13",
        "time": "16:00",
        "title": "ANMELDESTART Judo Level 1 - heute 16:00 Uhr",
        "text": ("Anmeldung erster Zeitraum WS 26/27: Dienstag 13.10.2026. Judo laeuft im "
                 "ersten Slot ab 16:00 Uhr (Spielsport 16:30, Fitness 17:00). "
                 "Level 1 ist sehr gefragt, also puenktlich sein. Kurs montags "
                 "18:30-19:55, ab Oktober zusaetzlich donnerstags 18:30."),
        "lead_days": [7, 3, 1],
    },
    {
        "id": "programmstart",
        "date": "2026-10-19",
        "title": "Programmstart erster Zeitraum WS 26/27",
        "text": "Ab heute laufen die Kurse des ersten Zeitraums.",
        "lead_days": [1],
    },
]

# Aenderungen, deren Text auf eines dieser Muster passt, gelten als "wichtig"
# und werden mit hoher Dringlichkeit gemeldet.
IMPORTANT_RE = re.compile(
    r"anmeld|buchen|buchbar|freischalt|warteliste|ausgebucht|karte kaufen|"
    r"termin|programmstart|zeitraum|bedienstet|beschäftigt|sportkarte|"
    r"20(2[6-9]|3\d)|wintersemester",
    re.I,
)

DAILY_HOUR = 9          # ab welcher Stunde die "alles ruhig"-Tagesmeldung kommt
FAIL_ALERT_AFTER = 6    # nach so vielen Fehlschlaegen in Folge wird gewarnt
TIMEOUT = 30
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) judo-watch/1.1 "
              "(privater Terminwecker, 1 Abruf pro Stunde)")

STATE_DIR = Path(os.environ.get("JUDO_STATE_DIR") or
                 Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "judo-rwth")
SNAP_DIR = STATE_DIR / "snapshots"
STATE_FILE = STATE_DIR / "state.json"        # gehoert ins Repo: Hashes, Termine, Aenderungen
RUNTIME_FILE = STATE_DIR / "runtime.json"    # nur lokal: ETags, Zeitstempel, Fehlerzaehler
LOG_FILE = STATE_DIR / "events.log"
# Zwischenablage fuer die Issue-Erstellung - bewusst NICHT im versionierten
# Zustand, sonst gaebe es bei jedem Lauf einen Commit.
NOTE_FILE = Path(os.environ.get("RUNNER_TEMP") or os.environ.get("TMPDIR") or "/tmp") / "judo_note.md"
CONFIG_FILE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "judo-rwth" / "config.json"

# In GitHub Actions gibt es keinen Desktop; dort wird ueber Job-Zusammenfassung,
# Issue und optional ntfy/Telegram gemeldet.
IN_ACTIONS = bool(os.environ.get("GITHUB_ACTIONS"))

SENT = []       # gesammelte Meldungen dieses Laufs: (titel, text, dringlichkeit)
UNZUSTELLBAR = []   # Meldungen, die ueber keinen einzigen Kanal rausgingen


# ---------------------------------------------------------------- Hilfsfunktionen

def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def log(line):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {line}\n")


def squash(text):
    """Whitespace normalisieren, Leerzeilen raus."""
    lines = [" ".join(l.split()) for l in text.splitlines()]
    return "\n".join(l for l in lines if l)


def settings():
    """Konfiguration aus Datei, ergaenzt/ueberschrieben durch Umgebungsvariablen."""
    cfg = load_json(CONFIG_FILE, {})
    for key, env in (("ntfy_topic", "JUDO_NTFY_TOPIC"),
                     ("ntfy_server", "JUDO_NTFY_SERVER"),
                     ("telegram_token", "JUDO_TELEGRAM_TOKEN"),
                     ("telegram_chat", "JUDO_TELEGRAM_CHAT")):
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    return cfg


# ---------------------------------------------------------------- Abruf & Parsing

def fetch(url, etag=None):
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "de-DE,de;q=0.9"}
    if etag:
        headers["If-None-Match"] = etag
    r = requests.get(url, headers=headers, timeout=TIMEOUT)
    if r.status_code == 304:
        return None, etag
    r.raise_for_status()
    # Buchungsseiten liefern iso-8859-1, das CMS utf-8 - requests raet sonst falsch
    if r.encoding is None or r.encoding.lower() == "iso-8859-1":
        m = re.search(rb'charset=["\']?([\w-]+)', r.content[:2000], re.I)
        if m:
            r.encoding = m.group(1).decode("ascii", "ignore")
    return r.text, r.headers.get("ETag")


def cell_text(td):
    if td is None:
        return ""
    inp = td.find("input")
    if inp is not None and inp.get("value"):
        return " ".join(inp["value"].split())
    return " ".join(td.get_text(" ").split())


def parse_booking(html):
    """Buchungsseite -> lesbarer, stabiler Textabzug."""
    soup = BeautifulSoup(html, "lxml")
    root = soup.select_one("#bs_content") or soup.select_one("main") or soup
    out = []

    head = root.select_one("h1")
    if head:
        out.append(f"# {' '.join(head.get_text().split())}")

    for block in root.select("div.bs_angblock"):
        desc = block.select_one("div.bs_kursbeschreibung")
        if desc:
            txt = " ".join(squash(desc.get_text(" ")).split())
            if txt:
                out.append("")
                out.append("[Beschreibung]")
                # satzweise ablegen: so schlaegt sich eine Textaenderung spaeter
                # in genau einer Diff-Zeile nieder statt im ganzen Absatz
                out.extend(sent.strip() for sent in re.split(r"(?<=[.!?:])\s+", txt) if sent.strip())
        for tr in block.select("table.bs_kurse tbody tr"):
            nr = cell_text(tr.select_one("td.bs_sknr"))
            if not nr:
                continue          # Aufklapp-/Fuellzeilen ohne Kursnummer
            det = tr.select_one("td.bs_sdet")
            titel = ""
            if det:
                span = det.find("span", recursive=False)
                titel = " ".join(span.get_text(" ").split()) if span else ""
            # vollstaendiger Zeitraum inkl. Jahr steckt im Detail-Aufklappblock
            zeitraum = ""
            if det:
                m = re.search(r"\d{2}\.\d{2}\.\d{4}\s*-\s*\d{2}\.\d{2}\.\d{4}",
                              det.get_text(" ").replace("­", ""))
                if m:
                    zeitraum = re.sub(r"\s*-\s*", "-", " ".join(m.group(0).split()))
            if not zeitraum:
                zeitraum = cell_text(tr.select_one("td.bs_szr"))
            tzo = []
            for inner in tr.select("td.bs_stzo table tr"):
                tzo.append(" ".join(cell_text(td) for td in inner.find_all("td") if cell_text(td)))
            preis = ""
            ps = tr.select_one("td.bs_spreis span")
            if ps:
                preis = " ".join(ps.get_text(" ").split())
            row = " | ".join([
                nr,
                titel,
                "; ".join(t for t in tzo if t),
                zeitraum,
                cell_text(tr.select_one("td.bs_skl")),
                preis,
                cell_text(tr.select_one("td.bs_sbuch")) or "-",
            ])
            out.append(row)

    if not out:
        raise ValueError("Buchungsseite: keine Kurstabelle gefunden - Seitenaufbau geaendert?")
    return "\n".join(out).strip()


def parse_cms(html):
    """CMS-Seite -> Text des Hauptbereichs."""
    soup = BeautifulSoup(html, "lxml")
    root = soup.select_one("main") or soup.select_one("#content")
    if root is None:
        raise ValueError("CMS-Seite: <main> nicht gefunden - Seitenaufbau geaendert?")
    for tag in root(["script", "style", "noscript"]):
        tag.decompose()
    txt = squash(root.get_text("\n"))
    txt = re.sub(r"^(Zum Inhalt springen|Zum Inhaltsbereich)$", "", txt, flags=re.M)
    return squash(txt)


def extract(kind, html):
    return parse_booking(html) if kind == "booking" else parse_cms(html)


# ---------------------------------------------------------------- Bewertung

def booking_highlights(old, new):
    """Kurszeilen vergleichen und die interessanten Aenderungen benennen."""
    def rows(text):
        d = {}
        for line in text.splitlines():
            parts = [p.strip() for p in line.split(" | ")]
            if len(parts) == 7 and parts[0].isdigit():
                d[parts[0]] = parts
        return d

    o, n = rows(old), rows(new)
    notes = []
    for nr in sorted(set(n) - set(o)):
        r = n[nr]
        notes.append(f"NEU: {nr} {r[1]} - {r[3]} - {r[2]} - Buchung: {r[6]}")
    for nr in sorted(set(o) - set(n)):
        notes.append(f"WEG: {nr} {o[nr][1]}")
    labels = {2: "Tag/Zeit/Ort", 3: "Zeitraum", 4: "Leitung", 5: "Preis", 6: "Buchungsstatus"}
    for nr in sorted(set(o) & set(n)):
        for i, label in labels.items():
            if o[nr][i] != n[nr][i]:
                notes.append(f"{label} bei {nr} ({n[nr][1]}): '{o[nr][i]}' -> '{n[nr][i]}'")
    return notes


def unified(old, new, name, context=1):
    return "\n".join(difflib.unified_diff(
        old.splitlines(), new.splitlines(),
        fromfile=f"{name} (vorher)", tofile=f"{name} (jetzt)",
        lineterm="", n=context))


# ---------------------------------------------------------------- Benachrichtigung

def _desktop(title, body, urgency):
    """notify-send, sofern ueberhaupt ein Desktop in Reichweite ist."""
    if IN_ACTIONS:
        return False
    env = os.environ.copy()
    if not env.get("DBUS_SESSION_BUS_ADDRESS"):
        sock = Path(f"/run/user/{os.getuid()}/bus")
        if not sock.exists():
            return False
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={sock}"
    env.setdefault("DISPLAY", ":0")
    try:
        subprocess.run(
            ["notify-send", "-a", "Judo RWTH", "-u", urgency,
             "-i", "appointment-soon", "--", title, body[:1200]],
            env=env, check=True, timeout=15,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return True
    except Exception as exc:
        log(f"notify-send fehlgeschlagen: {exc}")
        return False


def _ntfy(cfg, title, body, urgency):
    topic = cfg.get("ntfy_topic")
    if not topic:
        return False
    server = cfg.get("ntfy_server", "https://ntfy.sh").rstrip("/")
    prio = {"critical": "urgent", "normal": "default", "low": "low"}[urgency]
    try:
        r = requests.post(f"{server}/{topic}", timeout=15,
                          data=body.encode("utf-8"),
                          headers={"Title": title.encode("utf-8"),
                                   "Priority": prio, "Tags": "judo"})
        r.raise_for_status()
        return True
    except Exception as exc:
        log(f"ntfy fehlgeschlagen: {exc}")
        return False


def _telegram(cfg, title, body):
    token, chat = cfg.get("telegram_token"), cfg.get("telegram_chat")
    if not (token and chat):
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=15,
                          json={"chat_id": chat,
                                "text": f"*{title}*\n\n{body}"[:4000],
                                "parse_mode": "Markdown",
                                "disable_web_page_preview": True})
        r.raise_for_status()
        return True
    except Exception as exc:
        log(f"telegram fehlgeschlagen: {exc}")
        return False


def notify(title, body, urgency="normal"):
    cfg = settings()
    SENT.append((title, body, urgency))
    ok = _desktop(title, body, urgency)
    ok = _ntfy(cfg, title, body, urgency) or ok
    ok = _telegram(cfg, title, body) or ok
    if not ok:
        UNZUSTELLBAR.append(title)
    print(f"\n=== {title} ===\n{body}\n")
    log(f"[{urgency}] {title} :: {body.splitlines()[0] if body else ''}")
    return ok


def emit_actions_output(details):
    """Job-Zusammenfassung schreiben und dem Workflow sagen, ob ein Issue faellig ist."""
    if not IN_ACTIONS:
        return
    wichtig = [n for n in SENT if n[2] != "low"]

    lines = []
    for title, body, urgency in SENT:
        mark = {"critical": "🔴", "normal": "🟡", "low": "⚪"}[urgency]
        lines.append(f"### {mark} {title}\n\n{body}\n")
    if not lines:
        lines.append("### ⚪ Keine Aenderungen\n")
    summary = "\n".join(lines)
    if details:
        summary += "\n<details><summary>Vollstaendige Diffs</summary>\n\n```diff\n"
        summary += "\n\n".join(details)[:50000]
        summary += "\n```\n</details>\n"

    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(summary)

    NOTE_FILE.parent.mkdir(parents=True, exist_ok=True)
    NOTE_FILE.write_text(summary, encoding="utf-8")

    if os.environ.get("GITHUB_OUTPUT"):
        titel = " ".join(wichtig[0][0].split()) if wichtig else ""
        if len(wichtig) > 1:
            titel += f" (+{len(wichtig) - 1} weitere)"
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"changed={'true' if wichtig else 'false'}\n")
            fh.write(f"title={titel}\n")
            fh.write(f"note_file={NOTE_FILE}\n")
            fh.write(f"notify_failed={'true' if UNZUSTELLBAR else 'false'}\n")


# ---------------------------------------------------------------- Termine

def reminder_jobs(state, today):
    """Faellige Terminerinnerungen einsammeln."""
    fired = state.setdefault("reminders_fired", [])
    jobs = []
    for rem in REMINDERS:
        day = date.fromisoformat(rem["date"])
        for lead in sorted(set(rem.get("lead_days", [])) | {0}, reverse=True):
            when = day - timedelta(days=lead)
            tag = f"{rem['id']}@{lead}"
            if when <= today and tag not in fired:
                if lead == 0:
                    head = f"HEUTE: {rem['title']}"
                else:
                    head = f"In {lead} Tag{'en' if lead != 1 else ''}: {rem['title']}"
                    head += f" ({day:%d.%m.%Y})"
                if rem.get("time"):
                    head += f" - {rem['time']} Uhr"
                jobs.append((tag, head, rem["text"], "critical" if lead <= 1 else "normal"))
                fired.append(tag)
    return jobs


def countdown_lines(today):
    lines = []
    for rem in REMINDERS:
        day = date.fromisoformat(rem["date"])
        delta = (day - today).days
        if delta < 0:
            continue
        when = "heute" if delta == 0 else ("morgen" if delta == 1 else f"in {delta} Tagen")
        t = f" {rem['time']} Uhr" if rem.get("time") else ""
        lines.append(f"  {day:%d.%m.%Y}{t} ({when}): {rem['title']}")
    return lines


# ---------------------------------------------------------------- Hauptlauf

def check(force_daily=False, quiet=False):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    state = load_json(STATE_FILE, {})          # wird versioniert
    runtime = load_json(RUNTIME_FILE, {})      # bleibt lokal
    pages = state.setdefault("pages", {})
    rpages = runtime.setdefault("pages", {})
    now = datetime.now()
    first_run = not pages

    changes = []       # (name, dringlichkeit, notizen, url)
    details = []       # vollstaendige Diffs fuer die Job-Zusammenfassung
    problems = []

    for tgt in TARGETS:
        key, name = tgt["key"], tgt["name"]
        entry = pages.setdefault(key, {})
        rentry = rpages.setdefault(key, {})
        snap = SNAP_DIR / f"{key}.txt"
        try:
            html, etag = fetch(tgt["url"], rentry.get("etag"))
            if html is None:                       # 304 Not Modified
                rentry["last_check"] = now.isoformat(timespec="seconds")
                rentry["fails"] = 0
                continue
            text = extract(tgt["kind"], html)
        except Exception as exc:
            rentry["fails"] = rentry.get("fails", 0) + 1
            rentry["last_error"] = f"{type(exc).__name__}: {exc}"
            log(f"FEHLER {key}: {rentry['last_error']} (Fehlschlag {rentry['fails']})")
            if rentry["fails"] == FAIL_ALERT_AFTER:
                problems.append(f"{name}: seit {rentry['fails']} Versuchen nicht abrufbar/lesbar "
                                f"({rentry['last_error']})")
            continue

        rentry["fails"] = 0
        rentry.pop("last_error", None)
        rentry["etag"] = etag
        rentry["last_check"] = now.isoformat(timespec="seconds")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

        if entry.get("hash") == digest:
            continue

        old = snap.read_text(encoding="utf-8") if snap.exists() else ""
        snap.write_text(text + "\n", encoding="utf-8")
        entry["hash"] = digest

        if first_run or not old:
            entry["first_seen"] = now.isoformat(timespec="seconds")
            log(f"Erstaufnahme {key} ({len(text.splitlines())} Zeilen)")
            continue

        diff = unified(old, text, name, context=1)
        if not diff.strip():
            continue          # Hash passte nicht, Inhalt aber schon - nichts zu melden
        entry["last_change"] = now.isoformat(timespec="seconds")
        (SNAP_DIR / f"{key}.lastdiff.txt").write_text(
            f"{now:%Y-%m-%d %H:%M}  {tgt['url']}\n\n{diff}\n", encoding="utf-8")
        details.append(diff)

        notes = booking_highlights(old, text) if tgt["kind"] == "booking" else []
        if not notes:
            notes = [l[1:].strip() for l in diff.splitlines()
                     if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))][:8]

        urgency = "critical" if IMPORTANT_RE.search("\n".join(notes)) else "normal"
        changes.append((name, urgency, notes, tgt["url"]))

    # --- sofortige Meldungen
    for name, urgency, notes, url in changes:
        body = "\n".join("- " + n for n in notes[:10])
        if len(notes) > 10:
            body += f"\n... und {len(notes) - 10} weitere Aenderungen"
        body += f"\n\n{url}"
        notify(f"Neues auf: {name}", body, urgency)

    for p in problems:
        notify("Judo-Watcher: Seite nicht lesbar", p + "\n\nEventuell hat sich der Seitenaufbau "
               "geaendert und das Skript muss angepasst werden.", "normal")

    # --- Terminerinnerungen
    for tag, head, text, urgency in reminder_jobs(state, now.date()):
        notify(head, text, urgency)

    # --- Tagesmeldung "laeuft, nichts Neues"
    last_daily = state.get("last_daily")
    due = force_daily or (
        now.hour >= DAILY_HOUR and (last_daily is None or last_daily != now.date().isoformat())
    )
    if due and not changes:
        cd = countdown_lines(now.date())
        body = "Keine Aenderungen auf den beobachteten Seiten.\n\n"
        body += "\n".join(cd) if cd else "  (keine anstehenden Termine hinterlegt)"
        body += f"\n\nLetzte Pruefung: {now:%d.%m.%Y %H:%M}"
        notify("Judo-Watcher laeuft - nichts Neues", body, "low")
    if due:
        state["last_daily"] = now.date().isoformat()

    runtime["last_run"] = now.isoformat(timespec="seconds")
    save_json(STATE_FILE, state)
    save_json(RUNTIME_FILE, runtime)
    emit_actions_output(details)

    if not quiet and not changes:
        print(f"{now:%Y-%m-%d %H:%M}  keine Aenderungen "
              f"({len(TARGETS)} Seiten geprueft, {len(problems)} Probleme)")
    return 0


# ---------------------------------------------------------------- CLI-Ansichten

def show_status():
    state = load_json(STATE_FILE, {})
    runtime = load_json(RUNTIME_FILE, {})
    if not state:
        print("Noch kein Zustand - das Skript lief noch nie. Einmal 'judo_watch.py' aufrufen.")
        return 1
    print(f"Letzter Lauf: {runtime.get('last_run', '?')}\n")
    for tgt in TARGETS:
        e = state.get("pages", {}).get(tgt["key"], {})
        r = runtime.get("pages", {}).get(tgt["key"], {})
        print(f"{'!!' if r.get('fails') else '  '} {tgt['name']}")
        print(f"     geprueft:  {r.get('last_check', 'nie')}")
        seit = e.get("first_seen", "?")
        print(f"     Aenderung: {e.get('last_change', f'keine seit Erstaufnahme {seit}')}")
        if r.get("last_error"):
            print(f"     Fehler:    {r['last_error']} ({r.get('fails')}x)")
    cd = countdown_lines(date.today())
    if cd:
        print("\nAnstehende Termine:")
        print("\n".join(cd))
    print(f"\nZustand: {STATE_DIR}\nProtokoll: {LOG_FILE}")
    return 0


def show_diff(key=None):
    files = sorted(SNAP_DIR.glob("*.lastdiff.txt"))
    if key:
        files = [f for f in files if f.name.startswith(key)]
    if not files:
        print("Keine gespeicherten Aenderungen vorhanden.")
        return 1
    for f in files:
        print("=" * 72)
        print(f.name.replace(".lastdiff.txt", ""))
        print("=" * 72)
        print(f.read_text(encoding="utf-8"))
    return 0


def main():
    ap = argparse.ArgumentParser(description="Beobachtet die RWTH-Judo-/Hochschulsport-Seiten.")
    ap.add_argument("--status", action="store_true", help="Stand und Countdown anzeigen")
    ap.add_argument("--diff", nargs="?", const="", metavar="KEY",
                    help="letzte Aenderung(en) im Detail anzeigen")
    ap.add_argument("--daily", action="store_true", help="Tagesmeldung erzwingen")
    ap.add_argument("--test-notify", action="store_true", help="Benachrichtigung testen")
    ap.add_argument("--reset", action="store_true", help="gespeicherten Zustand loeschen")
    ap.add_argument("--quiet", action="store_true", help="keine Konsolenausgabe wenn nichts los ist")
    args = ap.parse_args()

    if args.test_notify:
        ok = notify("Judo-Watcher: Test",
                    "Wenn du das siehst, funktionieren die Benachrichtigungen.\n"
                    + "\n".join(countdown_lines(date.today())), "normal")
        emit_actions_output([])
        return 0 if ok or IN_ACTIONS else 1
    if args.reset:
        STATE_FILE.unlink(missing_ok=True)
        RUNTIME_FILE.unlink(missing_ok=True)
        for p in SNAP_DIR.glob("*.txt"):
            p.unlink()
        print("Zustand geloescht.")
        return 0
    if args.status:
        return show_status()
    if args.diff is not None:
        return show_diff(args.diff or None)
    return check(force_daily=args.daily, quiet=args.quiet)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
