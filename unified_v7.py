from __future__ import annotations

import ctypes
import csv
import io
import os
import re
import socket
import subprocess
import threading
import time
import traceback
import webbrowser

import sync_erp_lote as coordenadas
from HL_Conferencia_NFe import app as conferencia


MUTEX_HANDLE = None


def adquirir_instancia_unica() -> bool:
  global MUTEX_HANDLE
  if not hasattr(ctypes, "windll"):
    return True
  kernel=ctypes.windll.kernel32
  kernel.CreateMutexW.restype=ctypes.c_void_p
  kernel.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.c_bool,ctypes.c_wchar_p]
  kernel.CloseHandle.argtypes=[ctypes.c_void_p]
  for attempt in range(30):
    MUTEX_HANDLE=kernel.CreateMutexW(None,False,"Local\\HL_Coordenadas_V5_Unificado")
    if not MUTEX_HANDLE:raise RuntimeError('Nao foi possivel criar o controle de instancia.')
    if kernel.GetLastError()!=183:return True
    kernel.CloseHandle(MUTEX_HANDLE)
    MUTEX_HANDLE=None
    if attempt==0:
      print('Fechando a instancia anterior do HL para abrir esta versao...',flush=True)
      if not encerrar_instancias_legadas():return False
    time.sleep(.2)
  print('A instancia anterior ainda esta encerrando. Tente novamente.',flush=True)
  return False


def porta_ocupada(porta: int) -> bool:
  with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as teste:
    teste.settimeout(0.25)
    return teste.connect_ex(("127.0.0.1", porta)) == 0


def encerrar_instancias_legadas() -> bool:
  if os.name != "nt":
    return not any(porta_ocupada(p) for p in (8766, 8770))
  flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
  try:
    saida = subprocess.check_output(
      ["netstat", "-ano", "-p", "tcp"], text=True, errors="replace", creationflags=flags
    )
  except Exception:
    saida = ""
  pids = set()
  for linha in saida.splitlines():
    match = re.search(r"127\.0\.0\.1:(8766|8770)\s+\S+\s+LISTENING\s+(\d+)", linha, re.I)
    if match and int(match.group(2)) != os.getpid():
      pids.add(int(match.group(2)))
  nomes_permitidos = {"hl_coordenadas_v5.exe", "hl_conferencia_nfe_versao_final.exe",
                      "hl_coordenadas_publicador.exe", "hl_coordenadas_leitor.exe",
                      "hl_coordenadas_publicador_v7.exe", "hl_coordenadas_leitor_v7.exe"}
  for pid in pids:
    try:
      bruto = subprocess.check_output(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
        text=True, errors="replace", creationflags=flags
      )
      linha = next(csv.reader(io.StringIO(bruto)), [])
      nome = (linha[0] if linha else "").strip().lower()
      if nome in nomes_permitidos or re.fullmatch(r'hl_coordenadas_(publicador|leitor)_v\d+(?: \(\d+\))?\.exe',nome):
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], creationflags=flags, capture_output=True)
    except Exception:
      continue
  for _ in range(20):
    if not any(porta_ocupada(p) for p in (8766, 8770)):
      return True
    time.sleep(0.15)
  return not any(porta_ocupada(p) for p in (8766, 8770))


class TinyCompartilhado:
  def ensure_token(self):
    client = coordenadas.get_tiny_v3_client()
    client._refresh_se_precisar()
    return client.tokens

  def product_by_sku(self, sku: str):
    produto = coordenadas.consultar_sku_tiny_v3(sku)
    if not produto.get("encontrado"):
      return None
    return {
      "id": produto.get("id"),
      "sku": produto.get("sku") or sku,
      "description": produto.get("descricao") or sku,
      "location": produto.get("localizacao") or "",
    }


def iniciar_conferencia():
  try:
    conferencia.TINY = TinyCompartilhado()
    conferencia.main(open_browser=False, start_auth=False)
  except Exception:
    traceback.print_exc()


def main():
  if not adquirir_instancia_unica():
    return
  if not encerrar_instancias_legadas():
    print("As portas 8766 ou 8770 estao sendo usadas por outro programa.", flush=True)
    print("Feche o programa que ocupa essas portas e abra o HL Coordenadas novamente.", flush=True)
    webbrowser.open_new_tab("http://127.0.0.1:8766/")
    return
  threading.Thread(target=iniciar_conferencia, name="conferencia-nfe", daemon=True).start()
  coordenadas.main()


if __name__ == "__main__":
  main()
