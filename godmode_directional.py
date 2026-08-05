"""
godmode_directional.py — GODMODE v2
LONG di lembah (downtrend exhaustion)
SHORT di pucuk (uptrend exhaustion)
"""

import pandas as pd
import numpy as np


class MarketStructure:
    """Analisis struktur pasar — swing high/low, trend, support/resistance"""

    def __init__(self):
        self.trend = None
        self.support_levels = []
        self.resistance_levels = []
        self.swing_highs = []
        self.swing_lows = []

    def analyze(self, df: pd.DataFrame) -> str:
        """
        Deteksi trend dari price action:
        - bullish: higher highs + higher lows
        - bearish: lower highs + lower lows
        - ranging: mixed
        """
        if len(df) < 20:
            return 'unknown'

        highs = df['high'].values
        lows = df['low'].values

        # Swing high: high lebih tinggi dari 2 bar di kiri dan kanan
        self.swing_highs = []
        self.swing_lows = []

        for i in range(2, len(df) - 2):
            if (highs[i] > highs[i - 1] and highs[i] > highs[i - 2] and
                    highs[i] > highs[i + 1] and highs[i] > highs[i + 2]):
                self.swing_highs.append({'index': i, 'price': highs[i]})

            if (lows[i] < lows[i - 1] and lows[i] < lows[i - 2] and
                    lows[i] < lows[i + 1] and lows[i] < lows[i + 2]):
                self.swing_lows.append({'index': i, 'price': lows[i]})

        recent_highs = self.swing_highs[-3:] if len(self.swing_highs) >= 2 else []
        recent_lows = self.swing_lows[-3:] if len(self.swing_lows) >= 2 else []

        # Tentukan trend
        if len(recent_highs) >= 2 and len(recent_lows) >= 2:
            hh = recent_highs[-1]['price'] > recent_highs[-2]['price']
            hl = recent_lows[-1]['price'] > recent_lows[-2]['price']
            lh = recent_highs[-1]['price'] < recent_highs[-2]['price']
            ll = recent_lows[-1]['price'] < recent_lows[-2]['price']

            if hh and hl:
                self.trend = 'bullish'
            elif lh and ll:
                self.trend = 'bearish'
            else:
                self.trend = 'ranging'
        else:
            self.trend = 'ranging'

        self.support_levels = [l['price'] for l in recent_lows]
        self.resistance_levels = [h['price'] for h in recent_highs]

        return self.trend

    def is_near_support(self, price: float, threshold: float = 0.01) -> bool:
        """Cek apakah harga dekat support (untuk LONG)"""
        for support in self.support_levels:
            if abs(price - support) / price < threshold:
                return True
        return False

    def is_near_resistance(self, price: float, threshold: float = 0.01) -> bool:
        """Cek apakah harga dekat resistance (untuk SHORT)"""
        for resistance in self.resistance_levels:
            if abs(price - resistance) / price < threshold:
                return True
        return False

    def get_trend_strength(self) -> float:
        """Skor kekuatan trend 0-100"""
        if not self.swing_highs or not self.swing_lows:
            return 50

        highs_prices = [h['price'] for h in self.swing_highs[-4:]]
        lows_prices = [l['price'] for l in self.swing_lows[-4:]]

        if len(highs_prices) < 2 or len(lows_prices) < 2:
            return 50

        bullish_moves = 0
        total_moves = 0

        for i in range(1, len(highs_prices)):
            total_moves += 1
            if highs_prices[i] > highs_prices[i - 1]:
                bullish_moves += 1

        for i in range(1, len(lows_prices)):
            total_moves += 1
            if lows_prices[i] > lows_prices[i - 1]:
                bullish_moves += 1

        if total_moves == 0:
            return 50

        consistency = bullish_moves / total_moves

        if self.trend == 'bullish':
            return 50 + (consistency * 50)
        elif self.trend == 'bearish':
            return 50 + ((1 - consistency) * 50)
        else:
            return 50


