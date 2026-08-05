#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 BINANCE FUTURES PRO SCALPER V15.4 "GENIUS DIRECTIONAL"
 Industrial Hybrid Multi-Agentic Quant Trading Engine
================================================================================
 Aliansi 3 AI Agent:
   AGENT 1  Kimi K2.6            -> Master Macro Regime (kompas, 15 menit)
   AGENT 2  DeepSeek V4 Flash    -> Sentiment & Funding Squeeze Filter
   AGENT 3  Llama 3.3 70B        -> Supreme Veto Judge (Order Book)

 V15.3 BUG-FIX RELEASE (4 bug yang kamu laporkan):
   BUG 1  Tidak ada batas re-entry per pair   -> PositionRegistry + cooldown
   BUG 2  Tidak ada cap posisi live           -> MAX_OPEN_POSITIONS (default 6)
   BUG 3  Notif Telegram tidak ada WIN/LOSS   -> TradeJournal + watcher TP/SL
   BUG 4  Notif terkirim tapi tidak ter-entry -> notif dikirim SETELAH order fill
                                                 (execute-then-notify) + retry
 Bonus fix teknis:
   * URL Telegram: https://api.telegram.org/bot<TOKEN>/sendMessage
   * OpenRouter base_url: https://openrouter.ai/api/v1
   * EvoMap endpoint: /v1/chat/completions
   * res.choices[0].message.content (bukan res.choices.message)
   * if __name__ == "__main__"  (bukan `if name == "main"`)
   * Semua except polos diganti except Exception + logging
