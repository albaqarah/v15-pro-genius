"""
GODMODE V2 ENGINE - PATCHED v15.8.1
"""
import json, re, asyncio, logging
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta

log = logging.getLogger("GMv2")

# ============================================================
# AI RESULT CACHE - Pre-evaluate & instant signal response
# ============================================================
_AI_CACHE = {}
_AI_CACHE_TTL = 300  # seconds - cache valid for 2 minutes

def _ai_cache_get(key):
    """Get cached AI result if still fresh."""
    entry = _AI_CACHE.get(key)
    if entry and (time.time() - entry["ts"]) < _AI_CACHE_TTL:
        return entry
    if entry:
        del _AI_CACHE[key]  # expired
    return None

def _ai_cache_set(key, hermes=None, aegis=None):
    """Store AI result in cache."""
    if key not in _AI_CACHE:
        _AI_CACHE[key] = {"ts": time.time(), "hermes": None, "aegis": None}
    if hermes is not None:
        _AI_CACHE[key]["hermes"] = hermes
    if aegis is not None:
        _AI_CACHE[key]["aegis"] = aegis
    _AI_CACHE[key]["ts"] = time.time()

def _ai_cache_stats():
    """Return cache stats for logging."""
    total = len(_AI_CACHE)
    fresh = sum(1 for v in _AI_CACHE.values() if (time.time() - v["ts"]) < _AI_CACHE_TTL)
    return f"total={total} fresh={fresh}"

WIB = timezone(timedelta(hours=7))

FEATURE_WEIGHTS = {
    "trend": 25, "momentum": 20, "mtf_alignment": 15,
    "liquidity": 15, "volume": 10, "volatility": 10, "session": 5,
}
MEAN_REVERSION_WEIGHTS = {
    "trend": 5, "momentum": 30, "mtf_alignment": 5,
    "liquidity": 30, "volume": 10, "volatility": 10, "session": 10,
}
TIER_THRESHOLDS = {"sniper": 90, "execute": 75, "watch": 71, "reject": 0}

def extract_json_robust(text):
    if not text:
        return {}
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    depth, start = 0, -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0: start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start != -1:
                try: return json.loads(text[start:i+1])
                except Exception: start = -1
    try:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m: return json.loads(m.group(0))
    except Exception:
        pass
    return {}

def safe_float(v, d=50.0):
    try: return float(v)
    except (ValueError, TypeError): return d

def safe_int(v, d=50):
    try: return int(float(v))
    except (ValueError, TypeError): return d

