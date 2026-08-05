import os
import logging
from typing import Dict, Tuple

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("GODMODE_V15.8")

def cek_pucuk_lembah_agresif(df) -> Tuple[str, str, int]:
    try:
        latest = df.iloc[-1]
        
        price = latest["close"]
        rsi = latest["rsi"]
        bb_upper = latest["bb_upper"]
        bb_lower = latest["bb_lower"]
        stoch_k = latest["stoch_k"]
        
        rsi_pucuk = float(os.getenv("GODMODE_RSI_PUCUK", 68))
        rsi_lembah = float(os.getenv("GODMODE_RSI_LEMBAH", 32))
        
        if price >= bb_upper and rsi >= rsi_pucuk and stoch_k >= 80:
            alasan = f"PUCUK TERDETEKSI: Harga ({price}) tembus BB Atas ({bb_upper:.4f}), RSI ({rsi:.2f}) overbought."
            logger.info(f"GODMODE SIGNAL: SHORT - {alasan}")
            return "SHORT", alasan, 95
            
        elif price <= bb_lower and rsi <= rsi_lembah and stoch_k <= 20:
            alasan = f"LEMBAH TERDETEKSI: Harga ({price}) tembus BB Bawah ({bb_lower:.4f}), RSI ({rsi:.2f}) oversold."
            logger.info(f"GODMODE SIGNAL: LONG - {alasan}")
            return "LONG", alasan, 95
            
        else:
            return "WAIT", "Harga di area netral. GODMODE menahan diri agar tidak overtrade.", 0
            
    except Exception as e:
        logger.error(f"Error pada deteksi Pucuk/Lembah: {e}")
        return "ERROR", str(e), 0

def prompt_llama_godmode(signal: str, alasan: str, ticker: str) -> str:
    prompt = f"""
    SYSTEM: Anda adalah AI Trading GODMODE super agresif namun presisi tinggi. Misi Anda adalah win-rate 99%.
    TUGAS: Validasi sinyal teknikal berikut untuk koin {ticker}.
    
    SINYAL TEKNIKAL: {signal}
    ALASAN: {alasan}
    
    ATURAN VALIDASI MUTLAK:
    1. Jika sinyal adalah SHORT, pastikan ini adalah titik PUCUK (Resistance terkuat, Overbought ekstrem). Jangan SHORT di tengah trend naik!
    2. Jika sinyal adalah LONG, pastikan ini adalah titik LEMBAH (Support terkuat, Oversold ekstrem). Jangan LONG saat pisau jatuh!
    3. Jika data tidak meyakinkan bahwa ini ujung ekstrem, jawab "PASS".
    4. Jika valid, jawab dengan format: "EXECUTE | [ARAH] | TARGET_TP | TARGET_SL"
    """
    return prompt
