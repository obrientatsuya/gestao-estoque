from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE_URL = "https://api.tiny.com.br/public-api/v3"
TOKEN_URL = "https://accounts.tiny.com.br/realms/tiny/protocol/openid-connect/token"
JSON_PADRAO = Path("output/setores_tiny.json")
TOKEN_FILE = Path("tiny_v3_tokens.json")
CONFIG_FILE = Path("tiny_v3_config.json")
OUT_DIR = Path("output")


def formatar_duracao(segundos: float) -> str:
    segundos = max(0, int(segundos))
    horas, resto = divmod(segundos, 3600)
    minutos, seg = divmod(resto, 60)
    if horas:
        return f"{horas}h {minutos}min {seg}s"
    if minutos:
        return f"{minutos}min {seg}s"
    return f"{seg}s"


class RateLimiter:
    def __init__(self, rpm: int):
        self.intervalo = 60.0 / float(rpm)
        self.lock = threading.Lock()
        self.proxima = 0.0

    def aguardar(self):
        with self.lock:
            agora = time.monotonic()
            espera = self.proxima - agora
            if espera > 0:
                time.sleep(espera)
            self.proxima = time.monotonic() + self.intervalo


class TinyV3Client:
    def __init__(self, token_file: Path, rpm: int, config_file: Path | None = None):
        self.token_file = token_file
        self.config_file = config_file or CONFIG_FILE
        self.ratelimit = RateLimiter(rpm)
        self.token_lock = threading.Lock()
        self.tokens = self._ler_tokens()

    def _ler_tokens(self) -> dict:
        if not self.token_file.is_file():
            raise SystemExit(f"Token nao encontrado: rode tiny_v3_auth.py antes ({self.token_file}).")
        return json.loads(self.token_file.read_text(encoding="utf-8"))

    def _salvar_tokens(self, tokens: dict):
        tokens = dict(tokens)
        tokens["obtido_em"] = int(time.time())
        tokens["expires_at"] = tokens["obtido_em"] + int(tokens.get("expires_in", 0))
        self.token_file.write_text(json.dumps(tokens, ensure_ascii=False, indent=2), encoding="utf-8")
        self.tokens = tokens

    def _ler_credenciais(self) -> tuple[str, str]:
        client_id = os.getenv("TINY_V3_CLIENT_ID", "")
        client_secret = os.getenv("TINY_V3_CLIENT_SECRET", "")
        if client_id and client_secret:
            return client_id, client_secret

        if self.config_file.is_file():
            config = json.loads(self.config_file.read_text(encoding="utf-8"))
            client_id = str(config.get("client_id") or "").strip()
            client_secret = str(config.get("client_secret") or "").strip()
        return client_id, client_secret

    def _refresh_se_precisar(self, forcar: bool = False):
        if not forcar and int(self.tokens.get("expires_at", 0)) - time.time() > 90:
            return

        with self.token_lock:
            if not forcar and int(self.tokens.get("expires_at", 0)) - time.time() > 90:
                return

            client_id, client_secret = self._ler_credenciais()
            if not client_id or not client_secret:
                if forcar:
                    raise RuntimeError("Defina TINY_V3_CLIENT_ID/TINY_V3_CLIENT_SECRET ou tiny_v3_config.json para renovar token.")
                return

            body = urlencode({
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": self.tokens["refresh_token"],
            }).encode("utf-8")
            req = Request(TOKEN_URL, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
            with urlopen(req, timeout=40) as resp:
                self._salvar_tokens(json.loads(resp.read().decode("utf-8")))

    def request_read_json(self, path, limiter):
        return self.request_json('GET', path, limiter=limiter)

    def request_json(self, method: str, path: str, payload: dict | None = None, retry_auth: bool = True, limiter=None):
        self._refresh_se_precisar()
        (limiter or self.ratelimit).aguardar()

        data = None
        headers = {
            "Authorization": f"Bearer {self.tokens['access_token']}",
            "Accept": "application/json",
        }
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = Request(f"{BASE_URL}{path}", data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=60) as resp:
                raw = resp.read()
                quota = {k: resp.headers[k] for k in ('x-ratelimit-limit', 'x-ratelimit-remaining', 'retry-after')
                         if resp.headers.get(k) is not None}
                return {"status": resp.status, "body": json.loads(raw.decode("utf-8")) if raw else None,
                        "quota": quota}
        except HTTPError as exc:
            if exc.code == 401 and retry_auth:
                self._refresh_se_precisar(forcar=True)
                return self.request_json(method, path, payload, retry_auth=False, limiter=limiter)
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc

    def atualizar_localizacao(self, item: dict):
        produto_id = str(item.get("id") or "").strip()
        sku = str(item.get("sku") or "").strip()
        descricao = str(item.get("titulo") or "").strip()
        localizacao = str(item.get("localizacao") or "").strip()

        if not produto_id:
            raise RuntimeError("Item sem id do produto")
        if not sku or not descricao or not localizacao:
            raise RuntimeError("Item sem sku, descricao ou localizacao")

        atual = self.request_json("GET", f"/produtos/{produto_id}")
        produto_atual = atual.get("body") or {}
        tipo_produto = str(produto_atual.get("tipo") or "").upper()
        if tipo_produto == "K":
            return {
                "status": 0,
                "body": None,
                "pulou": True,
                "motivo": "Produto tipo K (kit) nao recebe localizacao em massa",
                "tipo_produto": tipo_produto,
            }

        estoque_atual = dict(produto_atual.get("estoque") or {})
        localizacao_atual = str(estoque_atual.get("localizacao") or "").strip()
        if localizacao_atual == localizacao:
            return {
                "status": 0,
                "body": None,
                "pulou": True,
                "motivo": "Localizacao ja estava igual; nada enviado",
                "tipo_produto": tipo_produto,
                "localizacao_atual": localizacao_atual,
            }

        if estoque_atual.get("controlar") is False:
            return {
                "status": 0,
                "body": None,
                "pulou": True,
                "motivo": "Produto com controle de estoque desligado; localizacao nao enviada por seguranca",
                "tipo_produto": tipo_produto,
            }

        estoque_atual.pop("quantidade", None)
        estoque_atual["controlar"] = True
        estoque_atual["localizacao"] = localizacao

        payload = {
            "sku": str(produto_atual.get("sku") or sku),
            "descricao": str(produto_atual.get("descricao") or descricao),
            "estoque": estoque_atual,
        }
        return self.request_json("PUT", f"/produtos/{produto_id}", payload)


def carregar_itens(caminho: Path, setor: str, limite: int, skip: int = 0) -> list[dict]:
    data = json.loads(caminho.read_text(encoding="utf-8"))
    itens = data.get("itens", [])
    if setor:
        itens = [item for item in itens if str(item.get("setor", "")).upper() == setor.upper()]
    if skip:
        itens = itens[skip:]
    if limite:
        itens = itens[:limite]
    return itens


def chunks(lista: list[dict], tamanho: int):
    for i in range(0, len(lista), tamanho):
        yield lista[i:i + tamanho]


def enviar_com_retry(client: TinyV3Client, item: dict, tentativas: int) -> dict:
    ultimo = ""
    for tentativa in range(1, tentativas + 1):
        try:
            resp = client.atualizar_localizacao(item)
            if resp.get("pulou"):
                return {
                    **item,
                    "ok": True,
                    "pulou": True,
                    "tentativas": tentativa,
                    "http_status": resp["status"],
                    "tipo_produto": resp.get("tipo_produto"),
                    "mensagem": resp.get("motivo") or "Produto pulado",
                }
            return {
                **item,
                "ok": True,
                "pulou": False,
                "tentativas": tentativa,
                "http_status": resp["status"],
                "mensagem": "Localizacao enviada",
            }
        except (TimeoutError, URLError, RuntimeError) as exc:
            ultimo = str(exc)
            if "HTTP 429" in ultimo or "timeout" in ultimo.lower() or isinstance(exc, (TimeoutError, URLError)):
                time.sleep(min(30, 3 * tentativa))
                continue
            if tentativa < tentativas:
                time.sleep(1.5 * tentativa)

    return {**item, "ok": False, "tentativas": tentativas, "mensagem": ultimo}


def salvar_resultado(path: Path, resultados: list[dict], total_planejado: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "total_planejado": total_planejado,
        "processados": len(resultados),
        "ok": sum(1 for r in resultados if r.get("ok")),
        "erro": sum(1 for r in resultados if not r.get("ok")),
        "resultados": resultados,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def carregar_resultados_anteriores(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("resultados", [])


def salvar_status(
    status_path: Path,
    pendentes_path: Path,
    falhas_path: Path,
    itens: list[dict],
    resultados: list[dict],
    inicio: float,
    finalizado: bool = False,
):
    feitos_skus = {str(r.get("sku") or "") for r in resultados}
    pendentes = [item for item in itens if str(item.get("sku") or "") not in feitos_skus]
    falhas = [r for r in resultados if not r.get("ok")]
    ok = sum(1 for r in resultados if r.get("ok"))
    pulados = sum(1 for r in resultados if r.get("pulou"))
    enviados = sum(1 for r in resultados if r.get("ok") and not r.get("pulou"))
    processados = len(resultados)
    total = len(itens)
    elapsed = time.time() - inicio
    taxa = processados / elapsed if elapsed > 0 and processados else 0
    eta = len(pendentes) / taxa if taxa else 0

    status = {
        "atualizado_em": datetime.now().isoformat(timespec="seconds"),
        "finalizado": finalizado,
        "total": total,
        "processados": processados,
        "ok": ok,
        "enviados": enviados,
        "pulados": pulados,
        "falhas": len(falhas),
        "faltam": len(pendentes),
        "percentual": round((processados / total) * 100, 2) if total else 100,
        "tempo_decorrido": formatar_duracao(elapsed),
        "tempo_restante_estimado": formatar_duracao(eta) if pendentes else "0s",
        "ultimo_sku": resultados[-1].get("sku") if resultados else "",
        "arquivos": {
            "status": str(status_path),
            "pendentes": str(pendentes_path),
            "falhas": str(falhas_path),
        },
    }

    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    pendentes_path.write_text(json.dumps({
        "total": len(pendentes),
        "itens": pendentes,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    falhas_path.write_text(json.dumps({
        "total": len(falhas),
        "itens": falhas,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return status


def main():
    parser = argparse.ArgumentParser(description="Envia localizacao do JSON para Tiny/Olist API V3.")
    parser.add_argument("--json", default=str(JSON_PADRAO))
    parser.add_argument("--pendentes-json", default="", help="Usa uma lista de pendentes como entrada, em vez do JSON completo.")
    parser.add_argument("--token-file", default=str(TOKEN_FILE))
    parser.add_argument("--setor", default="")
    parser.add_argument("--limite", type=int, default=20)
    parser.add_argument("--skip", type=int, default=0, help="Pula os primeiros N itens filtrados, util para testar outro bloco.")
    parser.add_argument("--rpm", type=int, default=30)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--lote", type=int, default=20, help="Tamanho do lote operacional/progresso. A API V3 atualiza 1 produto por request.")
    parser.add_argument("--tentativas", type=int, default=4)
    parser.add_argument("--executar", action="store_true")
    parser.add_argument("--saida", default="")
    parser.add_argument("--status", default=str(OUT_DIR / "envio_tiny_v3_status.json"))
    parser.add_argument("--pendentes", default=str(OUT_DIR / "envio_tiny_v3_pendentes.json"))
    parser.add_argument("--falhas", default=str(OUT_DIR / "envio_tiny_v3_falhas.json"))
    parser.add_argument("--continuar", action="store_true", help="Continua a partir do arquivo informado em --saida, pulando SKUs ja OK.")
    parser.add_argument("--mostrar-lista", action="store_true", help="Mostra todos os SKUs selecionados antes de enviar.")
    args = parser.parse_args()

    entrada_json = Path(args.pendentes_json) if args.pendentes_json else Path(args.json)
    itens = carregar_itens(entrada_json, args.setor, args.limite, args.skip)
    print(f"Selecionados: {len(itens)}")
    if args.mostrar_lista or len(itens) <= 50:
        for item in itens:
            print(f"- {item.get('sku')} -> {item.get('localizacao')} | id={item.get('id')} | {item.get('titulo')}")
    else:
        print("Lista completa omitida na tela. Os detalhes ficam nos arquivos de status/resultado.")

    if not args.executar:
        print("\nSimulacao. Use --executar para enviar.")
        return

    client = TinyV3Client(Path(args.token_file), args.rpm)
    saida = Path(args.saida) if args.saida else OUT_DIR / f"envio_tiny_v3_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    status_path = Path(args.status)
    pendentes_path = Path(args.pendentes)
    falhas_path = Path(args.falhas)
    resultados = carregar_resultados_anteriores(saida) if args.continuar else []
    ok_anteriores = {str(r.get("sku") or "") for r in resultados if r.get("ok")}
    if ok_anteriores:
        itens = [item for item in itens if str(item.get("sku") or "") not in ok_anteriores]
        print(f"Continuando: {len(ok_anteriores)} SKU(s) OK anteriores pulados; faltam {len(itens)}.")

    todos_para_status = list(carregar_itens(entrada_json, args.setor, args.limite, args.skip))
    inicio = time.time()
    salvar_status(status_path, pendentes_path, falhas_path, todos_para_status, resultados, inicio)

    for numero_lote, lote in enumerate(chunks(itens, args.lote), start=1):
        print(f"\nLote {numero_lote}: {len(lote)} produto(s)")
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            futuros = [executor.submit(enviar_com_retry, client, item, args.tentativas) for item in lote]
            for futuro in concurrent.futures.as_completed(futuros):
                resultado = futuro.result()
                resultados.append(resultado)
                status = "OK" if resultado.get("ok") else "ERRO"
                progresso = salvar_status(status_path, pendentes_path, falhas_path, todos_para_status, resultados, inicio)
                print(
                    f"{status} {resultado.get('sku')} -> {resultado.get('localizacao')} | "
                    f"{progresso['processados']}/{progresso['total']} | "
                    f"faltam {progresso['faltam']} | ETA {progresso['tempo_restante_estimado']} | "
                    f"{resultado.get('mensagem')}",
                    flush=True,
                )
                salvar_resultado(OUT_DIR / "envio_tiny_v3_parcial.json", resultados, len(todos_para_status))
        time.sleep(0.25)

    salvar_status(status_path, pendentes_path, falhas_path, todos_para_status, resultados, inicio, finalizado=True)
    salvar_resultado(saida, resultados, len(todos_para_status))
    print(f"\nResultado salvo em: {saida}")
    print(f"Status salvo em: {status_path}")
    print(f"Pendentes salvo em: {pendentes_path}")
    print(f"Falhas salvo em: {falhas_path}")


if __name__ == "__main__":
    main()
