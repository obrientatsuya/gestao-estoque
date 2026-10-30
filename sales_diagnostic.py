"""Amostra estrutural local, sem textos livres ou credenciais da API."""
import math
import re


def sanitized(value,key='',depth=0):
    if depth>6:return '<limite>'
    if isinstance(value,dict):
        return {str(k):sanitized(v,str(k),depth+1) for k,v in list(value.items())[:80]
                if not re.search(r'token|secret|senha|password|authorization|api.?key|client.?id',str(k),re.I)}
    if isinstance(value,list):return [sanitized(v,key,depth+1) for v in value[:20]]
    if value is None or isinstance(value,bool):return value
    if key.lower()=='tipo' and str(value).upper() in ('S','E','B','SAIDA','ENTRADA','BALANCO'):return value
    numeric=re.match(r'^(valor|preco|quantidade|saldo|unidades|total|limit|offset)',key,re.I)
    if numeric and isinstance(value,(int,float)) and math.isfinite(value):return value
    if numeric and isinstance(value,str) and re.fullmatch(r'[+-]?\d+(?:[.,]\d+)?',value):return value
    return '<'+type(value).__name__+'>'
