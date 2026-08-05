#!/usr/bin/env python3
import os, hmac, hashlib, time, asyncio, httpx
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
KEY = os.environ.get("BINANCE_API_KEY", "")
SEC = os.environ.get("BINANCE_API_SECRET", "")
BASE = "https://fapi.binance.com"

def sign(params):
    qs = "&".join(str(k)+"="+str(v) for k, v in sorted(params.items()))
    sig = hmac.new(SEC.encode(), qs.encode(), hashlib.sha256).hexdigest()
    return qs + "&signature=" + sig

async def main():
    print("[JANITOR] Started!")
    async with httpx.AsyncClient() as h:
        while True:
            try:
                ts = str(int(time.time() * 1000))
                url1 = BASE + "/fapi/v2/positionRisk?" + sign({"timestamp": ts})
                rp = await h.get(url1, headers={"X-MBX-APIKEY": KEY})
                live = set()
                for x in rp.json():
                    if abs(float(x.get("positionAmt", 0))) > 0:
                        live.add(x["symbol"])
                        print("[JANITOR] LIVE: " + x["symbol"])
                ts = str(int(time.time() * 1000))
                url2 = BASE + "/fapi/v1/openOrders?" + sign({"timestamp": ts})
                ro = await h.get(url2, headers={"X-MBX-APIKEY": KEY})
                for o in ro.json():
                    sym = o.get("symbol", "")
                    if sym and sym not in live:
                        ts = str(int(time.time() * 1000))
                        cancel_p = {"symbol": sym, "orderId": o["orderId"], "timestamp": ts}
                        url3 = BASE + "/fapi/v1/order?" + sign(cancel_p)
                        cr = await h.delete(url3, headers={"X-MBX-APIKEY": KEY})
                        if cr.status_code == 200:
                            print("[JANITOR] ORPHAN CANCELLED: " + sym + " " + o["type"])
                        else:
                            print("[JANITOR] FAIL: " + sym + " " + cr.text[:80])
            except Exception as e:
                print("[JANITOR] ERROR: " + str(e))
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
