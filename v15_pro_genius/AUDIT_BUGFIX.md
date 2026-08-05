# 🔎 AUDIT SKRIP V15 LAMA → V15.3

Hasil pembacaan baris-per-baris skrip yang kamu kirim. Semua temuan di bawah ini **fatal atau semi-fatal**, dan sudah diperbaiki di `bot_pro_genius_v15.py` versi 15.3.

## A. BUG FATAL (bot tidak mungkin trading)

| # | Temuan di skrip lama | Dampak | Perbaikan V15.3 |
|---|---|---|---|
| A1 | Tidak ada satu pun panggilan `create_market_order` / `create_order`. Fungsi eksekusi bursa **tidak pernah ada** | Notif Telegram masuk, saldo tidak bergerak → inilah **bug #4** kamu | `eksekusi_order()` lengkap: set leverage → market order → verifikasi fill → pasang TP/SL |
| A2 | `if name == "main":` (tanpa dunder) | `NameError`, bot mati sebelum jalan | `if __name__ == "__main__":` |
| A3 | Blok `run_morning_briefing_loop`, `kimi_timer_worker`, `main_loop` tertulis **tanpa indentasi** | `IndentationError` | Diformat ulang, lolos `py_compile` |
| A4 | `res.choices.message.content` | `AttributeError` — `choices` adalah list | `res.choices[0].message.content` |
| A5 | URL Telegram `https://telegram.org{token}/sendMessage` | Notif **selalu gagal**, dan ditelan `except: pass` | `https://api.telegram.org/bot{TOKEN}/sendMessage` + retry + log error |
| A6 | `base_url="https://openrouter.ai"` (tanpa `/api/v1`) | 404 di semua panggilan AI | `https://openrouter.ai/api/v1` |
| A7 | EvoMap `https://evomap.ai/chat/completions` | 404 | `.../v1/chat/completions`, base bisa di-override dari `.env` |
| A8 | `interogasi_ai_alliance()` **tidak pernah `return True`** kalau DeepSeek CONFIRMED — jatuh ke blok Llama tapi tanpa nilai balik eksplisit di jalur sukses awal | Alur veto rancu | Dipecah jadi `agent2_deepseek()`, `agent3_llama()`, `ai_alliance()` dengan return eksplisit |
| A9 | Exchange dibuat **tanpa apiKey/secret** | Live trading mustahil | Key dibaca dari `.env` + dukungan testnet |

## B. BUG MANAJEMEN RISIKO (bug #1, #2, #3 kamu)

| # | Temuan | Perbaikan |
|---|---|---|
| B1 | Tidak ada state posisi sama sekali → setiap 3 detik sinyal yang sama bisa menembak ulang tanpa batas | `PositionRegistry` + `IN_POSITION` lock + `REENTRY_COOLDOWN_SEC` + `MAX_REENTRY_PER_PAIR_DAY` + `SIGNAL_DEDUP_SEC` |
| B2 | Tidak ada cap posisi global (dokumentasi menyebut 6, kode tidak punya) | `MAX_OPEN_POSITIONS=6`, hitung `open + pending`, status `OVERFLOW_MAX_POS` |
| B3 | Tidak ada pencatatan hasil trade; Morning Briefing berisi **angka palsu hardcode** (`14 Win / 1 Loss`, `+$842.10`) | `TradeJournal` persisted + `notif_close()` WIN/LOSS + briefing dari data nyata |
| B4 | Breakeven Guard hanya diklaim di teks notifikasi, **tidak ada kodenya** | `geser_breakeven()`: cancel SL lama → `STOP_MARKET` baru di entry ±0.05% + notif |
| B5 | Tidak ada TP/SL yang dikirim ke bursa | `TAKE_PROFIT_MARKET` + `STOP_MARKET`, `reduceOnly`, `workingType=MARK_PRICE` |
| B6 | Tidak ada rekonsiliasi dengan bursa | `sync_exchange_positions()` tiap 30 detik |
| B7 | Tidak ada time-stop | `MAX_TRADE_AGE_SEC` (default 60 menit) |

## C. BUG KUALITAS KODE

| # | Temuan | Perbaikan |
|---|---|---|
| C1 | `except: return None, None, 50.0, 50.0, 0.0` → error disembunyikan, dan nilai netral 50/0 bisa memicu sinyal palsu (ATR 0 membuat barrier = harga open) | Exception di-raise & di-log; ATR/Stoch NaN atau non-finite → pair di-skip (`OFFLINE/ERR`) |
| C2 | Semua `except: pass` | `except Exception as exc` + `log.error` |
| C3 | Komentar bilang 26 pair, dokumentasi bilang 37+3, dashboard hanya render 10 | Satu sumber `ASSET_PAIRS` (override dari `.env`), dashboard render semua |
| C4 | Konstanta (0.8 ATR, 30/70, TP 0.12%, leverage) hardcode di kode | Semua pindah ke `.env` |
| C5 | Sizing tanpa `amount_to_precision` | Precision-aware, order tidak ditolak bursa |
| C6 | `datetime.now()` tanpa timezone padahal briefing patok WIB | `timezone(timedelta(hours=7))` eksplisit + `TZ=Asia/Jakarta` di PM2 |
| C7 | Briefing bisa mengirim berkali-kali dalam menit yang sama / terlewat kalau detik 0 kelewat | Penanda `sent_for` harian, cek per 20 detik |
| C8 | Tidak ada log file | `bot_v15.log` + stdout unbuffered |


---

## PATCH V15.6 (audit lanjutan)

| ID | Temuan | Perbaikan |
|---|---|---|
| D1 | Satu API key dipakai bertiga, satu key mati = tiga agen mati | Tiap agen punya klien + key sendiri (CLIENT_KIMI, CLIENT_EVOMAP, CLIENT_LLAMA) |
| D2 | Kimi K2 lama tidak mendukung structured-outputs (error 400 di VPS) | Pindah ke kimi-k2.6 + reasoning, plus mundur otomatis ke prompt-only |
| D3 | EvoMap membalas HTML (base_url salah) | Base resmi https://api.evomap.ai/v1 + prefix sk-evomap- otomatis + deteksi balasan HTML |
| D4 | Llama bisa dilempar ke provider lambat | Provider dikunci ke Groq lewat extra_body |
| D5 | Ukuran posisi memakai angka mati $40 | Ekuitas dibaca berkala dari Binance; margin = % ekuitas live, ada batas minimum |
| D6 | Pair logam dibuang gara-gara beda penulisan simbol | resolve_symbol() memetakan otomatis ke simbol perpetual bursa |
| D7 | Default TRADFI_PAIRS masih PAXG/USDT sisa versi lama | Dikembalikan ke XAU/USDT,XAG/USDT,XPT/USDT |
| D8 | Bot dimatikan tanpa jejak | SIGTERM/SIGINT ditangkap, shutdown rapi + notifikasi STOP |
| D9 | Tidak ada pemberitahuan hidup/mati bot | Notifikasi START/RESTART/STOP dengan penghitung restart di runtime_v15.json |
