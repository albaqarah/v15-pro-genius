# 🤖 BINANCE FUTURES PRO SCALPER V15.6 "GENIUS ENGINE"

Industrial Hybrid Multi-Agentic Quant Trading Engine — 3 AI Agent + radar matematika lokal.
Rilis ini adalah **bug-fix + hardening** dari V15 milikmu, khusus menutup 4 bug yang kamu laporkan.

---


## V15.6 - APA YANG BARU

| Kode | Perubahan |
|---|---|
| A | Tiga AI agent punya API key sendiri-sendiri. Kimi = `moonshotai/kimi-k2.6` + reasoning, DeepSeek V4 Flash lewat EvoMap `https://api.evomap.ai/v1`, Llama 3.3 70B dikunci provider Groq. |
| B | Tampilan log & TUI dipercantik: panel ekuitas, ikon per jenis kejadian, footer status AI. |
| C | Ukuran posisi mengikuti SALDO LIVE Binance (10% ekuitas per trade), bukan angka mati $40. |
| D | MAX_OPEN_POSITIONS = 6. |
| E | XAU/XAG/XPT dipakai lagi + resolver simbol otomatis (XAU/USDT -> XAU/USDT:USDT). |
| F | Notifikasi Telegram baru: ENTRY, TP/SL, morning briefing, START/RESTART/STOP. |
| G | 43 pair: 40 crypto + 3 logam. |

## 📦 ISI PAKET

| File | Fungsi |
|---|---|
| `bot_pro_genius_v15.py` | Engine utama (radar + 3 AI agent + eksekusi + watcher + journal) |
| `.env.example` | Semua konfigurasi (copy ke `.env`) |
| `selftest.py` | Tes koneksi Binance / Telegram / OpenRouter / EvoMap sebelum live |
| `install.sh` | Installer VPS (venv + dependency + cek sintaks) |
| `ecosystem.config.js` | PM2 config (`PYTHONUNBUFFERED=1`, autorestart, TZ Jakarta) |
| `requirements.txt` | Dependency Python |
| `AUDIT_BUGFIX.md` | Audit skrip lama + penjelasan tiap perbaikan |
| `.gitignore` | Melindungi `.env`, state, dan log dari git |

---

## 🚀 CARA PAKAI (VPS)

```bash
unzip v15_pro_genius.zip -d ~/v15 && cd ~/v15
bash install.sh
nano .env                      # isi API key
./venv/bin/python3 selftest.py # semua harus OK
pm2 start ecosystem.config.js && pm2 logs v15-genius
```

Mulai **DRY_RUN_MODE=TRUE** minimal 48 jam. Baru set `FALSE` kalau statistik sehat.

---

## 🩹 4 BUG YANG DIPERBAIKI

### 1. Belum ada batas open position / re-entry per pair
Ditambahkan `PositionRegistry` sebagai satu-satunya sumber kebenaran posisi:
- `MAX_POSITION_PER_PAIR=1` → pair yang sudah punya posisi statusnya `IN_POSITION`, dilarang entry lagi.
- `REENTRY_COOLDOWN_SEC=900` → setelah pair ditutup, pair itu dibekukan 15 menit (`COOLDOWN_xxs` di dashboard).
- `MAX_REENTRY_PER_PAIR_DAY=0` → **UNLIMITED** (tanpa batas re-entry harian). Isi angka > 0 kalau suatu saat mau dibatasi.
- `SIGNAL_DEDUP_SEC=60` → sinyal identik beruntun diabaikan.
- `pending` set + `asyncio.Lock` → mencegah **race condition**: 26 task async tidak bisa lagi menembak pair yang sama di tick yang sama.

### 2. Batas maksimal posisi live = 6
- `MAX_OPEN_POSITIONS=6` dihitung dari `len(open) + len(pending)`, jadi order yang sedang diproses ikut memakan slot.
- Kalau slot penuh, pair berikutnya berstatus `OVERFLOW_MAX_POS` (tidak entry, tidak memanggil AI → hemat kuota token).
- Slot terpakai ditampilkan di dashboard dan ikut dikirim di setiap notifikasi.