================================================================================
"""

import os
import sys
import json
import hmac
import hashlib
import time
import math
import signal
import asyncio
import logging
import traceback
from collections import deque
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import httpx
import ccxt.pro as ccxtpro
from dotenv import load_dotenv
from godmode_v2_engine import (
    FeatureEngine, GodModeScorer, ConsensusEngine, AITradeJournal,
    FEATURE_ENGINE, GM_SCORER, CONSENSUS, AI_JOURNAL,
    run_athena, run_hermes, run_aegis, run_godmode_v2_pipeline,
    extract_json_robust, safe_float, safe_int, dynamic_size,
    FEATURE_WEIGHTS, TIER_THRESHOLDS,
    REJECTION_TRACKER,
    pre_evaluate_pair, _ai_cache_get, _ai_cache_stats
)

try:
    from openai import OpenAI
except Exception:  # openai opsional (AI layer bisa dimatikan)
    OpenAI = None

load_dotenv()

# ==============================================================================
# 0. UTIL ENV
# ==============================================================================


def env_str(key: str, default: str = "") -> str:
    raw = os.environ.get(key, default)
    if raw is None:
        return ""
    return raw.strip().strip('"').strip("'").strip()


def env_bool(key: str, default: bool = True) -> bool:
    return env_str(key, "TRUE" if default else "FALSE").upper() in ("TRUE", "1", "YES", "ON")


def env_float(key: str, default: float) -> float:
    try:
        return float(env_str(key, str(default)))
    except Exception:
        return default


def env_int(key: str, default: int) -> int:
    try:
        return int(float(env_str(key, str(default))))
    except Exception:
        return default


# ==============================================================================
# 1. KONFIGURASI
# ==============================================================================

WIB = timezone(timedelta(hours=7))

# --- 40 pair crypto (Binance USDⓈ-M Futures) ---
CRYPTO_PAIRS = [
    'BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'XRP/USDT',
    'DOGE/USDT', 'ADA/USDT', 'AVAX/USDT', 'LINK/USDT', 'TRX/USDT',
    'GRAM/USDT', 'DOT/USDT', 'LTC/USDT', 'BCH/USDT', 'NEAR/USDT',
    'APT/USDT', 'ARB/USDT', 'OP/USDT', 'ATOM/USDT', 'FIL/USDT',
    'INJ/USDT', 'SUI/USDT', 'SEI/USDT', 'TIA/USDT', 'WLD/USDT',
    'ORDI/USDT', 'ENA/USDT', 'WIF/USDT', '1000PEPE/USDT', 'ONDO/USDT',
    'HYPE/USDT', 'JUP/USDT', 'RUNE/USDT', 'AAVE/USDT', 'UNI/USDT',
    'FET/USDT', 'GALA/USDT', 'CRV/USDT', 'LDO/USDT', '1000SHIB/USDT',
]

# --- 3 pair logam TradFi ---
TRADFI_DEFAULT_PAIRS = ['XAU/USDT', 'XAG/USDT', 'XPT/USDT']

ASSET_PAIRS = CRYPTO_PAIRS + TRADFI_DEFAULT_PAIRS
_custom_pairs = env_str("ASSET_PAIRS", "")
if _custom_pairs:
    ASSET_PAIRS = [p.strip().upper() for p in _custom_pairs.split(",") if p.strip()]

# Kalau pair ditulis beda nama di bursa (mis. XAU/USDT:USDT), bot mencari
# padanannya otomatis saat boot. FALSE = pair yang tidak ketemu tetap dicoba.
AUTO_RESOLVE_PAIRS = env_bool("AUTO_RESOLVE_PAIRS", True)

# --- Mode & eksekusi ---
DRY_RUN_MODE = env_bool("DRY_RUN_MODE", True)
TESTNET = env_bool("BINANCE_TESTNET", False)
LEVERAGE = env_int("LEVERAGE", 20)
# Margin per trade. MARGIN_AUTO=TRUE -> ikut % saldo LIVE (bukan patokan tetap).
MARGIN_AUTO = env_bool("MARGIN_AUTO", True)
MARGIN_PCT_OF_EQUITY = env_float("MARGIN_PCT_OF_EQUITY", 0.10)   # 10% ekuitas / trade
MARGIN_MIN_USDT = env_float("MARGIN_MIN_USDT", 5.0)              # batas bawah bursa
MARGIN_MAX_USDT = env_float("MARGIN_MAX_USDT", 0.0)              # 0 = tanpa batas atas
# --- NOTIONAL CAP (V15.4) ---
NOTIONAL_LIMIT_USDT = env_float("NOTIONAL_LIMIT_USD", 35.0)
MARGIN_USDT = env_float("MARGIN_PER_TRADE_USDT", 5.0)            # dipakai kalau MARGIN_AUTO=FALSE
SCAN_INTERVAL = env_float("SCAN_INTERVAL_SEC", 5.0)   # 43 pair -> beri nafas rate limit
TIMEFRAME = env_str("TIMEFRAME", "5m")

# --- BUG FIX 1 & 2: manajemen risiko posisi ---
MAX_OPEN_POSITIONS = env_int("MAX_OPEN_POSITIONS", 6)          # cap global slot posisi
MAX_POSITION_PER_PAIR = env_int("MAX_POSITION_PER_PAIR", 1)     # anti double entry
REENTRY_COOLDOWN_SEC = env_int("REENTRY_COOLDOWN_SEC", 900)     # jeda setelah close
MAX_REENTRY_PER_PAIR_DAY = env_int("MAX_REENTRY_PER_PAIR_DAY", 0)   # 0 / negatif = UNLIMITED
SIGNAL_DEDUP_SEC = env_int("SIGNAL_DEDUP_SEC", 60)              # anti spam sinyal
REENTRY_LIMIT_TXT = "\u221e" if MAX_REENTRY_PER_PAIR_DAY <= 0 else str(MAX_REENTRY_PER_PAIR_DAY)

# --- Sensor / radar ---
ATR_MULTIPLIER = env_float("ATR_MULTIPLIER", 0.8)
STOCH_OB = env_float("STOCH_OVERBOUGHT", 70.0)
STOCH_OS = env_float("STOCH_OVERSOLD", 30.0)
STOCH_PERIOD = env_int("STOCH_PERIOD", 5)
STOCH_SMOOTH_K = env_int("STOCH_SMOOTH_K", 3)
STOCH_SMOOTH_D = env_int("STOCH_SMOOTH_D", 3)
ATR_PERIOD = env_int("ATR_PERIOD", 14)

# --- TP / SL / Breakeven ---
TP_PCT = env_float("TP_PCT", 0.005)           # 0.50% KOTOR (bersih ~0.40% setelah fee)
SL_ATR_MULT = env_float("SL_ATR_MULT", 0.8)   # SL lebih ketat -> RR sehat
BREAKEVEN_TRIGGER_PCT = env_float("BREAKEVEN_TRIGGER_PCT", 0.0025)
BREAKEVEN_OFFSET_PCT = env_float("BREAKEVEN_OFFSET_PCT", 0.0015)  # WAJIB > fee bolak-balik
MAX_TRADE_AGE_SEC = env_int("MAX_TRADE_AGE_SEC", 3600)

# --- FEE & EKUITAS (dipakai untuk PnL bersih + peringatan setelan merugi) ---
FEE_TAKER_PCT = env_float("FEE_TAKER_PCT", 0.0005)   # 0.05% per sisi; 0.00045 kalau bayar fee pakai BNB
FEE_ROUNDTRIP_PCT = FEE_TAKER_PCT * 2                # biaya buka + tutup
TP_NET_PCT = TP_PCT - FEE_ROUNDTRIP_PCT              # profit riil per win
# Ekuitas dibaca LIVE dari Binance. Angka di bawah cuma cadangan kalau saldo
# belum bisa dibaca (mis. dry-run tanpa API key).
EQUITY_FALLBACK_USDT = env_float("EQUITY_FALLBACK_USDT", 40.0)
EQUITY_REFRESH_SEC = env_int("EQUITY_REFRESH_SEC", 60)
EQUITY_SAFETY_PCT = env_float("EQUITY_SAFETY_PCT", 0.90)   # pakai maks 90% ekuitas

# --- AI layer ---
AI_LAYER_ENABLED = env_bool("AI_LAYER_ENABLED", True)
AI_FAIL_OPEN = env_bool("AI_FAIL_OPEN", False)   # False = kalau AI error -> batal (aman)
OPENROUTER_BASE = env_str("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

# --- AGEN 1: KIMI K2.6 (OpenRouter, reasoning aktif) ---
KIMI_MODEL = env_str("KIMI_MODEL", "llama-3.1-8b-instant")
KIMI_API_KEY = env_str("OPENROUTER_API_KEY_KIMI") or env_str("OPENROUTER_API_KEY")
KIMI_REASONING = env_bool("KIMI_REASONING", False)
KIMI_MAX_TOKENS = env_int("KIMI_MAX_TOKENS", 300)   # reasoning butuh ruang lebih

# --- AGEN 2: DEEPSEEK V4 FLASH (EvoMap, endpoint & key sendiri) ---
DEEPSEEK_MODEL = env_str("DEEPSEEK_MODEL", "deepseek/deepseek-v4-flash")
EVOMAP_BASE = env_str("EVOMAP_BASE_URL", "https://api.evomap.ai/v1")

# --- AGEN 3: LLAMA 3.3 70B (OpenRouter, provider dikunci ke Groq) ---
LLAMA_MODEL = env_str("LLAMA_MODEL", "meta-llama/llama-3.3-70b-instruct")
LLAMA_API_KEY = env_str("OPENROUTER_API_KEY_LLAMA") or env_str("OPENROUTER_API_KEY")
LLAMA_PROVIDER = env_str("LLAMA_PROVIDER", "Groq")          # kosongkan = bebas
LLAMA_ALLOW_FALLBACK = env_bool("LLAMA_ALLOW_FALLBACK", True)
# Sebagian model (mis. kimi-k2 lewat provider Novita) menolak response_format
# json_object. TRUE = coba dulu, otomatis mundur ke prompt-only kalau ditolak.
AI_JSON_MODE = env_bool("AI_JSON_MODE", True)
# --- GODMODE DIRECTIONAL (V15.4) ---
GODMODE_ENABLED = env_bool("GODMODE_ENABLED", True)
GODMODE_MIN_SCORE = env_int("GODMODE_MIN_SCORE", 60)
GODMODE_DIRECTIONAL_BOOST = env_int("GODMODE_DIRECTIONAL_BOOST", 8)
GODMODE_COUNTER_TREND_PENALTY = env_int("GODMODE_COUNTER_TREND_PENALTY", 20)
GODMODE_EXHAUSTION_BONUS = env_int("GODMODE_EXHAUSTION_BONUS", 5)
# Dipakai kalau EvoMap mati / base URL salah, biar agen 2 tetap hidup.
EVOMAP_FALLBACK_MODEL = env_str("EVOMAP_FALLBACK_MODEL", "deepseek/deepseek-chat")
KIMI_INTERVAL_SEC = env_int("KIMI_INTERVAL_SEC", 300)
AI_TIMEOUT = env_float("AI_TIMEOUT_SEC", 12.0)

# --- Telegram ---
TELEGRAM_TOKEN = env_str("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = env_str("TELEGRAM_CHAT_ID")
TELEGRAM_API = "https://api.telegram.org"
BRIEFING_HOUR = env_int("BRIEFING_HOUR_WIB", 7)

# --- Dashboard / log ---
DASHBOARD_ENABLED = env_bool("DASHBOARD_ENABLED", True)
DASHBOARD_ROWS = env_int("DASHBOARD_ROWS", 26)
DASHBOARD_COLS = env_int("DASHBOARD_COLS", 3)      # radar 26 pair dibagi 3 kolom
HEARTBEAT_SEC = env_int("HEARTBEAT_SEC", 60)       # ringkasan berkala saat mode log (PM2)
STATE_FILE = env_str("STATE_FILE", "state_v15.json")
LOG_FILE = env_str("LOG_FILE", "bot_v15.log")

# --- Deteksi mode tampilan: TUI rich (terminal) vs log ringkas (PM2 / file) ---
try:
    from rich import box
    from rich.align import Align
    from rich.columns import Columns
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    RICH_OK = True
except Exception:      # rich opsional -> bot tetap jalan tanpa TUI
    RICH_OK = False

IS_TTY = bool(getattr(sys.stdout, "isatty", lambda: False)())
TUI_MODE = DASHBOARD_ENABLED and IS_TTY and RICH_OK
CONSOLE = Console() if RICH_OK else None

# Saat TUI aktif, log TIDAK boleh ikut ke stdout (biar tidak merusak layar)
_handlers = [logging.FileHandler(LOG_FILE, encoding="utf-8")]
if not TUI_MODE:
    _handlers.append(logging.StreamHandler(sys.stdout))
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    handlers=_handlers,
)
log = logging.getLogger("V15")
if DASHBOARD_ENABLED and IS_TTY and not RICH_OK:
    log.warning("Library 'rich' belum terpasang -> pakai mode log ringkas. Jalankan: pip install rich")

# V16: ATHENA cache
ATHENA_CACHE = {"market_regime": "CHOPPY", "bias": "NEUTRAL", "confidence": 30}
ATHENA_LAST_UPDATE = 0

MARKET_REGIME_GLOBAL = {
    "market_regime": "SIDEWAYS_CHOP",
    "recommended_tactic": "MEAN_REVERSION_ONLY",
    "rationale": "-",
    "last_updated": "INITIALIZING",
}

# ==============================================================================
# 2. EXCHANGE & AI CLIENTS
# ==============================================================================

EXCHANGE = ccxtpro.binance({
    "apiKey": env_str("BINANCE_API_KEY"),
    "secret": env_str("BINANCE_API_SECRET"),
    "enableRateLimit": True,
    "options": {"defaultType": "future"},
})
if TESTNET:
    EXCHANGE.set_sandbox_mode(True)

def evomap_key() -> str:
    """EvoMap minta format sk-evomap-<KEY>. Prefix ditambah otomatis."""
    key = env_str("EVOMAP_API_KEY")
    if not key:
        return ""
    return key if key.startswith("sk-evomap-") else f"sk-evomap-{key}"


def _buat_klien(api_key: str, base_url: str):
    if not (AI_LAYER_ENABLED and OpenAI is not None and api_key):
        return None
    try:
        return OpenAI(api_key=api_key, base_url=base_url, timeout=AI_TIMEOUT)
    except Exception as exc:      # noqa: BLE001
        print(f"[AI] gagal membuat klien {base_url}: {exc}", flush=True)
        return None


# Tiga agen = tiga klien berdiri sendiri, boleh beda API key & beda endpoint.
CLIENT_KIMI = _buat_klien(env_str("GROQ_API_KEY", ""), "https://api.groq.com/openai/v1")
CLIENT_LLAMA = _buat_klien(LLAMA_API_KEY, OPENROUTER_BASE)
DEEPSEEK_API_KEY = env_str("OPENROUTER_API_KEY_DEEPSEEK") or env_str("OPENROUTER_API_KEY")
CLIENT_EVOMAP = _buat_klien(DEEPSEEK_API_KEY, OPENROUTER_BASE)

# Dipakai untuk fallback DeepSeek lewat OpenRouter.
client_or = CLIENT_KIMI or CLIENT_LLAMA

AI_STATUS = {
    "kimi": "READY" if CLIENT_KIMI else "NO KEY",
    "deepseek": "READY" if CLIENT_EVOMAP else "NO KEY",
    "llama": "READY" if CLIENT_LLAMA else "NO KEY",
}


# ==============================================================================
# 3. TELEGRAM (dengan retry, anti silent-fail)
# ==============================================================================


async def tg_send(text: str, retries: int = 3) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram tidak dikonfigurasi, pesan dilewati.")
        return False
    url = f"{TELEGRAM_API}/bot{TELEGRAM_TOKEN}/sendMessage"

    # Coba 1: Kirim dengan Markdown
    md_text = text.replace("[", "(").replace("]", ")")
    payload_md = {"chat_id": TELEGRAM_CHAT_ID, "text": md_text,
                  "parse_mode": "Markdown", "disable_web_page_preview": True}
    for attempt in range(1, retries + 1):
        try:
            async with httpx.AsyncClient() as client:
                res = await client.post(url, json=payload_md, timeout=10.0)
            if res.status_code == 200:
                return True
        except Exception:
            pass

    # Coba 2: Kirim plain text (fallback)
    plain = text.replace("*", "").replace("_", " ").replace("`", "")
    plain = plain.replace("[", "(").replace("]", ")")
    payload_plain = {"chat_id": TELEGRAM_CHAT_ID, "text": plain,
                     "disable_web_page_preview": True}
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(url, json=payload_plain, timeout=10.0)
        if res.status_code == 200:
            return True
    except Exception as exc:
        log.error("TG plain error: %s", exc)
    return False


# ==============================================================================
# 4. TRADE JOURNAL  (BUG FIX 3: statistik WIN / LOSS)
# ==============================================================================


class TradeJournal:
    def __init__(self, path: str):
        self.path = path
        self.trades = deque(maxlen=2000)
        self.reentry_count = {}
        self.reentry_day = datetime.now(WIB).strftime("%Y-%m-%d")
        self._load()

    def _load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                self.trades = deque(data.get("trades", []), maxlen=2000)
                self.reentry_count = data.get("reentry_count", {})
                self.reentry_day = data.get("reentry_day", self.reentry_day)
        except Exception as exc:
            log.error("Gagal load state: %s", exc)

    def save(self):
        try:
            tmp = f"{self.path}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({
                    "trades": list(self.trades),
                    "reentry_count": self.reentry_count,
                    "reentry_day": self.reentry_day,
                }, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except Exception as exc:
            log.error("Gagal simpan state: %s", exc)

    # --- re-entry counter harian (BUG FIX 1) ---
    def _roll_day(self):
        today = datetime.now(WIB).strftime("%Y-%m-%d")
        if today != self.reentry_day:
            self.reentry_day = today
            self.reentry_count = {}

    def reentries_today(self, symbol: str) -> int:
        self._roll_day()
        return int(self.reentry_count.get(symbol, 0))

    def bump_reentry(self, symbol: str):
        self._roll_day()
        self.reentry_count[symbol] = self.reentries_today(symbol) + 1
        self.save()

    # --- pencatatan trade ---
    def record_close(self, trade: dict):
        self.trades.append(trade)
        self.save()

    def stats(self, hours: int = 24) -> dict:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        rows = []
        for t in self.trades:
            try:
                if datetime.fromisoformat(t["closed_at"]) >= cutoff:
                    rows.append(t)
            except Exception:
                continue
        wins = [t for t in rows if t.get("pnl_usdt", 0) > 0]
        losses = [t for t in rows if t.get("pnl_usdt", 0) <= 0]
        pnl = sum(t.get("pnl_usdt", 0.0) for t in rows)
        fee_total = sum(t.get("fee_usdt", 0.0) for t in rows)
        pnl_gross = sum(t.get("pnl_gross_usdt", t.get("pnl_usdt", 0.0)) for t in rows)
        gross_win = sum(t["pnl_usdt"] for t in wins)
        gross_loss = abs(sum(t["pnl_usdt"] for t in losses))
        best = max(rows, key=lambda t: t.get("pnl_pct", 0), default=None)
        worst = min(rows, key=lambda t: t.get("pnl_pct", 0), default=None)
        return {
            "total": len(rows),
            "win": len(wins),
            "loss": len(losses),
            "win_rate": (len(wins) / len(rows) * 100.0) if rows else 0.0,
            "pnl_usdt": pnl,
            "fee_usdt": fee_total,
            "pnl_gross_usdt": pnl_gross,
            "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0),
            "best": best,
            "worst": worst,
        }


JOURNAL = TradeJournal(STATE_FILE)


# ==============================================================================
# 5. POSITION REGISTRY  (BUG FIX 1 + 2: lock per pair & cap global)
# ==============================================================================


class PositionRegistry:
    """Sumber kebenaran tunggal untuk posisi terbuka + lock & cooldown."""

    def __init__(self):
        self.open = {}        # symbol -> dict posisi
        self.pending = set()  # symbol yang sedang diproses (anti race async)
        self.cooldown = {}    # symbol -> epoch boleh entry lagi
        self.last_signal = {}  # symbol -> epoch sinyal terakhir (dedup)
        self.lock = asyncio.Lock()

    # ---------- gate ----------
    def slots_used(self) -> int:
        return len(self.open) + len(self.pending)

    def slots_free(self) -> int:
        return max(0, MAX_OPEN_POSITIONS - self.slots_used())

    def gate_reason(self, symbol: str) -> str | None:
        """Return None kalau boleh entry, atau alasan penolakan."""
        now = datetime.now(timezone.utc).timestamp()
        if symbol in self.pending:
            return "PENDING_EXEC"
        if symbol in self.open:
            return "IN_POSITION"
        if len([s for s in self.open if s == symbol]) >= MAX_POSITION_PER_PAIR:
            return "PAIR_CAP"
        cd = self.cooldown.get(symbol, 0)
        if now < cd:
            return f"COOLDOWN_{int(cd - now)}s"
        if MAX_REENTRY_PER_PAIR_DAY > 0 and JOURNAL.reentries_today(symbol) >= MAX_REENTRY_PER_PAIR_DAY:
            return "REENTRY_LIMIT_DAY"   # dilewati kalau MAX_REENTRY_PER_PAIR_DAY = 0 (unlimited)
        if self.slots_free() <= 0:
            return "OVERFLOW_MAX_POS"
        if now - self.last_signal.get(symbol, 0) < SIGNAL_DEDUP_SEC:
            return "SIGNAL_DEDUP"
        return None

    def mark_signal(self, symbol: str):
        self.last_signal[symbol] = datetime.now(timezone.utc).timestamp()

    # ---------- lifecycle ----------
    async def reserve(self, symbol: str) -> bool:
        """Atomic gate check + reserve. No gap between check and lock."""
        async with self.lock:
            # Re-check inside lock to prevent race condition
            if self.gate_reason(symbol) is not None:
                return False
            self.pending.add(symbol)
            log.debug("REGISTRY.reserve %s OK (open=%d pending=%d)",
                      symbol, len(self.open), len(self.pending))
            return True

    async def release_pending(self, symbol: str):
        async with self.lock:
            self.pending.discard(symbol)

    async def commit(self, symbol: str, position: dict):
        async with self.lock:
            self.pending.discard(symbol)
            self.open[symbol] = position
        JOURNAL.bump_reentry(symbol)

    async def close(self, symbol: str):
        async with self.lock:
            self.open.pop(symbol, None)
            self.pending.discard(symbol)
            self.cooldown[symbol] = datetime.now(timezone.utc).timestamp() + REENTRY_COOLDOWN_SEC


REGISTRY = PositionRegistry()

def _safe_opened_at(pos):
    """Parse opened_at safely - handle both ISO string and timestamp float."""
    val = pos.get("opened_at")
    if val is None:
        return datetime.now(timezone.utc)
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val)
        except Exception:
            return datetime.now(timezone.utc)
    if isinstance(val, (int, float)):
        return datetime.fromtimestamp(val, tz=timezone.utc)
    return datetime.now(timezone.utc)



# ==============================================================================
# 5B. EKUITAS LIVE & MARGIN DINAMIS (tidak lagi patokan $40)
# ==============================================================================

EQUITY = {
    "total": 0.0,        # saldo wallet + unrealized
    "free": 0.0,         # margin yang masih bebas
    "sumber": "INIT",    # LIVE / PAPER / FALLBACK
    "updated": "-",
}


def equity_now() -> float:
    """Ekuitas terpakai untuk semua perhitungan risiko."""
    nilai = float(EQUITY.get("total") or 0.0)
    return nilai if nilai > 0 else EQUITY_FALLBACK_USDT


def margin_now() -> float:
    """Margin per trade. Ikut saldo riil kalau MARGIN_AUTO aktif."""
    if not MARGIN_AUTO:
        return MARGIN_USDT
    nilai = equity_now() * MARGIN_PCT_OF_EQUITY
    if MARGIN_MAX_USDT > 0:
        nilai = min(nilai, MARGIN_MAX_USDT)
    return max(MARGIN_MIN_USDT, round(nilai, 2))


def notional_now() -> float:
    return margin_now() * LEVERAGE


# [V15.4] margin_txt removed


async def refresh_equity(diam: bool = True) -> float:
    """Baca saldo USDT nyata dari Binance. Dry-run tanpa key -> ekuitas paper."""
    punya_key = bool(env_str("BINANCE_API_KEY"))
    if punya_key:
        try:
            bal = await EXCHANGE.fetch_balance()
            usdt = bal.get("USDT") or {}
            total = float(usdt.get("total") or 0.0)
            free = float(usdt.get("free") or total)
            if total > 0:
                EQUITY.update({"total": total, "free": free, "sumber": "LIVE",
                               "updated": datetime.now(WIB).strftime("%H:%M:%S")})
                return total
        except Exception as exc:
            if not diam:
                log.warning("Baca saldo gagal: %s", exc)
    # Paper: modal awal + akumulasi PnL bersih yang sudah tercatat.
    realized = 0.0
    try:
        realized = float(JOURNAL.stats(24 * 3650).get("pnl_usdt") or 0.0)
    except Exception:
        realized = 0.0
    total = max(EQUITY_FALLBACK_USDT + realized, 0.0)
    EQUITY.update({"total": total, "free": total,
                   "sumber": "PAPER" if DRY_RUN_MODE else "FALLBACK",
                   "updated": datetime.now(WIB).strftime("%H:%M:%S")})
    return total


async def equity_worker():
    """Segarkan saldo berkala supaya sizing selalu ikut dana riil."""
    while True:
        try:
            await refresh_equity()
        except Exception as exc:
            log.warning("equity_worker: %s", exc)
        await asyncio.sleep(max(15, EQUITY_REFRESH_SEC))


# ==============================================================================
# 6. INDIKATOR LOKAL
# ==============================================================================


def hitung_stoch_rsi(df: pd.DataFrame, period=STOCH_PERIOD, smooth_k=STOCH_SMOOTH_K,
                     smooth_d=STOCH_SMOOTH_D):
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=period).mean()
    rs = gain / (loss + 1e-9)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    rsi_min = rsi.rolling(window=period).min()
    rsi_max = rsi.rolling(window=period).max()
    stoch = (rsi - rsi_min) / (rsi_max - rsi_min + 1e-9) * 100.0
    k = stoch.rolling(window=smooth_k).mean()
    d = k.rolling(window=smooth_d).mean()
    return (float(k.iloc[-1]), float(d.iloc[-1]),
            float(k.iloc[-2]), float(d.iloc[-2]))



def hitung_rsi(df: pd.DataFrame, period: int = 5) -> pd.Series:
    """Calculate RSI for any period (default 5 for sniper)."""
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=period).mean()
    rs = gain / (loss + 1e-9)
    return 100.0 - (100.0 / (1.0 + rs))
def hitung_atr(df: pd.DataFrame, period=ATR_PERIOD) -> float:
    prev_close = df["close"].shift(1)
    tr = np.maximum(
        df["high"] - df["low"],
        np.maximum((df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()),
    )
    return float(pd.Series(tr).rolling(window=period).mean().iloc[-1])


async def ambil_metrics(symbol: str):
    ohlcv = await EXCHANGE.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=100)
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    if len(df) < max(ATR_PERIOD, STOCH_PERIOD) + 5:
        raise ValueError("data candle kurang")
    k, d, k_prev, d_prev = hitung_stoch_rsi(df)
    atr = hitung_atr(df)
    if not all(map(math.isfinite, (k, d, atr))):
        raise ValueError("indikator NaN")
    if atr <= 0:
        raise ValueError("ATR nol/negatif -> skip")
    # --- EMA 5/20 ---
    ema5_series = df["close"].ewm(span=5).mean()
    ema20_series = df["close"].ewm(span=20).mean()
    ema5 = float(ema5_series.iloc[-1])
    ema20 = float(ema20_series.iloc[-1])

    # --- RSI 5 ---
    rsi5_series = hitung_rsi(df, period=5)
    rsi5 = float(rsi5_series.iloc[-1])
    rsi5_prev = float(rsi5_series.iloc[-3]) if len(df) >= 3 else rsi5

    return {
        "live": float(df["close"].iloc[-1]),
        "open": float(df["open"].iloc[-1]),
        "k": k,
        "d": d,
        "k_prev": k_prev,
        "d_prev": d_prev,
        "atr": atr,
        "volume": float(df["volume"].iloc[-1]),
        "vol_spike": float(df["volume"].iloc[-1]) > 1.5 * df["volume"].iloc[-20:-1].mean() if len(df) >= 20 else False,
        "df": df,
        "ema5": ema5,
        "ema20": ema20,
        "rsi5": rsi5,
        "rsi5_prev": rsi5_prev,
    }


# ==============================================================================
# 6B. JAM PASAR TRADFI (XAU / XAG / XPT) — dilarang entry saat pasar tutup
# ==============================================================================
# Jadwal acuan COMEX / spot logam:
#   BUKA  : Minggu 18:00 ET
#   TUTUP : Jumat 17:00 ET
#   JEDA HARIAN : 17:00 - 18:00 ET (setiap hari kerja)
# Semua dihitung otomatis di zona America/New_York, jadi DST ikut menyesuaikan.

TRADFI_HOURS_ENABLED = env_bool("TRADFI_MARKET_HOURS_ENABLED", True)
TRADFI_PAIRS = set(
    p.strip().upper()
    for p in env_str("TRADFI_PAIRS", "XAU/USDT,XAG/USDT,XPT/USDT").split(",")
    if p.strip()
)
TRADFI_ENTRY_BUFFER_MIN = env_int("TRADFI_ENTRY_BUFFER_MIN", 15)
TRADFI_FORCE_CLOSE = env_bool("TRADFI_FORCE_CLOSE_BEFORE_CLOSE", True)
TRADFI_FORCE_CLOSE_BUFFER_MIN = env_int("TRADFI_FORCE_CLOSE_BUFFER_MIN", 5)

try:
    from zoneinfo import ZoneInfo
    NY_TZ = ZoneInfo("America/New_York")
except Exception:
    NY_TZ = timezone(timedelta(hours=-4))  # fallback kasar kalau tzdata tidak ada
    log.warning("tzdata tidak tersedia, jam TradFi pakai fallback UTC-4")

TRADFI_CLOSE_MIN = 17 * 60   # 17:00 ET
TRADFI_OPEN_MIN = 18 * 60    # 18:00 ET


def cek_pasar_tradfi(buffer_min: int = 0):
    """Return (buka: bool, alasan: str). buffer_min = tutup lebih awal X menit."""
    if not TRADFI_HOURS_ENABLED:
        return True, "HOURS_CHECK_OFF"
    now = datetime.now(NY_TZ)
    wd = now.weekday()          # Senin=0 ... Sabtu=5, Minggu=6
    minute_of_day = now.hour * 60 + now.minute
    tutup_efektif = TRADFI_CLOSE_MIN - max(0, buffer_min)

    if wd == 5:
        return False, "WEEKEND_SABTU"
    if wd == 6:
        if minute_of_day < TRADFI_OPEN_MIN:
            return False, "WEEKEND_MINGGU_PRE_OPEN"
        return True, "OPEN_SESI_MINGGU"
    if wd == 4 and minute_of_day >= tutup_efektif:
        return False, "WEEKEND_TUTUP_JUMAT"
    if wd <= 3 and tutup_efektif <= minute_of_day < TRADFI_OPEN_MIN:
        return False, "JEDA_HARIAN_1700_1800_ET"
    return True, "OPEN"


def is_tradfi(symbol: str) -> bool:
    s = symbol.upper()
    if s in TRADFI_PAIRS:
        return True
    # cocokkan juga bentuk lain: XAU/USDT:USDT, XAUUSDT, dll.
    basis = s.split("/")[0].split(":")[0]
    return any(basis == t.split("/")[0] for t in TRADFI_PAIRS)


def resolve_symbol(symbol: str, markets) -> str:
    """Cari nama simbol yang benar-benar dipakai bursa.

    Binance kadang memakai unified symbol berakhiran :USDT untuk perpetual
    (mis. XAU/USDT:USDT). Fungsi ini mencocokkan otomatis supaya pair logam
    tidak terbuang percuma hanya karena beda penulisan.
    """
    if not markets:
        return symbol
    if symbol in markets:
        return symbol
    for kandidat in (f"{symbol}:USDT", symbol.replace("/", ""),
                     f"{symbol}:USDC"):
        if kandidat in markets:
            return kandidat
    basis, _, kuota = symbol.partition("/")
    kuota = (kuota or "USDT").split(":")[0]
    for nama, pasar in markets.items():
        try:
            if (str(pasar.get("base", "")).upper() == basis
                    and str(pasar.get("quote", "")).upper() == kuota
                    and pasar.get("swap", pasar.get("contract", False))
                    and pasar.get("active", True)):
                return nama
        except Exception:
            continue
    return ""


def tradfi_boleh_entry(symbol: str):
    """Gate entry khusus pair logam. Return (boleh, alasan)."""
    if not is_tradfi(symbol):
        return True, "CRYPTO_24_7"
    return cek_pasar_tradfi(TRADFI_ENTRY_BUFFER_MIN)


def status_pasar_tradfi() -> str:
    if not TRADFI_HOURS_ENABLED:
        return "🟡 GATE OFF"
    buka, alasan = cek_pasar_tradfi()
    now_et = datetime.now(NY_TZ).strftime("%a %H:%M")
    return f"{'🟢 OPEN' if buka else '🌙 CLOSED'} ({alasan} | ET {now_et})"


# ==============================================================================
# 7. AI AGENTS
# ==============================================================================

PROMPT_KIMI = (
    "Analisis BTC/USDT futures untuk scalping 5 menit. Perhatikan: "
    "1) Struktur tren harga (higher highs/lows atau lower highs/lows) "
    "2) Momentum (RSI, MACD arah) "
    "3) Volume dan funding rate "
    "4) Level support/resistance kunci "
    "Balas JSON: 'market_regime' (BULLISH_TREND/BEARISH_TREND/SIDEWAYS_CHOP), "
    "'recommended_tactic' (MEAN_REVERSION_ONLY/TREND_FOLLOWING_AGGRESSIVE/SCALP_BOTH), "
    "'confidence' (1-100, seberapa yakin), "
    "'bias' (LONG_PREFERRED/SHORT_PREFERRED/NEUTRAL), "
    "'global_bias_rationale' (1 kalimat)."
)


SYS_JSON = (
    "Anda mesin yang hanya bisa membalas satu objek JSON valid. "
    "Dilarang menulis penjelasan, salam, atau blok markdown."
)

_NO_JSON_MODE = set()  # model yang terbukti menolak response_format


def _ekstrak_json(teks) -> dict:
    """Parse JSON dari balasan model walau dibungkus ```json atau kalimat."""
    if isinstance(teks, dict):
        return teks
    t = (teks or "").strip()
    if not t:
        return {}
    # Try markdown block
    if t.startswith("```"):
        bagian = t.split("```")
        if len(bagian) > 1:
            t = bagian[1]
        if t.lstrip().lower().startswith("json"):
            t = t.lstrip()[4:]
        t = t.strip()
    # Try direct parse
    try:
        return json.loads(t)
    except Exception:
        pass
    # Try to find ALL JSON objects (take last one - usually the answer)
    all_jsons = []
    depth = 0
    start = -1
    for idx, ch in enumerate(t):
        if ch == "{":
            if depth == 0:
                start = idx
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start != -1:
                try:
                    obj = json.loads(t[start:idx+1])
                    all_jsons.append(obj)
                except Exception:
                    pass
                start = -1
    if all_jsons:
        # Return last JSON that has "decision" or "veto_decision"
        for obj in reversed(all_jsons):
            if "decision" in obj or "veto_decision" in obj:
                return obj
        return all_jsons[-1]
    return {}