class FeatureEngine:
    def compute(self, df_5m, df_15m):
        if len(df_5m) < 50 or len(df_15m) < 20:
            return self._empty()
        try:
            r = {}
            r.update(self._trend(df_5m))
            r.update(self._momentum(df_5m))
            r.update(self._volatility(df_5m))
            r.update(self._volume(df_5m))
            r.update(self._mtf(df_5m, df_15m))
            r.update(self._structure(df_5m))
            r.update(self._session())
            r["_price"] = float(df_5m["close"].iloc[-1])
            r["setup_type"] = self._detect_setup_type(r)
            return r
        except Exception as e:
            log.warning("FeatureEngine: %s", e)
            return self._empty()

    def _detect_setup_type(self, features):
        rsi = features.get("rsi5", 50)
        sk = features.get("stoch_k", 50)
        ns = features.get("near_support", 0)
        nr = features.get("near_resistance", 0)
        if rsi < 20 and ns > 30: return "MEAN_REVERSION"
        if rsi < 30 and sk < 30 and ns > 50: return "MEAN_REVERSION"
        if rsi > 80 and nr > 30: return "MEAN_REVERSION"
        if rsi > 70 and sk > 70 and nr > 50: return "MEAN_REVERSION"
        return "TREND_CONTINUATION"

    def _empty(self):
        base = {k: 50.0 for k in [
            "trend_score","momentum_score","mtf_alignment_score",
            "volatility_score","volume_score","session_score",
            "rsi5","stoch_k","stoch_k_15m","atr_pct","atr_percentile",
            "volume_ratio","structure_score","near_support","near_resistance",
            "ema_slope","rsi_slope","macd_hist","bb_width","_price"
        ]}
        base["setup_type"] = "TREND_CONTINUATION"
        return base

    def _rsi(self, s, p=5):
        d = s.diff()
        g = d.where(d > 0, 0.0).rolling(p).mean()
        l = (-d.where(d < 0, 0.0)).rolling(p).mean()
        return 100.0 - 100.0 / (1.0 + g / (l + 1e-9))

    def _stoch_rsi(self, s, p=7, sk=3, sd=3):
        rsi = self._rsi(s, p)
        rmin = rsi.rolling(p).min()
        rmax = rsi.rolling(p).max()
        stoch = (rsi - rmin) / (rmax - rmin + 1e-9) * 100
        k = stoch.rolling(sk).mean()
        d = k.rolling(sd).mean()
        return float(k.iloc[-1]), float(d.iloc[-1])

    def _trend(self, df):
        c = df["close"]
        p = float(c.iloc[-1])
        e20 = float(c.ewm(span=20).mean().iloc[-1])
        e50 = float(c.ewm(span=50).mean().iloc[-1])
        if p > e20 > e50: al = 80
        elif p < e20 < e50: al = 20
        else: al = 50
        slope = (e20 - float(c.ewm(span=20).mean().iloc[-5])) / e20 * 10000
        slope_s = 50 + np.clip(slope * 5, -50, 50)
        score = np.clip(al * 0.6 + slope_s * 0.4, 0, 100)
        return {"trend_score": round(float(score),1), "ema_slope": round(float(slope_s),1)}

    def _momentum(self, df):
        c = df["close"]
        rsi = float(self._rsi(c, 5).iloc[-1])
        rsi_s = float(self._rsi(c, 5).iloc[-1] - self._rsi(c, 5).iloc[-3]) if len(c) > 5 else 0
        sk, sd = self._stoch_rsi(c)
        e12 = c.ewm(span=12).mean()
        e26 = c.ewm(span=26).mean()
        h = (e12 - e26) - (e12 - e26).ewm(span=9).mean()
        return {"rsi5": round(rsi,1), "rsi_slope": round(rsi_s,2),
                "stoch_k": round(sk,1), "stoch_d": round(sd,1),
                "macd_hist": round(float(h.iloc[-1]),6),
                "momentum_score": round(rsi,1)}

    def _volatility(self, df):
        h, l, c = df["high"], df["low"], df["close"]
        tr = pd.concat([h-l, (h-c.shift(1)).abs(), (l-c.shift(1)).abs()], axis=1).max(axis=1)
        atr = float(tr.rolling(14).mean().iloc[-1])
        p = float(c.iloc[-1])
        atr_pct = atr / p * 100 if p > 0 else 0
        atr_s = tr.rolling(14).mean()
        atr_pctl = float((atr_s.iloc[-1] > atr_s).sum() / len(atr_s) * 100)
        sma = c.rolling(20).mean()
        std = c.rolling(20).std()
        bbw = float(((sma + 2*std).iloc[-1] - (sma - 2*std).iloc[-1]) / sma.iloc[-1] * 100)
        return {"atr": round(atr,6), "atr_pct": round(atr_pct,4),
                "atr_percentile": round(atr_pctl,1), "bb_width": round(bbw,2),
                "volatility_score": round(np.clip(atr_pctl,0,100),1)}

    def _volume(self, df):
        v = df["volume"]
        r = float(v.iloc[-1]) / (float(v.rolling(20).mean().iloc[-1]) + 1e-9)
        return {"volume_ratio": round(r,2), "volume_spike": r > 1.5,
                "volume_score": round(float(np.clip(r*40,0,100)),1)}

    def _mtf(self, df5, df15):
        r5 = float(self._rsi(df5["close"], 5).iloc[-1])
        r15 = float(self._rsi(df15["close"], 14).iloc[-1])
        sk15, _ = self._stoch_rsi(df15["close"])
        e20_5 = float(df5["close"].ewm(span=20).mean().iloc[-1])
        e20_15 = float(df15["close"].ewm(span=20).mean().iloc[-1])
        p = float(df5["close"].iloc[-1])
        a5 = p > e20_5
        a15 = p > e20_15
        if a5 and a15: ms = 80
        elif not a5 and not a15: ms = 20
        else: ms = 50
        return {"mtf_alignment_score": round(float(ms),1),
                "rsi_15m": round(r15,1), "stoch_k_15m": round(sk15,1)}

    def _structure(self, df):
        h, l, p = df["high"].values, df["low"].values, float(df["close"].iloc[-1])
        sh, sl = [], []
        for i in range(2, len(df)-2):
            if h[i]>h[i-1] and h[i]>h[i-2] and h[i]>h[i+1] and h[i]>h[i+2]: sh.append(h[i])
            if l[i]<l[i-1] and l[i]<l[i-2] and l[i]<l[i+1] and l[i]<l[i+2]: sl.append(l[i])
        ns = max(0, 100 - min(abs(p-s)/p*20000 for s in sl[-3:]) if sl else 0)
        nr = max(0, 100 - min(abs(p-r)/p*20000 for r in sh[-3:]) if sh else 0)
        sc = 50
        if len(sh)>=2 and len(sl)>=2:
            if sh[-1]>sh[-2] and sl[-1]>sl[-2]: sc = 75
            elif sh[-1]<sh[-2] and sl[-1]<sl[-2]: sc = 25
        return {"structure_score": round(sc,1), "near_support": round(ns,1),
                "near_resistance": round(nr,1)}

    def _session(self):
        h = datetime.now(WIB).hour
        if 15<=h<21: return {"session":"LONDON","session_score":90}
        elif 21<=h or h<3: return {"session":"NEW_YORK","session_score":85}
        elif 3<=h<7: return {"session":"OVERLAP","session_score":95}
        else: return {"session":"ASIA","session_score":70}

FEATURE_ENGINE = FeatureEngine()

