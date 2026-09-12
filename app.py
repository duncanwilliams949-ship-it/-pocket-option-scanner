import os, asyncio, threading
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
from pocketoptionapi_async import AsyncPocketOptionClient

ASSET = os.getenv('PO_ASSET','EURUSD_otc')
TIMEFRAME = int(os.getenv('PO_TIMEFRAME','60'))
COUNT = int(os.getenv('PO_COUNT','100'))
IS_DEMO = os.getenv('PO_IS_DEMO','1') != '0'
SSID = os.getenv('PO_SSID','').strip()

app=FastAPI(title='QT Mobile Live Scanner')
state={'connected':False,'asset':ASSET,'timeframe':TIMEFRAME,'candles':[],
       'signal':'WAIT','confidence':0,'quality':'OFFLINE','reason':'Waiting for live Pocket Option data.',
       'updated':None,'error':None}

def ema(a,n):
    k=2/(n+1); e=a[0]
    for x in a[1:]: e=x*k+e*(1-k)
    return e

def rsi(a,n=14):
    if len(a)<=n:return 50.0
    gains=losses=0.0
    for i in range(len(a)-n,len(a)):
        d=a[i]-a[i-1]
        if d>=0:gains+=d
        else:losses-=d
    return 100.0 if losses==0 else 100-100/(1+gains/losses)

def analyze(c):
    if len(c)<30:return ('WAIT',0,'LOW','Waiting for enough candles.')
    closes=[x['close'] for x in c]; last=closes[-1]
    e9,e21=ema(closes,9),ema(closes,21); rv=rsi(closes)
    mom=closes[-1]-closes[-6]
    ups=sum(x['close']>x['open'] for x in c[-8:]); downs=8-ups
    bull=bear=0.0; reasons=[]
    # normalized component scores; this is a model score, not win probability
    ema_score=min(35,abs(e9-e21)/last*100000*4)
    if e9>e21: bull+=ema_score; reasons.append('EMA 9 > 21')
    elif e9<e21: bear+=ema_score; reasons.append('EMA 9 < 21')
    r_score=min(25,abs(rv-50)*1.4)
    if rv>52: bull+=r_score; reasons.append(f'RSI {rv:.0f}')
    elif rv<48: bear+=r_score; reasons.append(f'RSI {rv:.0f}')
    m_score=min(25,abs(mom)/last*100000*3)
    if mom>0: bull+=m_score; reasons.append('momentum up')
    elif mom<0: bear+=m_score; reasons.append('momentum down')
    flow_score=min(15,abs(ups-downs)/8*15)
    if ups>downs: bull+=flow_score; reasons.append(f'candle flow {ups}/8 up')
    elif downs>ups: bear+=flow_score; reasons.append(f'candle flow {downs}/8 down')
    total=bull+bear
    if total<8 or abs(bull-bear)<10:return ('WAIT',max(45,round(50+abs(bull-bear))), 'LOW','Signals are not aligned.')
    direction='BUY' if bull>bear else 'SELL'
    score=round(50+abs(bull-bear)/max(total,1)*50)
    if score<65:return ('WAIT',score,'LOW','Signals are not aligned strongly enough.')
    quality='HIGH' if score>=80 else 'MEDIUM'
    return direction,min(score,99),quality,' • '.join(reasons)

def normalize(x):
    ts=x.timestamp.timestamp() if hasattr(x.timestamp,'timestamp') else x.timestamp
    return {'timestamp':ts,'open':float(x.open),'high':float(x.high),'low':float(x.low),'close':float(x.close),'volume':float(getattr(x,'volume',0) or 0)}

async def worker():
    if not SSID:
        state['error']='Live connection is not configured. Set PO_SSID on your private server.'
        return
    client=None
    try:
        client=AsyncPocketOptionClient(SSID,is_demo=IS_DEMO,enable_logging=False,persistent_connection=True,auto_reconnect=True)
        ok=await client.connect(); state['connected']=bool(ok)
        if not ok: raise RuntimeError('Pocket Option connection failed')
        while True:
            candles=await client.get_candles(ASSET,TIMEFRAME,COUNT)
            data=[normalize(x) for x in candles]
            sig,conf,quality,reason=analyze(data)
            state.update({'connected':True,'candles':data[-COUNT:],'signal':sig,'confidence':conf,
                          'quality':quality,'reason':reason,'updated':datetime.now(timezone.utc).isoformat(),'error':None})
            await asyncio.sleep(2)
    except Exception as e:
        state.update({'connected':False,'quality':'OFFLINE','error':str(e)[:300]})
    finally:
        if client:
            try: await client.disconnect()
            except Exception: pass

@app.on_event('startup')
async def startup(): threading.Thread(target=lambda:asyncio.run(worker()),daemon=True).start()

@app.get('/api/state')
def api_state(): return JSONResponse(state)

@app.get('/',response_class=HTMLResponse)
def home():
    with open(os.path.join(os.path.dirname(__file__),'index.html'),encoding='utf-8') as f:return f.read()

if __name__=='__main__': uvicorn.run(app,host='0.0.0.0',port=int(os.getenv('PORT','8765')))