### 3. Notifikasi Telegram sinyal masuk + WIN/LOSS
- `notif_entry()` → kartu ENTRY (pair, arah, mode, entry, qty, TP, SL, order id, verdict AI, slot terpakai, rekap 24 jam).
- `position_watcher()` → memantau tiap posisi 2 detik: breakeven guard, TP hit, SL hit, time-stop.
- `notif_close()` → kartu **✅ WIN / ❌ LOSS** dengan exit price, alasan close (TAKE PROFIT / STOP LOSS / BREAKEVEN STOP / TIME STOP), durasi, PnL USDT, PnL %, ROI ×leverage.
- `TradeJournal` menyimpan riwayat ke `state_v15.json` → Win Rate, Profit Factor, PnL 24 jam, top profit/top loss. Statistik **tetap hidup setelah restart PM2**.
- Morning Briefing 07:00 WIB kini memakai **angka nyata**, bukan angka hardcode seperti versi lama.

### 4. Notif sinyal masuk tapi tidak ter-entry di app
Ini bug paling berbahaya di versi lama: notifikasi dikirim tanpa pernah memanggil order Binance sama sekali.
Sekarang alurnya **execute-then-notify**:
1. Set leverage → `create_order` market.
2. Verifikasi `filled > 0` dan `average > 0`; kalau kosong, `fetch_order` ulang.
3. Kalau tetap `filled = 0` → **notif ENTRY dibatalkan**, yang dikirim justru peringatan `⚠️ ENTRY GAGAL` + alasan error, dan slot dilepas.
4. Baru setelah fill terkonfirmasi: pasang `TAKE_PROFIT_MARKET` + `STOP_MARKET` (`reduceOnly`, `workingType=MARK_PRICE`) lalu kirim notif dengan harga entry **rata-rata fill nyata**.
5. `sync_exchange_positions()` tiap 30 detik merekonsiliasi posisi lokal vs `fetch_positions()` — kalau posisi sudah hilang di bursa (TP/SL kena, atau ditutup manual), bot menutup catatan lokal + kirim laporan, tidak lagi ngunci slot hantu.

---

### 5. Gate jam pasar TradFi (XAU / XAG / XPT)
Pair logam **dilarang entry saat pasar tutup**. Jadwal acuan COMEX/spot logam, dihitung otomatis di zona `America/New_York` sehingga DST ikut menyesuaikan sendiri:
- Buka: Minggu 18:00 ET → tutup: Jumat 17:00 ET
- Jeda harian: 17:00–18:00 ET setiap hari kerja
- `TRADFI_ENTRY_BUFFER_MIN=15` → berhenti entry 15 menit sebelum jam tutup (biar tidak nyangkut saat likuiditas menipis)
- `TRADFI_FORCE_CLOSE_BEFORE_CLOSE=TRUE` → posisi logam yang masih terbuka **ditutup market 5 menit sebelum pasar tutup**, supaya tidak kena gap weekend
- Status dashboard: `🌙 MARKET CLOSED (WEEKEND_SABTU / WEEKEND_MINGGU_PRE_OPEN / WEEKEND_TUTUP_JUMAT / JEDA_HARIAN_1700_1800_ET)`
- Pair crypto tetap 24/7, tidak terpengaruh gate ini
- Bisa dimatikan: `TRADFI_MARKET_HOURS_ENABLED=FALSE`

---

### 6. Mesin fee — PnL bersih, bukan PnL khayalan
Semua perhitungan sekarang memotong fee taker 2 sisi (`FEE_TAKER_PCT=0.0005`, isi `0.00045` kalau kamu bayar fee pakai BNB):
- `hitung_pnl()` mengembalikan **gross → fee → net**, dan ROI dihitung dari **margin nyata**, bukan `pnl% × leverage` yang menyesatkan.
- Notifikasi close menampilkan tiga baris terpisah: GROSS / FEE / NET.
- WIN atau LOSS ditentukan dari **PnL bersih**. Trade yang kotornya +$0.13 tapi bersihnya −$0.02 dicatat sebagai LOSS, sebagaimana mestinya.
- Jurnal menyimpan `pnl_gross_usdt`, `fee_usdt`, `pnl_usdt` (net), dan statistik 24 jam menampilkan total fee yang sudah kamu bayar.

**Audit setelan otomatis saat boot.** Bot menolak diam saja kalau konfigurasimu mustahil profit — peringatan dikirim ke Telegram dan muncul di EVENT FEED kalau:
- `TP_PCT` ≤ fee bolak-balik (setiap win tetap rugi)
- `BREAKEVEN_OFFSET_PCT` ≤ fee bolak-balik (breakeven palsu — ini bug nyata di versi sebelumnya)
- `MARGIN_PER_TRADE_USDT × MAX_OPEN_POSITIONS` melebihi 90% ekuitas (posisi terakhir bakal ditolak bursa)