def _chat_json(client, model: str, prompt: str, max_tokens: int = 200,
               extra_body=None) -> dict:
    """Panggil satu model dan paksa hasilnya jadi dict JSON.

    Dipakai bertiga oleh Kimi / DeepSeek / Llama, masing-masing boleh bawa
    klien sendiri (base_url + API key sendiri) dan extra_body sendiri
    (reasoning untuk Kimi, provider routing untuk Llama).
    """
    if client is None:
        raise RuntimeError("klien AI tidak aktif (API key kosong?)")
    kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYS_JSON},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    if extra_body:
        kwargs["extra_body"] = extra_body

    if AI_JSON_MODE and model not in _NO_JSON_MODE:
        try:
            res = client.chat.completions.create(
                response_format={"type": "json_object"}, **kwargs)
            raw_content = res.choices[0].message.content
            log.info("AI_RAW model=%s content=%s", model, str(raw_content)[:300])
            return _ekstrak_json(raw_content)
        except Exception as exc:
            pesan = str(exc).lower()
            tanda = ("structured-outputs" in pesan or "response_format" in pesan
                     or "json_object" in pesan or "invalid_request_body" in pesan)
            if not tanda:
                raise
            _NO_JSON_MODE.add(model)
            log.warning("Model %s tolak json_object -> pindah mode prompt-only", model)
    res = client.chat.completions.create(**kwargs)
    return _ekstrak_json(res.choices[0].message.content)


def _or_json(model: str, prompt: str, max_tokens: int = 200) -> dict:
    """Jalur OpenRouter umum (dipakai fallback DeepSeek)."""
    return _chat_json(client_or, model, prompt, max_tokens)


def _extra_kimi():
    return {"reasoning": {"enabled": True}} if KIMI_REASONING else None


def _extra_llama():
    if not LLAMA_PROVIDER:
        return None
    return {"provider": {"order": [LLAMA_PROVIDER],
                         "allow_fallbacks": LLAMA_ALLOW_FALLBACK}}


async def agent1_kimi_macro():
    """AGEN 1 — Kimi K2.6 lewat OpenRouter, reasoning aktif."""
    global MARKET_REGIME_GLOBAL
    if CLIENT_KIMI is None:
        AI_STATUS["kimi"] = "NO KEY"
        MARKET_REGIME_GLOBAL["last_updated"] = "NO KEY"
        return
    try:
        data = await asyncio.to_thread(
            _chat_json, CLIENT_KIMI, KIMI_MODEL, PROMPT_KIMI,
            KIMI_MAX_TOKENS, _extra_kimi())
        AI_STATUS["kimi"] = "OK"
        confidence = int(data.get("confidence", 50))
        regime_val = data.get("market_regime", "SIDEWAYS_CHOP")
        # Kalau confidence rendah, paksa SIDEWAYS (lebih aman)
        if confidence < 40:
            regime_val = "SIDEWAYS_CHOP"
            log.info("KIMI confidence %d < 40 -> forced SIDEWAYS", confidence)
        MARKET_REGIME_GLOBAL = {
            "market_regime": regime_val,
            "recommended_tactic": data.get("recommended_tactic", "MEAN_REVERSION_ONLY"),
            "rationale": data.get("global_bias_rationale", "-"),
            "bias": data.get("bias", "NEUTRAL"),
            "confidence": confidence,
            "last_updated": datetime.now(WIB).strftime("%H:%M:%S"),
        }
        log.info("KIMI regime=%s bias=%s conf=%d", regime_val, 
                 MARKET_REGIME_GLOBAL["bias"], confidence)
        push_event("REGIME", f"Kimi: {regime_val} ({confidence}%) · "
                             f"{MARKET_REGIME_GLOBAL['recommended_tactic']}")
    except Exception as exc:
        AI_STATUS["kimi"] = "ERROR"
        # V15.4: local fallback berdasarkan RSI/ATR (tanpa AI)
        log.warning("Kimi gagal: %s -> using local regime", exc)
        try:
            _ohlcv = await EXCHANGE.fetch_ohlcv("BTC/USDT", timeframe="15m", limit=50)
            _df = pd.DataFrame(_ohlcv, columns=["timestamp","open","high","low","close","volume"])
            _delta = _df["close"].diff()
            _gain = _delta.where(_delta > 0, 0.0).rolling(14).mean()
            _loss = (-_delta.where(_delta < 0, 0.0)).rolling(14).mean()
            _rs = _gain / (_loss + 1e-9)
            _rsi = float((100.0 - (100.0 / (1.0 + _rs))).iloc[-1])
            _atr = hitung_atr(_df)
            _atr_pct = _atr / float(_df["close"].iloc[-1]) * 100
            if _rsi > 60 and _atr_pct > 0.3:
                _regime = "BULLISH_TREND"
            elif _rsi < 40 and _atr_pct > 0.3:
                _regime = "BEARISH_TREND"
            else:
                _regime = "SIDEWAYS_CHOP"
            MARKET_REGIME_GLOBAL.update({
                "market_regime": _regime,
                "recommended_tactic": "TREND_FOLLOWING_AGGRESSIVE" if _regime != "SIDEWAYS_CHOP" else "MEAN_REVERSION_ONLY",
                "confidence": 30,
                "bias": "NEUTRAL",
                "rationale": f"LOCAL fallback RSI={_rsi:.0f} ATR%={_atr_pct:.2f}",
                "last_updated": datetime.now(WIB).strftime("%H:%M:%S"),
            })
            log.info("LOCAL regime=%s RSI=%.0f ATR%%=%.2f", _regime, _rsi, _atr_pct)
        except Exception as exc2:
            MARKET_REGIME_GLOBAL["last_updated"] = "FALLBACK_FAIL"
            log.error("Local regime fallback gagal: %s", exc2)
    # [PATCH-B] Sync ATHENA_CACHE from MARKET_REGIME_GLOBAL after update
    # [PATCH-I] Normalize regime names from Kimi -> V2 format
    _raw_regime = MARKET_REGIME_GLOBAL.get("market_regime", "CHOPPY")
    _regime_map = {
        "SIDEWAYS_CHOP": "CHOPPY", "CHOPPY": "CHOPPY",
        "BULLISH_TREND": "TRENDING_UP", "TRENDING_UP": "TRENDING_UP",
        "BEARISH_TREND": "TRENDING_DOWN", "TRENDING_DOWN": "TRENDING_DOWN",
        "HIGH_VOLATILITY": "CHOPPY", "LOW_VOLATILITY": "CHOPPY",
        "TRANSITIONING": "TRANSITIONING",
    }
    _norm_regime = _regime_map.get(_raw_regime, "CHOPPY")
    _raw_bias = MARKET_REGIME_GLOBAL.get("bias", "NEUTRAL")
    _bias_map = {
        "NEUTRAL": "NEUTRAL", "LONG_LEANING": "LONG_LEANING",
        "SHORT_LEANING": "SHORT_LEANING",
        "BULLISH": "LONG_LEANING", "BEARISH": "SHORT_LEANING",
    }
    _norm_bias = _bias_map.get(_raw_bias, "NEUTRAL")
    _conf = MARKET_REGIME_GLOBAL.get("confidence", 30)
    ATHENA_CACHE.update({
        "market_regime": _norm_regime,
        "bias": _norm_bias,
        "confidence": _conf,
        "bias_strength": _conf,
        "volatility_state": "NORMAL",
        "session_quality": min(100, max(0, _conf + 10)),
        "reasoning": MARKET_REGIME_GLOBAL.get("rationale", "-"),
        "warnings": [],
    })
    log.info("ATHENA_CACHE synced: raw=%s->norm=%s bias=%s->%s conf=%s",
             _raw_regime, _norm_regime, _raw_bias, _norm_bias, _conf)


def _baca_putusan_agent2(data: dict, tag: str):
    raw = str(data.get("decision", "")).upper()
    ok = raw in ("CONFIRMED", "EXECUTE", "BUY", "SELL", "OPEN", "YES", "GO")
    reason = data.get("audit_log", data.get("reason", data.get("analysis", "-")))
    if not ok:
        log.info("DeepSeek raw decision: %s | data: %s", raw, str(data)[:200])
    return (ok, f"{tag}{reason}")


async def agent2_deepseek(symbol: str, side: str, m: dict, funding: float):
    """AGEN 2 — DeepSeek V4 Flash lewat EvoMap (base_url & API key sendiri)."""
    prompt = (
        f"Anda adalah filter final bot scalping crypto. Setup: {symbol} {side} pada harga {m['live']:.4f}. "
        f"ATR={m['atr']:.6f}, StochRSI K={m['k']:.1f} D={m['d']:.1f}, funding={funding:.6f}. "
        f"Regime={MARKET_REGIME_GLOBAL.get('market_regime','?')}. "
        "Ini bot scalping 5 menit, BUKAN investasi jangka panjang. "
        "Jika teknikal mendukung arah trade, bilang CONFIRMED. "
        "Hanya REJECT kalau ada sinyal kuat reversal atau manipulasi harga. "
        "Jangan terlalu konservatif - bot butuh entry untuk profit. "
        "Balas JSON: 'decision' (CONFIRMED/REJECT), 'sentiment_score' (1-100), 'audit_log' (1 kalimat singkat)."
    )
    if CLIENT_EVOMAP is not None:
        try:
            data = await asyncio.to_thread(
                _chat_json, CLIENT_EVOMAP, DEEPSEEK_MODEL, prompt, 220)
            AI_STATUS["deepseek"] = "OK"
            if not data:
                log.warning("EvoMap returned empty dict for %s %s", symbol, side)
            return _baca_putusan_agent2(data, "")
        except Exception as exc:
            AI_STATUS["deepseek"] = "FALLBACK"
            log.warning("EvoMap gagal (%s) -> fallback %s", exc, EVOMAP_FALLBACK_MODEL)
    # Jaring pengaman: DeepSeek lewat OpenRouter supaya agen 2 tetap hidup.
    if client_or is not None and EVOMAP_FALLBACK_MODEL:
        try:
            log.info("DeepSeek fallback -> %s via OpenRouter", EVOMAP_FALLBACK_MODEL)
            data = await asyncio.to_thread(
                _or_json, EVOMAP_FALLBACK_MODEL, prompt, 220)
            AI_STATUS["deepseek"] = "FALLBACK_OK"
            return _baca_putusan_agent2(data, "[FB] ")
        except Exception as exc:
            log.error("DeepSeek fallback gagal: %s", exc)
            AI_STATUS["deepseek"] = "FALLBACK_ERROR"
    AI_STATUS["deepseek"] = "OFF"
    return (AI_FAIL_OPEN, "EvoMap & fallback gagal")
    try:
        data = await asyncio.to_thread(_or_json, EVOMAP_FALLBACK_MODEL, prompt, 220)
        AI_STATUS["deepseek"] = "FALLBACK"
        return _baca_putusan_agent2(data, "[fallback] ")
    except Exception as exc:
        AI_STATUS["deepseek"] = "ERROR"
        log.error("Agent2 DeepSeek error: %s", exc)
        return (AI_FAIL_OPEN, f"DeepSeek driver failure: {exc}")


async def agent3_llama(symbol: str, side: str, m: dict, book_summary: str):
    """AGEN 3 — Llama 3.3 70B lewat OpenRouter, provider dikunci ke Groq."""
    if CLIENT_LLAMA is None:
        AI_STATUS["llama"] = "NO KEY"
        return (AI_FAIL_OPEN, "Llama tidak aktif (API key kosong)")
    prompt = (
        f"Anda filter final bot scalping 5 menit. Audit {symbol} {side}.\n"
        f"Data: harga={m['live']:.4f} ATR={m['atr']:.6f} K={m['k']:.1f} D={m['d']:.1f}\n"
        f"Order book: {book_summary}\n"
        f"Market regime: {MARKET_REGIME_GLOBAL.get('market_regime','?')}\n"
        f"GODMODE confluence score: {m.get('godmode_score', 50)}/100\n"
        "ATURAN:\n"
        "- GODMODE score >= 75: SETUJUI meski market CHOPPY (momentum kuat override)\n"
        "- GODMODE score >= 65 + order book imbalance > 1.5: SETUJUI\n"
        "- GODMODE score < 65 + CHOPPY: TOLAK (whipsaw risk)\n"
        "- ATR sangat rendah + score < 70: TOLAK\n"
        "- Order book menunjukkan wall besar lawan arah: TOLAK\n"
        "Balas JSON: 'veto_decision' (VETO_EXECUTE/VETO_ABORT), 'veto_reason' (1 kalimat singkat)."
    )
    try:
        data = await asyncio.to_thread(
            _chat_json, CLIENT_LLAMA, LLAMA_MODEL, prompt, 200, _extra_llama())
        AI_STATUS["llama"] = "OK"
        raw_dec = str(data.get("veto_decision", "")).upper().strip()
        ok = raw_dec in ("VETO_EXECUTE", "EXECUTE", "CONFIRMED", "APPROVE", "YES")
        if not ok:
            log.info("LLAMA_DEBUG %s raw=%s body=%s", symbol, raw_dec, str(data)[:200])
        return (ok, data.get("veto_reason", "-"))
    except Exception as exc:
        AI_STATUS["llama"] = "ERROR"
        log.error("Agent3 Llama error: %s", exc)
        return (AI_FAIL_OPEN, f"Llama driver failure: {exc}")


async def ringkas_order_book(symbol: str) -> str:
    try:
        ob = await EXCHANGE.fetch_order_book(symbol, limit=20)
        bid_vol = sum(float(x[1]) for x in ob.get("bids", [])[:20])
        ask_vol = sum(float(x[1]) for x in ob.get("asks", [])[:20])
        ratio = bid_vol / (ask_vol + 1e-9)
        return f"bid20={bid_vol:.2f} ask20={ask_vol:.2f} imbalance={ratio:.2f}"
    except Exception as exc:
        return f"orderbook unavailable ({exc})"


async def ambil_funding(symbol: str) -> float:
    try:
        fr = await EXCHANGE.fetch_funding_rate(symbol)
        return float(fr.get("fundingRate") or 0.0)
    except Exception:
        return 0.0