class GodModeScorer:
    def __init__(self):
        self.weights = dict(FEATURE_WEIGHTS)

    def _detect_setup_type(self, features, direction):
        rsi = features.get("rsi5", 50)
        sk = features.get("stoch_k", 50)
        sk15 = features.get("stoch_k_15m", 50)
        ns = features.get("near_support", 0)
        nr = features.get("near_resistance", 0)
        if direction == "LONG":
            # Strong: stoch_K extreme (primary sensor signal)
            if sk < 15 and ns > 30: return "MEAN_REVERSION"
            # Strong: RSI extreme
            if rsi < 20 and ns > 30: return "MEAN_REVERSION"
            # Moderate: both RSI and stoch_K oversold + near support
            if rsi < 35 and sk < 35 and ns > 50: return "MEAN_REVERSION"
            # Multi-TF: stoch_K oversold on both 5m and 15m
            if sk < 20 and sk15 < 30 and ns > 20: return "MEAN_REVERSION"
        else:
            # Strong: stoch_K extreme (primary sensor signal)
            if sk > 85 and nr > 30: return "MEAN_REVERSION"
            # Strong: RSI extreme
            if rsi > 80 and nr > 30: return "MEAN_REVERSION"
            # Moderate: both RSI and stoch_K overbought + near resistance
            if rsi > 65 and sk > 65 and nr > 50: return "MEAN_REVERSION"
            # Multi-TF: stoch_K overbought on both 5m and 15m
            if sk > 80 and sk15 > 70 and nr > 20: return "MEAN_REVERSION"
        return "TREND_CONTINUATION"

    def score(self, features, direction):
        setup_type = self._detect_setup_type(features, direction)
        w = dict(MEAN_REVERSION_WEIGHTS) if setup_type == "MEAN_REVERSION" else dict(self.weights)
        breakdown = {"setup_type": setup_type}

        raw_trend = features.get("trend_score", 50)
        if setup_type == "MEAN_REVERSION":
            breakdown["trend"] = 50
        else:
            breakdown["trend"] = raw_trend if direction == "LONG" else 100 - raw_trend

        rsi = features.get("rsi5", 50)
        slope = features.get("rsi_slope", 0)
        if setup_type == "MEAN_REVERSION":
            sk_val = features.get("stoch_k", 50)
            # Blend RSI5 with stoch_K: stoch_K often leads RSI in extremes
            if direction == "LONG":
                rsi_signal = (100 - rsi)
                stoch_signal = (100 - sk_val)
                extreme_signal = max(rsi_signal, stoch_signal)
                mom = extreme_signal * 0.7 + max(0, slope * 10 + 50) * 0.3
            else:
                rsi_signal = rsi
                stoch_signal = sk_val
                extreme_signal = max(rsi_signal, stoch_signal)
                mom = extreme_signal * 0.7 + max(0, -slope * 10 + 50) * 0.3
        else:
            if direction == "LONG":
                mom = (100 - rsi) * 0.6 + max(0, slope * 10 + 50) * 0.4
            else:
                mom = rsi * 0.6 + max(0, -slope * 10 + 50) * 0.4
        breakdown["momentum"] = np.clip(mom, 0, 100)

        mtf = features.get("mtf_alignment_score", 50)
        if setup_type == "MEAN_REVERSION":
            breakdown["mtf_alignment"] = 50
        else:
            breakdown["mtf_alignment"] = mtf if direction == "LONG" else 100 - mtf

        ns = features.get("near_support", 0)
        nr = features.get("near_resistance", 0)
        raw_liq = np.clip(ns if direction == "LONG" else nr, 0, 100)
        breakdown["liquidity"] = np.clip(raw_liq * 1.1, 0, 100) if setup_type == "MEAN_REVERSION" else raw_liq

        vs = features.get("volume_score", 50)
        spike = features.get("volume_spike", False)
        breakdown["volume"] = min(100, vs + (15 if spike else 0))

        vol = features.get("volatility_score", 50)
        atr_pct = features.get("atr_pct", 0.1)
        if 0.05 < atr_pct < 0.3: v = 70
        elif atr_pct < 0.05: v = 30
        else: v = 40
        breakdown["volatility"] = (v + vol) / 2

        breakdown["session"] = features.get("session_score", 50)

        total = sum(breakdown.get(k, 50) * (wt / 100.0) for k, wt in w.items())
        score = np.clip(total, 0, 100)

        if setup_type == "MEAN_REVERSION":
            if direction == "LONG" and rsi < 10: score = min(100, score + 5)
            elif direction == "SHORT" and rsi > 90: score = min(100, score + 5)

        if score >= TIER_THRESHOLDS["sniper"]: tier = "SNIPER"
        elif score >= TIER_THRESHOLDS["execute"]: tier = "EXECUTE"
        elif score >= TIER_THRESHOLDS["watch"]: tier = "WATCH"
        else: tier = "REJECT"

        return round(float(score), 1), tier, breakdown

    def best_direction(self, features):
        sc_l, tl, bl = self.score(features, "LONG")
        sc_s, ts, bs = self.score(features, "SHORT")
        if sc_l > sc_s: return "LONG", sc_l, tl, bl
        elif sc_s > sc_l: return "SHORT", sc_s, ts, bs
        else: return "NEUTRAL", sc_l, tl, bl

GM_SCORER = GodModeScorer()

ATHENA_SYSTEM = """You are ATHENA, Market Intelligence module for a crypto futures system.
You classify broad market conditions using the rules below. You do not trade and never mention a specific pair.
You run every 5 minutes; your output is cached and reused across all pairs until your next update.

CLASSIFICATION RULES - apply in order top to bottom, use the FIRST rule that matches:
1. market_regime = TRENDING_UP if btc.trend=UP AND btc.ema_alignment=BULLISH AND eth.trend != DOWN
2. market_regime = TRENDING_DOWN if btc.trend=DOWN AND btc.ema_alignment=BEARISH AND eth.trend != UP
3. market_regime = TRANSITIONING if btc.trend != eth.trend
4. market_regime = CHOPPY if none of the above matched

- bias = LONG_LEANING if TRENDING_UP; SHORT_LEANING if TRENDING_DOWN; NEUTRAL otherwise
- bias_strength = 70-100 if TRENDING_UP/DOWN; 40-69 if TRANSITIONING; 0-39 if CHOPPY
- volatility_state = same as volatility_raw input, unchanged
- session_quality = 70-100 if LONDON/NY/OVERLAP, 40-69 if ASIA; subtract 20 if EXTREME volatility
- confidence = 75-90 if btc and eth agree; 45-65 if they disagree or EXTREME volatility; <=40 if any input missing

DO NOT default to CHOPPY or NEUTRAL out of caution. Apply rules literally and commit to the result.
reasoning: max 15 words, must name the ONE input value that drove your classification.

OUTPUT - return ONLY this JSON object. No markdown fences, no text before or after:
{"market_regime":"TRENDING_UP"|"TRENDING_DOWN"|"CHOPPY"|"TRANSITIONING","bias":"LONG_LEANING"|"SHORT_LEANING"|"NEUTRAL","bias_strength":0-100,"volatility_state":"LOW"|"NORMAL"|"ELEVATED"|"EXTREME","session_quality":0-100,"confidence":0-100,"reasoning":"string, max 15 words","warnings":["string"]}

EXAMPLES

Input: {"btc":{"trend":"UP","ema_alignment":"BULLISH","atr_percentile":65},"eth":{"trend":"UP","ema_alignment":"BULLISH","atr_percentile":60},"funding_rate_aggregate":0.01,"open_interest_change_pct":2.1,"volatility_raw":"NORMAL","session":"NY"}
Output: {"market_regime":"TRENDING_UP","bias":"LONG_LEANING","bias_strength":85,"volatility_state":"NORMAL","session_quality":90,"confidence":85,"reasoning":"BTC and ETH both trending up, EMA bullish","warnings":[]}

Input: {"btc":{"trend":"FLAT","ema_alignment":"MIXED","atr_percentile":35},"eth":{"trend":"FLAT","ema_alignment":"MIXED","atr_percentile":40},"funding_rate_aggregate":0.002,"open_interest_change_pct":0.3,"volatility_raw":"LOW","session":"ASIA"}
Output: {"market_regime":"CHOPPY","bias":"NEUTRAL","bias_strength":20,"volatility_state":"LOW","session_quality":50,"confidence":75,"reasoning":"BTC and ETH both flat, EMA mixed, low volatility","warnings":[]}

Input: {"btc":{"trend":"UP","ema_alignment":"BULLISH","atr_percentile":55},"eth":{"trend":"DOWN","ema_alignment":"BEARISH","atr_percentile":50},"funding_rate_aggregate":-0.01,"open_interest_change_pct":-1.5,"volatility_raw":"ELEVATED","session":"LONDON"}
Output: {"market_regime":"TRANSITIONING","bias":"NEUTRAL","bias_strength":45,"volatility_state":"ELEVATED","session_quality":70,"confidence":60,"reasoning":"BTC up but ETH down, trends disagree","warnings":["btc_eth_divergence"]}"""


