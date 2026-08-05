#!/usr/bin/env python3
"""Patch V15.3 -> V15.4 GENIUS DIRECTIONAL
   Jalankan: python3 patch_v154.py
"""
import shutil, os, re

SRC = os.path.expanduser("~/bot_v15.bak-ui-1825")
DST = "bot_v15.4_genius.py"
BAK = "bot_v15.4_genius.py.bak"

if not os.path.exists(SRC):
    print(f"ERROR: {SRC} tidak ditemukan!")
    exit(1)

# Backup
shutil.copy2(SRC, DST)
shutil.copy2(DST, BAK)
print(f"OK: Copied {SRC} -> {DST}")

with open(DST, "r") as f:
    code = f.read()

# === PATCH 1: Version header ===
code = code.replace(
    'BINANCE FUTURES PRO SCALPER V15.3 "GENIUS ENGINE"',
    'BINANCE FUTURES PRO SCALPER V15.4 "GENIUS DIRECTIONAL"'
)

# === PATCH 2: NOTIONAL LIMIT config ===
NOTIONAL_CFG = '''
# --- NOTIONAL CAP (V15.4) ---
NOTIONAL_LIMIT_USDT = env_float("NOTIONAL_LIMIT_USD", 35.0)
'''
code = code.replace(
    'MARGIN_MAX_USDT = env_float("MARGIN_MAX_USDT", 0.0)              # 0 = tanpa batas atas',
    'MARGIN_MAX_USDT = env_float("MARGIN_MAX_USDT", 0.0)              # 0 = tanpa batas atas' + NOTIONAL_CFG
)

# === PATCH 3: GODMODE config ===
GM_CFG = '''
# --- GODMODE DIRECTIONAL (V15.4) ---
GODMODE_ENABLED = env_bool("GODMODE_ENABLED", True)
GODMODE_MIN_SCORE = env_int("GODMODE_MIN_SCORE", 70)
GODMODE_DIRECTIONAL_BOOST = env_int("GODMODE_DIRECTIONAL_BOOST", 15)
GODMODE_COUNTER_TREND_PENALTY = env_int("GODMODE_COUNTER_TREND_PENALTY", 20)
GODMODE_EXHAUSTION_BONUS = env_int("GODMODE_EXHAUSTION_BONUS", 10)
'''
code = code.replace(
    'AI_JSON_MODE = env_bool("AI_JSON_MODE", True)',
    'AI_JSON_MODE = env_bool("AI_JSON_MODE", True)' + GM_CFG
)

# === PATCH 4: NOTIONAL CAP in eksekusi_order ===
code = code.replace(
    '    notional = notional_now()\n    amount = notional / entry_ref',
    '    notional = notional_now()\n    if NOTIONAL_LIMIT_USDT > 0 and notional > NOTIONAL_LIMIT_USDT:\n        notional = NOTIONAL_LIMIT_USDT\n        log.info("NOTIONAL cap $%.2f for %s", NOTIONAL_LIMIT_USDT, symbol)\n    amount = notional / entry_ref'
)

# === PATCH 5: Hapus margin_txt (dead code) ===
code = code.replace(
    'def margin_txt() -> str:\n    tag = "auto" if MARGIN_AUTO else "tetap"\n    return f"${margin_now():,.2f} ({tag})"',
    '# margin_txt() removed in V15.4 (dead code)'
)

# === PATCH 6: Replace posisi_masih_terbuka ===
OLD_FUNC = '''async def posisi_masih_terbuka(symbol: str) -> bool:
    try:
        positions = await EXCHANGE.fetch_positions([symbol])
        for p in positions:
            if abs(float(p.get("contracts") or 0)) > 0:
                return True
    except Exception as exc:
        log.warning("fetch_positions %s: %s", symbol, exc)
    return False'''