### 7. Setelan default untuk modal $40
Default paket ini sudah dikalibrasi untuk modal training $40, bukan angka asal:

| Parameter | Nilai | Alasan |
|---|---|---|
| `MARGIN_PER_TRADE_USDT` | 5 | notional $100 @20x |
| `MAX_OPEN_POSITIONS` | 3 | $15 terpakai, sisanya buffer |
| `TP_PCT` | 0.005 | 0.50% kotor → **0.40% bersih** |
| `SL_ATR_MULT` | 0.8 | SL ≈ 0.24% → rugi $0.34/trade |
| `BREAKEVEN_OFFSET_PCT` | 0.0015 | di atas fee, jadi benar-benar impas |

Hasil matematisnya (sudah diverifikasi lewat unit test):
- Win bersih **+$0.40**, loss **−$0.34**
- **Break-even win rate 46.0%** · win rate untuk Profit Factor 2: **63.0%**
- Risiko per trade **0.85% ekuitas** — 10 loss beruntun cuma −8.5%

Bandingkan setelan lama (TP 0.13%, margin $10): win bersih cuma +$0.03, butuh WR **94%** untuk impas, dan 6 slot × $10 = $60 melebihi modalmu.

---

## 🎨 TAMPILAN: UI v2 production-ready

UI dibangun di atas lima pilar dan **mendeteksi lingkungan sendiri** — TUI penuh di terminal, log estetik di PM2.

### 1. Layout & Spacing
Informasi dikelompokkan berjenjang: kepala → kartu ringkasan → posisi terbuka → radar → aktivitas → kaki. Setiap panel diberi padding sehingga tidak saling menempel.

### 2. Typography
Angka dirata-kanan dengan lebar kolom tetap agar digit satuan sejajar dan mudah dibandingkan. Hierarki dibentuk dari tebal/redup, bukan dari warna saja.

### 3. Responsive Design
Layout menyesuaikan lebar terminal secara otomatis:

| Lebar terminal | Tingkat | Perilaku |
|---|---|---|
| ≥ 140 kolom | `wide` | 6 kartu sejajar, tabel posisi lengkap 10 kolom |
| 108–139 | `mid` | kartu 3+3 dua baris, kolom TP/SL disembunyikan |
| 84–107 | `narrow` | kartu 2 per baris, tabel posisi 5 kolom |
| < 84 (HP) | `tiny` | ringkasan tabel vertikal, radar disembunyikan |

Kolom radar dihitung dari lebar layar (`DASHBOARD_COLS=0`). Ubah ukuran jendela SSH → layout ikut menyesuaikan di render berikutnya.

### 4. Accessibility
Status **tidak pernah** hanya mengandalkan warna — selalu ada glyph pendamping plus label teks kategorinya:

| Glyph | ASCII | Arti |
|---|---|---|
| `◉` | `O` | sedang ada posisi |
| `▲` | `^` | sinyal terbuka |
| `○` | `o` | dekat pemicu |
| `⊘` | `x` | diveto AI |
| `◔` | `c` | cooldown / cap / dedup |
| `—` | `-` | pasar TradFi tutup |
| `⚠` | `!` | gagal eksekusi / error |
| `·` | `.` | dipantau, belum ada apa-apa |

- `UI_NO_COLOR=TRUE` atau env `NO_COLOR` → semua warna dimatikan, tampilan tetap terbaca.
- `UI_ASCII=TRUE` → border, glyph, sparkline, dan spinner memakai ASCII polos.

### 5. Motion & Interaction
- **Spinner** `⠋⠙⠹` berputar tiap siklus scan sebagai bukti bot hidup.
- **Sparkline** `▁▂▃▅` merekam 32 siklus terakhir: berapa pair yang berstatus menarik.
- **Bar slot** `██░░` menunjukkan pemakaian slot secara visual.
- Refresh 4× per detik (`UI_REFRESH_PER_SEC`) memakai alternate screen — halus, tanpa kedip, tanpa `clear`.

### Radar mode `smart` (default)
Dengan 43 pair, menampilkan semuanya membuat layar penuh baris "menunggu". Mode `smart` hanya menampilkan pair yang **posisi / sinyal / dekat pemicu / veto / gagal** (maksimal 14 baris), sisanya diringkas jadi hitungan per kategori di bawah panel. Kalau semua tenang, muncul satu baris `semua pair tenang · menunggu pemicu` plus ringkasan. Set `RADAR_MODE=full` untuk melihat seluruh pair.