async def pre_evaluate_pair(client_hermes, model_hermes, client_aegis, model_aegis,
                             symbol, df_5m, df_15m, market_ctx, spread_bps, ob_imbalance):
    """Pre-evaluate a symbol for both directions and cache results.
    Call this from bot background task every 30s for all watched symbols.
    When sensor later detects extreme, pipeline uses cached result instantly.
    """
    features = FEATURE_ENGINE.compute(df_5m, df_15m)
    if not features or features.get("_price", 0) <= 0:
        return 0

    direction, gm_score, gm_tier, breakdown = GM_SCORER.best_direction(features)
    if direction == "NEUTRAL" or gm_score < TIER_THRESHOLDS["watch"]:
        return 0

    setup_type = breakdown.get("setup_type", "TREND_CONTINUATION")
    athena_out = market_ctx or {"market_regime":"CHOPPY","bias":"NEUTRAL","confidence":30}

    # Pre-evaluate the primary direction
    cache_key = f"{symbol}:{direction}:{setup_type}"
    cached = _ai_cache_get(cache_key)
    if cached and cached.get("hermes") and cached.get("aegis"):
        return 0  # Already cached

    try:
        hermes_out = await run_hermes(client_hermes, model_hermes, symbol, direction, features, athena_out, setup_type)
        if hermes_out.get("setup_score", 0) >= 35:
            aegis_out = await run_aegis(client_aegis, model_aegis, symbol, direction, features, athena_out, hermes_out, spread_bps, ob_imbalance, setup_type)
            _ai_cache_set(cache_key, hermes=hermes_out, aegis=aegis_out)
            log.debug("pre_evaluate %s %s: cached (hermes=%s)", symbol, direction, hermes_out.get("setup_score", 0))
            return 1
    except Exception as e:
        log.debug("pre_evaluate %s %s error: %s", symbol, direction, e)
    return 0

async def run_athena(client, model, btc_data, eth_data, funding, oi_change, session, volatility_raw="NORMAL"):
    prompt = json.dumps({
        "btc": btc_data if isinstance(btc_data, dict) else {"trend":"?","ema_alignment":"?","atr_percentile":50},
        "eth": eth_data if isinstance(eth_data, dict) else {"trend":"?","ema_alignment":"?","atr_percentile":50},
        "funding_rate_aggregate": funding,
        "open_interest_change_pct": oi_change,
        "volatility_raw": volatility_raw,
        "session": session,
    })
    default = {"market_regime":"CHOPPY","bias":"NEUTRAL","bias_strength":30,
               "volatility_state":"NORMAL","session_quality":50,"confidence":30,
               "reasoning":"fallback - AI unavailable","warnings":["athena_fallback"]}
    if client is None: return default
    try:
        res = await asyncio.to_thread(
            client.chat.completions.create, model=model,
            messages=[{"role":"system","content":ATHENA_SYSTEM},{"role":"user","content":prompt}],
            max_tokens=450, temperature=0.1)
        msg = res.choices[0].message
        raw = msg.content
        if raw is None and hasattr(msg, "reasoning_content"): raw = msg.reasoning_content
        if raw is None: raw = str(msg)
        log.info("ATHENA_RAW: %s", str(raw)[:500])
        data = extract_json_robust(raw)
        if not data:
            log.warning("ATHENA: empty parse, raw=%s", str(raw)[:300])
            return default
        for k, v in default.items(): data.setdefault(k, v)
        return data
    except Exception as e:
        log.error("ATHENA error: %s", e)
        return default

HERMES_SYSTEM = """You are HERMES, Setup Probability Analyst.
You evaluate ONE pair using pre-computed normalized features.
You do not see raw charts. Called only after GodMode score cleared threshold.
Your job is an independent statistical read.

RULES:
1. Reason only from features, market_context, and setup_type given. Never assume.
2. If features conflict (trend high but volume low), reflect conflict in lower setup_score.
3. For TREND_CONTINUATION setups: set late_entry=true if momentum suggests move already extended.
4. For MEAN_REVERSION setups: late_entry is less relevant (you are catching a reversal, not chasing momentum). Only set late_entry=true if RSI/Stoch has already begun reverting significantly from extreme.
5. For MEAN_REVERSION: base expected_rr on distance to opposite S/R level, not trend continuation.
6. Never use absolute language.
7. strengths/weaknesses: max 3 items, each <= 12 words.

Return ONLY this JSON:
{"setup_score":0-100,"probability_estimate":0-100,"expected_rr":number,"late_entry":true|false,"strengths":["string"],"weaknesses":["string"],"confidence":0-100}"""

