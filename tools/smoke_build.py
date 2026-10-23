"""Smoke HTTP dos executaveis, com dados isolados e sem operacoes no ERP."""
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import time
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]


def free(port):
    with socket.socket() as probe:return probe.connect_ex(('127.0.0.1',port))!=0


def get(port,path):
    with urlopen(f'http://127.0.0.1:{port}{path}',timeout=3) as response:return response.read()


def main():
    if not all(free(port) for port in (8766,8770)):
        raise SystemExit('Feche as instancias HL antes deste teste; nenhum processo sera encerrado automaticamente.')
    for edition,label in [('publicador','Publicador'),('leitor','Leitor')]:
        data=ROOT/'output'/'smoke_v7'/edition
        conference=data/'HL_Conferencia_NFe'
        conference.mkdir(parents=True,exist_ok=True)
        empty=data/'notas_vazias';empty.mkdir(exist_ok=True)
        with sqlite3.connect(conference/'conferencia.sqlite3') as db:
            db.execute('CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT)')
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('watch_dir',str(empty)))
        env={**os.environ,'HL_COORDENADAS_DATA_DIR':str(data/'coordenadas'),'LOCALAPPDATA':str(data),'HL_ABRIR_NAVEGADOR':'0','HL_SKIP_TINY_AUTH':'1'}
        exe=ROOT/'dist'/f'HL_Coordenadas_{label}_V7.exe'
        with (data/'console.log').open('w',encoding='utf-8') as log:
            process=subprocess.Popen([str(exe)],env=env,cwd=ROOT,stdin=subprocess.PIPE,stdout=log,stderr=log,text=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:
                deadline=time.monotonic()+40
                while True:
                    try:
                        health=json.loads(get(8766,'/health'))
                        nfe=json.loads(get(8770,'/api/health'))
                        break
                    except Exception:
                        if process.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Servidor nao iniciou; consulte o log isolado.')
                        time.sleep(.25)
                assert health['edicao']==edition
                assert health['versao']=='7.1'
                assert not health.get('token_salvo')
                assert nfe['ok']
                assert b'/ui/v7.css' in get(8766,'/')
                assert b'2.35' in get(8766,'/ui/v7.css')
                assert b'Public Sans' in get(8770,'/ui/v7.css')
                assert b'countQueue' in get(8770,'/app.js')
                assert json.loads(get(8770,'/api/notes'))['notes']==[]
                print(f'{label}: dois servidores, HTML/CSS/JS/fontes e dados isolados OK')
            finally:
                if process.poll() is None:
                    process.communicate('SAIR\n',timeout=15)
                deadline=time.monotonic()+10
                while not all(free(port) for port in (8766,8770)) and time.monotonic()<deadline:time.sleep(.1)
                assert all(free(port) for port in (8766,8770)),'Portas ainda ocupadas apos SAIR'


if __name__=='__main__':main()
