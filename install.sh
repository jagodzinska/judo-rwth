#!/usr/bin/env bash
# Richtet judo_watch.py als systemd-User-Timer ein (stuendlich, ohne root).
set -euo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE="$UNITS/judo-watch.service"
TIMER="$UNITS/judo-watch.timer"

if [[ "${1:-}" == "--uninstall" ]]; then
    systemctl --user disable --now judo-watch.timer 2>/dev/null || true
    rm -f "$SERVICE" "$TIMER"
    systemctl --user daemon-reload
    echo "Timer entfernt. Zustand liegt weiterhin unter ${XDG_STATE_HOME:-$HOME/.local/state}/judo-rwth"
    exit 0
fi

mkdir -p "$UNITS"

cat > "$SERVICE" <<UNIT
[Unit]
Description=Judo RWTH - Hochschulsport-Seiten auf Aenderungen pruefen
After=network-online.target

[Service]
Type=oneshot
ExecStart=$HIER/judo_watch.py --quiet
# damit notify-send den Desktop findet, falls die Variablen mal fehlen
Environment=DISPLAY=:0
UNIT

cat > "$TIMER" <<'UNIT'
[Unit]
Description=Judo RWTH - stuendliche Pruefung

[Timer]
OnCalendar=hourly
# Streuung, damit nicht alle zur vollen Stunde auf den Server gehen
RandomizedDelaySec=10m
# verpasste Laeufe (Rechner aus, abgemeldet) beim naechsten Start nachholen
Persistent=true
AccuracySec=1m
Unit=judo-watch.service

[Install]
WantedBy=timers.target
UNIT

systemctl --user daemon-reload
systemctl --user enable --now judo-watch.timer
echo "Eingerichtet. Naechster Lauf:"
systemctl --user list-timers judo-watch.timer --no-pager