async def run_hermes(client, model, symbol, direction, features, market_ctx, setup_type="TREND_CONTINUATION"):
    prompt = f"INPUT:\nSymbol: {symbol}\nDirection: {direction}\nSetup Type: {setup_type}\nFeatures: {json.dumps({k:v for k,v in features.items() if k not in ('_price','setup_type')})}\nMarket Context: {json.dumps(market_ctx)}"
    empty = {"setup_score":50,"probability_estimate":40,"expected_rr":1.0,
             "late_entry":False,"strengths":[],"weaknesses":["AI unavailable - moderate fallback"],"confidence":30}
    if client is None:
        log.warning("HERMES: client None -> fail-closed")
        return empty
    try:
        res = await asyncio.to_thread(
            client.chat.completions.create, model=model,
            messages=[{"role":"system","content":HERMES_SYSTEM},{"role":"user","content":prompt}],
            max_tokens=400, temperature=0.15)
        msg = res.choices[0].message
        raw = msg.content
        # DeepSeek models sometimes put output in reasoning_content or reasoning
        if raw is None and hasattr(msg, "reasoning_content"):
            raw = msg.reasoning_content
        if raw is None and hasattr(msg, "reasoning"):
            raw = msg.reasoning
        if raw is None:
            raw = str(msg)
        log.info("HERMES_RAW %s: %s", symbol, str(raw)[:400])
        data = extract_json_robust(raw)
        if not data or "setup_score" not in data:
            # DeepSeek V4 Flash puts analysis in reasoning, not content.
            # Try to extract score/confidence from reasoning text directly.
            reasoning_text = str(raw) if raw else ""
            rt = reasoning_text.lower()

            # Determine sentiment from reasoning keywords
            strong_bull = ["extreme overbought", "stoch_k=100", "stoch_k 100",
                           "rsi5=8", "rsi5=9", "strongly overbought",
                           "rolling over", "exhaustion likely", "bearish divergence"]
            moderate_bull = ["overbought", "supports short", "short setup",
                             "mean reversion", "resistance level", "near resistance",
                             "volume spike", "high rsi", "high stoch"]
            strong_bear = ["not overbought", "weak setup", "insufficient signal",
                           "momentum still strong", "trend too strong",
                           "doesn.*t support short", "uptrend intact"]
            moderate_bear = ["low volume", "no confirmation", "mixed signals",
                             "neutral rsi", "unclear direction"]

            sb = sum(1 for kw in strong_bull if __import__('re').search(kw, rt))
            mb = sum(1 for kw in moderate_bull if __import__('re').search(kw, rt))
            sbr = sum(1 for kw in strong_bear if __import__('re').search(kw, rt))
            mbr = sum(1 for kw in moderate_bear if __import__('re').search(kw, rt))

            bull_score = sb * 3 + mb
            bear_score = sbr * 3 + mbr

            if bull_score > bear_score:
                score = min(75, 50 + bull_score * 4)
                conf = min(70, 35 + bull_score * 5)
                st = "MEAN_REVERSION" if mb > 1 else "TREND_CONTINUATION"
                extracted = {"setup_score": score, "confidence": conf,
                             "setup_type": st, "direction_agrees": True,
                             "late_entry": False,
                             "rationale": "extracted from reasoning"}
                log.info("HERMES %s: extracted from reasoning -> score=%s conf=%s",
                         symbol, score, conf)
                return extracted
            elif bear_score > bull_score + 2:
                extracted = {"setup_score": 30, "confidence": 40,
                             "setup_type": "TREND_CONTINUATION",
                             "direction_agrees": True,
                             "rationale": "extracted from reasoning: bearish"}
                log.info("HERMES %s: extracted from reasoning -> score=30 conf=40 (bearish)", symbol)
                return extracted
            else:
                # Neutral - return moderate score so V2 can still evaluate
                extracted = {"setup_score": 50, "confidence": 40,
                             "setup_type": "MEAN_REVERSION",
                             "direction_agrees": True,
                             "rationale": "extracted from reasoning: neutral"}
                log.info("HERMES %s: extracted from reasoning -> score=50 conf=40 (neutral)", symbol)
                return extracted
        data.setdefault("confidence", 0)
        data.setdefault("late_entry", False)
        return data
    except Exception as e:
        log.error("HERMES %s error: %s", symbol, e)
        return empty

AEGIS_SYSTEM = """You are AEGIS, Risk Officer for 75x leverage crypto futures.
1.3% adverse move = total liquidation. Default assumption: every trade is WRONG until evidence says otherwise.
Your job is FIND REASONS TO REJECT.

RULES:
1. If evidence inconclusive or thin, REJECT. Need positive case that risk is acceptable.
2. If your confidence < 50, approve=false regardless of risk_score.
3. rejection_reasons must be non-empty when approve=false. Max 3 reasons, each <= 15 words.
4. Never use absolute language.
5. Market regime is directionally relevant, NOT a blanket risk flag.
   CHOPPY/RANGING regime is a legitimate concern for TREND_CONTINUATION setups.
   For MEAN_REVERSION setups, choppy/ranging conditions are often IDEAL.
   CRITICAL RULES:
   a) Do NOT reject MEAN_REVERSION solely due to "choppy regime" or "low confidence."
   b) If a PAIR-SPECIFIC regime is provided (SIDEWAYS, TRENDING_UP/DOWN), trust it over
      the global regime. Global CHOPPY does not mean every pair is choppy.
   c) High RSI (>85) or Stochastic extremes (>90, <10) in MEAN_REVERSION are PRO signals.
      Do NOT cite "High RSI5" or "Overbought stochastics" as rejection reasons for MR SHORT.
   d) "Low volume" alone is insufficient for rejection in MR setups with RSI > 85 or < 15.
   e) Focus rejection on structural risks: wide spread, conflicting 5m vs 15m data, or
      price trapped between resistance levels without clear reversal pattern.

WHAT TO HUNT:
- Liquidity trap / stop-hunt
- False breakout
- Momentum exhaustion (but for SHORT at overbought+resistance or LONG at oversold+support, exhaustion IS the expected setup signal — do not flag it as a risk in those cases)
- Timeframe conflict (5m vs 15m)
- Spread/orderbook risk
- Session transition
- Funding extremes

Return ONLY this JSON:
{"approve":true|false,"risk_score":0-100,"trap_probability":0-100,"rejection_reasons":["string"],"confidence":0-100}"""

