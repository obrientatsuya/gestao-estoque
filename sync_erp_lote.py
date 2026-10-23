from __future__ import annotations

import getpass
import json
import os
import pathlib
import shutil
import sqlite3
import sys
import threading
import time
import uuid
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from enviar_localizacoes_tiny_v3 import TinyV3Client, TOKEN_FILE
from tiny_v3_auth import montar_url_autorizacao, post_form, salvar_tokens
from box_insights import BoxInsights
from shared_sales import SharedSales
from warehouse_controls import WarehouseControls
from app_edition import load_edition
from runtime_config import load_public_config
import mimetypes

SOURCE_DIR = pathlib.Path(__file__).resolve().parent
BUNDLE_DIR = pathlib.Path(getattr(sys, "_MEIPASS", SOURCE_DIR))
FROZEN = bool(getattr(sys, "frozen", False))
EDITION = load_edition(BUNDLE_DIR)
DATA_FOLDER = 'HL_Coordenadas_V5_Publicador' if EDITION.get('edicao') == 'publicador' else 'HL_Coordenadas_V5'
USER_DATA_DIR = pathlib.Path(os.getenv("HL_COORDENADAS_DATA_DIR") or (
  pathlib.Path(os.getenv("LOCALAPPDATA") or pathlib.Path.home()) / DATA_FOLDER
  if FROZEN else SOURCE_DIR
))
APP_DIR = str(BUNDLE_DIR)
HTML_APP = "HL_COORDENADAS_v7.html"
PORTA = 8766
VERSAO_PONTE = "7.2"
DELAY_LOTE_SEG = 0.6
SYNC_LOG_DIR = str(USER_DATA_DIR / "output")
AUDIT_DB = USER_DATA_DIR / "auditoria.sqlite3"
TINY_V3_RPM = int(os.getenv("TINY_V3_RPM", "30"))
MAX_JOBS_FINALIZADOS = 25
JOB_TTL_SEG = 24 * 60 * 60
TOKEN_FILE_ABS = USER_DATA_DIR / TOKEN_FILE
CONFIG_FILE_ABS = USER_DATA_DIR / "tiny_v3_config.json"
PUBLIC_CONFIG = load_public_config(BUNDLE_DIR)
SUPABASE_URL = PUBLIC_CONFIG['supabase_url']
SUPABASE_KEY = PUBLIC_CONFIG['supabase_key']


def inicializar_dados_usuario():
  USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
  pathlib.Path(SYNC_LOG_DIR).mkdir(parents=True, exist_ok=True)
  for nome, destino in (
    ("tiny_v3_config.json", CONFIG_FILE_ABS),
    ("tiny_v3_tokens.json", TOKEN_FILE_ABS),
  ):
    origem = BUNDLE_DIR / nome
    if origem != destino and origem.is_file() and not destino.exists():
      shutil.copy2(origem, destino)


inicializar_dados_usuario()

jobs = {}
jobs_lock = threading.Lock()
tiny_v3_client = None
tiny_v3_lock = threading.Lock()
audit_lock = threading.RLock()