async def ai_alliance(symbol: str, side: str, m: dict):
    """V16: Full GODMODE V2 pipeline. ATHENA -> HERMES -> AEGIS -> Consensus."""
    if not AI_LAYER_ENABLED:
        return True, "AI layer disabled"
    try:
        # Fetch 5m + 15m data
        ohlcv_5m = await EXCHANGE.fetch_ohlcv(symbol, timeframe="5m", limit=100)
        df_5m = pd.DataFrame(ohlcv_5m, columns=["timestamp","open","high","low","close","volume"])
        ohlcv_15m = await EXCHANGE.fetch_ohlcv(symbol, timeframe="15m", limit=50)
        df_15m = pd.DataFrame(ohlcv_15m, columns=["timestamp","open","high","low","close","volume"])

        # Orderbook for spread/imbalance
        ob = await EXCHANGE.fetch_order_book(symbol, limit=20)
        bid_vol = sum(float(x[1]) for x in ob.get("bids", [])[:20])
        ask_vol = sum(float(x[1]) for x in ob.get("asks", [])[:20])
        ob_imbalance = bid_vol / (ask_vol + 1e-9)

        # Run full V2 pipeline: Feature -> GODMODE -> HERMES -> AEGIS -> Consensus
        pipeline = await run_godmode_v2_pipeline(
            symbol=symbol,
            df_5m=df_5m,
            df_15m=df_15m,
            client_athena=CLIENT_LLAMA or CLIENT_KIMI or client_or,
            model_athena="meta-llama/llama-3.1-8b-instruct",
            client_hermes=CLIENT_KIMI or client_or,
            model_hermes="deepseek/deepseek-v4-flash",
            client_aegis=CLIENT_LLAMA or client_or,
            model_aegis="meta-llama/llama-3.3-70b-instruct",
            base_margin=margin_now(),
            market_ctx=ATHENA_CACHE,
            spread_bps=5.0,
            ob_imbalance=ob_imbalance,
        )

        decision = pipeline.get("consensus", {}).get("decision", "REJECT")
        reason = pipeline.get("consensus", {}).get("reason", "-")
        score = pipeline.get("score", 0)
        tier = pipeline.get("tier", "REJECT")

        # Update godmode_score for logging
        m["godmode_score"] = score
        m["godmode_tier"] = tier

        log.info("GMV2 %s %s score=%.1f tier=%s decision=%s reason=%s",
                 symbol, side, score, tier, decision, reason)

        if decision in ("SNIPER", "EXECUTE", "WATCH"):
            return True, f"GMV2 {tier} score={score:.1f}: {reason}"
        else:
            return False, f"GMV2 {decision}: {reason}"

    except Exception as e:
        log.error("ai_alliance V2 error %s: %s", symbol, e)
        return False, f"Pipeline error: {e}"



# ==============================================================================
# 5C. GODMODE DIRECTIONAL (V15.4) - MarketStructure + Scoring
# ==============================================================================
class MarketStructure:
    def __init__(self):
        self.trend = "ranging"
        self.support_levels = []
        self.resistance_levels = []
        self.swing_highs = []
        self.swing_lows = []

    def analyze(self, df):
        if len(df) < 20:
            return "ranging"
        h, lo = df["high"].values, df["low"].values
        self.swing_highs, self.swing_lows = [], []
        for i in range(2, len(df) - 2):
            if h[i] > h[i-1] and h[i] > h[i-2] and h[i] > h[i+1] and h[i] > h[i+2]:
                self.swing_highs.append({"idx": i, "price": h[i]})
            if lo[i] < lo[i-1] and lo[i] < lo[i-2] and lo[i] < lo[i+1] and lo[i] < lo[i+2]:
                self.swing_lows.append({"idx": i, "price": lo[i]})
        rh = self.swing_highs[-3:] if len(self.swing_highs) >= 2 else []
        rl = self.swing_lows[-3:] if len(self.swing_lows) >= 2 else []
        if len(rh) >= 2 and len(rl) >= 2:
            hh = rh[-1]["price"] > rh[-2]["price"]
            hl = rl[-1]["price"] > rl[-2]["price"]
            lh = rh[-1]["price"] < rh[-2]["price"]
            ll = rl[-1]["price"] < rl[-2]["price"]
            if hh and hl: self.trend = "bullish"
            elif lh and ll: self.trend = "bearish"
            else: self.trend = "ranging"
        else:
            self.trend = "ranging"
        self.support_levels = [x["price"] for x in rl]
        self.resistance_levels = [x["price"] for x in rh]
        return self.trend

    def near_support(self, price, thresh=0.01):
        return any(abs(price - s) / price < thresh for s in self.support_levels)

    def near_resistance(self, price, thresh=0.01):
        return any(abs(price - r) / price < thresh for r in self.resistance_levels)

MARKET_STRUCTURE = MarketStructure()

def godmode_exhaustion(df):
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / (loss + 1e-9)
    rsi_series = 100.0 - (100.0 / (1.0 + rs))
    rsi_val = float(rsi_series.iloc[-1])
    if rsi_val < 30: return "oversold", rsi_val
    elif rsi_val > 70: return "overbought", rsi_val
    return "neutral", rsi_val

def deteksi_divergensi(df, period=14):
    """Detect RSI divergence: bullish (lembah) / bearish (pucuk)."""
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / (loss + 1e-9)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    closes = df["close"].values
    rsi_vals = rsi.values
    n = len(closes)
    if n < 10: return "none"
    lookback = min(n - 1, 20)
    rc = closes[-lookback:]
    rr = rsi_vals[-lookback:]
    half = lookback // 2
    mi1 = int(np.argmin(rc[:half]))
    mi2 = int(np.argmin(rc[half:])) + half
    if mi2 > mi1 and rc[mi2] < rc[mi1] and rr[mi2] > rr[mi1]:
        return "bullish"
    mai1 = int(np.argmax(rc[:half]))
    mai2 = int(np.argmax(rc[half:])) + half
    if mai2 > mai1 and rc[mai2] > rc[mai1] and rr[mai2] < rr[mai1]:
        return "bearish"
    return "none"

def godmode_evaluate(action, m, df):
    if not GODMODE_ENABLED:
        return action, 80, ["GODMODE off"]
    score = 50
    reasons = []
    if action == "LONG" and m["k"] < 35:
        score += 15; reasons.append("oversold")
    elif action == "SHORT" and m["k"] > 65:
        score += 15; reasons.append("overbought")
    if action == "LONG" and m["k"] > m["d"]:
        score += 10; reasons.append("K>D")
    elif action == "SHORT" and m["k"] < m["d"]:
        score += 10; reasons.append("K<D")
    trend = MARKET_STRUCTURE.trend
    price = m["live"]
    if action == "LONG" and (trend == "bullish" or MARKET_STRUCTURE.near_support(price)):
        score += GODMODE_DIRECTIONAL_BOOST
        reasons.append("aligned")
        if MARKET_STRUCTURE.near_support(price):
            score += GODMODE_EXHAUSTION_BONUS; reasons.append("support")
    elif action == "SHORT" and (trend == "bearish" or MARKET_STRUCTURE.near_resistance(price)):
        score += GODMODE_DIRECTIONAL_BOOST
        reasons.append("aligned")
        if MARKET_STRUCTURE.near_resistance(price):
            score += GODMODE_EXHAUSTION_BONUS; reasons.append("resistance")
    elif (action == "LONG" and trend == "bearish") or (action == "SHORT" and trend == "bullish"):
        score -= GODMODE_COUNTER_TREND_PENALTY
        reasons.append("counter")
    else:
        score += 10
        reasons.append("ranging+10")
    if len(df) >= 20:
        try:
            ex, rv = godmode_exhaustion(df)
            if action == "LONG" and ex == "oversold":
                score += GODMODE_EXHAUSTION_BONUS; reasons.append("RSI")
            elif action == "SHORT" and ex == "overbought":
                score += GODMODE_EXHAUSTION_BONUS; reasons.append("RSI")
        except Exception:
            pass
    # SNIPER BONUS: RSI Divergence (+20)
    try:
        div = deteksi_divergensi(df)
        if action == "LONG" and div == "bullish":
            score += 20; reasons.append("DIV_BULL")
        elif action == "SHORT" and div == "bearish":
            score += 20; reasons.append("DIV_BEAR")
    except Exception:
        pass
    # SNIPER BONUS: Volume Spike (+10)
    try:
        if len(df) >= 20:
            avg_vol = df["volume"].iloc[-20:-1].mean()
            cur_vol = df["volume"].iloc[-1]
            if avg_vol > 0 and cur_vol > 1.5 * avg_vol:
                score += 10; reasons.append("VOL_SPIKE")
    except Exception:
        pass

    score = max(0, min(100, score))
    if score >= GODMODE_MIN_SCORE:
        return action, score, reasons
    return None, score, reasons + ["SKIP"]

# ==============================================================================
# 8. EKSEKUSI ORDER  (BUG FIX 4: execute-then-notify)
# ==============================================================================


async def siapkan_leverage(symbol: str):
    try:
        await EXCHANGE.set_leverage(LEVERAGE, symbol)
    except Exception as exc:
        log.warning("set_leverage %s gagal: %s", symbol, exc)


def hitung_target(side: str, entry: float, atr: float):
    if side == "LONG":
        tp = entry * (1 + TP_PCT)
        sl = entry - (SL_ATR_MULT * atr)
    else:
        tp = entry * (1 - TP_PCT)
        sl = entry + (SL_ATR_MULT * atr)
    return tp, sl


async def eksekusi_order(symbol: str, side: str, m: dict):
    """Kirim market order + TP/SL ke Binance. Return dict posisi atau raise."""
    entry_ref = m["live"]
    notional = notional_now()
    if NOTIONAL_LIMIT_USDT > 0 and notional > NOTIONAL_LIMIT_USDT:
        notional = NOTIONAL_LIMIT_USDT
        log.info("NOTIONAL cap $%.2f for %s", NOTIONAL_LIMIT_USDT, symbol)
    amount = notional / entry_ref
    try:
        amount = float(EXCHANGE.amount_to_precision(symbol, amount))
    except Exception:
        amount = round(amount, 6)
    if amount <= 0:
        raise ValueError("amount 0 setelah pembulatan precision")

    order_side = "buy" if side == "LONG" else "sell"
    close_side = "sell" if side == "LONG" else "buy"

    await siapkan_leverage(symbol)
    order = await EXCHANGE.create_order(symbol, "market", order_side, amount)

    # verifikasi fill nyata (inti BUG FIX 4)
    filled = float(order.get("filled") or 0.0)
    avg = float(order.get("average") or order.get("price") or 0.0)
    if filled <= 0 or avg <= 0:
        await asyncio.sleep(1.0)
        try:
            fetched = await EXCHANGE.fetch_order(order["id"], symbol)
            filled = float(fetched.get("filled") or 0.0)
            avg = float(fetched.get("average") or fetched.get("price") or 0.0)
        except Exception as exc:
            log.warning("fetch_order %s gagal: %s", symbol, exc)
    if filled <= 0:
        raise RuntimeError("order tidak terisi (filled=0) -> notif dibatalkan")
    entry = avg or entry_ref

    tp, sl = hitung_target(side, entry, m["atr"])
    tp_id = sl_id = None
    try:
        tp_order = await EXCHANGE.create_order(
            symbol, "TAKE_PROFIT_MARKET", close_side, filled, None,
            {"stopPrice": float(EXCHANGE.price_to_precision(symbol, tp)),
             "reduceOnly": True, "workingType": "MARK_PRICE"})
        tp_id = tp_order.get("id")
    except Exception as exc:
        log.error("Gagal pasang TP %s: %s", symbol, exc)
    try:
        sl_order = await EXCHANGE.create_order(
            symbol, "STOP_MARKET", close_side, filled, None,
            {"stopPrice": float(EXCHANGE.price_to_precision(symbol, sl)),
             "reduceOnly": True, "workingType": "MARK_PRICE"})
        sl_id = sl_order.get("id")
    except Exception as exc:
        log.error("Gagal pasang SL %s: %s", symbol, exc)

    return {
        "symbol": symbol, "side": side, "entry": entry, "amount": filled,
        "tp": tp, "sl": sl, "tp_id": tp_id, "sl_id": sl_id,
        "breakeven_done": False, "paper": False,
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "order_id": order.get("id"),
    }


def posisi_paper(symbol: str, side: str, m: dict) -> dict:
    entry = m["live"]
    tp, sl = hitung_target(side, entry, m["atr"])
    # [PATCH-D] Use pipeline margin if available (WATCH tier = 0.5x)
    effective_margin = m.get("pipeline_margin", margin_now())
    effective_notional = effective_margin * LEVERAGE
    return {
        "symbol": symbol, "side": side, "entry": entry,
        "amount": effective_notional / entry,
        "tp": tp, "sl": sl, "tp_id": None, "sl_id": None,
        "breakeven_done": False, "paper": True,
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "order_id": "PAPER",
        "setup_type": m.get("setup_type", "TC"),
    }


# ==============================================================================
# 9. NOTIFIKASI
# ==============================================================================


def fmt(x: float) -> str:
    if x >= 1000:
        return f"{x:,.2f}"
    if x >= 1:
        return f"{x:,.4f}"
    return f"{x:.6f}"


GARIS = "━" * 26


def bar_roi(pct: float, lebar: int = 10) -> str:
    """Bar visual untuk ROI, dipakai di notifikasi close."""
    penuh = min(int(abs(pct) / 2.0), lebar)      # 1 blok = 2% ROI
    blok = ("▰" if pct >= 0 else "▰") * max(penuh, 1)
    sisa = "▱" * (lebar - max(penuh, 1))
    return blok + sisa


def _mode_txt() -> str:
    return "🧪 DRY-RUN" if DRY_RUN_MODE else "🔴 LIVE"


def _ai_ringkas() -> str:
    ikon = {"OK": "🟢", "READY": "⚪", "FALLBACK": "🟡",
            "ERROR": "🔴", "NO KEY": "⚫", "OFF": "⚫"}
    return (f"{ikon.get(AI_STATUS['kimi'], '⚪')} Kimi  "
            f"{ikon.get(AI_STATUS['deepseek'], '⚪')} DeepSeek  "
            f"{ikon.get(AI_STATUS['llama'], '⚪')} Llama")


async def notif_entry(pos: dict, alasan: str):
    stats = JOURNAL.stats(24)
    arah = "🟢 LONG" if pos["side"] == "LONG" else "🔴 SHORT"
    margin = (pos["entry"] * pos["amount"]) / max(LEVERAGE, 1)
    jarak_sl = abs(pos["sl"] - pos["entry"]) / pos["entry"] * 100.0
    msg = (
        f"🚀 *ENTRY · {pos['symbol']} · {arah}*\n"
        f"`{GARIS}`\n"
        f"🧾 *Mode*   {_mode_txt()}\n"
        f"💵 *Entry*  `{fmt(pos['entry'])}`\n"
        f"📦 *Qty*    `{pos['amount']}`  ·  margin `${margin:,.2f}` x{LEVERAGE}\n"
        f"🎯 *TP*     `{fmt(pos['tp'])}`  `+{TP_PCT*100:.2f}%` → bersih `+{TP_NET_PCT*100:.2f}%`\n"
        f"🛡️ *SL*     `{fmt(pos['sl'])}`  `-{jarak_sl:.2f}%`  ({SL_ATR_MULT}×ATR)\n"
        f"`{GARIS}`\n"
        f"🧭 *Regime* {MARKET_REGIME_GLOBAL['market_regime']}\n"
        f"🤖 {_ai_ringkas()}\n"
        f"🧠 _{str(alasan)[:170]}_\n"
        f"`{GARIS}`\n"
        f"🎟️ Slot `{REGISTRY.slots_used()}/{MAX_OPEN_POSITIONS}`  ·  "
        f"💰 Ekuitas `${equity_now():,.2f}`\n"
        f"📊 24h `{stats['win']}W/{stats['loss']}L`  ·  "
        f"WR `{stats['win_rate']:.1f}%`  ·  Net `{stats['pnl_usdt']:+.2f}`\n"
        f"🆔 `{pos['order_id']}`"
    )
    await tg_send(msg)


