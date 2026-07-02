from __future__ import annotations
import json, time, urllib.parse, urllib.request
from typing import Any, Dict, Optional

_CACHE: Dict[str, Dict[str, Any]] = {}

class HttpClient:
    def __init__(self, user_agent: str = 'joanbot-v14', timeout: int = 10):
        self.user_agent=user_agent; self.timeout=timeout

    def get_json(self, url: str, params: Optional[Dict[str, Any]]=None, ttl: int = 0) -> Any:
        if params:
            qs=urllib.parse.urlencode({k:v for k,v in params.items() if v is not None})
            url=url + ('&' if '?' in url else '?') + qs
        now=time.time(); c=_CACHE.get(url)
        if ttl and c and now-c['ts']<ttl:
            return c['value']
        req=urllib.request.Request(url, headers={'User-Agent': self.user_agent, 'Accept':'application/json'})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            raw=r.read().decode('utf-8','ignore')
            val=json.loads(raw)
        if ttl:
            _CACHE[url]={'ts': now, 'value': val}
        return val
