"""Publica apenas unidades consultadas no Tiny; nao altera o estoque do ERP."""
from datetime import datetime
import json
import math
import re


class BoxOccupancy:
    def __init__(self,insights,request):
        self.insights=insights
        self.request=request

    @staticmethod
    def position(value):
        if not isinstance(value,dict):raise ValueError('Informe a caixa da consulta.')
        result={key:str(value.get(key) or '').strip().upper() for key in ('setor','prateleira','caixa')}
        for key,pattern in [('setor',r'[1-9]\d*S'),('prateleira',r'P[1-9]\d*'),('caixa',r'C[1-9]\d*')]:
            if not re.fullmatch(pattern,result[key]):raise ValueError('Coordenada da caixa invalida.')
        return result

    def publish(self,position,skus,started):
        position=self.position(position)
        cutoff=datetime.fromisoformat(started)
        items=[]
        with self.insights.connect() as db:
            for sku in sorted(set(skus)):
                record=db.execute('SELECT data FROM products WHERE sku=?',(sku,)).fetchone()
                if not record:raise ValueError('Ocupacao nao salva: falta estoque de um SKU.')
                product=json.loads(record[0])
                value=product.get('disponivel')
                if value is None or isinstance(value,bool):raise ValueError('Ocupacao nao salva: quantidade ausente.')
                value=float(value)
                if not math.isfinite(value):raise ValueError('Ocupacao nao salva: quantidade invalida.')
                stamp=datetime.fromisoformat(product.get('atualizado_em') or '')
                if stamp<cutoff:raise ValueError('Ocupacao nao salva: a consulta de estoque esta desatualizada.')
                items.append({'sku':sku,'unidades':max(0,value)})
        if not items:raise ValueError('Caixa sem SKUs para atualizar.')
        rows=self.request('rpc/atualizar_ocupacao_caixa',{
            'p_setor':position['setor'],'p_prateleira':position['prateleira'],'p_caixa':position['caixa'],
            'p_itens':items,'p_consultado_em':started})
        if not isinstance(rows,list) or {r.get('sku') for r in rows}!=set(skus):
            raise ValueError('O Supabase nao confirmou a ocupacao completa da caixa.')
        return {'salvo':True,'posicao':position,'itens':rows,'unidades':sum(i['unidades'] for i in items)}