NEW_FUNC = '''async def cek_posisi_bursa(symbol: str):
    """V15.4: galat jaringan -> None (treat as closed, lebih aman)."""
    try:
        positions = await EXCHANGE.fetch_positions([symbol])
        for p in positions:
            if abs(float(p.get("contracts") or 0)) > 0:
                return True
        return False
    except Exception as exc:
        log.warning("cek_posisi_bursa %s: %s", symbol, exc)
        return None'''

code = code.replace(OLD_FUNC, NEW_FUNC)
code = code.replace(
    'still_open = await posisi_masih_terbuka(symbol)',
    'still_open = await cek_posisi_bursa(symbol)'
)

# === PATCH 7: GODMODE class + evaluate function ===
GM_CLASS = '''
# ==============================================================================
# 5C. GODMODE DIRECTIONAL (V15.4) — LONG di lembah, SHORT di pucuk
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
        h, l = df["high"].values, df["low"].values
        self.swing_highs, self.swing_lows = [], []
        for i in range(2, len(df) - 2):
            if h[i] > h[i-1] and h[i] > h[i-2] and h[i] > h[i+1] and h[i] > h[i+2]:
                self.swing_highs.append({"idx": i, "price": h[i]})
            if l[i] < l[i-1] and l[i] < l[i-2] and l[i] < l[i+1] and l[i] < l[i+2]:
                self.swing_lows.append({"idx": i, "price": l[i]})
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

    def strength(self):
        if len(self.swing_highs) < 2 or len(self.swing_lows) < 2:
            return 50
        hp = [x["price"] for x in self.swing_highs[-4:]]
        lp = [x["price"] for x in self.swing_lows[-4:]]
        if len(hp) < 2 or len(lp) < 2:
            return 50
        bull = total = 0
        for i in range(1, len(hp)):
            total += 1
            if hp[i] > hp[i-1]: bull += 1
        for i in range(1, len(lp)):
            total += 1
            if lp[i] > lp[i-1]: bull += 1
        if total == 0: return 50
        c = bull / total
        if self.trend == "bullish": return int(50 + c * 50)
        elif self.trend == "bearish": return int(50 + (1 - c) * 50)
        return 50

MARKET_STRUCTURE = MarketStructure()

def godmode_exhaustion(df):
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / (loss + 1e-9)
    rsi = float((100.0 - (100.0 / (1.0 + rs))).iloc[-1])
    if rsi < 30: return "oversold", rsi
    elif rsi > 70: return "overbought", rsi
    return "neutral", rsi

def godmode_evaluate(action, m, df):
    if not GODMODE_ENABLED:
        return action, 80, ["GODMODE off"]
    score = 50
    reasons = []
    # base: stoch RSI
    if action == "LONG" and m["k"] < 35:
        score += 15; reasons.append(f"oversold K={m['k']:.0f}")
    elif action == "SHORT" and m["k"] > 65:
        score += 15; reasons.append(f"overbought K={m['k']:.0f}")
    # K vs D crossover
    if action == "LONG" and m["k"] > m["d"]:
        score += 10; reasons.append("K>D cross")
    elif action == "SHORT" and m["k"] < m["d"]:
        score += 10; reasons.append("K<D cross")
    # trend alignment
    trend = MARKET_STRUCTURE.trend
    price = m["live"]
    if action == "LONG" and (trend == "bullish" or MARKET_STRUCTURE.near_support(price)):
        score += GODMODE_DIRECTIONAL_BOOST
        reasons.append(f"aligned +{GODMODE_DIRECTIONAL_BOOST}")
        if MARKET_STRUCTURE.near_support(price):
            score += GODMODE_EXHAUSTION_BONUS
            reasons.append("at support")
    elif action == "SHORT" and (trend == "bearish" or MARKET_STRUCTURE.near_resistance(price)):
        score += GODMODE_DIRECTIONAL_BOOST
        reasons.append(f"aligned +{GODMODE_DIRECTIONAL_BOOST}")
        if MARKET_STRUCTURE.near_resistance(price):
            score += GODMODE_EXHAUSTION_BONUS
            reasons.append("at resistance")
    elif (action == "LONG" and trend == "bearish") or (action == "SHORT" and trend == "bullish"):
        score -= GODMODE_COUNTER_TREND_PENALTY
        reasons.append(f"counter-trend -{GODMODE_COUNTER_TREND_PENALTY}")
    # exhaustion
    if len(df) >= 20:
        ex, rsi = godmode_exhaustion(df)
        if action == "LONG" and ex == "oversold":
            score += GODMODE_EXHAUSTION_BONUS; reasons.append(f"RSI {rsi:.0f}")
        elif action == "SHORT" and ex == "overbought":
            score += GODMODE_EXHAUSTION_BONUS; reasons.append(f"RSI {rsi:.0f}")
    score = max(0, min(100, score))
    if score >= GODMODE_MIN_SCORE:
        return action, score, reasons
    return None, score, reasons + [f"SKIP {score}<{GODMODE_MIN_SCORE}"]
'''