async def run_aegis(client, model, symbol, direction, features, market_ctx, hermes_out, spread_bps, ob_imbalance, setup_type="TREND_CONTINUATION"):
    prompt = f"INPUT:\nSymbol: {symbol}\nDirection: {direction}\nSetup Type: {setup_type}\nLeverage: 75\nSpread (bps): {spread_bps}\nOrderbook imbalance: {ob_imbalance}\nFeatures: {json.dumps({k:v for k,v in features.items() if k not in ('_price','setup_type')})}\nMarket Context: {json.dumps(market_ctx)}\nHERMES output: {json.dumps(hermes_out)}"
    reject = {"approve":False,"risk_score":100,"trap_probability":100,
              "rejection_reasons":["AI unavailable - fail-closed"],"confidence":0}
    if client is None:
        log.warning("AEGIS: client None -> fail-closed REJECT")
        return reject
    try:
        res = await asyncio.to_thread(
            client.chat.completions.create, model=model,
            messages=[{"role":"system","content":AEGIS_SYSTEM},{"role":"user","content":prompt}],
            max_tokens=350, temperature=0.1)
        msg = res.choices[0].message
        raw = msg.content
        if raw is None and hasattr(msg, "reasoning_content"):
            raw = msg.reasoning_content
        if raw is None and hasattr(msg, "reasoning"):
            raw = msg.reasoning
        if raw is None:
            raw = str(msg)
        log.info("AEGIS_RAW %s: %s", symbol, str(raw)[:400])
        data = extract_json_robust(raw)
        if not data or "approve" not in data:
            log.warning("AEGIS %s: invalid response -> REJECT, raw=%s", symbol, str(raw)[:200])
            return reject
        data.setdefault("confidence", 0)
        data.setdefault("rejection_reasons", [])
        return data
    except Exception as e:
        log.error("AEGIS %s error: %s -> REJECT", symbol, e)
        return reject

class ConsensusEngine:
    def evaluate(self, gm_score, gm_tier, athena, hermes, aegis, setup_type="TREND_CONTINUATION"):
        result = {
            "godmode_score": gm_score, "godmode_tier": gm_tier, "setup_type": setup_type,
            "athena_confidence": safe_int(athena.get("confidence", 0)),
            "hermes_score": safe_int(hermes.get("setup_score", 0)),
            "hermes_probability": safe_int(hermes.get("probability_estimate", 0)),
            "aegis_approve": aegis.get("approve", False),
            "aegis_risk": safe_int(aegis.get("risk_score", 100)),
            "aegis_trap": safe_int(aegis.get("trap_probability", 100)),
        }
        # SNIPER BYPASS: GODMODE >= 90 with fresh signal = skip consensus, go direct
        if gm_tier == "SNIPER" and gm_score >= 90:
            aegis_ok = aegis.get("approve", False)
            hermes_score = safe_float(hermes.get("setup_score", 0))
            # If AEGIS approves or HERMES has no data (stale cache), go EXECUTE
            if aegis_ok or hermes_score == 0:
                result["decision"] = "EXECUTE"
                result["reason"] = f"SNIPER BYPASS - GM={gm_score} AEGIS={'OK' if aegis_ok else 'pending'} HERMES={hermes_score}"
                result["path"] = "SNIPER_BYPASS"
                result["size_multiplier"] = 1.2
                result["consensus_score"] = gm_score
                return result
        scores = [gm_score]
        weights = [50] if setup_type == "MEAN_REVERSION" else [40]
        scores.append(safe_float(hermes.get("setup_score", 0)))
        # Cap AEGIS risk penalty for WATCH tier - don't let risk tank consensus
        aegis_risk = safe_float(aegis.get("risk_score", 100))
        if not aegis.get("approve", False) and gm_tier not in ("EXECUTE", "SNIPER"):
            aegis_risk = min(aegis_risk, 50)  # Cap risk at 50 for WATCH tier
        scores.append(100 - aegis_risk)
        if setup_type == "MEAN_REVERSION":
            weights.extend([25, 25])
        else:
            weights.extend([30, 30])
        total_w = sum(weights)
        consensus = sum(s * w for s, w in zip(scores, weights)) / total_w
        result["consensus_score"] = round(consensus, 1)

        if not aegis.get("approve", False):
            aegis_reasons = [r.lower() for r in aegis.get("rejection_reasons", [])]
            benign_kw = ["high stoch", "choppy", "rsi not extreme",
                         "insufficient trend", "low confidence",
                         "conflicting trend", "neutral bias", "low conviction"]
            is_benign = all(any(kw in r for kw in benign_kw) for r in aegis_reasons) if aegis_reasons else False
            if gm_tier == "SNIPER" and not is_benign:
                result["decision"] = "REJECT"
                result["reason"] = "AEGIS VETO: " + "; ".join(aegis.get("rejection_reasons", ["unknown"])[:2])
            elif gm_tier == "EXECUTE" and gm_score >= 75:
                aegis_note = "; ".join(aegis.get("rejection_reasons", ["unknown"])[:2])
                log.info("EXECUTE SOFTEN: AEGIS rejected %s (GM=%d) allowing reduced size: %s",
                         result.get("symbol", "?"), gm_score, aegis_note)
                result["decision"] = "WATCH"
                result["reason"] = f"EXECUTE SOFTEN - GM={gm_score} AEGIS note: {aegis_note}"
                result["path"] = "EXECUTE_SOFTEN"
                result["size_multiplier"] = 0.5
                return result
            elif gm_tier in ("EXECUTE",):
                if is_benign:
                    aegis_note = "; ".join(aegis.get("rejection_reasons", ["unknown"])[:2])
                    log.info("AEGIS BYPASS: %s benign reason: %s", result.get("symbol","?"), aegis_note)
                    result["decision"] = "WATCH"
                    result["reason"] = f"AEGIS BYPASS - GM={gm_score} note: {aegis_note}"
                    result["path"] = "AEGIS_BYPASS"
                    result["size_multiplier"] = 0.4
                    return result
                result["decision"] = "REJECT"
                result["reason"] = "AEGIS VETO: " + "; ".join(aegis.get("rejection_reasons", ["unknown"])[:2])
            else:
                aegis_note = "; ".join(aegis.get("rejection_reasons", ["unknown"])[:2])
                log.info("CONSENSUS: AEGIS note on %s tier (not vetoed): %s", gm_tier, aegis_note)
                # Downgrade consensus score by 10% but don't reject
                consensus = consensus * 0.9
                result["consensus_score"] = round(consensus, 1)
        if hermes.get("late_entry", True) and setup_type != "MEAN_REVERSION":
            result["decision"] = "REJECT"
            result["reason"] = "HERMES: late entry detected"
        elif consensus >= 95:
            result["decision"] = "SNIPER"
            result["reason"] = f"SNIPER PATH - Ultra high consensus ({consensus:.1f})"
            result["path"] = "SNIPER"
            result["size_multiplier"] = 1.2
        elif consensus >= 85:
            result["decision"] = "EXECUTE"
            result["reason"] = f"BALANCED PATH - Good consensus ({consensus:.1f})"
            result["path"] = "BALANCED"
            result["size_multiplier"] = 1.0
        elif consensus >= 70:
            result["decision"] = "WATCH"
            result["reason"] = f"Moderate consensus ({consensus:.1f}) - reduced size"
            result["path"] = "WATCH"
            result["size_multiplier"] = 0.5
        elif gm_tier == "WATCH" and consensus >= 70:
            # GM says WATCH and consensus is moderate - allow with reduced size
            result["decision"] = "WATCH"
            result["reason"] = f"GM WATCH + moderate consensus ({consensus:.1f}) - reduced size"
            result["path"] = "WATCH"
            result["size_multiplier"] = 0.3
        else:
            result["decision"] = "REJECT"
            result["reason"] = f"Low consensus ({consensus:.1f}) - skip"
            result["path"] = "REJECT"
            result["size_multiplier"] = 0
        return result