class GodModeDirectional:
    """
    GODMODE v2 — Trading dengan arah pasar

    Prinsip:
    - LONG saat: lembah dalam uptrend / support terdekat + oversold
    - SHORT saat: pucuk dalam downtrend / resistance terdekat + overbought
    - Hindari counter-trend kecuali ada konfirmasi kuat
    """

    def __init__(self, config: dict):
        self.config = config
        self.market = MarketStructure()
        self.min_score = config.get('GODMODE_MIN_SCORE', 70)
        self.directional_boost = config.get('GODMODE_DIRECTIONAL_BOOST', 15)
        self.counter_trend_penalty = 20

    def evaluate(self, signal: dict, df: pd.DataFrame) -> dict:
        """
        Evaluasi sinyal dengan awareness terhadap struktur pasar

        Returns: {
            'action': 'LONG'|'SHORT'|'SKIP',
            'score': int,
            'reasons': list[str],
            'confidence': str
        }
        """
        result = {
            'action': 'SKIP',
            'score': 0,
            'reasons': [],
            'confidence': 'low'
        }

        if len(df) < 50:
            result['reasons'].append('Data tidak cukup untuk analisis')
            return result

        # 1. Analisis struktur pasar
        trend = self.market.analyze(df)
        trend_strength = self.market.get_trend_strength()
        price = df['close'].iloc[-1]

        # 2. Hitung base score dari indikator
        base_score = self._calculate_base_score(signal, df)

        # 3. Deteksi exhaustion
        exhaustion = self._detect_exhaustion(df, trend)

        # 4. Tentukan arah + skor
        if signal.get('action') == 'LONG':
            if trend == 'bullish' or self.market.is_near_support(price):
                # TREND ALIGNMENT — skor naik
                boosted = min(100, base_score + self.directional_boost)
                result['action'] = 'LONG'
                result['score'] = boosted
                result['reasons'].append(f'Trend bullish aligned (+{self.directional_boost})')
                if exhaustion == 'oversold':
                    result['score'] = min(100, result['score'] + 10)
                    result['reasons'].append('Oversold di support')
            elif trend == 'bearish':
                # COUNTER-TREND — penalty
                penalized = max(0, base_score - self.counter_trend_penalty)
                result['action'] = 'LONG'
                result['score'] = penalized
                result['reasons'].append(f'Counter-trend bearish ({self.counter_trend_penalty} penalty)')
            else:
                result['action'] = 'LONG'
                result['score'] = base_score
                result['reasons'].append('Ranging market')

        elif signal.get('action') == 'SHORT':
            if trend == 'bearish' or self.market.is_near_resistance(price):
                boosted = min(100, base_score + self.directional_boost)
                result['action'] = 'SHORT'
                result['score'] = boosted
                result['reasons'].append(f'Trend bearish aligned (+{self.directional_boost})')
                if exhaustion == 'overbought':
                    result['score'] = min(100, result['score'] + 10)
                    result['reasons'].append('Overbought di resistance')
            elif trend == 'bullish':
                penalized = max(0, base_score - self.counter_trend_penalty)
                result['action'] = 'SHORT'
                result['score'] = penalized
                result['reasons'].append(f'Counter-trend bullish ({self.counter_trend_penalty} penalty)')
            else:
                result['action'] = 'SHORT'
                result['score'] = base_score
                result['reasons'].append('Ranging market')

        # 5. Confidence level
        if result['score'] >= self.min_score:
            result['confidence'] = 'high'
        elif result['score'] >= self.min_score - 15:
            result['confidence'] = 'medium'
        else:
            result['action'] = 'SKIP'
            result['confidence'] = 'low'
            result['reasons'].append(f'Skor {result["score"]} < min {self.min_score}')

        return result

    def _calculate_base_score(self, signal: dict, df: pd.DataFrame) -> int:
        """Hitung skor dasar dari indikator"""
        score = 50  # baseline

        # RSI
        rsi = signal.get('rsi', 50)
        if signal.get('action') == 'LONG' and rsi < 35:
            score += 15
        elif signal.get('action') == 'SHORT' and rsi > 65:
            score += 15

        # Volume spike
        vol = df['volume'].iloc[-1]
        vol_avg = df['volume'].iloc[-20:].mean()
        if vol_avg > 0 and vol / vol_avg > 1.5:
            score += 10

        # MACD momentum
        macd = signal.get('macd', 0)
        macd_signal = signal.get('macd_signal', 0)
        if signal.get('action') == 'LONG' and macd > macd_signal:
            score += 10
        elif signal.get('action') == 'SHORT' and macd < macd_signal:
            score += 10

        return min(100, score)

    def _detect_exhaustion(self, df: pd.DataFrame, trend: str) -> str:
        """Deteksi kelelahan momentum"""
        rsi = self._calc_rsi(df['close'], 14)

        if rsi < 30:
            return 'oversold'
        elif rsi > 70:
            return 'overbought'
        return 'neutral'

    @staticmethod
    def _calc_rsi(series: pd.Series, period: int = 14) -> float:
        """Hitung RSI"""
        delta = series.diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain.iloc[-1] / loss.iloc[-1] if loss.iloc[-1] != 0 else 100
        return 100 - (100 / (1 + rs))