async def notif_close(trade: dict):
    stats = JOURNAL.stats(24)
    win = trade["pnl_usdt"] > 0
    roi = trade["roi_pct"]
    if win:
        judul = "✅ *TAKE PROFIT*" if "TAKE PROFIT" in trade["reason"] else "✅ *CLOSED — WIN*"
    else:
        judul = "❌ *STOP LOSS*" if "STOP LOSS" in trade["reason"] else "❌ *CLOSED — LOSS*"
    pf = stats["profit_factor"]
    pf_txt = "∞" if pf == float("inf") else f"{pf:.2f}"
    wr = stats["win_rate"]
    tren = "📈" if stats["pnl_usdt"] >= 0 else "📉"
    msg = (
        f"{judul} · {trade['symbol']} · {trade['side']}\n"
        f"`{GARIS}`\n"
        f"`{bar_roi(roi)}`  *{roi:+.2f}%* ROI\n"
        f"`{GARIS}`\n"
        f"💵 Entry → Exit  `{fmt(trade['entry'])}` → `{fmt(trade['exit'])}`\n"
        f"🏷️ Alasan       {trade['reason']}\n"
        f"⏱️ Durasi       {trade['duration_min']:.1f} menit\n"
        f"`{GARIS}`\n"
        f"📐 Gross        `{trade.get('pnl_gross_usdt', 0.0):+.3f}` USDT "
        f"({trade['pnl_pct']:+.3f}%)\n"
        f"🧾 Fee          `-{trade.get('fee_usdt', 0.0):.3f}` USDT\n"
        f"💰 *NET*        `{trade['pnl_usdt']:+.3f}` USDT\n"
        f"`{GARIS}`\n"
        f"{tren} *REKAP 24 JAM*\n"
        f"├ W/L      `{stats['win']}W / {stats['loss']}L`\n"
        f"├ Win rate `{wr:.1f}%`\n"
        f"├ PF       `{pf_txt}`\n"
        f"└ Net      `{stats['pnl_usdt']:+.2f}` USDT\n"
        f"`{GARIS}`\n"
        f"💰 Ekuitas `${equity_now():,.2f}` ({EQUITY['sumber']})  ·  "
        f"🎟️ Slot `{REGISTRY.slots_used()}/{MAX_OPEN_POSITIONS}`\n"
        f"🔒 Cooldown {trade['symbol']} `{REENTRY_COOLDOWN_SEC//60}m` · "
        f"entry hari ini `{JOURNAL.reentries_today(trade['symbol'])}/{REENTRY_LIMIT_TXT}`"
    )
    await tg_send(msg)


# --- Notifikasi siklus hidup bot: START / RESTART / STOP ---------------
RUNTIME_FILE = env_str("RUNTIME_FILE", "runtime_v15.json")


def runtime_load() -> dict:
    try:
        with open(RUNTIME_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def runtime_save(data: dict):
    try:
        tmp = RUNTIME_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, RUNTIME_FILE)
    except Exception as exc:
        log.warning("runtime_save gagal: %s", exc)


async def notif_start(peringatan_txt: str = ""):
    rt = runtime_load()
    stop_terakhir = rt.get("last_stop")
    restart = bool(stop_terakhir)
    judul = "♻️ *BOT RESTART*" if restart else "🟢 *BOT START*"
    jeda = ""
    if restart:
        try:
            delta = (datetime.now(timezone.utc)
                     - datetime.fromisoformat(stop_terakhir)).total_seconds()
            jeda = f"\n⏸️ Mati selama `{int(delta // 60)}m {int(delta % 60)}s`"
        except Exception:
            jeda = ""
    runtime_save({**rt, "last_start": datetime.now(timezone.utc).isoformat(),
                  "last_stop": None})
    n_tradfi = sum(1 for p in ASSET_PAIRS if is_tradfi(p))
    msg = (
        f"{judul} · V15.6 GENIUS\n"
        f"`{GARIS}`\n"
        f"🧾 Mode      {_mode_txt()}\n"
        f"💰 Ekuitas   `${equity_now():,.2f}` ({EQUITY['sumber']})\n"
        f"💵 Margin    `${margin_now():,.2f}`/trade · notional `${notional_now():,.2f}`\n"
        f"🎟️ Slot      `{MAX_OPEN_POSITIONS}` · 1 posisi/pair · cd `{REENTRY_COOLDOWN_SEC//60}m`\n"
        f"🪙 Pair      `{len(ASSET_PAIRS)}` ({len(ASSET_PAIRS)-n_tradfi} crypto + {n_tradfi} logam)\n"
        f"🎯 TP/SL     `+{TP_PCT*100:.2f}%` → bersih `+{TP_NET_PCT*100:.2f}%` / `{SL_ATR_MULT}×ATR`\n"
        f"🛡️ BE        trigger `{BREAKEVEN_TRIGGER_PCT*100:.2f}%` → kunci `+{BREAKEVEN_OFFSET_PCT*100:.2f}%`\n"
        f"🏛️ TradFi    {status_pasar_tradfi()}\n"
        f"🤖 {_ai_ringkas()}\n"
        f"🖥️ UI        {'rich TUI' if TUI_MODE else 'log ringkas'}"
        f"{jeda}"
    )
    if peringatan_txt:
        msg += peringatan_txt
    await tg_send(msg)


async def notif_stop(alasan: str = "shutdown"):
    s = JOURNAL.stats(24)
    rt = runtime_load()
    runtime_save({**rt, "last_stop": datetime.now(timezone.utc).isoformat()})
    terbuka = ", ".join(REGISTRY.open.keys()) or "tidak ada"
    msg = (
        f"🛑 *BOT STOP* · V15.6 GENIUS\n"
        f"`{GARIS}`\n"
        f"🏷️ Alasan    {alasan}\n"
        f"⏱️ Uptime    `{_uptime_txt()}`\n"
        f"📊 24 jam    `{s['win']}W/{s['loss']}L` · WR `{s['win_rate']:.1f}%` · "
        f"Net `{s['pnl_usdt']:+.2f}` USDT\n"
        f"💰 Ekuitas   `${equity_now():,.2f}`\n"
        f"📌 Posisi    {terbuka}\n"
        f"`{GARIS}`\n"
        f"_Posisi yang masih terbuka TIDAK ikut tertutup. Pantau manual di app._"
    )
    await tg_send(msg)


# ==============================================================================
# 10. SENSOR + PIPELINE ENTRY
# ==============================================================================


async def proses_pair(symbol: str, board: dict):
    try:
        m = await ambil_metrics(symbol)
    except Exception as exc:
        board[symbol] = f"⚪ OFFLINE/ERR ({str(exc)[:18]})"
        return

    # SNIPER FIX: Block garbage data (K=0 or D=0 = bad candle data)
    if m["k"] == 0.0 or m["d"] == 0.0:
        board[symbol] = f"⚪  BAD DATA [K:{m["k"]:.1f}|D:{m["d"]:.1f}]"
        return
    if m["k"] == 0.0 or m["d"] == 0.0:
        board[symbol] = "BAD DATA"
        return

    barrier_up = m["open"] + (ATR_MULTIPLIER * m["atr"])
    barrier_dn = m["open"] - (ATR_MULTIPLIER * m["atr"])
    regime = MARKET_REGIME_GLOBAL["market_regime"]
    # V15.4 debug: log all sensor data
    # SENSOR: log all pairs (reduced verbosity - only log if K/D extreme or regime changed)
    if m["k"] > 80 or m["k"] < 20 or m["d"] > 80 or m["d"] < 20:
        log.info("SENSOR %s live=%.2f open=%.2f K=%.1f D=%.1f ATR=%.4f up=%.2f dn=%.2f regime=%s",
                 symbol, m["live"], m["open"], m["k"], m["d"], m["atr"],
                 barrier_up, barrier_dn, regime)
    bias = MARKET_REGIME_GLOBAL.get("bias", "NEUTRAL")
    side = None

    # ================================================================
    # LAYER 1: PER-PAIR REGIME CLASSIFICATION (Rule-Based)
    # ================================================================
    price = m["live"]
    ema5 = m["ema5"]
    ema20 = m["ema20"]
    rsi5 = m["rsi5"]
    rsi5_prev = m["rsi5_prev"]
    k_now = m["k"]
    d_now = m["d"]
    k_prev = m["k_prev"]
    d_prev = m["d_prev"]

    spread_pct = abs(ema5 - ema20) / (ema20 + 1e-9) * 100
    atr_pct = m["atr"] / (price + 1e-9) * 100

    pair_regime = "SIDEWAYS"
    if spread_pct < 0.12 and atr_pct < 0.25:
        pair_regime = "CHOPPY"
    elif spread_pct > 0.35:
        pair_regime = "TRENDING_UP" if ema5 > ema20 else "TRENDING_DOWN"

    log.debug("REGIME %s %s spread=%.3f%% atr=%.3f%%", symbol, pair_regime, spread_pct, atr_pct)

    # CHOPPY = EXTREME ONLY (RSI OR Stochastic extreme)
    if pair_regime == "CHOPPY":
        _extreme_overbought = rsi5 > 80 or rsi5_prev > 80 or k_now > 90 or d_now > 90
        _extreme_oversold = rsi5 < 20 or rsi5_prev < 20 or k_now < 10 or d_now < 10
        if not (_extreme_overbought or _extreme_oversold):
            board[symbol] = f"\u26aa CHOPPY [K:{k_now:.1f} RSI:{rsi5:.0f}]"
            log.debug("CHOPPY_SKIP %s K=%.1f D=%.1f RSI=%.1f (not extreme)", symbol, k_now, d_now, rsi5)
            return

    # ================================================================
    # LAYER 2: ATHENA MACRO (Global context - already loaded)
    # ================================================================
    global_regime = regime  # from MARKET_REGIME_GLOBAL

    # ================================================================
    # LAYER 3: CONVERGENCE SNIPER (Regime-Aware)
    # ================================================================
    # Stochastic alignment (not cross - catches signals even if cross was 2-3 candles ago)
    stoch_bullish = k_now < 20 and k_now > d_now   # oversold + K above D
    stoch_bearish = k_now > 80 and k_now < d_now   # overbought + K below D

    price_above_ema20 = price > ema20
    ema20_dist_pct = (price - ema20) / (ema20 + 1e-9) * 100
    candle_below_ema5 = price < ema5
    candle_above_ema5 = price > ema5

    # Convergence factor flags
    trend_short_ok = (not price_above_ema20) or (ema20_dist_pct > 0.5)
    trend_long_ok = price_above_ema20 or (ema20_dist_pct < -0.5)
    rsi_short_ok = rsi5 > 70 or rsi5_prev > 70
    rsi_long_ok = rsi5 < 30 or rsi5_prev < 30
    stoch_short_ok = (stoch_bearish and k_now > 80) or k_now > 95  # extreme bypass
    stoch_long_ok = (stoch_bullish and k_now < 20) or k_now < 5    # extreme bypass
    trigger_short = candle_below_ema5
    trigger_long = candle_above_ema5

    short_f = sum([trend_short_ok, rsi_short_ok, stoch_short_ok, trigger_short])
    long_f = sum([trend_long_ok, rsi_long_ok, stoch_long_ok, trigger_long])

    # --- TRENDING: with-trend 2/4, counter-trend 4/4 + extreme RSI ---
    if pair_regime in ("TRENDING_UP", "TRENDING_DOWN"):
        if pair_regime == "TRENDING_UP":
            # With-trend LONG: RSI must have pulled back (not overbought!)
            rsi_pullback_long = rsi5 < 55 and rsi5 > rsi5_prev  # RSI pulled back then rising
            if rsi_pullback_long and trigger_long and long_f >= 2:
                side = "LONG"
                log.info("TREND_LONG %s f=%d/4 RSI5=%.1f(pullback) regime=%s", symbol, long_f, rsi5, pair_regime)
            # Counter-trend SHORT: only extreme overbought + all 4
            elif rsi5 > 85 and short_f >= 4:
                side = "SHORT"
                log.info("EXTREME_SHORT %s RSI5=%.1f f=%d/4 regime=%s", symbol, rsi5, short_f, pair_regime)
        else:
            # With-trend SHORT: RSI must have pulled back (not oversold!)
            rsi_pullback_short = rsi5 > 45 and rsi5 < rsi5_prev  # RSI pulled back then falling
            if rsi_pullback_short and trigger_short and short_f >= 2:
                side = "SHORT"
                log.info("TREND_SHORT %s f=%d/4 RSI5=%.1f(pullback) regime=%s", symbol, short_f, rsi5, pair_regime)
            # Counter-trend LONG: only extreme oversold + all 4
            elif rsi5 < 15 and long_f >= 4:
                side = "LONG"
                log.info("EXTREME_LONG %s RSI5=%.1f f=%d/4 regime=%s", symbol, rsi5, long_f, pair_regime)

    # --- SIDEWAYS: sniper mode, 3/4 factors ---
    if side is None and pair_regime in ("SIDEWAYS", "SIDEWAYS_CHOP"):
        # Sideways sniper: RSI extreme + Stoch alignment + EMA5 trigger (3/4, drop trend)
        if (rsi_short_ok or k_now > 95) and stoch_short_ok and trigger_short:
            side = "SHORT"
            log.info("SIDEWAYS_SHORT %s f=%d/4 RSI5=%.1f K=%.1f<D=%.1f", symbol, short_f, rsi5, k_now, d_now)
        elif (rsi_long_ok or k_now < 5) and stoch_long_ok and trigger_long:
            side = "LONG"
            log.info("SIDEWAYS_LONG %s f=%d/4 RSI5=%.1f K=%.1f>D=%.1f", symbol, long_f, rsi5, k_now, d_now)

    # --- BIAS FALLBACK ---
    if side is None:
        if bias == "SHORT_PREFERRED" and rsi5 > 60 and k_now > 80 and candle_below_ema5:
            side = "SHORT"
            log.info("BIAS_SHORT %s RSI5=%.1f K=%.1f bias=%s", symbol, rsi5, k_now, bias)
        elif bias == "LONG_PREFERRED" and rsi5 < 40 and k_now < 20 and candle_above_ema5:
            side = "LONG"
            log.info("BIAS_LONG %s RSI5=%.1f K=%.1f bias=%s", symbol, rsi5, k_now, bias)
# DISABLED SNIPER -     # Jalur 4: Pure momentum crossover (paling sensitif)
# # DISABLED SNIPER -     elif m["k"] > m["d"] and m["k"] > 50 and m["d"] < 50:
# DISABLED SNIPER -         side = "LONG"   # K cross above D from below 50
# # DISABLED SNIPER -     elif m["k"] < m["d"] and m["k"] < 50 and m["d"] > 50:
# DISABLED SNIPER -         side = "SHORT"  # K cross below D from above 50
# DISABLED SNIPER - 
    if side is None:
        tag = "IN POSITION" if symbol in REGISTRY.open else "IDLE"
        board[symbol] = f"⚪  {tag} [K:{m['k']:.1f}|D:{m['d']:.1f}]"
        return
    log.info("SIGNAL %s -> %s (RSI5=%.1f K=%.1f D=%.1f EMA5=%.4f EMA20=%.4f pair_regime=%s global=%s bias=%s)", symbol, side, m["rsi5"], m["k"], m["d"], m["ema5"], m["ema20"], pair_regime, regime, bias)
    # SNIPER PATCH 4: HTF 15m confirmation
    try:
        _htf = await EXCHANGE.fetch_ohlcv(symbol, timeframe="15m", limit=50)
        _htf_df = pd.DataFrame(_htf, columns=["timestamp","open","high","low","close","volume"])
        # HTF: 15m EMA20 slope direction (support/oppose entry)
        _ema20_15m_s = _htf_df["close"].ewm(span=20).mean()
        _ema20_15m = float(_ema20_15m_s.iloc[-1])
        _ema20_15m_prev = float(_ema20_15m_s.iloc[-3])
        _htf_price = float(_htf_df["close"].iloc[-1])
        _ema20_15m_rising = _ema20_15m > _ema20_15m_prev
        _htf_ok = True
        # Skip HTF block in CHOPPY market (15m EMA20 flip-flops)
        _is_choppy = (pair_regime == "CHOPPY" or global_regime in ("SIDEWAYS_CHOP",))
        log.info("HTF_DEBUG %s pair_regime=%s global_regime=%s is_choppy=%s", symbol, pair_regime, global_regime, _is_choppy)
        if not _is_choppy and side == "LONG" and not _ema20_15m_rising:
            _htf_dist = (_ema20_15m - _htf_price) / _ema20_15m * 100
            if _htf_dist < 1.0:
                _htf_ok = False
                log.info("HTF_BLOCK %s LONG (15m EMA20 falling, dist=%.2f%%)", symbol, _htf_dist)
        elif not _is_choppy and side == "SHORT" and _ema20_15m_rising:
            _htf_dist = (_htf_price - _ema20_15m) / _ema20_15m * 100
            if _htf_dist < 1.0:
                log.info("HTF_BLOCK %s SHORT (15m EMA20 rising, dist=%.2f%%)", symbol, _htf_dist)

        if not _htf_ok:
            board[symbol] = "HTF EMA20 BLOCK"
            return
    except Exception:
        pass
    # SNIPER PATCH 5: Volume spike required
    if not m.get("vol_spike", False) and False:  # TEMP DISABLED
        log.info("VOL_BLOCK %s no volume spike", symbol)
        board[symbol] = "NO VOL SPIKE"
        return

    # V15.4: GODMODE DIRECTIONAL SCORING
    m["godmode_score"] = 50
    if GODMODE_ENABLED:
        try:
            _ohlcv = await EXCHANGE.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=100)
            _df = pd.DataFrame(_ohlcv, columns=["timestamp","open","high","low","close","volume"])
            MARKET_STRUCTURE.analyze(_df)
            side_gm, gm_score, gm_reasons = godmode_evaluate(side, m, _df)
            m["godmode_score"] = gm_score
            gm_txt = " | ".join(gm_reasons[:3])
            log.info("GODMODE %s %s score=%d %s | regime=%s", symbol, side, gm_score, gm_txt, MARKET_REGIME_GLOBAL.get("market_regime","?"))
            if side_gm is None:
                log.info("GODMODE_SKIP %s %s score=%d %s", symbol, side, gm_score, gm_txt)
                board[symbol] = f"GODMODE SKIP [{gm_score}] {gm_txt[:20]}"
                return
            log.info("GODMODE_PASS %s %s score=%d %s", symbol, side, gm_score, gm_txt)
        except Exception as gm_exc:
            log.error("GODMODE_CRASH %s: %s", symbol, gm_exc)
            m["godmode_score"] = 50
    # GATE JAM PASAR TRADFI
    boleh_tradfi, alasan_tradfi = tradfi_boleh_entry(symbol)
    if not boleh_tradfi:
        board[symbol] = f"🌙 MARKET CLOSED ({alasan_tradfi})"
        return
    gate = REGISTRY.gate_reason(symbol)
    gate = REGISTRY.gate_reason(symbol)
    if gate is not None:
        log.info("GATE %s: %s", symbol, gate)
        board[symbol] = f"🔒 {gate}"
        return

    # Atomic: mark signal first, then reserve (prevents rapid re-entry)
    REGISTRY.mark_signal(symbol)
    if not await REGISTRY.reserve(symbol):
        board[symbol] = "🔒 SLOT RACE"
        return

    board[symbol] = f"🚨 {side} SENSOR — AUDIT AI..."
    try:
        approved, alasan = await ai_alliance(symbol, side, m)
        if not approved:
            board[symbol] = f"❌ VETO: {str(alasan)[:22]}"
            push_event("VETO", f"{symbol} {side} ditolak AI: {str(alasan)[:70]}")
            await REGISTRY.release_pending(symbol)
            return

        if DRY_RUN_MODE:
            pos = posisi_paper(symbol, side, m)
        else:
            pos = await eksekusi_order(symbol, side, m)  # notif hanya jika ini sukses

        await REGISTRY.commit(symbol, pos)
        await notif_entry(pos, alasan)
        board[symbol] = f"🟢 OPEN {side} @ {fmt(pos['entry'])}"
        push_event("ENTRY", f"{symbol} {side} @ {fmt(pos['entry'])} · qty {pos['amount']} · "
                            f"slot {REGISTRY.slots_used()}/{MAX_OPEN_POSITIONS}")
        log.info("ENTRY %s %s @ %s qty %s", symbol, side, pos["entry"], pos["amount"])
        _last_entry_time[symbol] = datetime.now(timezone.utc).timestamp()
    except Exception as exc:
        await REGISTRY.release_pending(symbol)
        board[symbol] = f"⛔ EXEC FAIL: {str(exc)[:20]}"
        push_event("FAIL", f"{symbol} {side} gagal entry: {str(exc)[:70]}")
        log.error("Eksekusi %s gagal: %s\n%s", symbol, exc, traceback.format_exc())
        await tg_send(f"⚠️ *ENTRY GAGAL* `{symbol}` {side}\nAlasan: `{str(exc)[:250]}`\n"
                      f"_Tidak ada posisi terbuka — notif sinyal dibatalkan._")