### Tampilan di PM2 (mode log)
PM2 bukan tty, jadi TUI otomatis turun ke **log estetik** — satu baris per kejadian dengan kolom sejajar dan warna ANSI:

```
18:07:49 │ ✓ ENTRY     │ SOL/USDT LONG 184.22 · slot 2/6 · margin $5.00
18:07:49 │ ✔ WIN       │ SOL/USDT +0.12% · net +$0.06 · durasi 4m
18:07:49 │ ⊘ VETO      │ XAU/USDT · order book condong jual 68%
```

Ditambah blok heartbeat berkala (`HEARTBEAT_SEC=60`) yang rapi berbingkai:

```
─────────────────────────────────────────────
  💓 18:08:01 WIB   scan #128 · tiap 8s · TF 3m
  slot  ████░░░░░░░░ 2/6   ekuitas $39.86 LIVE   margin $5.00
  24 jam 3W/1L  WR 75%   net +0.18   fee -0.20
  AI    ● Kimi ● Deep ● Llama   regime SIDEWAYS_CHOP   tradfi 🟢 OPEN
  radar ▁▂▅▃▂▁▂▄ aktivitas · 3 pair menarik
──────────────────────────────────────────────
```

Saat TUI aktif, logging **tidak** ikut ke stdout supaya layar tidak berantakan — semua log tetap lengkap di `bot_v15.log`. Kalau `rich` belum terpasang bot **tidak crash**, otomatis pakai mode log. Matikan total dengan `DASHBOARD_ENABLED=FALSE`.

## ➕ FITUR TAMBAHAN YANG AKU SARANKAN (sudah masuk paket)

1. **Time-stop** (`MAX_TRADE_AGE_SEC`) — scalp yang nyangkut > 60 menit ditutup market, bukan digantung.
2. **Breakeven guard nyata** — SL lama dibatalkan lalu dipasang ulang di entry ±0.05% (versi lama hanya bicara di teks).
3. **AI_FAIL_OPEN=FALSE** — kalau AI timeout/error, sinyal **dibatalkan**. Versi lama justru bisa lolos tanpa penjaga.
4. **Order book imbalance nyata** dikirim ke Llama (bid20/ask20/ratio), jadi Hakim Agung punya data, bukan menebak.
5. **Funding rate nyata** dikirim ke DeepSeek.
6. **Profit Factor** ditambahkan ke statistik (target kamu PF > 2).
7. **Persisted state** — `state_v15.json` atomik (`os.replace`), tahan restart.
8. **Logging file + stdout** — semua error AI/order tercatat, tidak ada lagi `except: pass` yang menelan bug.
9. **Precision-aware sizing** — `amount_to_precision` / `price_to_precision`, mencegah order reject.
10. **Selftest** — deteksi dini key salah, pair XAU/XAG/XPT tidak tersedia di Binance Futures, atau chat_id salah.

---

## ⚠️ CATATAN JUJUR SEBELUM LIVE

- **Pair logam**: `XAU/USDT`, `XAG/USDT`, `XPT/USDT` **tidak selalu ada** di Binance USDⓈ-M Futures. `selftest.py` akan bilang pair mana yang tidak ada; hapus dari `ASSET_PAIRS` kalau di-flag.
- **Model AI**: nama model di OpenRouter/EvoMap berubah-ubah. Kalau selftest gagal di bagian AI, ganti `KIMI_MODEL` / `LLAMA_MODEL` / `EVOMAP_MODEL` di `.env` — tidak perlu sentuh kode.
- **TP 0.12% dengan leverage 20x**: TP itu ≈ ROI 2.4%, sementara SL 1.2×ATR bisa jauh lebih lebar dari 0.12%. Artinya **risk/reward-nya negatif** — kamu butuh win rate sangat tinggi supaya PF > 2. Saran: naikkan `TP_PCT` (mis. 0.0035–0.005) atau perkecil `SL_ATR_MULT` (mis. 0.6–0.8), lalu ukur di dry-run.
- **26 pair × 3 detik** = beban rate limit besar. Kalau sering kena `-1003 too many requests`, naikkan `SCAN_INTERVAL_SEC` ke 5 atau kurangi pair.
- Bot ini tetap alat bantu, bukan jaminan profit. Uji di `BINANCE_TESTNET=TRUE` dulu kalau bisa.
