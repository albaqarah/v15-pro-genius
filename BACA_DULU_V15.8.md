# PRO GENIUS BOT - V15.8 (GODMODE AGRESSIVE PUCUK/LEMBAH)

## APA YANG BARU DI V15.8?

1. **GODMODE Agresif Tapi Pintar (Lembah & Pucuk)** 
   - Bot sekarang punya deteksi **Ekstrem Reversal** yang brutal. 
   - Hanya akan **LONG di lembah terdalam** (BB Lower + RSI Oversold + Stoch Oversold).
   - Hanya akan **SHORT di pucuk tertinggi** (BB Upper + RSI Overbought + Stoch Overbought).
   - Prompt AI Llama di-tweak habis-habisan agar bot tidak "goblok" (asal tebak tren tengah).

2. **Perbaikan Bug V15.7**
   - **.env.example Fixed:** 8 Variabel (seperti `LLAMA_MIN_SCORE`, `TP_GUARD`, dll) sudah dimasukkan ke `.env.example`.
   - **Margin Guard (-2019 Fix):** Menambahkan proteksi ekuitas. Jika saldo ($38) tidak cukup untuk membuka slot baru (5 slot x $10), bot akan *pass* secara senyap, tidak akan spam error "-2019 Margin insufficient" ke log/Telegram.

## CARA UPDATE
1. Timpa file `patch_godmode.py` lama dengan yang ada di zip ini.
2. Cek `bot_pro_genius_v15.py` untuk melihat integrasi margin guard.
3. Buka `.env.example`, salin 8 baris variabel baru ke file `.env` milikmu yang aktif di VPS.
4. Restart bot: `pm2 restart ecosystem.config.js`