# ==============================================================================
# 11. POSITION WATCHER: breakeven + deteksi TP/SL + WIN/LOSS
# ==============================================================================


def hitung_pnl(pos: dict, exit_price: float):
    """Kembalikan (pnl_pct_harga, gross_usdt, fee_usdt, net_usdt, roi_margin_pct).

    Fee dihitung taker 2 sisi (buka + tutup) dari notional masing-masing sisi,
    dan ROI dihitung dari margin nyata -- bukan pnl% x leverage yang menyesatkan.
    """
    if pos["side"] == "LONG":
        pnl_pct = (exit_price - pos["entry"]) / pos["entry"]
    else:
        pnl_pct = (pos["entry"] - exit_price) / pos["entry"]
    notional_in = pos["entry"] * pos["amount"]
    notional_out = exit_price * pos["amount"]
    gross = pnl_pct * notional_in
    fee = (notional_in + notional_out) * FEE_TAKER_PCT
    net = gross - fee
    margin = notional_in / max(LEVERAGE, 1)
    roi = (net / margin * 100.0) if margin > 0 else 0.0
    return pnl_pct * 100.0, gross, fee, net, roi


async def tutup_posisi(pos: dict, exit_price: float, reason: str):
    symbol = pos["symbol"]
    if not pos["paper"]:
        try:
            await EXCHANGE.cancel_all_orders(symbol)
            log.info("Canceled all orders for %s via ccxt", symbol)
        except Exception as exc:
            log.warning("cancel_all_orders %s: %s", symbol, exc)
        # Backup: cancel conditional orders via REST API
        try:
            async with httpx.AsyncClient() as http:
                ts = str(int(datetime.now(timezone.utc).timestamp() * 1000))
                params = {"symbol": symbol.replace("/", "").replace(":USDT", ""),
                          "timestamp": ts}
                qs = "&".join("%s=%s" % (k, v) for k, v in sorted(params.items()))
                sig = hmac.new(
                    EXCHANGE.secret.encode(), qs.encode(), hashlib.sha256
                ).hexdigest()
                url = "https://fapi.binance.com/fapi/v1/allOpenOrders?" + qs + "&signature=" + sig
                r = await http.delete(url, headers={"X-MBX-APIKEY": EXCHANGE.apiKey})
                if r.status_code == 200:
                    log.info("Backup cancel all orders for %s via REST OK", symbol)
                else:
                    log.warning("Backup cancel REST %s: %s", symbol, r.text[:100])
        except Exception as exc:
            log.warning("Backup cancel REST %s: %s", symbol, exc)
    pnl_pct, gross, fee, net, roi = hitung_pnl(pos, exit_price)
    opened = _safe_opened_at(pos)
    trade = {
        "symbol": symbol, "side": pos["side"], "entry": pos["entry"], "exit": exit_price,
        "amount": pos["amount"], "reason": reason, "paper": pos["paper"],
        "pnl_pct": pnl_pct, "pnl_gross_usdt": gross, "fee_usdt": fee,
        "pnl_usdt": net, "roi_pct": roi,
        "opened_at": pos["opened_at"],
        "closed_at": datetime.now(timezone.utc).isoformat(),
        "duration_min": (datetime.now(timezone.utc) - opened).total_seconds() / 60.0,
    }
    JOURNAL.record_close(trade)
    await REGISTRY.close(symbol)
    await notif_close(trade)
    push_event("WIN" if net > 0 else "LOSS",
               f"{symbol} {pos['side']} {reason} · net {net:+.3f} USDT "
               f"(gross {gross:+.3f} · fee {fee:.3f})")
    log.info("CLOSE %s %s %s net %.4f USDT (gross %.4f fee %.4f)",
             symbol, pos["side"], reason, net, gross, fee)


async def paksa_market_close(pos: dict):
    if pos["paper"]:
        return
    side = "sell" if pos["side"] == "LONG" else "buy"
    try:
        await EXCHANGE.create_order(pos["symbol"], "market", side, pos["amount"], None,
                                    {"reduceOnly": True})
    except Exception as exc:
        log.error("Force close %s gagal: %s", pos["symbol"], exc)


async def geser_breakeven(pos: dict):
    """Pindahkan SL ke entry +/- offset setelah profit trigger."""
    symbol, side = pos["symbol"], pos["side"]
    new_sl = pos["entry"] * (1 + BREAKEVEN_OFFSET_PCT) if side == "LONG" \
        else pos["entry"] * (1 - BREAKEVEN_OFFSET_PCT)
    pos["sl"] = new_sl
    pos["breakeven_done"] = True
    push_event("BREAKEVEN", f"{symbol} {side} SL dikunci di {fmt(new_sl)}")
    if pos["paper"]:
        log.info("[PAPER] Breakeven guard %s -> %s", symbol, new_sl)
        return
    close_side = "sell" if side == "LONG" else "buy"
    try:
        if pos.get("sl_id"):
            try:
                await EXCHANGE.cancel_order(pos["sl_id"], symbol)
            except Exception as exc:
                log.warning("cancel SL lama %s: %s", symbol, exc)
        order = await EXCHANGE.create_order(
            symbol, "STOP_MARKET", close_side, pos["amount"], None,
            {"stopPrice": float(EXCHANGE.price_to_precision(symbol, new_sl)),
             "reduceOnly": True, "workingType": "MARK_PRICE"})
        pos["sl_id"] = order.get("id")
        await tg_send(f"🛡️ *BREAKEVEN GUARD LOCKED* `{symbol}` {side}\n"
                      f"SL dipindah ke `{fmt(new_sl)}` (entry {BREAKEVEN_OFFSET_PCT*100:.2f}%)")
    except Exception as exc:
        log.error("Breakeven %s gagal: %s", symbol, exc)


async def position_watcher():
    while True:
        try:
            for symbol, pos in list(REGISTRY.open.items()):
                try:
                    ticker = await EXCHANGE.fetch_ticker(symbol)
                    price = float(ticker.get("last") or ticker.get("close") or 0.0)
                except Exception as exc:
                    log.warning("ticker %s: %s", symbol, exc)
                    continue
                if price <= 0:
                    continue
                pos["last_price"] = price      # dipakai dashboard untuk PnL berjalan
                side = pos["side"]

                # TradFi: tutup paksa sebelum pasar logam tutup / masuk weekend
                if TRADFI_FORCE_CLOSE and is_tradfi(symbol):
                    masih_buka, alasan_pasar = cek_pasar_tradfi(TRADFI_FORCE_CLOSE_BUFFER_MIN)
                    if not masih_buka:
                        await paksa_market_close(pos)
                        await tutup_posisi(pos, price, f"TRADFI MARKET CLOSE ({alasan_pasar})")
                        continue

                # Breakeven guard
                if not pos.get("breakeven_done", False):
                    moved = (price - pos["entry"]) / pos["entry"] if side == "LONG" \
                        else (pos["entry"] - price) / pos["entry"]
                    if moved >= BREAKEVEN_TRIGGER_PCT:
                        await geser_breakeven(pos)

                # V15.4: Cek exchange DULU sebelum putuskan TP/SL
                if not pos["paper"]:
                    still_open = await cek_posisi_bursa(symbol)
                    if still_open is None:
                        continue  # network error, skip
                    if not still_open:
                        # Posisi sudah close di exchange! Ambil harga riil
                        actual_price = price
                        try:
                            trades = await EXCHANGE.fetch_my_trades(symbol, limit=5)
                            if trades:
                                # Cari trade terakhir yang reduce-only
                                for t in reversed(trades):
                                    if t.get("reduceOnly", False) or t.get("info", {}).get("reduceOnly", False):
                                        actual_price = float(t.get("price") or price)
                                        break
                                if actual_price == price and trades:
                                    actual_price = float(trades[-1].get("price") or price)
                        except Exception as exc:
                            log.warning("fetch_my_trades %s: %s", symbol, exc)
                        # Tentukan reason berdasarkan harga riil
                        if side == "LONG":
                            reason = "TAKE PROFIT" if actual_price >= pos["entry"] else "STOP LOSS"
                        else:
                            reason = "TAKE PROFIT" if actual_price <= pos["entry"] else "STOP LOSS"
                        if pos.get("breakeven_done", False) and reason == "STOP LOSS":
                            reason = "BREAKEVEN STOP"
                        log.info("EXCHANGE_CLOSE %s %s price=%.6f reason=%s", 
                                 symbol, side, actual_price, reason)
                        await tutup_posisi(pos, actual_price, reason)
                        continue

                # Paper mode: pakai harga lokal
                hit_tp = (price >= pos["tp"] if side == "LONG" else price <= pos["tp"]) if pos.get("tp", 0) > 0 else False
                hit_sl = (price <= pos["sl"] if side == "LONG" else price >= pos["sl"]) if pos.get("sl", 0) > 0 else False
                if hit_tp or hit_sl:
                    reason = "TAKE PROFIT" if hit_tp else (
                        "BREAKEVEN STOP" if pos.get("breakeven_done", False) else "STOP LOSS")
                    await tutup_posisi(pos, price, reason)
                    continue

                # Time stop
                age = (datetime.now(timezone.utc)
                       - _safe_opened_at(pos)).total_seconds()
                if age > MAX_TRADE_AGE_SEC:
                    await paksa_market_close(pos)
                    await tutup_posisi(pos, price, "TIME STOP")
        except Exception as exc:
            log.error("watcher error: %s", exc)
        await asyncio.sleep(10.0)  # WS real-time, slow poll backup


async def cek_posisi_bursa(symbol: str):
    try:
        positions = await EXCHANGE.fetch_positions([symbol])
        for p in positions:
            if abs(float(p.get("contracts") or 0)) > 0:
                return True
        return False
    except Exception as exc:
        log.warning("cek_posisi_bursa %s: %s", symbol, exc)
        return None



async def force_cleanup_orphan():
    """V15.4: Paksa bersihkan posisi yang sudah close tapi masih di registry."""
    if DRY_RUN_MODE:
        return
    try:
        positions = await EXCHANGE.fetch_positions()
        live = {p["symbol"].replace(":USDT","") for p in positions
                if abs(float(p.get("contracts") or 0)) > 0}
        orphans = [s for s in REGISTRY.open if s.replace(":USDT","") not in live]
        for symbol in orphans:
            pos = REGISTRY.open[symbol]
            price = pos.get("last_price", pos["entry"])
            log.warning("ORPHAN CLEANUP: %s", symbol)
            await tutup_posisi(pos, price, "ORPHAN CLEANUP")
            push_event("CLEANUP", f"{symbol} orphan removed")
        if orphans:
            log.info("Cleaned %d orphan positions", len(orphans))
    except Exception as exc:
        log.error("force_cleanup gagal: %s", exc)

async def sync_exchange_positions():
    """Rekonsiliasi: kalau bursa sudah tidak punya posisi, lepaskan lock lokal."""
    while True:
        await asyncio.sleep(5.0)
        if DRY_RUN_MODE:
            continue
        try:
            positions = await EXCHANGE.fetch_positions()
            live = {p["symbol"].replace(":USDT","") for p in positions
                    if abs(float(p.get("contracts") or 0)) > 0}
            # Run orphan cleanup every cycle
            await force_cleanup_orphan()
            # Cancel orders for pairs with no position
            try:
                positions = await EXCHANGE.fetch_positions()
                live_syms = {p["symbol"].replace(":USDT","") for p in positions
                             if abs(float(p.get("contracts") or 0)) > 0}
            except Exception:
                live_syms = set()
            for symbol, pos in list(REGISTRY.open.items()):
                # Normalize symbol for comparison
                sym_clean = symbol.replace(":USDT", "")
                if sym_clean not in live:
                    try:
                        ticker = await EXCHANGE.fetch_ticker(symbol)
                        price = float(ticker.get("last") or pos.get("last_price") or pos["entry"])
                    except Exception:
                        price = pos.get("last_price", pos["entry"])
                    log.info("SYNC: %s sudah tidak di exchange -> close", symbol)
                    await tutup_posisi(pos, price, "CLOSED ON EXCHANGE")
                else:
                    # Position still live - update price
                    try:
                        ticker = await EXCHANGE.fetch_ticker(symbol)
                        pos["last_price"] = float(ticker.get("last") or 0)
                    except Exception:
                        pass
            # --- SYNC FIX: Register exchange positions missing from REGISTRY ---
            # On restart REGISTRY kosong tapi exchange punya posisi live.
            # Tanpa ini, bot kira semua slot kosong dan entry berlebihan.
            try:
                _reg_clean = {s.replace(":USDT", "") for s in REGISTRY.open}
                for _p in positions:
                    _sym = (_p.get("symbol") or "").replace(":USDT", "")
                    if not _sym or _sym in _reg_clean:
                        continue
                    _contracts = abs(float(_p.get("contracts") or 0))
                    if _contracts <= 0:
                        continue
                    _ep = float(_p.get("entryPrice") or 0)
                    _sd = "LONG" if (_p.get("side") or "") == "long" else "SHORT"
                    # Match to ASSET_PAIRS format
                    _reg_sym = None
                    for _ap in ASSET_PAIRS:
                        if _ap.replace(":USDT", "") == _sym:
                            _reg_sym = _ap
                            break
                    if not _reg_sym:
                        _reg_sym = _p.get("symbol", _sym)
                    _synced = {
                        "symbol": _reg_sym,
                        "side": _sd,
                        "entry": _ep,
                        "last_price": _ep,
                        "amount": _contracts,
                        "margin": float(_p.get("initialMargin") or 0),
                        "source": "EXCHANGE_SYNC",
                        "breakeven_done": False,
                        "tp": 0.0,
                        "sl": 0.0,
                        "opened_at": datetime.now(timezone.utc).isoformat(),
                        "paper": False,
                    }
                    async with REGISTRY.lock:
                        REGISTRY.open[_reg_sym] = _synced
                    _reg_clean.add(_sym)
                    log.warning(
                        "SYNC_REGISTER: %s %s @ %.6f qty=%.4f (posisi dari exchange, tidak ada di registry)",
                        _reg_sym, _sd, _ep, _contracts
                    )
            except Exception as _se:
                log.debug("sync register error: %s", _se)
            # --- END SYNC FIX ---
        except Exception as exc:
            # SNIPER FIX: Cancel orphan orders (orders without live position)
            try:
                all_orders = await EXCHANGE.fetch_open_orders()
                live_syms_clean = set(live_syms) if 'live_syms' in dir() else set()
                # Also get live positions fresh
                try:
                    _pos = await EXCHANGE.fetch_positions()
                    live_syms_clean = {p['symbol'].replace(':USDT','') for p in _pos
                                       if abs(float(p.get('contracts') or 0)) > 0}
                except Exception:
                    pass
                for o in all_orders:
                    o_sym = o.get('symbol','').replace(':USDT','')
                    if o_sym and o_sym not in live_syms_clean:
                        try:
                            await EXCHANGE.cancel_order(o['id'], o['symbol'])
                            log.warning('ORPHAN_ORDER_CANCELLED: %s %s %s', o['symbol'], o['side'], o['type'])
                        except Exception as e:
                            log.warning('orphan cancel fail %s: %s', o['symbol'], e)
            except Exception as e:
                log.warning('orphan order scan fail: %s', e)
            log.error("sync posisi gagal: %s", exc)