code = code.replace(
    '# ==============================================================================\n# 8. EKSEKUSI ORDER',
    GM_CLASS + '\n# ==============================================================================\n# 8. EKSEKUSI ORDER'
)

# === PATCH 8: GODMODE hook in proses_pair ===
OLD_PROSES = '''    if side is None:
        tag = "IN POSITION" if symbol in REGISTRY.open else "IDLE"
        board[symbol] = f"\\u26aa  {tag} [K:{m[\'k\']:.1f}|D:{m[\'d\']:.1f}]"
        return
    # GATE JAM PASAR TRADFI'''

NEW_PROSES = '''    if side is None:
        tag = "IN POSITION" if symbol in REGISTRY.open else "IDLE"
        board[symbol] = f"\\u26aa  {tag} [K:{m[\'k\']:.1f}|D:{m[\'d\']:.1f}]"
        return
    # --- GODMODE DIRECTIONAL (V15.4) ---
    if GODMODE_ENABLED:
        try:
            _ohlcv = await EXCHANGE.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=100)
            _df = pd.DataFrame(_ohlcv, columns=["timestamp","open","high","low","close","volume"])
            MARKET_STRUCTURE.analyze(_df)
        except Exception:
            _df = pd.DataFrame()
        side_gm, gm_score, gm_reasons = godmode_evaluate(side, m, _df)
        gm_txt = " | ".join(gm_reasons[:3])
        log.info("GODMODE %s %s score=%d %s", symbol, side, gm_score, gm_txt)
        if side_gm is None:
            board[symbol] = f"\\U0001f7e1 GODMODE [{gm_score}] {gm_txt[:20]}"
            return
    # GATE JAM PASAR TRADFI'''

code = code.replace(OLD_PROSES, NEW_PROSES)

# === PATCH 9: Filter ATR nol ===
OLD_ATR_CHECK = '''    if not all(map(math.isfinite, (k, d, atr))):
        raise ValueError("indikator NaN")
    return {'''

NEW_ATR_CHECK = '''    if not all(map(math.isfinite, (k, d, atr))):
        raise ValueError("indikator NaN")
    if atr <= 0:
        raise ValueError("ATR nol/negatif -> skip pair ini")
    return {'''

code = code.replace(OLD_ATR_CHECK, NEW_ATR_CHECK)

# === SAVE ===
with open(DST, "w") as f:
    f.write(code)

new_lines = code.count("\n")
print(f"\n=== PATCH SELESAI ===")
print(f"File: {DST}")
print(f"Baris: {new_lines}")
print(f"Syntax check...")

import py_compile
try:
    py_compile.compile(DST, doraise=True)
    print("SYNTAX: OK")
except py_compile.PyCompileError as e:
    print(f"SYNTAX ERROR: {e}")
    print("Restore dari backup...")
    shutil.copy2(BAK, DST)
    print("Restored. Fix error manual.")
