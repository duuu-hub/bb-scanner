from __future__ import annotations
import os, time
from decimal import Decimal
from bitget_demo_lifecycle_test import BitgetDemoClassic, PRODUCT_TYPE, MARGIN_COIN, q_down, q_nearest, q_up, wait_for_fill

def d(x):
    return Decimal(str(x or "0"))

def main():
    if os.getenv("DEMO_CONFIRM") != "MAKER_POST_ONLY_SMOKE":
        raise RuntimeError("confirmation mismatch")
    c=BitgetDemoClassic()
    symbol="BTCUSDT"
    cfgs=c.public_get("/api/v2/mix/market/contracts",{"productType":"usdt-futures","symbol":symbol}) or []
    ticks=c.public_get("/api/v2/mix/market/ticker",{"productType":"usdt-futures","symbol":symbol}) or []
    if not cfgs or not ticks: raise RuntimeError("missing public data")
    cfg=cfgs[0]; t=ticks[0]
    bid=d(t.get("bidPr") or t.get("bidPrice") or t.get("lastPr"))
    price_place=int(cfg.get("pricePlace") or 8)
    end=d(cfg.get("priceEndStep") or "1")
    step=Decimal(1).scaleb(-price_place)*end
    maker=q_down(bid*Decimal("0.90"),step)
    tp=q_nearest(maker*Decimal("1.10"),step)
    sl=q_nearest(maker*Decimal("0.95"),step)
    min_qty=d(cfg.get("minTradeNum"))
    size_step=d(cfg.get("sizeMultiplier"))
    min_usdt=max(d(cfg.get("minTradeUSDT")),Decimal("20"))
    qty=max(min_qty,min_usdt/maker)
    if size_step>0: qty=q_up(qty,size_step)
    vol=int(cfg.get("volumePlace") or 8)
    qty_s=f"{qty:.{vol}f}"
    price_s=f"{maker:.{price_place}f}"
    tp_s=f"{tp:.{price_place}f}"; sl_s=f"{sl:.{price_place}f}"
    placed=c.private_post("/api/v2/mix/order/place-order",{
        "symbol":symbol,"productType":PRODUCT_TYPE,"marginMode":"crossed","marginCoin":MARGIN_COIN,
        "size":qty_s,"price":price_s,"side":"buy","tradeSide":"open","orderType":"limit",
        "force":"post_only","clientOid":f"mk_smoke_{int(time.time())}"[:32],
        "presetStopSurplusPrice":tp_s,"presetStopLossPrice":sl_s,
    })
    oid=str(placed.get("orderId") or "")
    if not oid: raise RuntimeError("missing order id")
    print(f"[OK] post_only Demo order accepted orderId={oid} price={price_s} bid={bid}")
    time.sleep(2)
    detail=c.private_get("/api/v2/mix/order/detail",{"symbol":symbol,"productType":PRODUCT_TYPE,"orderId":oid}) or {}
    state=str(detail.get("state") or "").lower()
    filled=d(detail.get("baseVolume"))
    print(f"[INFO] pre-cancel state={state} filled={filled} force={detail.get('force')} orderType={detail.get('orderType')}")
    if filled>0:
        close=c.private_post("/api/v2/mix/order/place-order",{
            "symbol":symbol,"productType":PRODUCT_TYPE,"marginMode":"crossed","marginCoin":MARGIN_COIN,
            "size":str(filled),"side":"buy","tradeSide":"close","orderType":"market",
            "clientOid":f"mk_smoke_close_{int(time.time())}"[:32],
        })
        cid=str(close.get("orderId") or "")
        if cid: wait_for_fill(c,symbol,cid)
        raise RuntimeError("smoke maker unexpectedly filled; emergency close sent")
    c.private_post("/api/v2/mix/order/cancel-order",{
        "symbol":symbol,"productType":PRODUCT_TYPE,"marginCoin":MARGIN_COIN,"orderId":oid,
    })
    detail2=c.private_get("/api/v2/mix/order/detail",{"symbol":symbol,"productType":PRODUCT_TYPE,"orderId":oid}) or {}
    st2=str(detail2.get("state") or "").lower()
    if st2 not in {"cancelled","canceled"}:
        raise RuntimeError(f"cancel not confirmed: {st2}")
    print(f"[OK] post_only Demo maker place -> rest -> cancel confirmed state={st2}")

if __name__=="__main__":
    main()