# ==============================================================================
# 12. WORKERS: kimi timer, morning briefing, dashboard
# ==============================================================================



async def pre_evaluator_worker():
    """Background task: Pre-evaluate pairs for AI cache.
    - Slot penuh → PAUSE HERMES+AEGIS (hemat API)
    - Slot tersedia → pre-evaluate (isi cache untuk instant entry)
    - ATHENA tetap jalan via kimi_timer_worker (terpisah)
    """
    PRE_EVAL_INTERVAL = 60
    PRE_EVAL_IDLE = 60
    BATCH_SIZE = 5
    BATCH_DELAY = 2.0

    await asyncio.sleep(15)

    while True:
        try:
            # ===== SLOT GATE: Pause kalau slot penuh =====
            used = REGISTRY.slots_used()
            free = REGISTRY.slots_free()
            if free <= 0:
                log.debug("pre_evaluator: slots full (%d/%d), pausing HERMES+AEGIS",
                          used, MAX_OPEN_POSITIONS)
                await asyncio.sleep(PRE_EVAL_IDLE)
                continue

            pairs = list(ASSET_PAIRS)
            evaluated = 0
            skipped = 0

            for i in range(0, len(pairs), BATCH_SIZE):
                batch = pairs[i:i+BATCH_SIZE]
                for symbol in batch:
                    # Mid-sweep: stop kalau slot terisi
                    if REGISTRY.slots_free() <= 0:
                        log.info("pre_evaluator: slots filled mid-sweep, stopping")
                        break

                    # Skip pairs yang sudah punya posisi
                    gate = REGISTRY.gate_reason(symbol)
                    if gate in ("IN_POSITION", "PENDING_EXEC", "PAIR_CAP"):
                        skipped += 1
                        continue

                    try:
                        ohlcv_5m = await EXCHANGE.fetch_ohlcv(symbol, timeframe="5m", limit=100)
                        df_5m = pd.DataFrame(ohlcv_5m, columns=["timestamp","open","high","low","close","volume"])
                        ohlcv_15m = await EXCHANGE.fetch_ohlcv(symbol, timeframe="15m", limit=50)
                        df_15m = pd.DataFrame(ohlcv_15m, columns=["timestamp","open","high","low","close","volume"])

                        ob = await EXCHANGE.fetch_order_book(symbol, limit=20)
                        bid_vol = sum(float(x[1]) for x in ob.get("bids", [])[:20])
                        ask_vol = sum(float(x[1]) for x in ob.get("asks", [])[:20])
                        ob_imbalance = bid_vol / (ask_vol + 1e-9)

                        client_h = CLIENT_KIMI or CLIENT_LLAMA
                        client_a = CLIENT_LLAMA or CLIENT_KIMI
                        result = await pre_evaluate_pair(
                            client_hermes=client_h,
                            model_hermes="deepseek/deepseek-v4-flash",
                            client_aegis=client_a,
                            model_aegis="meta-llama/llama-3.3-70b-instruct",
                            symbol=symbol,
                            df_5m=df_5m,
                            df_15m=df_15m,
                            market_ctx=ATHENA_CACHE,
                            spread_bps=5.0,
                            ob_imbalance=ob_imbalance,
                        )
                        if result:
                            evaluated += 1
                        else:
                            skipped += 1
                    except Exception as e:
                        skipped += 1
                        continue

                if i + BATCH_SIZE < len(pairs):
                    await asyncio.sleep(BATCH_DELAY)

                if REGISTRY.slots_free() <= 0:
                    break

            log.info("pre_evaluator: sweep done %d/%d slots - %d cached, %d skipped, cache: %s",
                     used, MAX_OPEN_POSITIONS, evaluated, skipped, _ai_cache_stats())

        except Exception as e:
            log.error("pre_evaluator error: %s", e)

        await asyncio.sleep(PRE_EVAL_INTERVAL)

async def kimi_timer_worker():
    while True:
        await agent1_kimi_macro()
        await asyncio.sleep(KIMI_INTERVAL_SEC)


