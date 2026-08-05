#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V15.6 PRO GENIUS - SELF TEST
Cek satu per satu: ENV, Telegram, Binance (saldo + 43 pair), dan 3 AI agent
yang sekarang punya API key masing-masing.

    python3 selftest.py
"""
import asyncio
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()

OK = "\033[92m  OK  \033[0m"
BAD = "\033[91m FAIL \033[0m"
WRN = "\033[93m WARN \033[0m"
DIM = "\033[90m"
RST = "\033[0m"
CYA = "\033[96m"

SKOR = {"ok": 0, "fail": 0, "warn": 0}


def judul(teks):
    print(f"\n{CYA}\u250c\u2500\u2500 {teks} {'\u2500' * max(2, 58 - len(teks))}{RST}")


def baris(nama, status, detail=""):
    SKOR[status] += 1
    tag = {"ok": OK, "fail": BAD, "warn": WRN}[status]
    print(f"{CYA}\u2502{RST} [{tag}] {nama:<28} {DIM}{detail}{RST}")


def env(nama, default=""):
    return (os.getenv(nama) or default).strip()


def benar(nama, default="TRUE"):
    return env(nama, default).upper() in ("TRUE", "1", "YES", "Y", "ON")


# ==============================================================================
# 1. ENV
# ==============================================================================
def tes_env():
    judul("1. KONFIGURASI (.env)")
    dry = benar("DRY_RUN_MODE", "TRUE")
    baris("Mode", "ok" if dry else "warn",
          "DRY-RUN (aman)" if dry else "LIVE - UANG BENERAN!")

    wajib = ["TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"]
    if not dry:
        wajib += ["BINANCE_API_KEY", "BINANCE_API_SECRET"]
    kosong = [k for k in wajib if not env(k)]
    baris("Variabel wajib", "ok" if not kosong else "fail",
          "lengkap" if not kosong else f"kosong: {', '.join(kosong)}")

    kimi = env("OPENROUTER_API_KEY_KIMI") or env("OPENROUTER_API_KEY")
    llama = env("OPENROUTER_API_KEY_LLAMA") or env("OPENROUTER_API_KEY")
    evo = env("EVOMAP_API_KEY")
    baris("Key Kimi", "ok" if kimi else "warn", "ada" if kimi else "kosong")
    baris("Key Llama", "ok" if llama else "warn", "ada" if llama else "kosong")
    baris("Key EvoMap", "ok" if evo else "warn", "ada" if evo else "kosong")

    maxpos = env("MAX_OPEN_POSITIONS", "6")
    auto = benar("MARGIN_AUTO", "TRUE")
    pct = float(env("MARGIN_PCT_OF_EQUITY", "0.10") or 0.10)
    baris("Slot posisi", "ok", f"MAX_OPEN_POSITIONS={maxpos}")
    baris("Sizing", "ok",
          f"AUTO {pct * 100:.0f}% ekuitas live" if auto
          else f"MANUAL {env('MARGIN_PER_TRADE_USDT', '5')} USDT")
    baris("TP / SL", "ok",
          f"TP {float(env('TP_PCT', '0.005')) * 100:.2f}% kotor | SL {env('SL_ATR_MULT', '0.8')}x ATR")
    return dry


# ==============================================================================
# 2. TELEGRAM
# ==============================================================================
async def tes_telegram():
    judul("2. TELEGRAM")
    token, chat = env("TELEGRAM_TOKEN"), env("TELEGRAM_CHAT_ID")
    if not token or not chat:
        baris("Telegram", "fail", "token / chat id kosong")
        return
    import httpx
    base = "https://api.telegram.org/bot" + token
    async with httpx.AsyncClient(timeout=15) as cl:
        try:
            r = await cl.get(f"{base}/getMe")
            d = r.json()
            if d.get("ok"):
                baris("getMe", "ok", f"@{d['result'].get('username')}")
            else:
                baris("getMe", "fail", str(d)[:90])
                return
        except Exception as e:
            baris("getMe", "fail", str(e)[:90])
            return
        try:
            teks = (
                "\u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557\n"
                "\u2551   \u2705 *SELF TEST V15.6*   \u2551\n"
                "\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255d\n"
                "Jalur notifikasi hidup. Bot siap."
            )
            r = await cl.post(f"{base}/sendMessage",
                              json={"chat_id": chat, "text": teks,
                                    "parse_mode": "Markdown"})
            d = r.json()
            baris("sendMessage", "ok" if d.get("ok") else "fail",
                  f"message_id {d['result']['message_id']}" if d.get("ok") else str(d)[:90])
        except Exception as e:
            baris("sendMessage", "fail", str(e)[:90])


# ==============================================================================
# 3. BINANCE
# ==============================================================================
async def tes_binance(dry):
    judul("3. BINANCE FUTURES")
    try:
        import ccxt.pro as ccxtpro
    except Exception as e:
        baris("import ccxt", "fail", str(e)[:90])
        return

    ex = ccxtpro.binance({
        "apiKey": env("BINANCE_API_KEY"),
        "secret": env("BINANCE_API_SECRET"),
        "enableRateLimit": True,
        "options": {"defaultType": "future"},
    })
    if benar("BINANCE_TESTNET", "FALSE"):
        ex.set_sandbox_mode(True)

    try:
        markets = await ex.load_markets()
        baris("load_markets", "ok", f"{len(markets)} market")
    except Exception as e:
        baris("load_markets", "fail", str(e)[:90])
        await ex.close()
        return

    # --- resolver simbol: sama persis dengan logika bot ---
    def resolve(sym):
        for kandidat in (sym, f"{sym}:USDT", sym.replace("/", ""), f"{sym}:USDC"):
            if kandidat in markets:
                return kandidat
        base, _, quote = sym.partition("/")
        for nama, m in markets.items():
            if (m.get("base") == base and m.get("quote") == quote.split(":")[0]
                    and m.get("swap") and m.get("active")):
                return nama
        return None

    logam = [s.strip() for s in env("TRADFI_PAIRS", "XAU/USDT,XAG/USDT,XPT/USDT").split(",") if s.strip()]
    for sym in logam:
        nyata = resolve(sym)
        if not nyata:
            baris(f"pair {sym}", "warn", "tidak ditemukan di bursa ini")
        elif nyata != sym:
            baris(f"pair {sym}", "ok", f"dipetakan ke {nyata}")
        else:
            baris(f"pair {sym}", "ok", "tersedia")

    try:
        t = await ex.fetch_ticker("BTC/USDT")
        baris("ticker BTC/USDT", "ok", f"last={t['last']}")
    except Exception as e:
        baris("ticker BTC/USDT", "fail", str(e)[:90])

    ekuitas = float(env("EQUITY_FALLBACK_USDT", "40") or 40)
    sumber = "fallback .env"
    if env("BINANCE_API_KEY"):
        try:
            bal = await ex.fetch_balance()
            u = bal.get("USDT", {})
            ekuitas = float(u.get("total") or 0) or ekuitas
            sumber = "SALDO LIVE"
            baris("saldo USDT", "ok", f"total {ekuitas:.4f} | free {float(u.get('free') or 0):.4f}")
        except Exception as e:
            baris("saldo USDT", "fail", str(e)[:90])
    else:
        baris("saldo USDT", "warn", "API key kosong, pakai EQUITY_FALLBACK_USDT")

    # --- simulasi sizing dari ekuitas ---
    if benar("MARGIN_AUTO", "TRUE"):
        pct = float(env("MARGIN_PCT_OF_EQUITY", "0.10") or 0.10)
        mmin = float(env("MARGIN_MIN_USDT", "5") or 5)
        mmax = float(env("MARGIN_MAX_USDT", "0") or 0)
        margin = max(mmin, ekuitas * pct)
        if mmax > 0:
            margin = min(margin, mmax)
    else:
        margin = float(env("MARGIN_PER_TRADE_USDT", "5") or 5)
    lev = float(env("LEVERAGE", "20") or 20)
    slot = int(env("MAX_OPEN_POSITIONS", "6") or 6)
    baris("margin per trade", "ok",
          f"{margin:.2f} USDT ({sumber}) -> notional {margin * lev:.2f}")
    pakai = margin * slot
    aman = ekuitas * float(env("EQUITY_SAFETY_PCT", "0.90") or 0.90)
    baris(f"{slot} slot penuh", "ok" if pakai <= aman else "warn",
          f"butuh {pakai:.2f} USDT dari ekuitas {ekuitas:.2f}"
          + ("" if pakai <= aman else " - TERLALU BESAR, turunkan MARGIN_PCT_OF_EQUITY"))
    await ex.close()


# ==============================================================================
# 4. TIGA AI AGENT (key masing-masing)
# ==============================================================================
async def tes_ai():
    judul("4. ALIANSI 3 AI AGENT")
    if not benar("AI_LAYER_ENABLED", "TRUE"):
        baris("AI layer", "warn", "dimatikan di .env")
        return
    try:
        from openai import AsyncOpenAI
    except Exception as e:
        baris("import openai", "fail", f"{e} -> pip install openai")
        return

    timeout = float(env("AI_TIMEOUT_SEC", "20") or 20)
    prompt = 'Jawab HANYA JSON: {"ok": true}'

    async def tanya(nama, key, base, model, extra=None):
        if not key:
            baris(nama, "warn", "API key kosong, agen ini akan dilewati bot")
            return
        cl = AsyncOpenAI(api_key=key, base_url=base, timeout=timeout)
        try:
            res = await cl.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=int(env("KIMI_MAX_TOKENS", "700") or 700) if "Kimi" in nama else 120,
                extra_body=extra or {},
            )
            isi = (res.choices[0].message.content or "").strip()
            if isi.lstrip().startswith("<"):
                baris(nama, "fail", "balasan HTML - base_url salah")
                return
            baris(nama, "ok", f"{model} -> {isi[:48].replace(chr(10), ' ')}")
        except Exception as e:
            baris(nama, "fail", str(e)[:110])
        finally:
            try:
                await cl.close()
            except Exception:
                pass

    orb = env("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

    # AGEN 1 - Kimi K2.6 + reasoning
    kimi_key = env("OPENROUTER_API_KEY_KIMI") or env("OPENROUTER_API_KEY")
    kimi_extra = {"reasoning": {"enabled": True}} if benar("KIMI_REASONING", "TRUE") else {}
    await tanya("Agen 1 Kimi K2.6", kimi_key, orb,
                env("KIMI_MODEL", "moonshotai/kimi-k2.6"), kimi_extra)

    # AGEN 2 - DeepSeek V4 Flash via EvoMap
    evo = env("EVOMAP_API_KEY")
    if evo and not evo.startswith("sk-evomap-"):
        evo = f"sk-evomap-{evo}"
    await tanya("Agen 2 DeepSeek EvoMap", evo,
                env("EVOMAP_BASE_URL", "https://api.evomap.ai/v1"),
                env("EVOMAP_MODEL", "evomap-deepseek-v4-flash"))

    # AGEN 3 - Llama 3.3 70B lewat provider Groq
    llama_key = env("OPENROUTER_API_KEY_LLAMA") or env("OPENROUTER_API_KEY")
    prov = env("LLAMA_PROVIDER", "Groq")
    extra = {"provider": {"order": [prov],
                          "allow_fallbacks": benar("LLAMA_ALLOW_FALLBACK", "TRUE")}} if prov else {}
    await tanya(f"Agen 3 Llama ({prov or 'auto'})", llama_key, orb,
                env("LLAMA_MODEL", "meta-llama/llama-3.3-70b-instruct"), extra)


# ==============================================================================
async def main():
    print(f"\n{CYA}\u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557")
    print("\u2551          V15.6 PRO GENIUS - PEMERIKSAAN SISTEM            \u2551")
    print(f"\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255d{RST}")
    dry = tes_env()
    await tes_telegram()
    await tes_binance(dry)
    await tes_ai()

    judul("RINGKASAN")
    print(f"{CYA}\u2502{RST}  \u2705 lolos {SKOR['ok']}   \u26a0\ufe0f  perhatian {SKOR['warn']}   \u274c gagal {SKOR['fail']}")
    if SKOR["fail"] == 0:
        print(f"{CYA}\u2514\u2500{RST} Semua jalur inti sehat. Bot boleh dinyalakan.\n")
    else:
        print(f"{CYA}\u2514\u2500{RST} Ada yang gagal. Perbaiki dulu sebelum menyalakan bot.\n")
    sys.exit(1 if SKOR["fail"] else 0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\ndibatalkan")