CONSENSUS = ConsensusEngine()

def dynamic_size(base_margin, consensus_score, confidence):
    factor = 1.0
    if consensus_score >= 95: factor = 1.2
    elif consensus_score >= 85: factor = 1.0
    elif consensus_score >= 75: factor = 0.7
    elif consensus_score >= 70: factor = 0.5
    else: factor = 0
    if confidence < 50: factor *= 0.7
    return round(base_margin * factor, 2)

class RejectionTracker:
    def __init__(self, cooldown_sec=60, score_change_threshold=1):
        self.cooldown_sec = cooldown_sec
        self.score_change_threshold = score_change_threshold
        self._rejections = {}

    def should_evaluate(self, symbol, direction, current_score):
        key = (symbol, direction)
        if key not in self._rejections: return True
        ts, prev_score = self._rejections[key]
        elapsed = (datetime.now(timezone.utc) - ts).total_seconds()
        score_delta = abs(current_score - prev_score)
        if elapsed < self.cooldown_sec and score_delta < self.score_change_threshold:
            log.debug("COOLDOWN: %s %s skipped (elapsed=%.0fs, delta=%.1f)", symbol, direction, elapsed, score_delta)
            return False
        return True

    def record_rejection(self, symbol, direction, score):
        self._rejections[(symbol, direction)] = (datetime.now(timezone.utc), score)

    def clear(self, symbol, direction=None):
        if direction: self._rejections.pop((symbol, direction), None)
        else: self._rejections = {k:v for k,v in self._rejections.items() if k[0] != symbol}

    def cleanup(self, max_age_sec=3600):
        now = datetime.now(timezone.utc)
        self._rejections = {k:v for k,v in self._rejections.items() if (now-v[0]).total_seconds() < max_age_sec}

REJECTION_TRACKER = RejectionTracker()

