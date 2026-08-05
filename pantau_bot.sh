#!/usr/bin/env bash
cd ~/binance-futures-pro-scalper-v14 || exit 1

clear
echo "===== JPMAX DASHBOARD MONITOR ====="
echo "Ctrl+C untuk keluar"
echo

while true; do
  clear
  awk '
    /╔══════════════════════════════════════════════════════════════════════════════/ {buf=$0 ORS; cap=1; next}
    cap {buf=buf $0 ORS}
    /╚══════════════════════════════════════════════════════════════════════════════/ {last=buf; cap=0}
    END {
      if (last != "") print last;
      else {
        print "Dashboard belum ada di logs/bot.log, fallback tail:";
        system("tail -n 30 logs/bot.log");
      }
    }
  ' logs/bot.log
  echo
  echo "Last update: $(date -u '+%Y-%m-%d %H:%M:%S UTC') / $(TZ=Asia/Jakarta date '+%H:%M:%S WIB')"
  echo "Ctrl+C untuk keluar"
  sleep 3
done