async def morning_briefing_worker():
    sent_for = None
    while True:
        now = datetime.now(WIB)
        stamp = now.strftime("%Y-%m-%d")
        if now.hour == BRIEFING_HOUR and now.minute == 0 and sent_for != stamp:
            sent_for = stamp
            s = JOURNAL.stats(24)
            pf = "∞" if s["profit_factor"] == float("inf") else f"{s['profit_factor']:.2f}"
            best = s["best"]
            worst = s["worst"]
            best_txt = f"{best['symbol']} ({best['pnl_pct']:+.2f}%)" if best else "-"
            worst_txt = f"{worst['symbol']} ({worst['pnl_pct']:+.2f}%)" if worst else "-"
            n_tradfi = sum(1 for p in ASSET_PAIRS if is_tradfi(p))
            wr_bar = "▰" * int(s["win_rate"] // 10) + "▱" * (10 - int(s["win_rate"] // 10))
            report = (
                f"☀️ *MORNING BRIEFING* · {now.strftime('%d %b %Y')}\n"
                f"`{GARIS}`\n"
                f"⏰ {now.strftime('%H:%M')} WIB · periode 24 jam terakhir\n"
                f"`{GARIS}`\n"
                f"📊 *PERFORMA*\n"
                f"├ Trade     `{s['total']}`\n"
                f"├ W / L     `{s['win']}W / {s['loss']}L`\n"
                f"├ Win rate  `{wr_bar}` `{s['win_rate']:.1f}%`\n"
                f"└ PF        `{pf}`\n"
                f"`{GARIS}`\n"
                f"💰 *KEUANGAN*\n"
                f"├ Gross     `{s.get('pnl_gross_usdt', 0.0):+.2f}` USDT\n"
                f"├ Fee       `-{s.get('fee_usdt', 0.0):.2f}` USDT\n"
                f"├ *NET*     `{s['pnl_usdt']:+.2f}` USDT\n"
                f"└ Ekuitas   `${equity_now():,.2f}` ({EQUITY['sumber']})\n"
                f"`{GARIS}`\n"
                f"🔥 *LEADERBOARD*\n"
                f"├ 🔼 Terbaik  {best_txt}\n"
                f"└ 🔽 Terburuk {worst_txt}\n"
                f"`{GARIS}`\n"
                f"🧭 Regime    {MARKET_REGIME_GLOBAL['market_regime']}\n"
                f"🏛️ TradFi    {status_pasar_tradfi()}\n"
                f"🪙 Pair      `{len(ASSET_PAIRS)}` ({len(ASSET_PAIRS)-n_tradfi} crypto + {n_tradfi} logam)\n"
                f"🎟️ Slot      `{REGISTRY.slots_used()}/{MAX_OPEN_POSITIONS}`\n"
                f"🤖 {_ai_ringkas()}\n"
                f"🧾 Mode      {_mode_txt()} · uptime `{_uptime_txt()}`"
            )
            await tg_send(report)
        await asyncio.sleep(20.0)


# ==============================================================================
# 12B. UI ENGINE — rich TUI di terminal, log ringkas di PM2 (auto-detect)
# ==============================================================================

EVENT_FEED = deque(maxlen=14)
BOOT_TS = datetime.now(timezone.utc)
_LAST_HEARTBEAT = 0.0
LIVE = None

EVENT_IKON = {
    "ENTRY": "🚀", "WIN": "✅", "LOSS": "❌", "VETO": "🛡",
    "FAIL": "⚠", "BREAKEVEN": "🔒", "REGIME": "🧭", "MARKET": "🏛",
    "INFO": "ℹ",
}

EVENT_STYLE = {
    "ENTRY": "bold green",
    "WIN": "bold bright_green",
    "LOSS": "bold red",
    "VETO": "yellow",
    "FAIL": "bold red",
    "BREAKEVEN": "cyan",
    "REGIME": "magenta",
    "MARKET": "blue",
    "INFO": "white",
}


def push_event(kind: str, text: str):
    """Catat kejadian penting. Di TUI masuk panel EVENT FEED, di PM2 jadi 1 baris log."""
    ts = datetime.now(WIB).strftime("%H:%M:%S")
    EVENT_FEED.append((ts, kind, text))
    if not TUI_MODE:
        ikon = EVENT_IKON.get(kind, "·")
        print(f"  {ts}  {ikon}  {kind:<9} │ {text}", flush=True)


def _uptime_txt() -> str:
    secs = int((datetime.now(timezone.utc) - BOOT_TS).total_seconds())
    hours, minutes = divmod(secs // 60, 60)
    return f"{hours}j {minutes:02d}m"


def _pnl_text(value: float, suffix: str = ""):
    style = "bold green" if value > 0 else ("bold red" if value < 0 else "dim")
    return Text(f"{value:+.2f}{suffix}", style=style)


def _board_style(status: str) -> str:
    if status.startswith("🟢"):
        return "bold green"
    if status.startswith("🚨"):
        return "bold yellow"
    if status.startswith("❌"):
        return "red"
    if status.startswith("⛔"):
        return "bold red"
    if status.startswith("🔒"):
        return "yellow"
    if status.startswith("🌙"):
        return "blue"
    return "dim"


def _bar(nilai: int, total: int, lebar: int = 12) -> str:
    total = max(total, 1)
    isi = min(int(round(nilai / total * lebar)), lebar)
    return "█" * isi + "░" * (lebar - isi)


def _panel_header():
    mode = "[bold black on green] DRY-RUN [/]" if DRY_RUN_MODE else "[bold white on red] LIVE [/]"
    n_tradfi = sum(1 for p in ASSET_PAIRS if is_tradfi(p))
    kiri = Text.assemble(
        ("⚡ V15.6 ", "bold bright_cyan"),
        ("GENIUS ENGINE", "bold white"),
        ("  │  ", "dim"),
        (f"{len(ASSET_PAIRS)} pair", "cyan"),
        (f" ({len(ASSET_PAIRS)-n_tradfi}c+{n_tradfi}m)", "dim"),
        ("  │  ", "dim"),
        (f"TF {TIMEFRAME}", "cyan"),
    )
    kanan = Text.assemble(
        (datetime.now(WIB).strftime("%d %b %H:%M:%S"), "bright_white"),
        (" WIB  │  ", "dim"),
        (f"⏱ {_uptime_txt()}", "cyan"),
    )
    grid = Table.grid(expand=True)
    grid.add_column(justify="left", ratio=1)
    grid.add_column(justify="center", width=12)
    grid.add_column(justify="right", ratio=1)
    grid.add_row(kiri, mode, kanan)
    return Panel(grid, box=box.DOUBLE, border_style="bright_cyan", padding=(0, 1))


def _panel_stats():
    s = JOURNAL.stats(24)
    pf = s["profit_factor"]
    pf_txt = "∞" if pf == float("inf") else f"{pf:.2f}"
    regime = MARKET_REGIME_GLOBAL["market_regime"]
    reg_style = {"BULLISH_TREND": "bright_green", "BEARISH_TREND": "bright_red",
                 "SIDEWAYS_CHOP": "yellow"}.get(regime, "white")
    reg_ikon = {"BULLISH_TREND": "▲", "BEARISH_TREND": "▼",
                "SIDEWAYS_CHOP": "◆"}.get(regime, "●")
    p1 = Panel(f"[{reg_style}]{reg_ikon} {regime}[/]\n"
               f"[dim]{MARKET_REGIME_GLOBAL['recommended_tactic']}[/]\n"
               f"[dim]upd {MARKET_REGIME_GLOBAL['last_updated']}[/]",
               title="🧭 KOMPAS KIMI", box=box.ROUNDED,
               border_style=reg_style, padding=(0, 1))

    used = REGISTRY.slots_used()
    p2 = Panel(f"[bold bright_white]{used}[/][dim]/{MAX_OPEN_POSITIONS} slot[/]\n"
               f"[cyan]{_bar(used, MAX_OPEN_POSITIONS)}[/]\n"
               f"[dim]cd {REENTRY_COOLDOWN_SEC//60}m · re-entry {REENTRY_LIMIT_TXT}[/]",
               title="🎟️  SLOT", box=box.ROUNDED,
               border_style="cyan", padding=(0, 1))

    wr = s["win_rate"]
    wr_style = "bright_green" if wr >= 55 else ("yellow" if wr >= 45 else "bright_red")
    net_style = "bold bright_green" if s["pnl_usdt"] >= 0 else "bold bright_red"
    p3 = Panel(f"[green]{s['win']}W[/] [dim]/[/] [red]{s['loss']}L[/]  "
               f"[{wr_style}]{wr:.0f}%[/]  [dim]PF[/] {pf_txt}\n"
               f"[dim]fee[/] [red]-{s.get('fee_usdt', 0.0):.2f}[/]\n"
               f"[{net_style}]{s['pnl_usdt']:+.2f} USDT[/]",
               title="📊 24 JAM", box=box.ROUNDED,
               border_style="magenta", padding=(0, 1))

    eq_style = "bright_green" if EQUITY["sumber"] == "LIVE" else "yellow"
    p4 = Panel(f"[{eq_style}]${equity_now():,.2f}[/] [dim]{EQUITY['sumber']}[/]\n"
               f"[dim]margin[/] ${margin_now():,.2f} [dim]· notional[/] ${notional_now():,.0f}\n"
               f"[dim]lev {LEVERAGE}x · upd {EQUITY['updated']}[/]",
               title="💰 EKUITAS LIVE", box=box.ROUNDED,
               border_style=eq_style, padding=(0, 1))

    ikon = {"OK": "[green]●[/]", "READY": "[dim]○[/]", "FALLBACK": "[yellow]●[/]",
            "ERROR": "[red]●[/]", "NO KEY": "[dim]○[/]", "OFF": "[dim]○[/]"}
    p5 = Panel(f"{ikon.get(AI_STATUS['kimi'], '○')} Kimi K2.6\n"
               f"{ikon.get(AI_STATUS['deepseek'], '○')} DeepSeek V4\n"
               f"{ikon.get(AI_STATUS['llama'], '○')} Llama 3.3",
               title="🤖 ALIANSI AI", box=box.ROUNDED,
               border_style="bright_magenta", padding=(0, 1))

    tradfi = status_pasar_tradfi()
    p6 = Panel(f"{tradfi}\n"
               f"[dim]TP {TP_PCT*100:.2f}% → net {TP_NET_PCT*100:.2f}%[/]\n"
               f"[dim]SL {SL_ATR_MULT}×ATR · BE +{BREAKEVEN_OFFSET_PCT*100:.2f}%[/]",
               title="🏛️  TRADFI & RISIKO", box=box.ROUNDED,
               border_style="blue", padding=(0, 1))

    return Columns([p1, p2, p3, p4, p5, p6], equal=True, expand=True)


def _panel_positions():
    if not REGISTRY.open:
        return Panel(Align.center("[dim]— belum ada posisi terbuka —[/]"),
                     title="📌 OPEN POSITIONS", box=box.ROUNDED,
                     border_style="green", padding=(0, 1))
    tabel = Table(expand=True, box=box.SIMPLE_HEAD, pad_edge=False,
                  header_style="bold cyan", row_styles=["", "on grey11"])
    for nama, rata in (("PAIR", "left"), ("SIDE", "center"), ("ENTRY", "right"),
                       ("LAST", "right"), ("TP", "right"), ("SL", "right"),
                       ("PnL %", "right"), ("NET $", "right"), ("BE", "center"),
                       ("AGE", "right")):
        tabel.add_column(nama, justify=rata, no_wrap=True)
    for sym, p in REGISTRY.open.items():
        last = float(p.get("last_price") or p["entry"])
        pnl_pct, _gross, _fee, net, _roi = hitung_pnl(p, last)
        umur = (datetime.now(timezone.utc)
                - _safe_opened_at(p)).total_seconds() / 60.0
        panah = "▲" if p["side"] == "LONG" else "▼"
        tabel.add_row(
            Text(sym, style="bold white"),
            Text(f"{panah} {p['side']}", style="green" if p["side"] == "LONG" else "red"),
            fmt(p["entry"]), fmt(last), fmt(p["tp"]), fmt(p["sl"]),
            _pnl_text(pnl_pct, "%"), _pnl_text(net),
            "[green]🛡[/]" if p["breakeven_done"] else "[dim]·[/]",
            f"{umur:.0f}m",
        )
    return Panel(tabel, title=f"📌 OPEN POSITIONS ({len(REGISTRY.open)})",
                 box=box.ROUNDED, border_style="green", padding=(0, 1))


def _panel_radar(board: dict):
    pairs = ASSET_PAIRS[:DASHBOARD_ROWS]
    kolom = max(1, DASHBOARD_COLS)
    baris = math.ceil(len(pairs) / kolom)
    grid = Table.grid(expand=True, padding=(0, 2))
    for _ in range(kolom):
        grid.add_column(ratio=1)
    for r in range(baris):
        cells = []
        for c in range(kolom):
            idx = r + c * baris
            if idx < len(pairs):
                sym = pairs[idx]
                status = board.get(sym, "⚪ INITIALIZING")
                nama = sym.replace("/USDT", "")
                gaya = _board_style(status)
                cells.append(Text.assemble(
                    (f"{nama:<6}", "bold bright_white" if is_tradfi(sym) else "white"),
                    ("│ ", "dim"),
                    (status[:30], gaya),
                ))
            else:
                cells.append("")
        grid.add_row(*cells)
    return Panel(grid, title=f"⚡ RADAR SINYAL ({len(pairs)} pair)",
                 box=box.ROUNDED, border_style="bright_black", padding=(0, 1))


def _panel_events():
    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(width=8, no_wrap=True)
    grid.add_column(width=3, no_wrap=True)
    grid.add_column(width=10, no_wrap=True)
    grid.add_column(ratio=1)
    if not EVENT_FEED:
        grid.add_row("", "", "", "[dim]menunggu kejadian pertama...[/]")
    for ts, kind, text in list(EVENT_FEED)[-10:]:
        grid.add_row(f"[dim]{ts}[/]",
                     EVENT_IKON.get(kind, "·"),
                     Text(kind, style=EVENT_STYLE.get(kind, "white")),
                     Text(str(text)[:110], style="grey70"))
    return Panel(grid, title="📰 EVENT FEED", box=box.ROUNDED,
                 border_style="yellow", padding=(0, 1))


def _panel_footer():
    teks = Text.assemble(
        ("🟢 sinyal  ", "green"), ("🔒 cooldown  ", "yellow"),
        ("❌ veto AI  ", "red"), ("🌙 pasar tutup  ", "blue"),
        ("⚪ idle", "dim"),
        ("   │   ", "dim"), ("Ctrl+C untuk berhenti", "dim italic"),
    )
    return Align.center(teks)


def build_dashboard(board: dict):
    return Group(
        _panel_header(),
        _panel_stats(),
        _panel_positions(),
        _panel_radar(board),
        _panel_events(),
        _panel_footer(),
    )


def ui_start():
    global LIVE
    if not TUI_MODE:
        log.info("UI: mode log ringkas (tty=%s, rich=%s)", IS_TTY, RICH_OK)
        return
    try:
        LIVE = Live(Group(), console=CONSOLE, refresh_per_second=4, screen=True)
        LIVE.start()
    except Exception as exc:
        LIVE = None
        log.error("Gagal start TUI: %s -- lanjut mode log ringkas", exc)


def ui_stop():
    global LIVE
    if LIVE is not None:
        try:
            LIVE.stop()
        except Exception:
            pass
        LIVE = None


def ui_render(board: dict):
    """Dipanggil tiap akhir siklus scan."""
    global _LAST_HEARTBEAT
    if TUI_MODE and LIVE is not None:
        try:
            LIVE.update(build_dashboard(board))
        except Exception as exc:
            log.warning("render dashboard: %s", exc)
        return
    if HEARTBEAT_SEC <= 0:
        return
    now = datetime.now(timezone.utc).timestamp()
    if now - _LAST_HEARTBEAT < HEARTBEAT_SEC:
        return
    _LAST_HEARTBEAT = now
    s = JOURNAL.stats(24)
    jam = datetime.now(WIB).strftime("%H:%M:%S")
    print(
        f"  {jam}  💓  HEARTBEAT │ "
        f"slot {REGISTRY.slots_used()}/{MAX_OPEN_POSITIONS} · "
        f"eq ${equity_now():,.2f} ({EQUITY['sumber']}) · "
        f"margin ${margin_now():,.2f} · "
        f"{MARKET_REGIME_GLOBAL['market_regime']} · "
        f"24h {s['win']}W/{s['loss']}L WR {s['win_rate']:.1f}% net {s['pnl_usdt']:+.2f} · "
        f"AI {AI_STATUS['kimi']}/{AI_STATUS['deepseek']}/{AI_STATUS['llama']} · "
        f"TradFi {status_pasar_tradfi()}",
        flush=True)


# ==============================================================================
# 13. MAIN LOOP
# ==============================================================================


STOP_EVENT = None
STOP_SIGNAL = {"nama": "manual"}


def _pasang_signal_handler():
    """Tangkap SIGTERM/SIGINT (PM2 stop/restart) supaya bot pamit dulu."""
    loop = asyncio.get_running_loop()

    def _minta_berhenti(nama: str):
        STOP_SIGNAL["nama"] = nama
        if STOP_EVENT is not None and not STOP_EVENT.is_set():
            STOP_EVENT.set()

    for sig, nama in ((signal.SIGTERM, "SIGTERM"), (signal.SIGINT, "SIGINT")):
        try:
            loop.add_signal_handler(sig, _minta_berhenti, nama)
        except Exception:
            pass      # Windows / environment tanpa dukungan signal



# ==============================================================================
# 5B. WEBSOCKET USER DATA STREAM (REAL-TIME TP/SL DETECTION)
# ==============================================================================
async def user_data_stream_ws():
    import websockets as _ws_mod
    listen_key = None
    async def _get_listen_key():
        ts = str(int(time.time() * 1000))
        p = {"timestamp": ts}
        qs = "&".join(f"{k}={v}" for k,v in sorted(p.items()))
        sig = hmac.new(EXCHANGE.secret.encode(),qs.encode(),hashlib.sha256).hexdigest()
        url = f"https://fapi.binance.com/fapi/v1/listenKey?{qs}&signature={sig}"
        async with httpx.AsyncClient() as h:
            r = await h.post(url, headers={"X-MBX-APIKEY": EXCHANGE.apiKey})
            r.raise_for_status()
            return r.json()["listenKey"]
    async def _keepalive():
        while True:
            await asyncio.sleep(1800)
            try:
                await _get_listen_key()
                log.info("[WS] ListenKey keepalive OK")
            except Exception as e:
                log.warning("[WS] keepalive fail: %s", e)
    def _resolve(raw):
        for k in REGISTRY.open:
            c = k.replace("/","").replace(":USDT","")
            if c == raw: return k
        return None
    while True:
        ka = None
        try:
            listen_key = await _get_listen_key()
            log.info("[WS] Connected: %s...", listen_key[:16])
            ka = asyncio.create_task(_keepalive())
            ws_url = f"wss://fstream.binance.com/ws/{listen_key}"
            async with _ws_mod.connect(ws_url, ping_interval=20) as ws:
                log.info("[WS] Listening to User Data Stream")
                async for msg in ws:
                    try: data = json.loads(msg)
                    except (json.JSONDecodeError, KeyError): continue
                    if data.get("e") != "ORDER_TRADE_UPDATE": continue
                    o = data.get("o", {})
                    raw = o.get("s", "")
                    sym = _resolve(raw)
                    if not sym or sym not in REGISTRY.open: continue
                    st = o.get("X", "")
                    xt = o.get("x", "")
                    ot = o.get("o", "")
                    ro = o.get("R", False)
                    if st=="FILLED" and ro and xt=="TRADE":
                        if ot in ("TAKE_PROFIT_MARKET","STOP_MARKET","TAKE_PROFIT","STOP_LOSS"):
                            pos = REGISTRY.open.get(sym)
                            if not pos: continue
                            ap = float(o.get("ap","0"))
                            ep = ap if ap > 0 else pos.get("last_price", pos["entry"])
                            if "TAKE_PROFIT" in ot: reason="TAKE PROFIT"
                            elif pos.get("breakeven_done"): reason="BREAKEVEN STOP"
                            else: reason="STOP LOSS"
                            log.info("[WS] FILLED %s %s @ %.6f %s", sym, o.get("S"), ep, reason)
                            try: await tutup_posisi(pos, ep, reason)
                            except Exception as e:
                                log.error("[WS] tutup fail %s: %s", sym, e)
                                await REGISTRY.close(sym)
        except Exception as e:
            log.warning("[WS] Error: %s, retry in 10s", e)
        finally:
            if ka: ka.cancel()
        await asyncio.sleep(10)

_last_entry_time = {}  # symbol -> timestamp of last ENTRY

async def janitor_loop():
    """Bersihkan orphan orders (regular + algo/TP/SL) HANYA jika posisi SUDAH TIDAK ADA."""
    while True:
        try:
            await asyncio.sleep(12)

            # Triple check posisi
            all_syms = set()
            for check in range(3):
                try:
                    pos = await EXCHANGE.fetch_positions()
                    for p in pos:
                        amt = abs(float(p["info"].get("positionAmt", 0)))
                        if amt > 0:
                            raw = p["info"].get("symbol", "")
                            if raw:
                                all_syms.add(raw)
                except Exception:
                    pass
                if check < 2:
                    await asyncio.sleep(5)

            act = all_syms
            log.info("[JANITOR] Active positions: %s", act)

            # Cek semua pair di ASSET_PAIRS
            for pr in ASSET_PAIRS:
                if isinstance(pr, str):
                    sym_raw = pr
                elif isinstance(pr, dict):
                    sym_raw = pr.get("symbol", "")
                else:
                    sym_raw = getattr(pr, "symbol", "")
                if not sym_raw:
                    continue

                if ":" not in sym_raw and "/" in sym_raw:
                    sym = sym_raw + ":USDT"
                else:
                    sym = sym_raw

                try:
                    mid = EXCHANGE.market(sym)["id"]
                except Exception:
                    continue

                # Skip if entry just happened (exchange position delay)
                _entry_ts = _last_entry_time.get(sym_raw, 0)
                if (datetime.now(timezone.utc).timestamp() - _entry_ts) < 60:
                    log.debug("[JANITOR] Skip %s - entry just happened", mid)
                    continue
                if mid in act:
                    continue
                # Skip if position tracked in REGISTRY (may not show in exchange yet)
                _sym_clean = sym_raw.replace("/", "").replace(":USDT", "")
                if any(_sym_clean + s in REGISTRY.open or _sym_clean + s in REGISTRY.pending for s in ("/USDT", "/USDT:USDT")):
                    continue

                # Cancel regular orders
                had_orphan = False
                try:
                    oo = await EXCHANGE.fetch_open_orders(sym)
                    if oo:
                        had_orphan = True
                        await EXCHANGE.cancel_all_orders(sym)
                        log.info("[JANITOR] Cancelled %d regular orders %s", len(oo), mid)
                except Exception as e:
                    pass

                # Cancel algo orders (TP/SL)
                try:
                    r = await EXCHANGE.request("openAlgoOrders", "fapiPrivate", "GET", {"symbol": mid})
                    orders = r.get("orders", r) if isinstance(r, dict) else r
                    if orders:
                        had_orphan = True
                        for ao in orders:
                            try:
                                await EXCHANGE.request("algoOrder", "fapiPrivate", "DELETE",
                                                       {"symbol": mid, "algoId": ao["algoId"]})
                                log.info("[JANITOR] Cancelled algo %s #%s", mid, ao["algoId"])
                            except Exception as e2:
                                pass
                except Exception:
                    pass

                if had_orphan:
                    log.info("[JANITOR] Cleaned orphan for %s", mid)

        except Exception as e:
            log.error("[JANITOR] Loop error: %s", e)


async def main_loop():
    # [PATCH-E] Cleanup stale rejection cooldowns every cycle
    REJECTION_TRACKER.cleanup(max_age_sec=3600)
    global ASSET_PAIRS, STOP_EVENT
    STOP_EVENT = asyncio.Event()
    _pasang_signal_handler()
    log.info("Boot V15.6 | pairs=%s | dry_run=%s | max_pos=%s",
             len(ASSET_PAIRS), DRY_RUN_MODE, MAX_OPEN_POSITIONS)
    try:
        await EXCHANGE.load_markets()
    except Exception as exc:
        log.error("load_markets gagal: %s", exc)

    # --- Cocokkan nama pair dengan yang benar-benar ada di bursa ---
    try:
        markets = EXCHANGE.markets or {}
        if markets and AUTO_RESOLVE_PAIRS:
            final, diganti, hilang = [], [], []
            for p in ASSET_PAIRS:
                nyata = resolve_symbol(p, markets)
                if not nyata:
                    hilang.append(p)
                    continue
                if nyata != p:
                    diganti.append(f"{p}->{nyata}")
                    if is_tradfi(p):
                        TRADFI_PAIRS.add(nyata.upper())
                final.append(nyata)
            if diganti:
                log.info("Pair disesuaikan: %s", ", ".join(diganti))
                push_event("MARKET", f"{len(diganti)} pair disesuaikan namanya")
            if hilang:
                log.warning("Pair tidak ada di bursa, dilewati: %s", ", ".join(hilang))
                push_event("MARKET", f"{len(hilang)} pair dilewati: {', '.join(hilang)}")
            ASSET_PAIRS = final
            if not ASSET_PAIRS:
                log.error("Semua pair invalid, bot berhenti.")
                return
    except Exception as exc:
        log.error("Filter pair gagal: %s", exc)

    await refresh_equity(diam=False)
    log.info("Ekuitas %s: $%.2f | margin/trade $%.2f | notional $%.2f",
             EQUITY["sumber"], equity_now(), margin_now(), notional_now())

    # --- Audit setelan: cegah konfigurasi yang matematis merugi ---
    peringatan = []
    if TP_NET_PCT <= 0:
        peringatan.append(
            f"TP {TP_PCT*100:.3f}% <= fee bolak-balik {FEE_ROUNDTRIP_PCT*100:.3f}% → "
            "setiap WIN tetap RUGI.")
    elif TP_NET_PCT < FEE_ROUNDTRIP_PCT:
        peringatan.append(
            f"TP bersih cuma {TP_NET_PCT*100:.3f}% sedangkan fee {FEE_ROUNDTRIP_PCT*100:.3f}% → "
            "butuh win rate ekstrem untuk impas.")
    if BREAKEVEN_OFFSET_PCT <= FEE_ROUNDTRIP_PCT:
        peringatan.append(
            f"BREAKEVEN_OFFSET {BREAKEVEN_OFFSET_PCT*100:.3f}% <= fee "
            f"{FEE_ROUNDTRIP_PCT*100:.3f}% → breakeven stop tetap rugi.")
    margin_dibutuhkan = margin_now() * MAX_OPEN_POSITIONS
    batas_aman = equity_now() * EQUITY_SAFETY_PCT
    if margin_dibutuhkan > batas_aman:
        peringatan.append(
            f"Butuh ${margin_dibutuhkan:.2f} margin untuk {MAX_OPEN_POSITIONS} slot, "
            f"tapi batas aman cuma ${batas_aman:.2f} dari ekuitas ${equity_now():.2f} → "
            "posisi terakhir bisa ditolak bursa.")
    for w in peringatan:
        log.warning(w)
        push_event("FAIL", w)
    risiko_txt = ""
    if peringatan:
        risiko_txt = "\n⚠\ufe0f *PERINGATAN SETELAN*\n" + "\n".join(f"• {w}" for w in peringatan)

    await notif_start(risiko_txt)

    # V15.4: Clean orphan positions on boot
    await force_cleanup_orphan()
    tasks = [
        asyncio.create_task(equity_worker()),
        asyncio.create_task(kimi_timer_worker()),
        asyncio.create_task(morning_briefing_worker()),
        asyncio.create_task(position_watcher()),
        asyncio.create_task(sync_exchange_positions()),
        asyncio.create_task(user_data_stream_ws()),
        asyncio.create_task(janitor_loop()),
        asyncio.create_task(pre_evaluator_worker()),
    ]
    board = {}
    ui_start()
    push_event("INFO", f"Engine online · {len(ASSET_PAIRS)} pair · "
                       f"{'DRY-RUN' if DRY_RUN_MODE else 'LIVE'}")
    alasan_stop = "shutdown"
    try:
        while not STOP_EVENT.is_set():
            await asyncio.gather(*[proses_pair(p, board) for p in ASSET_PAIRS],
                                 return_exceptions=True)
            ui_render(board)
            try:
                await asyncio.wait_for(STOP_EVENT.wait(), timeout=SCAN_INTERVAL)
            except asyncio.TimeoutError:
                pass
        alasan_stop = f"sinyal {STOP_SIGNAL['nama']}"
    except asyncio.CancelledError:
        alasan_stop = "dibatalkan"
        raise
    except Exception as exc:
        alasan_stop = f"crash: {exc}"
        log.error("main_loop crash: %s\n%s", exc, traceback.format_exc())
        raise
    finally:
        ui_stop()
        for t in tasks:
            t.cancel()
        try:
            await notif_stop(alasan_stop)
        except Exception as exc:
            log.warning("notif_stop gagal: %s", exc)
        try:
            await EXCHANGE.close()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        print("\n🛑 Bot dihentikan oleh user.")