class AITradeJournal:
    def __init__(self):
        self.entries = []
        self.feature_stats = {}

    def log_trade(self, trade_data):
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": trade_data.get("symbol"),
            "direction": trade_data.get("direction"),
            "setup_type": trade_data.get("setup_type", "UNKNOWN"),
            "godmode_score": trade_data.get("godmode_score"),
            "consensus_score": trade_data.get("consensus_score"),
            "tier": trade_data.get("tier"),
            "hermes_prob": trade_data.get("hermes_probability"),
            "aegis_risk": trade_data.get("aegis_risk"),
            "features": trade_data.get("features", {}),
            "athena_regime": trade_data.get("athena_regime"),
            "pnl_usdt": trade_data.get("pnl_usdt", 0),
            "pnl_pct": trade_data.get("pnl_pct", 0),
            "exit_reason": trade_data.get("exit_reason"),
            "result": "WIN" if trade_data.get("pnl_usdt", 0) > 0 else "LOSS",
        }
        self.entries.append(entry)
        self._update_feature_stats(entry)
        return entry

    def _update_feature_stats(self, entry):
        features = entry.get("features", {})
        result = entry.get("result", "LOSS")
        for fname, fval in features.items():
            if fname.startswith("_"): continue
            if fname not in self.feature_stats:
                self.feature_stats[fname] = {"wins":[],"losses":[],"count":0}
            bucket = self.feature_stats[fname]
            bucket["count"] += 1
            if result == "WIN": bucket["wins"].append(safe_float(fval))
            else: bucket["losses"].append(safe_float(fval))

    def get_feature_importance(self):
        importance = {}
        for fname, stats in self.feature_stats.items():
            if stats["count"] < 5: continue
            win_avg = np.mean(stats["wins"]) if stats["wins"] else 50
            loss_avg = np.mean(stats["losses"]) if stats["losses"] else 50
            importance[fname] = round(win_avg - loss_avg, 2)
        return dict(sorted(importance.items(), key=lambda x: abs(x[1]), reverse=True))

    def weekly_summary(self):
        if not self.entries: return "No trades yet."
        wins = [e for e in self.entries if e["result"] == "WIN"]
        losses = [e for e in self.entries if e["result"] == "LOSS"]
        total = len(self.entries)
        pnl = sum(e.get("pnl_usdt", 0) for e in self.entries)
        wr = len(wins) / total * 100 if total > 0 else 0
        top_features = self.get_feature_importance()
        top3 = list(top_features.items())[:3]
        return (f"📊 AI JOURNAL SUMMARY\nTotal: {total} | W: {len(wins)} | L: {len(losses)}\n"
                f"Win Rate: {wr:.1f}% | PnL: {pnl:+.3f} USDT\n"
                f"Top Features: {', '.join(f'{k}({v:+.1f})' for k,v in top3)}")

AI_JOURNAL = AITradeJournal()

async def run_godmode_v2_pipeline(
    symbol, df_5m, df_15m,
    client_athena, model_athena,
    client_hermes, model_hermes,
    client_aegis, model_aegis,
    base_margin=5.0, market_ctx=None, spread_bps=5.0, ob_imbalance=1.0,
):
    result = {"action":"SKIP","score":0,"tier":"REJECT","setup_type":"TREND_CONTINUATION",
              "consensus":{},"features":{},"sizing":0,"journal_entry":None}
    features = FEATURE_ENGINE.compute(df_5m, df_15m)
    result["features"] = features
    if not features or features.get("_price", 0) <= 0:
        result["consensus"] = {"decision":"REJECT","reason":"feature computation failed"}
        return result

    direction, gm_score, gm_tier, breakdown = GM_SCORER.best_direction(features)
    result["score"] = gm_score
    result["tier"] = gm_tier
    setup_type = breakdown.get("setup_type", "TREND_CONTINUATION")
    result["setup_type"] = setup_type

    if direction == "NEUTRAL":
        result["consensus"] = {"decision":"REJECT","reason":"neutral - no edge"}
        return result
    if gm_score < TIER_THRESHOLDS["watch"]:
        result["consensus"] = {"decision":"REJECT","reason":f"score {gm_score} < {TIER_THRESHOLDS['watch']}"}
        return result

    if not REJECTION_TRACKER.should_evaluate(symbol, direction, gm_score):
        result["consensus"] = {"decision":"REJECT","reason":"cooldown - recently evaluated"}
        return result

    athena_out = market_ctx or {"market_regime":"CHOPPY","bias":"NEUTRAL","confidence":30}
    log.debug("ATHENA_CTX: regime=%s bias=%s conf=%s setup=%s",
              athena_out.get("market_regime"), athena_out.get("bias"),
              athena_out.get("confidence"), setup_type)

    hermes_out = await run_hermes(client_hermes, model_hermes, symbol, direction, features, athena_out, setup_type)
    hermes_score = safe_float(hermes_out.get("setup_score", 0))
    hermes_conf = safe_float(hermes_out.get("confidence", 0))
    if hermes_score < 35 or hermes_conf < 20:
        result["consensus"] = {"decision":"REJECT","reason":f"HERMES low: score={hermes_score} conf={hermes_conf}"}
        REJECTION_TRACKER.record_rejection(symbol, direction, gm_score)
        return result

    aegis_out = await run_aegis(client_aegis, model_aegis, symbol, direction, features, athena_out, hermes_out, spread_bps, ob_imbalance, setup_type)
    if not aegis_out.get("approve", False):
        _aegis_r = "; ".join(aegis_out.get("rejection_reasons",[])[:2])
        if gm_tier in ("EXECUTE", "SNIPER"):
            result["consensus"] = {"decision":"REJECT","reason":"AEGIS VETO: " + _aegis_r}
            # Don't record - AEGIS may approve next cycle with fresh data
            AI_JOURNAL.log_trade({"symbol":symbol,"direction":direction,"setup_type":setup_type,
            "aegis_risk":aegis_out.get("risk_score",100),
            "athena_regime":athena_out.get("market_regime","?"),
            "pnl_usdt":0,"pnl_pct":0,"exit_reason":"AEGIS_VETO"})
            return result
        else:
            log.info("GMV2 %s %s: AEGIS note on WATCH: %s", symbol, direction, _aegis_r)

    consensus = CONSENSUS.evaluate(gm_score, gm_tier, athena_out, hermes_out, aegis_out, setup_type)
    result["consensus"] = consensus
    if consensus["decision"] in ("SNIPER", "EXECUTE", "WATCH"):
        result["action"] = direction
        REJECTION_TRACKER.clear(symbol, direction)
    else:
        result["action"] = "SKIP"
        if gm_tier not in ("EXECUTE", "SNIPER"):  # Don't cooldown high-score pairs
            REJECTION_TRACKER.record_rejection(symbol, direction, gm_score)

    if result["action"] != "SKIP":
        result["sizing"] = dynamic_size(base_margin, consensus["consensus_score"], consensus.get("athena_confidence", 50))
    return result