def inicializar_auditoria():
  with sqlite3.connect(AUDIT_DB) as db:
    db.execute("""
      CREATE TABLE IF NOT EXISTS eventos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sku TEXT NOT NULL,
        operador TEXT,
        posicao_anterior TEXT,
        posicao_nova TEXT,
        acao TEXT NOT NULL,
        criado_em TEXT NOT NULL
      )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_eventos_sku_data ON eventos(sku, criado_em DESC)")
    db.execute("""
      CREATE TABLE IF NOT EXISTS snapshot_estoque (
        sku TEXT PRIMARY KEY,
        posicao TEXT,
        observado_em TEXT NOT NULL
      )
    """)


def registrar_evento_local(payload):
  sku = str(payload.get("sku") or "").strip().upper()
  acao = str(payload.get("acao") or "").strip()
  if not sku or not acao:
    return
  with audit_lock, sqlite3.connect(AUDIT_DB) as db:
    db.execute(
      "INSERT INTO eventos(sku,operador,posicao_anterior,posicao_nova,acao,criado_em) VALUES(?,?,?,?,?,?)",
      (
        sku,
        str(payload.get("operador") or ""),
        str(payload.get("posicao_anterior") or ""),
        str(payload.get("posicao_nova") or ""),
        acao,
        str(payload.get("criado_em") or datetime.now().astimezone().isoformat()),
      ),
    )


def listar_eventos_local(sku="", limite=200):
  limite = max(1, min(int(limite or 200), 1000))
  with audit_lock, sqlite3.connect(AUDIT_DB) as db:
    db.row_factory = sqlite3.Row
    if sku:
      rows = db.execute(
        "SELECT * FROM eventos WHERE sku=? ORDER BY criado_em DESC,id DESC LIMIT ?",
        (str(sku).strip().upper(), limite),
      ).fetchall()
    else:
      rows = db.execute("SELECT * FROM eventos ORDER BY criado_em DESC,id DESC LIMIT ?", (limite,)).fetchall()
  return [dict(row) for row in rows]


def reconciliar_snapshot_estoque(itens):
  agora = datetime.now().astimezone().isoformat()
  atual = {
    str(item.get("sku") or "").strip().upper(): str(item.get("posicao") or "").strip()
    for item in itens if isinstance(item, dict) and str(item.get("sku") or "").strip()
  }
  with audit_lock, sqlite3.connect(AUDIT_DB) as db:
    anterior = {row[0]: row[1] for row in db.execute("SELECT sku,posicao FROM snapshot_estoque")}
    ausentes = sorted(set(anterior) - set(atual)) if anterior else []
    for sku in ausentes:
      db.execute(
        "INSERT INTO eventos(sku,operador,posicao_anterior,posicao_nova,acao,criado_em) VALUES(?,?,?,?,?,?)",
        (sku, "SISTEMA", anterior[sku], "", "ausencia_detectada", agora),
      )
    db.execute("DELETE FROM snapshot_estoque")
    db.executemany(
      "INSERT INTO snapshot_estoque(sku,posicao,observado_em) VALUES(?,?,?)",
      [(sku, posicao, agora) for sku, posicao in atual.items()],
    )
  return {"total": len(atual), "ausentes_detectados": len(ausentes), "skus_ausentes": ausentes[:100]}


inicializar_auditoria()


def json_response(handler, payload, status=200):
  data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
  handler.send_response(status)
  handler.send_header("Content-Type", "application/json; charset=utf-8")
  handler.send_header("Access-Control-Allow-Origin", "*")
  handler.send_header("Access-Control-Allow-Headers", "Content-Type")
  handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
  handler.send_header("Content-Length", str(len(data)))
  handler.end_headers()
  handler.wfile.write(data)


def ler_json(handler):
  length = int(handler.headers.get("Content-Length") or 0)
  if length <= 0:
    return {}
  raw = handler.rfile.read(length).decode("utf-8", errors="replace")
  return json.loads(raw or "{}")


def normalizar_item(item):
  sku = str(item.get("sku") or "").strip().upper()
  localizacao = str(item.get("localizacao") or "").strip()
  if not sku or not localizacao:
    return None
  return {"sku": sku, "localizacao": localizacao}


def normalizar_localizacao(valor):
  return str(valor or "").strip()


def get_tiny_v3_client():
  global tiny_v3_client
  with tiny_v3_lock:
    if tiny_v3_client is None:
      tiny_v3_client = TinyV3Client(TOKEN_FILE_ABS, TINY_V3_RPM, CONFIG_FILE_ABS)
    return tiny_v3_client


def reset_tiny_v3_client():
  global tiny_v3_client
  with tiny_v3_lock:
    tiny_v3_client = None


box_insights = BoxInsights(USER_DATA_DIR / 'caixas_tiny.sqlite3', get_tiny_v3_client)
shared_sales = SharedSales(box_insights, SUPABASE_URL, SUPABASE_KEY, USER_DATA_DIR, EDITION)
warehouse = WarehouseControls(box_insights,lambda path,payload=None:shared_sales.request(path,payload))


def atualizar_status_erp_supabase(sku, ok, erro=""):
  sku = str(sku or "").strip().upper()
  if not sku:
    return
  payload = {
    "sincronizado_erp_em": datetime.now().astimezone().isoformat() if ok else None,
    "erro_erp": None if ok else str(erro or "erro")[:500],
  }
  filtro = urlencode({"sku": f"eq.{sku}"})
  req = Request(
    f"{SUPABASE_URL}/rest/v1/estoque?{filtro}",
    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    headers={
      "apikey": SUPABASE_KEY,
      "Authorization": f"Bearer {SUPABASE_KEY}",
      "Content-Type": "application/json",
    },
    method="PATCH",
  )
  try:
    with urlopen(req, timeout=30):
      return
  except HTTPError as exc:
    detalhe = exc.read().decode("utf-8", errors="replace")
    raise RuntimeError(f"Falha ao atualizar status no Supabase: HTTP {exc.code}: {detalhe}") from exc


def registrar_historico_erp(sku, localizacao, ok, mensagem="", pulou=False):
  acao = "tiny_ja_igual" if ok and pulou else ("tiny_sincronizado" if ok else "tiny_erro")
  detalhe = str(localizacao or "").strip()
  if not ok and mensagem:
    detalhe = f"{detalhe} | {str(mensagem)[:350]}".strip(" |")
  payload = {
    "sku": str(sku or "").strip().upper(),
    "operador": "SISTEMA",
    "posicao_anterior": "",
    "posicao_nova": detalhe,
    "acao": acao,
    "criado_em": datetime.now().astimezone().isoformat(),
  }
  registrar_evento_local(payload)
  req = Request(
    f"{SUPABASE_URL}/rest/v1/historico_movimentos",
    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    headers={
      "apikey": SUPABASE_KEY,
      "Authorization": f"Bearer {SUPABASE_KEY}",
      "Content-Type": "application/json",
      "Prefer": "return=minimal",
    },
    method="POST",
  )
  try:
    with urlopen(req, timeout=30):
      return
  except Exception as exc:
    print(f"[AUDITORIA] Nao foi possivel registrar {payload['sku']}: {exc}", flush=True)


def listar_atividade_estoque_supabase(sku="", limite=200):
  limite = max(1, min(int(limite or 200), 1000))
  parametros = {
    "select": "sku,operador,andar,setor,prateleira,caixa,lado,atualizado_em",
    "atualizado_em": "not.is.null",
    "order": "atualizado_em.desc",
    "limit": str(limite),
  }
  sku = str(sku or "").strip().upper()
  if sku:
    parametros["sku"] = f"eq.{sku}"
  req = Request(
    f"{SUPABASE_URL}/rest/v1/estoque?{urlencode(parametros)}",
    headers={
      "apikey": SUPABASE_KEY,
      "Authorization": f"Bearer {SUPABASE_KEY}",
    },
  )
  with urlopen(req, timeout=30) as resposta:
    return json.loads(resposta.read().decode("utf-8"))


def ler_credenciais_v3():
  client_id = os.getenv("TINY_V3_CLIENT_ID", "")
  client_secret = os.getenv("TINY_V3_CLIENT_SECRET", "")
  redirect_uri = os.getenv("TINY_V3_REDIRECT_URI", "http://localhost:8766/callback")
  if CONFIG_FILE_ABS.is_file():
    try:
      config = json.loads(CONFIG_FILE_ABS.read_text(encoding="utf-8"))
      client_id = client_id or str(config.get("client_id") or "").strip()
      client_secret = client_secret or str(config.get("client_secret") or "").strip()
      redirect_uri = str(config.get("redirect_uri") or redirect_uri).strip()
    except Exception:
      pass
  return client_id, client_secret, redirect_uri


def candidatos_sku(sku):
  sku = str(sku or "").strip().upper()
  if not sku:
    return []
  if sku.startswith("NC-"):
    return [sku, sku[3:]]
  return [sku, "NC-" + sku]


def buscar_produto_v3_por_sku(client, sku):
  alvo = str(sku or "").strip().upper()
  for termo in candidatos_sku(alvo):
    query = urlencode({"codigo": termo, "limit": 100, "offset": 0})
    resp = client.request_json("GET", f"/produtos?{query}")
    itens = (resp.get("body") or {}).get("itens") or []
    exatos = [
      item for item in itens
      if str(item.get("sku") or "").strip().upper() in {alvo, termo}
    ]
    if exatos:
      return exatos[0]
    if len(itens) == 1:
      return itens[0]
  return None


def buscar_produto_v3_por_sku_exato(client, sku):
  alvo = str(sku or "").strip().upper()
  if not alvo:
    return None
  query = urlencode({"codigo": alvo, "limit": 100, "offset": 0})
  resp = client.request_json("GET", f"/produtos?{query}")
  itens = (resp.get("body") or {}).get("itens") or []
  return next(
    (item for item in itens if str(item.get("sku") or "").strip().upper() == alvo),
    None,
  )


def consultar_sku_tiny_v3(sku):
  client = get_tiny_v3_client()
  produto = buscar_produto_v3_por_sku(client, sku)
  if not produto:
    return {"encontrado": False, "sku": str(sku or "").strip().upper()}
  detalhe = client.request_json("GET", f"/produtos/{produto.get('id')}").get("body") or produto
  estoque = detalhe.get("estoque") or produto.get("estoque") or {}
  return {
    "encontrado": True,
    "id": detalhe.get("id") or produto.get("id"),
    "sku": detalhe.get("sku") or produto.get("sku"),
    "descricao": detalhe.get("descricao") or produto.get("descricao"),
    "tipo": detalhe.get("tipo") or produto.get("tipo"),
    "localizacao": normalizar_localizacao(estoque.get("localizacao")),
    "controlar": estoque.get("controlar"),
  }


def sincronizar_sku_tiny_v3(sku, localizacao):
  client = get_tiny_v3_client()
  produto = buscar_produto_v3_por_sku(client, sku)
  if not produto:
    return {
      "sku": sku,
      "localizacao": localizacao,
      "ok": False,
      "mensagem": "SKU nao encontrado no Tiny V3",
    }

  item = {
    "id": produto.get("id"),
    "sku": produto.get("sku") or sku,
    "titulo": produto.get("descricao") or sku,
    "localizacao": localizacao,
  }
  resp = client.atualizar_localizacao(item)
  if resp.get("pulou"):
    return {
      "sku": sku,
      "localizacao": localizacao,
      "ok": True,
      "pulou": True,
      "tipo_produto": resp.get("tipo_produto"),
      "mensagem": resp.get("motivo") or "Produto pulado",
    }
  return {
    "sku": sku,
    "localizacao": localizacao,
    "ok": True,
    "pulou": False,
    "http_status": resp.get("status"),
    "mensagem": "Localizacao enviada no Tiny V3",
  }


def renomear_sku_tiny_v3(sku_antigo, sku_novo, localizacao):
  sku_antigo = str(sku_antigo or "").strip().upper()
  sku_novo = str(sku_novo or "").strip().upper()
  localizacao = normalizar_localizacao(localizacao)
  if not sku_antigo or not sku_novo or sku_antigo == sku_novo:
    raise ValueError("Informe codigos SKU diferentes e validos.")

  client = get_tiny_v3_client()
  produto = buscar_produto_v3_por_sku(client, sku_antigo)
  if not produto:
    raise RuntimeError(f"SKU {sku_antigo} nao encontrado no Tiny V3")

  existente = buscar_produto_v3_por_sku_exato(client, sku_novo)
  if existente and str(existente.get("id")) != str(produto.get("id")):
    raise RuntimeError(f"SKU {sku_novo} ja existe no Tiny V3")

  produto_id = produto.get("id")
  detalhe = client.request_json("GET", f"/produtos/{produto_id}").get("body") or produto
  estoque = dict(detalhe.get("estoque") or {})
  estoque.pop("quantidade", None)
  if localizacao:
    estoque["localizacao"] = localizacao

  payload = {
    "sku": sku_novo,
    "descricao": str(detalhe.get("descricao") or produto.get("descricao") or sku_novo),
    "estoque": estoque,
  }
  resposta = client.request_json("PUT", f"/produtos/{produto_id}", payload)
  return {
    "ok": True,
    "sku_antigo": sku_antigo,
    "sku_novo": sku_novo,
    "localizacao": localizacao,
    "http_status": resposta.get("status"),
    "mensagem": "SKU atualizado no Tiny V3",
  }


def salvar_relatorio_job(job_id, resultados):
  os.makedirs(SYNC_LOG_DIR, exist_ok=True)
  caminho = os.path.join(SYNC_LOG_DIR, f"sync_erp_v3_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{job_id[:8]}.json")
  falhas = [r for r in resultados if not r.get("ok")]
  pulados = [r for r in resultados if r.get("pulou")]
  enviados = [r for r in resultados if r.get("ok") and not r.get("pulou")]
  payload = {
    "gerado_em": datetime.now().isoformat(timespec="seconds"),
    "job_id": job_id,
    "total": len(resultados),
    "enviados": len(enviados),
    "pulados": len(pulados),
    "falhas": len(falhas),
    "resultados": resultados,
    "skus_com_erro": falhas,
  }
  with open(caminho, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)
  return caminho


def salvar_relatorio_job_parcial(job_id, itens, resultados, status, mensagem):
  os.makedirs(SYNC_LOG_DIR, exist_ok=True)
  processados = {str(r.get("sku") or "").strip().upper() for r in resultados}
  pendentes = [item for item in itens if str(item.get("sku") or "").strip().upper() not in processados]
  caminho = os.path.join(SYNC_LOG_DIR, f"sync_erp_v3_{status}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{job_id[:8]}.json")
  falhas = [r for r in resultados if not r.get("ok")]
  pulados = [r for r in resultados if r.get("pulou")]
  enviados = [r for r in resultados if r.get("ok") and not r.get("pulou")]
  payload = {
    "gerado_em": datetime.now().isoformat(timespec="seconds"),
    "job_id": job_id,
    "status": status,
    "mensagem": mensagem,
    "total": len(itens),
    "processados": len(resultados),
    "enviados": len(enviados),
    "pulados": len(pulados),
    "falhas": len(falhas),
    "faltam": len(pendentes),
    "pendentes": pendentes,
    "resultados": resultados,
    "skus_com_erro": falhas,
  }
  with open(caminho, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)
  return caminho, pendentes


def atualizar_job(job_id, **campos):
  with jobs_lock:
    job = jobs.get(job_id)
    if not job:
      return
    job.update(campos)


def limpar_jobs_antigos():
  agora = time.time()
  status_finais = {"concluido", "concluido_com_erros", "pausado", "cancelado", "erro"}
  with jobs_lock:
    finalizados = []
    for job_id, job in list(jobs.items()):
      if job.get("status") not in status_finais:
        continue
      finalizado_em = float(job.get("finalizado_em") or job.get("criado_em") or agora)
      if agora - finalizado_em > JOB_TTL_SEG:
        jobs.pop(job_id, None)
      else:
        finalizados.append((finalizado_em, job_id))

    finalizados.sort(reverse=True)
    for _, job_id in finalizados[MAX_JOBS_FINALIZADOS:]:
      jobs.pop(job_id, None)


def ler_controle_job(job_id):
  with jobs_lock:
    job = jobs.get(job_id) or {}
    return job.get("controle", "")


def solicitar_controle_job(job_id, acao):
  with jobs_lock:
    job = jobs.get(job_id)
    if not job:
      return False
    job["controle"] = acao
    job["mensagem"] = "Pausa solicitada; aguardando SKU atual terminar." if acao == "pausar" else "Cancelamento solicitado; aguardando SKU atual terminar."
    return True


def processar_lote(job_id, itens):
  resultados = []
  eventos = []
  enviados_count = 0
  pulados_count = 0

  try:
    print(f"[LOTE] Job {job_id} recebido: {len(itens)} SKU(s)", flush=True)
    atualizar_job(
      job_id,
      status="carregando_tiny",
      mensagem="Carregando API Tiny...",
      iniciado_em=time.time(),
    )

    get_tiny_v3_client()
    print(f"[LOTE] Job {job_id}: API Tiny V3 pronta", flush=True)
    atualizar_job(job_id, status="rodando", mensagem="Enviando SKUs ao Tiny V3...")

    for idx, item in enumerate(itens, start=1):
      controle = ler_controle_job(job_id)
      if controle in ("pausar", "cancelar"):
        status_final = "pausado" if controle == "pausar" else "cancelado"
        msg_final = "Sincronizacao pausada pelo usuario." if controle == "pausar" else "Sincronizacao cancelada pelo usuario."
        relatorio, pendentes = salvar_relatorio_job_parcial(job_id, itens, resultados, status_final, msg_final)
        falhas = [r for r in resultados if not r.get("ok")]
        atualizar_job(
          job_id,
          status=status_final,
          mensagem=msg_final,
          finalizado_em=time.time(),
          atual=len(resultados),
          sincronizados=enviados_count,
          pulados=pulados_count,
          falhas=falhas,
          resultados=resultados,
          eventos=eventos,
          pendentes=pendentes,
          faltam=len(pendentes),
          relatorio=relatorio,
        )
        print(f"[LOTE] Job {job_id} {status_final}: {len(resultados)}/{len(itens)} processados. Relatorio: {relatorio}", flush=True)
        limpar_jobs_antigos()
        return

      sku = item["sku"]
      loc = item["localizacao"]
      print(f"[LOTE] {idx}/{len(itens)} {sku} -> {loc}", flush=True)
      atualizar_job(
        job_id,
        atual=idx,
        sku_atual=sku,
        localizacao_atual=loc,
        mensagem=f"Enviando {sku} -> {loc}",
      )

      try:
        resultado = sincronizar_sku_tiny_v3(sku, loc)
        ok = bool(resultado.get("ok"))
        msg = resultado.get("mensagem") or ""
        if ok and resultado.get("pulou"):
          pulados_count += 1
        elif ok:
          enviados_count += 1
        try:
          atualizar_status_erp_supabase(sku, ok, "" if ok else msg)
        except Exception as exc_status:
          print(f"[LOTE] Aviso status Supabase {sku}: {exc_status}", flush=True)
        registrar_historico_erp(sku, loc, ok, msg, bool(resultado.get("pulou")))
        resultados.append({**resultado, "sku": sku, "localizacao": loc})
        label = "PULOU" if resultado.get("pulou") else ("OK" if ok else "ERRO")
        print(f"[LOTE] {idx}/{len(itens)} {sku}: {label} - {msg}", flush=True)
      except Exception as exc:
        msg = str(exc)
        resultado = {"sku": sku, "localizacao": loc, "ok": False, "mensagem": msg}
        try:
          atualizar_status_erp_supabase(sku, False, msg)
        except Exception as exc_status:
          print(f"[LOTE] Aviso status Supabase {sku}: {exc_status}", flush=True)
        registrar_historico_erp(sku, loc, False, msg)
        resultados.append(resultado)
        label = "ERRO"
        print(f"[LOTE] {idx}/{len(itens)} {sku}: EXCECAO - {msg}", flush=True)

      eventos.append({
        "hora": datetime.now().strftime("%H:%M:%S"),
        "indice": idx,
        "total": len(itens),
        "sku": sku,
        "localizacao": loc,
        "status": label,
        "mensagem": msg,
      })
      falhas = [r for r in resultados if not r.get("ok")]
      atualizar_job(
        job_id,
        atual=idx,
        sku_atual=sku,
        localizacao_atual=loc,
        mensagem=f"{label} {sku}: {msg}",
        sincronizados=enviados_count,
        pulados=pulados_count,
        falhas=falhas,
        resultados=resultados,
        eventos=eventos,
      )
      if idx < len(itens):
        time.sleep(DELAY_LOTE_SEG)

    relatorio = salvar_relatorio_job(job_id, resultados)
    falhas = [r for r in resultados if not r.get("ok")]
    status_final = "concluido_com_erros" if falhas else "concluido"
    atualizar_job(
      job_id,
      status=status_final,
      mensagem="Concluido com erros." if falhas else "Concluido.",
      finalizado_em=time.time(),
      atual=len(itens),
      sincronizados=enviados_count,
      pulados=pulados_count,
      falhas=falhas,
      resultados=resultados,
      eventos=eventos,
      relatorio=relatorio,
    )
    print(f"[LOTE] Job {job_id} concluido: enviados={enviados_count}, pulados={pulados_count}, falhas={len(falhas)}", flush=True)
    print(f"[LOTE] Relatorio: {relatorio}", flush=True)
    if falhas:
      print("[LOTE] SKUs nao sincronizados:", flush=True)
      for r in falhas:
        print(f"  - {r.get('sku')}: {r.get('mensagem')}", flush=True)
    limpar_jobs_antigos()
  except Exception as exc:
    msg = str(exc)
    atualizar_job(job_id, status="erro", mensagem=msg, finalizado_em=time.time())
    print(f"[LOTE] Job {job_id} falhou antes/durante processamento: {msg}", flush=True)
    limpar_jobs_antigos()


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
  daemon_threads = True
  request_queue_size = 128
  allow_reuse_address = False


class Handler(BaseHTTPRequestHandler):
  def log_message(self, fmt, *args):
    return

  def do_OPTIONS(self):
    self.send_response(204)
    self.send_header("Access-Control-Allow-Origin", "*")
    self.send_header("Access-Control-Allow-Headers", "Content-Type")
    self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    self.end_headers()

  def do_GET(self):
    path = urlparse(self.path).path
    query = parse_qs(urlparse(self.path).query)

    if path == '/ui/runtime-config.js':
      body = ('window.HL_CONFIG=' + json.dumps(PUBLIC_CONFIG) + ';').encode('utf-8')
      self.send_response(200)
      self.send_header('Content-Type', 'application/javascript; charset=utf-8')
      self.send_header('Cache-Control', 'no-store')
      self.send_header('Content-Length', str(len(body)))
      self.end_headers()
      self.wfile.write(body)
      return
    if path.startswith('/ui/'):
      root = (BUNDLE_DIR / 'ui').resolve()
      target = (root / path[4:]).resolve()
      if root not in target.parents or not target.is_file():
        return json_response(self, {'ok':False,'erro':'arquivo_nao_encontrado'}, 404)
      body = target.read_bytes()
      self.send_response(200)
      self.send_header('Content-Type', mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
      self.send_header('Cache-Control', 'public, max-age=3600')
      self.send_header('Content-Length', str(len(body)))
      self.end_headers()
      self.wfile.write(body)
      return

    if path == "/health":
      token_ok = TOKEN_FILE_ABS.is_file()
      client_id, client_secret, redirect_uri = ler_credenciais_v3()
      return json_response(self, {
        "ok": True,
        "servico": "HL Localizador lote",
        "versao": VERSAO_PONTE,
        "edicao": EDITION.get('edicao', 'padrao'),
        "lote": True,
        "tiny_api": "v3",
        "token_salvo": token_ok,
        "credenciais_salvas": bool(client_id and client_secret),
        "autorizar_url": "/autorizar-tiny" if client_id and client_secret else "",
      })

    if path == "/autorizar-tiny":
      client_id, client_secret, redirect_uri = ler_credenciais_v3()
      if not client_id or not client_secret:
        return json_response(self, {
          "ok": False,
          "erro": "credenciais_nao_configuradas",
          "mensagem": "Crie tiny_v3_config.json com client_id e client_secret.",
        }, 500)
      destino = montar_url_autorizacao(client_id, redirect_uri)
      self.send_response(302)
      self.send_header("Location", destino)
      self.end_headers()
      return

    if path == "/callback":
      code = (query.get("code") or [""])[0]
      erro = (query.get("error") or [""])[0]
      if erro:
        body = f"Tiny retornou erro: {erro}".encode("utf-8")
        self.send_response(400)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return
      if not code:
        body = b"Callback recebido sem code."
        self.send_response(400)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return
      client_id, client_secret, redirect_uri = ler_credenciais_v3()
      try:
        tokens = post_form({
          "grant_type": "authorization_code",
          "client_id": client_id,
          "client_secret": client_secret,
          "redirect_uri": redirect_uri,
          "code": code,
        })
        salvar_tokens(tokens, TOKEN_FILE_ABS)
        reset_tiny_v3_client()
        body = "Autorizacao Tiny V3 concluida. Pode voltar ao HL Coordenadas.".encode("utf-8")
        self.send_response(200)
      except BaseException as exc:
        body = f"Falha ao salvar token Tiny V3: {exc}".encode("utf-8", errors="replace")
        self.send_response(500)
      self.send_header("Content-Type", "text/plain; charset=utf-8")
      self.send_header("Content-Length", str(len(body)))
      self.end_headers()
      self.wfile.write(body)
      return

    if path == "/sync-progresso":
      limpar_jobs_antigos()
      job_id = (query.get("id") or [""])[0]
      with jobs_lock:
        job = jobs.get(job_id)
      if not job:
        return json_response(self, {"ok": False, "erro": "job_nao_encontrado"}, 404)
      return json_response(self, job)

    if path == "/consultar":
      sku = (query.get("sku") or [""])[0]
      try:
        data = consultar_sku_tiny_v3(sku)
        return json_response(self, data)
      except Exception as exc:
        return json_response(self, {"encontrado": False, "erro": str(exc)}, 500)

    if path == "/audit-events":
      sku = (query.get("sku") or [""])[0]
      limite = (query.get("limit") or ["200"])[0]
      return json_response(self, {"ok": True, "eventos": listar_eventos_local(sku, limite)})

    if path == "/audit-current":
      sku = (query.get("sku") or [""])[0]
      limite = (query.get("limit") or ["200"])[0]
      try:
        itens = listar_atividade_estoque_supabase(sku, limite)
        return json_response(self, {"ok": True, "itens": itens})
      except Exception as exc:
        return json_response(self, {"ok": False, "erro": str(exc)}, 502)

    if path == "/box-insights-job":
      job = shared_sales.job((query.get('id') or [''])[0])
      return json_response(self, {"ok": bool(job), **job}, 200 if job else 404)

    if path == '/invoice-import':
      return json_response(self, {'ok': True, **box_insights.import_status()})

    if path == '/sales-summary':
      try:
        return json_response(self, {'ok': True, 'itens':shared_sales.all_shared()})
      except ValueError as exc:
        return json_response(self, {'ok':False,'erro':str(exc)}, 502)

    if path == '/sales-sync':
      return json_response(self, {'ok':True, **shared_sales.status()})
    if path == '/units-refresh':
      return json_response(self, {'ok':True, **warehouse.status()})
    if path == '/box-locks':
      try:return json_response(self, {'ok':True,'itens':warehouse.locks()})
      except ValueError as exc:return json_response(self, {'ok':False,'erro':str(exc)},502)

    html_path = os.path.join(APP_DIR, HTML_APP)
    if path in ("", "/") and os.path.isfile(html_path):
      with open(html_path, "rb") as f:
        data = f.read()
      self.send_response(200)
      self.send_header("Content-Type", "text/html; charset=utf-8")
      self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
      self.send_header("Pragma", "no-cache")
      self.send_header("Content-Length", str(len(data)))
      self.end_headers()
      self.wfile.write(data)
      return

    return json_response(self, {"ok": False, "erro": "rota_nao_encontrada"}, 404)

  def do_POST(self):
    path = urlparse(self.path).path

    try:
      data = ler_json(self)
    except Exception as exc:
      return json_response(self, {"ok": False, "erro": "json_invalido", "mensagem": str(exc)}, 400)
    if not isinstance(data, dict):
      return json_response(self, {"ok": False, "erro": "json_invalido", "mensagem": "O corpo deve ser um objeto JSON."}, 400)

    if path == '/invoice-import':
      return json_response(self, {'ok':False,'erro':'Importacao de notas fiscais desativada temporariamente.'}, 409)
    if path == '/units-refresh':
      try:
        if shared_sales.busy:raise ValueError('Aguarde a atualizacao de vendas terminar antes de atualizar todas as unidades.')
        return json_response(self, {'ok':True,**warehouse.start()})
      except ValueError as exc:return json_response(self, {'ok':False,'erro':str(exc)},409)
    if path == '/box-lock':
      try:return json_response(self, {'ok':True,'item':warehouse.set_lock(data.get('posicao'),data.get('bloqueada'))})
      except ValueError as exc:return json_response(self, {'ok':False,'erro':str(exc)},409)
    if path == '/sales-sync':
      try:
        if data.get('acao') == 'iniciar':
          if warehouse.active:raise ValueError('Aguarde a atualizacao de unidades terminar antes de consultar vendas.')
          if not shared_sales.publisher_key():
            raise ValueError('Configure vendas_publicador.json apenas no PC autorizado.')
          shared_sales.start(None, refresh=True)
          result = shared_sales.status()
        elif data.get('acao') == 'pausar':
          result = shared_sales.status(pause=True)
        else:
          return json_response(self, {'ok': False, 'erro': 'Acao invalida'}, 400)
        return json_response(self, {'ok': True, **result})
      except ValueError as exc:
        return json_response(self, {'ok': False, 'erro': str(exc)}, 409)

    if path == '/box-insights':
      raw_skus = data.get('skus')
      if not isinstance(raw_skus, list) or len(raw_skus) > 150:
        return json_response(self, {'ok': False, 'erro': 'Informe ate 150 SKUs por caixa.'}, 400)
      skus = sorted({str(s).strip().upper() for s in raw_skus if str(s).strip()})
      if any(len(s) > 100 for s in skus):
        return json_response(self, {'ok': False, 'erro': 'SKU invalido'}, 400)
      action = data.get('acao', 'cache')
      if action == 'cache':
        return json_response(self, {'ok': True, **shared_sales.cache(skus)})
      if action not in ('consultar', 'atualizar', 'continuar'):
        return json_response(self, {'ok': False, 'erro': 'Acao invalida'}, 400)
      if not skus:
        return json_response(self, {'ok': True, **shared_sales.cache([])})
      try:
        if warehouse.active:raise ValueError('A atualizacao geral de unidades ja esta consultando o estoque. Aguarde sua conclusao.')
        jid = shared_sales.start(skus, action == 'atualizar', action == 'continuar', data.get('posicao'))
        return json_response(self, {'ok': True, 'job_id': jid})
      except ValueError as exc:
        return json_response(self, {'ok': False, 'erro': str(exc)}, 409)

    if path == "/sync-lote":
      itens_recebidos = data.get("itens", [])
      if not isinstance(itens_recebidos, list):
        return json_response(self, {"ok": False, "erro": "itens_invalidos", "mensagem": "O campo itens deve ser uma lista."}, 400)
      itens = [normalizar_item(i) for i in itens_recebidos if isinstance(i, dict)]
      itens = [i for i in itens if i]
      if not itens:
        return json_response(self, {"ok": False, "erro": "lote_vazio"}, 400)

      limpar_jobs_antigos()
      job_id = uuid.uuid4().hex
      with jobs_lock:
        jobs[job_id] = {
          "ok": True,
          "id": job_id,
          "status": "pendente",
          "total": len(itens),
          "atual": 0,
          "sincronizados": 0,
          "pulados": 0,
          "falhas": [],
          "resultados": [],
          "eventos": [],
          "pendentes": itens,
          "faltam": len(itens),
          "controle": "",
          "criado_em": time.time(),
        }

      t = threading.Thread(target=processar_lote, args=(job_id, itens), daemon=True)
      t.start()
      return json_response(self, {"ok": True, "job_id": job_id, "total": len(itens), "status": "pendente"})

    if path == "/audit-events":
      try:
        registrar_evento_local(data)
        return json_response(self, {"ok": True})
      except Exception as exc:
        return json_response(self, {"ok": False, "erro": str(exc)}, 500)

    if path == "/audit-snapshot":
      itens = data.get("itens") or []
      if not isinstance(itens, list):
        return json_response(self, {"ok": False, "erro": "itens_invalidos"}, 400)
      try:
        return json_response(self, {"ok": True, **reconciliar_snapshot_estoque(itens)})
      except Exception as exc:
        return json_response(self, {"ok": False, "erro": str(exc)}, 500)

    if path == "/sync-controle":
      job_id = str(data.get("id") or "").strip()
      acao = str(data.get("acao") or "").strip().lower()
      if acao not in ("pausar", "cancelar"):
        return json_response(self, {"ok": False, "erro": "acao_invalida"}, 400)
      if not solicitar_controle_job(job_id, acao):
        return json_response(self, {"ok": False, "erro": "job_nao_encontrado"}, 404)
      return json_response(self, {"ok": True, "id": job_id, "acao": acao})

    if path == "/sync":
      sku = str(data.get("sku") or "").strip().upper()
      localizacao = str(data.get("localizacao") or "").strip()
      if not sku or not localizacao:
        return json_response(self, {"ok": False, "erro": "sku e localizacao sao obrigatorios"}, 400)
      try:
        resultado = sincronizar_sku_tiny_v3(sku, localizacao)
        ok = bool(resultado.get("ok"))
        msg = resultado.get("mensagem") or ""
        try:
          atualizar_status_erp_supabase(sku, ok, "" if ok else msg)
        except Exception as exc_status:
          print(f"[SYNC] Aviso status Supabase {sku}: {exc_status}", flush=True)
        registrar_historico_erp(sku, localizacao, ok, msg, bool(resultado.get("pulou")))
        return json_response(self, {**resultado, "ok": ok, "mensagem": msg})
      except Exception as exc:
        msg = str(exc)
        try:
          atualizar_status_erp_supabase(sku, False, msg)
        except Exception as exc_status:
          print(f"[SYNC] Aviso status Supabase {sku}: {exc_status}", flush=True)
        registrar_historico_erp(sku, localizacao, False, msg)
        return json_response(self, {
          "ok": False,
          "erro": "falha_tiny",
          "sku": sku,
          "localizacao": localizacao,
          "mensagem": msg,
        }, 502)

    if path == "/renomear-sku":
      sku_antigo = str(data.get("sku_antigo") or "").strip().upper()
      sku_novo = str(data.get("sku_novo") or "").strip().upper()
      localizacao = str(data.get("localizacao") or "").strip()
      if not sku_antigo or not sku_novo or sku_antigo == sku_novo:
        return json_response(self, {"ok": False, "erro": "skus_invalidos", "mensagem": "Informe SKUs diferentes e validos."}, 400)
      try:
        resultado = renomear_sku_tiny_v3(sku_antigo, sku_novo, localizacao)
        return json_response(self, resultado)
      except Exception as exc:
        return json_response(self, {
          "ok": False,
          "erro": "falha_renomear_tiny",
          "mensagem": str(exc),
        }, 502)

    return json_response(self, {"ok": False, "erro": "rota_nao_encontrada"}, 404)


def abrir_paginas_iniciais():
  if os.getenv('HL_ABRIR_NAVEGADOR', '1') == '0':
    return
  time.sleep(1)
  app_url = f"http://127.0.0.1:{PORTA}/"
  try:
    webbrowser.open_new_tab(app_url)
    time.sleep(0.5)
    client_id, client_secret, _ = ler_credenciais_v3()
    if client_id and client_secret and EDITION.get('edicao') != 'leitor':
      webbrowser.open_new_tab(f"{app_url}autorizar-tiny")
  except Exception as exc:
    print(f"Nao foi possivel abrir o navegador automaticamente: {exc}", flush=True)
    print(f"Abra manualmente: {app_url}", flush=True)


def trocar_chaves_console():
  client_id_atual, client_secret_atual, redirect_atual = ler_credenciais_v3()
  print("\nALTERAR CHAVES DO TINY", flush=True)
  print("Pressione Enter para manter o valor atual.", flush=True)
  novo_client_id = input("Novo client_id: ").strip() or client_id_atual
  try:
    novo_client_secret = getpass.getpass("Novo client_secret: ").strip() or client_secret_atual
  except Exception:
    novo_client_secret = input("Novo client_secret: ").strip() or client_secret_atual
  novo_redirect = input(f"Redirect URI [{redirect_atual}]: ").strip() or redirect_atual
  if not novo_client_id or not novo_client_secret:
    print("Alteracao cancelada: client_id e client_secret nao podem ficar vazios.\n", flush=True)
    return

  config = {
    "client_id": novo_client_id,
    "client_secret": novo_client_secret,
    "redirect_uri": novo_redirect,
  }
  CONFIG_FILE_ABS.write_text(
    json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
  )
  if TOKEN_FILE_ABS.exists():
    TOKEN_FILE_ABS.unlink()
  reset_tiny_v3_client()
  print("Chaves atualizadas. Abrindo a autorizacao do Tiny...\n", flush=True)
  webbrowser.open_new_tab(f"http://127.0.0.1:{PORTA}/autorizar-tiny")


def console_comandos(server):
  print("\nComandos disponiveis:", flush=True)
  print("  TROCAR CHAVES  altera as credenciais do Tiny", flush=True)
  print("  AUTORIZAR      abre novamente a autorizacao do Tiny", flush=True)
  print("  SAIR           encerra o programa\n", flush=True)
  while True:
    try:
      comando = input("> ").strip().upper()
    except (EOFError, KeyboardInterrupt):
      return
    if comando in ("TROCAR CHAVES", "CHAVES"):
      trocar_chaves_console()
    elif comando == "AUTORIZAR":
      webbrowser.open_new_tab(f"http://127.0.0.1:{PORTA}/autorizar-tiny")
    elif comando == "SAIR":
      print("Encerrando HL Coordenadas V7...", flush=True)
      server.shutdown()
      return
    elif comando:
      print("Comando invalido. Use TROCAR CHAVES, AUTORIZAR ou SAIR.", flush=True)


def main():
  if os.name == "nt":
    os.system("title HL Coordenadas V7")
  try:
    server = ThreadingHTTPServer(("127.0.0.1", PORTA), Handler)
  except OSError as exc:
    print(f"Nao foi possivel iniciar na porta {PORTA}: {exc}", flush=True)
    print("Feche outra instancia do HL Coordenadas e tente novamente.", flush=True)
    input("Pressione Enter para fechar...")
    return
  print(f"HL Coordenadas V7 rodando em http://127.0.0.1:{PORTA}/", flush=True)
  print(f"Edicao: {EDITION.get('edicao', 'padrao').upper()}", flush=True)
  print(f"Dados locais: {USER_DATA_DIR}", flush=True)
  threading.Thread(target=console_comandos, args=(server,), daemon=True).start()
  threading.Thread(target=abrir_paginas_iniciais, daemon=True).start()
  try:
    server.serve_forever()
  finally:
    server.server_close()


if __name__ == "__main__":
  main()
