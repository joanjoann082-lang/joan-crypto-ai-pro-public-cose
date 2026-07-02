from __future__ import annotations
from typing import Any, Dict, List
from datetime import datetime, timezone
import urllib.parse
from .http import HttpClient
from ..utils import fnum, clamp, utc_now_iso

class MacroClient:
    """
    V16 robust macro collector.
    Fixes the V14 issue where Yahoo symbols were pre-encoded and then encoded again,
    which could create fake all-zero macro snapshots. Uses quote endpoint first and
    chart endpoint as fallback. It must never crash the runner.
    """
    SYMBOLS = ['^VIX', 'QQQ', 'SPY', 'DIA', 'DX-Y.NYB', '^TNX', 'GC=F', 'CL=F']

    def __init__(self):
        self.http = HttpClient('joanbot-macro-v16', timeout=8)

    def _quote_batch(self) -> Dict[str, Dict[str, float]]:
        vals: Dict[str, Dict[str, float]] = {}
        symbols = ','.join(self.SYMBOLS)  # IMPORTANT: do not pre-encode; HttpClient encodes params.
        for base in ['https://query1.finance.yahoo.com/v7/finance/quote', 'https://query2.finance.yahoo.com/v7/finance/quote']:
            try:
                j = self.http.get_json(base, {'symbols': symbols}, ttl=60)
                res = j.get('quoteResponse', {}).get('result', []) if isinstance(j, dict) else []
                for r in res:
                    sym = r.get('symbol')
                    if not sym:
                        continue
                    vals[sym] = {
                        'price': fnum(r.get('regularMarketPrice')),
                        'chg': fnum(r.get('regularMarketChangePercent')),
                    }
                if vals:
                    break
            except Exception:
                continue
        return vals

    def _chart_one(self, sym: str) -> Dict[str, float]:
        # Yahoo chart endpoint handles encoded path symbols differently from quote.
        url = 'https://query1.finance.yahoo.com/v8/finance/chart/' + urllib.parse.quote(sym, safe='')
        try:
            j = self.http.get_json(url, {'range': '5d', 'interval': '1d'}, ttl=120)
            result = (((j or {}).get('chart') or {}).get('result') or [{}])[0]
            meta = result.get('meta', {}) if isinstance(result, dict) else {}
            closes = (((result.get('indicators') or {}).get('quote') or [{}])[0].get('close') or []) if isinstance(result, dict) else []
            clean = [fnum(x) for x in closes if x is not None]
            price = fnum(meta.get('regularMarketPrice')) or (clean[-1] if clean else 0.0)
            prev = clean[-2] if len(clean) >= 2 else 0.0
            chg = ((price / prev) - 1.0) * 100.0 if price and prev else 0.0
            return {'price': price, 'chg': chg}
        except Exception:
            return {'price': 0.0, 'chg': 0.0}

    def _fear_greed(self) -> tuple[float, bool]:
        try:
            fg = self.http.get_json('https://api.alternative.me/fng/', {'limit': 1, 'format': 'json'}, ttl=300)
            v = fnum(fg.get('data', [{}])[0].get('value'), 50)
            return v, True
        except Exception:
            return 50.0, False

    def snapshot(self) -> Dict[str, Any]:
        vals = self._quote_batch()
        # Fallback missing/zero symbols through chart.
        for sym in self.SYMBOLS:
            cur = vals.get(sym, {})
            if not cur or (abs(fnum(cur.get('price'))) < 1e-12 and sym in ('^VIX', '^TNX')):
                fb = self._chart_one(sym)
                if fb.get('price') or fb.get('chg'):
                    vals[sym] = fb

        fg, fg_ok = self._fear_greed()
        vix = vals.get('^VIX', {}).get('price', 0)
        qqq = vals.get('QQQ', {}).get('chg', 0)
        spy = vals.get('SPY', {}).get('chg', 0)
        dia = vals.get('DIA', {}).get('chg', 0)
        dxy = vals.get('DX-Y.NYB', {}).get('chg', 0)
        us10y = vals.get('^TNX', {}).get('chg', 0)
        gold = vals.get('GC=F', {}).get('chg', 0)
        oil = vals.get('CL=F', {}).get('chg', 0)

        data_ok = any(abs(fnum(v)) > 1e-12 for v in [vix, qqq, spy, dia, dxy, us10y, gold, oil])
        risk = 50
        notes: List[str] = []
        if not data_ok:
            notes.append('MACRO_SOURCE_EMPTY')
        if vix >= 30:
            risk -= 26; notes.append('VIX_EXTREME_RISK_OFF')
        elif vix >= 22:
            risk -= 14; notes.append('VIX_ELEVATED')
        elif 0 < vix < 17:
            risk += 10; notes.append('VIX_LOW')
        for name, val in [('QQQ', qqq), ('SPY', spy), ('DIA', dia)]:
            if val > 0.35:
                risk += 5; notes.append(f'{name}_GREEN')
            elif val < -0.55:
                risk -= 6; notes.append(f'{name}_RED')
        if dxy > 0.35:
            risk -= 5; notes.append('DXY_STRONG')
        elif dxy < -0.35:
            risk += 3; notes.append('DXY_WEAK')
        if us10y > 1.0:
            risk -= 4; notes.append('YIELDS_UP')
        if oil > 2.0:
            risk -= 4; notes.append('OIL_SPIKE')
        if fg < 25:
            risk -= 5; notes.append('EXTREME_FEAR')
        elif fg > 75:
            risk -= 4; notes.append('EXTREME_GREED')
        risk = clamp(risk, 0, 100)
        mode = 'RISK_ON' if risk >= 63 else 'RISK_OFF' if risk <= 40 else 'NEUTRAL'
        return {
            'ts': utc_now_iso(), 'items': vals, 'vix': vix, 'qqq_chg': qqq, 'spy_chg': spy,
            'dia_chg': dia, 'dxy_chg': dxy, 'us10y_chg': us10y, 'oil_chg': oil,
            'gold_chg': gold, 'fear_greed': fg, 'fear_greed_ok': fg_ok,
            'risk_score': risk, 'mode': mode, 'notes': notes, 'data_ok': data_ok,
            'source': 'yahoo_quote_chart_fallback_v16'
        }

