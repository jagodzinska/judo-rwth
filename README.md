# Judo Level 1 RWTH – Terminwächter

Beobachtet stündlich die Hochschulsport-Seiten rund um **Judo Level 1** und meldet
sich per Desktop-Benachrichtigung, sobald sich dort etwas ändert.

## Was beobachtet wird

| Seite | worauf es ankommt |
|---|---|
| [Judo Level 1 (Wintersemester)](https://buchung.hsz.rwth-aachen.de/angebote/Wintersemester/_Judo_Level_1.html) | neue Kursnummern, neue Zeiträume (Jahreswechsel 25/26 → 26/27), Buchungsstatus („keine Buchung" → „buchen" / „Warteliste" / „ausgebucht") |
| [Bedienstetensportkarte](https://buchung.hsz.rwth-aachen.de/angebote/aktueller_zeitraum/_Bedienstetensportkarte.html) | wann die Karte für das Wintersemester kaufbar wird |
| [Programmstart, Anmeldung und Termine](https://hochschulsport.rwth-aachen.de/go/id/bgxpbf) | Anmeldetage und Uhrzeit-Slots |
| [Judo-Übersicht](https://hochschulsport.rwth-aachen.de/cms/hsz/sport/sportangebot/~ilywg/judo/) | Kursbeschreibung, Hinweise zur Halle |
| [Teilnahmeinformationen](https://hochschulsport.rwth-aachen.de/cms/hsz/sport/~itoe/Teilnahmeinformationen/) | Regeln für Bedienstete |

## Meldeverhalten

* **sofort** (stündlich geprüft), sobald sich auf einer Seite inhaltlich etwas ändert.
  Änderungen mit Wörtern wie *Anmeldung, buchen, Zeitraum, Warteliste, 2026/2027* …
  kommen mit hoher Dringlichkeit (Benachrichtigung bleibt stehen).
* **einmal täglich** ab 9 Uhr eine leise „läuft, nichts Neues"-Meldung samt Countdown –
  damit sichtbar bleibt, dass der Wächter arbeitet.
* **Terminerinnerungen** zu den hinterlegten Stichtagen (siehe `REMINDERS` im Skript),
  jeweils einige Tage vorher und am Tag selbst.
* Wenn eine Seite sechsmal hintereinander nicht abrufbar oder nicht mehr lesbar ist
  (z. B. weil das HSZ den Seitenaufbau umstellt), gibt es ebenfalls eine Meldung.

## Bedienung

```bash
./judo_watch.py                # einmal prüfen (macht der Timer stündlich)
./judo_watch.py --status       # Stand + Countdown, ohne Abruf
./judo_watch.py --diff         # letzte Änderung im Detail (unified diff)
./judo_watch.py --daily        # Tagesmeldung erzwingen
./judo_watch.py --test-notify  # Benachrichtigungsweg testen
./judo_watch.py --reset        # Gedächtnis löschen; nächster Lauf nimmt neu auf
```

## Variante 1: lokal per systemd-Timer

```bash
./install.sh              # systemd-User-Timer, stündlich, ohne root
./install.sh --uninstall  # wieder entfernen
```

Kontrolle:

```bash
systemctl --user list-timers judo-watch.timer
journalctl --user -u judo-watch.service -n 50
```

Der Timer läuft im Benutzer-Kontext, also nur solange du angemeldet bist – genau dann,
wenn du die Benachrichtigungen auch sehen kannst. Verpasste Läufe werden nach dem
Anmelden nachgeholt (`Persistent=true`). Soll er auch ohne Anmeldung laufen:
`sudo loginctl enable-linger $USER`.

## Variante 2: GitHub Actions (läuft ohne eigenen Rechner)

Der Workflow `.github/workflows/judo-watch.yml` macht dasselbe stündlich in der Cloud.
Repo: <https://github.com/jagodzinska/judo-rwth>

* **Kostenlos**, weil das Repo öffentlich ist (öffentliche Repos haben unbegrenzte
  Action-Minuten). Beobachtet werden ohnehin nur öffentliche HSZ-Seiten.
* **Der Zustand liegt im Repo** unter `state/`. Der Workflow committet ihn zurück –
  damit ist die Git-History automatisch das Änderungsprotokoll der HSZ-Seiten:
  `git log -p state/snapshots/judo_l1_ws.txt` zeigt, was sich wann geändert hat.
* **Benachrichtigung** geht per **ntfy** aufs Handy (Topic liegt im Repo-Secret
  `JUDO_NTFY_TOPIC`). Zusätzlich legt jeder Fund ein **Issue** an und schreibt alles in
  die Job-Zusammenfassung – das ist das durchsuchbare Archiv, absichtlich ohne
  E-Mail-Abo, damit die täglichen Zustands-Commits nicht das Postfach fluten.

### Wenn der Meldeweg selbst ausfällt

ntfy ist der einzige aktive Kanal, ein stiller Ausfall wäre also fatal. Deshalb bricht
der Workflow bewusst mit einem Fehler ab, wenn eine Meldung über **keinen** Kanal
zugestellt werden konnte – als allerletzter Schritt, damit Issue und Zustand vorher
noch geschrieben werden. Fehlgeschlagene Workflows meldet GitHub per E-Mail, und zwar
unabhängig davon, ob man das Repo abonniert hat. Die tägliche Statusmeldung wirkt
dadurch als Lebenszeichen: Bleibt ntfy stumm, wird der Lauf spätestens am nächsten Tag
rot und die Mail kommt.

### Commit-Rauschen

Der Workflow committet **nur, wenn sich wirklich etwas geändert hat**, plus einmal
täglich durch die Tagesmeldung. Damit das klappt, ist der Zustand zweigeteilt:

* `state/state.json`, `state/snapshots/`, `state/events.log` → versioniert
* `state/runtime.json` (ETags, Zeitstempel, Fehlerzähler) → `.gitignore`, ändert sich
  bei jedem Lauf und hätte sonst 24 Commits pro Tag erzeugt

Der tägliche Commit hat einen Nebennutzen: GitHub **deaktiviert geplante Workflows nach
60 Tagen ohne Repo-Aktivität**. Solange die Tagesmeldung läuft, passiert das nicht.

### Manuell auslösen

Actions → „Judo-Watcher" → *Run workflow*. Mit angehaktem `daily` erzwingt das die
Tagesmeldung, sonst wird nur geprüft.

### Wichtige Einschränkung zum Timing

GitHub führt `schedule`-Jobs unter Last **verzögert** aus, typisch 5–20 Minuten,
gelegentlich mehr. Zum Beobachten „ändert sich die Seite?" ist das völlig ausreichend.
Für den Anmeldestart am 13.10.2026 um 16:00 Uhr verlass dich bitte **nicht** darauf –
dafür sind die Terminerinnerungen da, die schon Tage vorher feuern.

### Push aufs Handy (optional, gilt für beide Varianten)

Lokal über `~/.config/judo-rwth/config.json`, in Actions über Repo-Secrets
(Settings → Secrets and variables → Actions):

| Secret | wofür |
|---|---|
| `JUDO_NTFY_TOPIC` | ntfy.sh-Topic, in der ntfy-App abonnieren |
| `JUDO_TELEGRAM_TOKEN` | Bot-Token von @BotFather |
| `JUDO_TELEGRAM_CHAT` | eigene Chat-ID |

Achtung bei ntfy.sh: Topics sind öffentlich, wer den Namen kennt, liest mit – also einen
langen, nicht erratbaren Namen wählen.

### Beide Varianten parallel

Lokaler Timer und Actions-Workflow stören sich nicht: der lokale Lauf hat seinen Zustand
in `~/.local/state/judo-rwth`, der Workflow seinen in `state/` im Repo. Du bekommst die
Meldung dann eben zweimal – auf dem Desktop und als Issue.

## Konfigurationsdatei (lokal)

`~/.config/judo-rwth/config.json`, alle Schlüssel optional:

```json
{
  "ntfy_topic": "irgendein-langer-eigener-name",
  "ntfy_server": "https://ntfy.sh",
  "telegram_token": "123456:ABC...",
  "telegram_chat": "987654321"
}
```

Dieselben Werte lassen sich auch als Umgebungsvariablen setzen
(`JUDO_NTFY_TOPIC`, `JUDO_NTFY_SERVER`, `JUDO_TELEGRAM_TOKEN`, `JUDO_TELEGRAM_CHAT`);
so kommen sie in GitHub Actions aus den Repo-Secrets.

## Dateien

| Datei | wozu |
|---|---|
| `judo_watch.py` | das Skript, läuft lokal wie in Actions |
| `install.sh` | richtet den lokalen systemd-Timer ein (`--uninstall` entfernt ihn) |
| `.github/workflows/judo-watch.yml` | der stündliche Cloud-Lauf |
| `state/` | Zustand des Actions-Laufs (versioniert, außer `runtime.json`) |
| `~/.local/state/judo-rwth/` | Zustand des lokalen Laufs, inkl. `events.log` |
| `~/.config/judo-rwth/config.json` | optionale lokale Konfiguration |

`JUDO_STATE_DIR` überschreibt das Zustandsverzeichnis – so laufen beide Varianten
nebeneinander, ohne sich in die Quere zu kommen.

## Stand der Recherche (10.09.2026)

Für das **Wintersemester 2026/2027** stehen die Termine schon fest, die Judo-Seite
zeigt aber noch die Kurse aus 25/26:

* **Anmeldestart 1. Zeitraum: Dienstag, 13.10.2026, ab 16:00 Uhr.**
  Die Freischaltung läuft in drei Slots: 16:00 alles außer Spielsport und Fitness
  (**Judo fällt in diesen ersten Slot**), 16:30 Spielsport, 17:00 Fitness.
* Programmstart 1. Zeitraum: 19.10.2026. In den HSZ-eigenen Hallen endet der
  1. Zeitraum schon am 22.12.2026 (Inbetriebnahme Sportkomplex Königshügel),
  der 2. Zeitraum startet überall am 18.01.2027 (Anmeldung dafür: 12.01.2027).
* **Bedienstetensportkarte:** Voraussetzung für jede Buchung. Sie ist schon eine Woche
  vor Semesterbeginn buchbar, aber **nur bis zum Tag vor dem Anmeldestart**, also
  spätestens **Montag, 12.10.2026**. Danach geht die Vorausbuchung technisch nicht mehr.
  Preis: 25 € durchgehend bzw. 12,50 € pro Zeitraum. Die aktuell dort gelistete Karte
  gilt noch bis 11.10.2026 – die Winterkarte muss also erst noch erscheinen; genau
  darauf schaut der Wächter.
* Judo Level 1 lag zuletzt bei **18 €**, montags **18:30–19:55**, zwei parallele Gruppen.
  Eine der beiden Gruppen ist passwortgeschützt und richtet sich an fortgeschrittene
  Anfänger – das Passwort gibt es beim Kursleiter.
* Judogi kann für Level 1 ausgeliehen werden: erst **Kaution 50 €** buchen (mit
  Körpergröße), danach **Leihgebühr 5 €**, Abholung im Service Point.
* Barfuß, kein Schmuck; für die ersten Einheiten reicht lange, reißfeste Kleidung.

Die Stichtage stecken als `REMINDERS` im Skript. Falls das HSZ die Termine verschiebt,
meldet der Wächter die Änderung auf der Terminseite – die Liste dann dort anpassen.
