"""Testa a troca real de EXEs com dados isolados; requer copia antiga em tmp."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
from smoke_build import free,get

ROOT=Path(__file__).resolve().parents[1]


def wait_version(process,version):
    deadline=time.monotonic()+45
    while time.monotonic()<deadline:
        try:
            if json.loads(get(8766,'/health'))['versao']==version:return
        except Exception:pass
        if process.poll() is not None:raise RuntimeError('EXE encerrou antes de iniciar.')
        time.sleep(.2)
    raise RuntimeError('Tempo esgotado ao aguardar a versao '+version)


def main():
    if not all(free(p) for p in (8766,8770)):raise SystemExit('Feche as instancias HL antes do teste.')
    old_exe=ROOT/'tmp/instancia_antiga/HL_Coordenadas_Leitor_V7.exe'
    if not old_exe.is_file():raise SystemExit('Separe a copia V7.1 antiga em tmp/instancia_antiga primeiro.')
    data=ROOT/'output/replacement_test'
    conference=data/'HL_Conferencia_NFe';conference.mkdir(parents=True,exist_ok=True)
    empty=data/'notas_vazias';empty.mkdir(exist_ok=True)
    with sqlite3.connect(conference/'conferencia.sqlite3') as db:
        db.execute('CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT)')
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('watch_dir',str(empty)))
    env={**os.environ,'HL_COORDENADAS_DATA_DIR':str(data/'coordenadas'),'LOCALAPPDATA':str(data),'HL_ABRIR_NAVEGADOR':'0','HL_SKIP_TINY_AUTH':'1'}
    kwargs=dict(env=env,cwd=ROOT,stdin=subprocess.PIPE,text=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    new=None
    with (data/'old.log').open('w') as old_log,(data/'new.log').open('w') as new_log:
        old=subprocess.Popen([str(old_exe)],stdout=old_log,stderr=old_log,**kwargs)
        try:
            wait_version(old,'7.1')
            new=subprocess.Popen([str(ROOT/'dist/HL_Coordenadas_Leitor_V7.exe')],stdout=new_log,stderr=new_log,**kwargs)
            wait_version(new,'7.2')
            old.wait(timeout=15)
            assert json.loads(get(8770,'/api/health'))['ok']
            print('Troca automatica V7.1 -> V7.2 OK: processo antigo fechado e dois servidores novos ativos.')
        finally:
            for process in (new,old):
                if process is not None and process.poll() is None:process.communicate('SAIR\n',timeout=15)


if __name__=='__main__':main()