class NewsFilter:
    KEYWORDS={
        'GEOPOLITICAL': ['hormuz','iran','israel','war','missile','attack','strait','gulf','sanction','military'],
        'MACRO_SCHEDULED': ['fomc','federal reserve','powell','cpi','pce','nfp','payroll','inflation','rate cut','rate hike'],
        'ETF_FLOW': ['bitcoin etf','ethereum etf','etf inflow','etf outflow','blackrock','fidelity ibit','gbtc','eth etf'],
        'REGULATION': ['sec','cftc','mika','mica','ban','lawsuit','regulation','regulator'],
        'EXCHANGE_RISK': ['binance','coinbase','kraken','outage','withdrawal halted','proof of reserves'],
        'SECURITY': ['hack','exploit','stolen','bridge exploit','wallet drain','phishing'],
        'NASDAQ_RISK': ['nasdaq','nvidia','semiconductor','ai stocks','risk off','treasury yields']
    }
    def __init__(self): self.http=HttpClient('joanbot-news-v14', timeout=12)
    def fetch(self, query: str='bitcoin OR ethereum OR crypto OR Nasdaq OR Hormuz OR Federal Reserve', max_records: int=25) -> List[Dict[str, Any]]:
        q=urllib.parse.quote(query)
        url='https://api.gdeltproject.org/api/v2/doc/doc'
        try:
            j=self.http.get_json(url, {'query':query,'mode':'artlist','format':'json','maxrecords':max_records,'sort':'hybridrel'}, ttl=300)
            arts=j.get('articles',[]) if isinstance(j,dict) else []
        except Exception:
            arts=[]
        out=[]
        for a in arts:
            title=str(a.get('title',''))[:300]; src=str(a.get('sourceCountry','') or a.get('domain','')); url=str(a.get('url',''))
            out.append(self.score_article(title, src, url, a))
        return out
    def score_article(self, title: str, source: str='', url: str='', raw: Dict[str, Any]|None=None) -> Dict[str, Any]:
        t=title.lower(); cats=[]; severity=0.0; direction='UNKNOWN'; affected=[]
        for cat, words in self.KEYWORDS.items():
            hits=[w for w in words if w in t]
            if hits:
                cats.append(cat); severity += min(22, 8+4*len(hits))
        if any(x in t for x in ['hormuz','war','missile','attack','oil spike']): direction='RISK_OFF'; affected += ['BTC','ETH','NASDAQ','OIL']
        if any(x in t for x in ['etf inflow','rate cut','dovish','approval']): direction='RISK_ON'; affected += ['BTC','ETH']
        if any(x in t for x in ['hack','exploit','withdrawal halted','lawsuit']): direction='CRYPTO_SPECIFIC_RISK'; affected += ['BTC','ETH']
        sev=clamp(severity,0,100)
        bucket='HIGH' if sev>=70 else 'MEDIUM' if sev>=35 else 'LOW'
        return {'ts':utc_now_iso(),'source':source,'title':title,'url':url,'category':','.join(cats) or 'GENERAL','severity':sev,'bucket':bucket,'direction':direction,'affected':sorted(set(affected)),'raw':raw or {}}
    def aggregate(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not events: return {'severity':0,'bucket':'LOW','direction':'UNKNOWN','events':[],'notes':[]}
        severity=max(fnum(e.get('severity')) for e in events)
        cats={}
        for e in events:
            for c in str(e.get('category','')).split(','):
                if c: cats[c]=cats.get(c,0)+1
        bucket='HIGH' if severity>=70 else 'MEDIUM' if severity>=35 else 'LOW'
        # direction by weighted vote
        votes={}
        for e in events:
            d=e.get('direction','UNKNOWN'); votes[d]=votes.get(d,0)+fnum(e.get('severity'))
        direction=max(votes.items(), key=lambda x:x[1])[0] if votes else 'UNKNOWN'
        notes=[f'{k}:{v}' for k,v in sorted(cats.items(), key=lambda x:x[1], reverse=True)[:5]]
        return {'severity':severity,'bucket':bucket,'direction':direction,'events':events[:8],'notes':notes}

class EconomicCalendar:
    # No paid calendar dependency. Conservative scheduled-risk approximation from known event keywords/news.
    def scheduled_risk(self, news_agg: Dict[str, Any]) -> Dict[str, Any]:
        sev=fnum(news_agg.get('severity'))
        notes=list(news_agg.get('notes',[]))
        scheduled=any('MACRO_SCHEDULED' in n for n in notes)
        return {'scheduled_macro_risk': scheduled, 'risk_score': min(100, sev + (15 if scheduled else 0)), 'notes': notes}

