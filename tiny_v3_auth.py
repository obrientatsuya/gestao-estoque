from __future__ import annotations

import argparse
import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen


TOKEN_URL = "https://accounts.tiny.com.br/realms/tiny/protocol/openid-connect/token"
AUTH_URL = "https://accounts.tiny.com.br/realms/tiny/protocol/openid-connect/auth"
TOKEN_FILE = Path("tiny_v3_tokens.json")
CONFIG_FILE = Path("tiny_v3_config.json")


def env_ou_arg(valor: str | None, nome_env: str) -> str:
    final = valor or os.getenv(nome_env, "")
    if not final and CONFIG_FILE.is_file():
        config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        chave = nome_env.replace("TINY_V3_", "").lower()
        final = str(config.get(chave) or "").strip()
    if not final:
        raise SystemExit(f"Informe --{nome_env.lower().replace('_', '-')} ou defina {nome_env}.")
    return final


def post_form(data: dict) -> dict:
    body = urlencode(data).encode("utf-8")
    req = Request(
        TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=40) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        detalhe = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(
            "Falha ao trocar o codigo por token.\n"
            f"HTTP {exc.code}: {detalhe}\n\n"
            "Verifique se TINY_V3_CLIENT_SECRET esta correto no mesmo terminal, "
            "se o redirect_uri e exatamente o mesmo e se o code nao foi usado antes."
        ) from exc


def salvar_tokens(tokens: dict, caminho: Path):
    tokens = dict(tokens)
    tokens["obtido_em"] = int(time.time())
    tokens["expires_at"] = tokens["obtido_em"] + int(tokens.get("expires_in", 0))
    caminho.write_text(json.dumps(tokens, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Token salvo em: {caminho.resolve()}")


def salvar_config(client_id: str, client_secret: str, redirect_uri: str):
    if CONFIG_FILE.is_file():
        return
    CONFIG_FILE.write_text(json.dumps({
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def montar_url_autorizacao(client_id: str, redirect_uri: str) -> str:
    query = urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "openid",
        "response_type": "code",
    })
    return f"{AUTH_URL}?{query}"


def aguardar_code_local(redirect_uri: str) -> str:
    parsed = urlparse(redirect_uri)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/callback"
    recebido = {"code": "", "erro": ""}

    class CallbackHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def do_GET(self):
            query = parse_qs(urlparse(self.path).query)
            recebido["code"] = (query.get("code") or [""])[0]
            recebido["erro"] = (query.get("error") or [""])[0]
            recebido["erro_desc"] = (query.get("error_description") or [""])[0]
            recebido["callback"] = self.path
            ok = bool(recebido["code"])
            body = (
                "Autorizacao concluida. Pode voltar para o terminal."
                if ok
                else (
                    "Nao recebi o codigo de autorizacao.\n\n"
                    f"Erro: {recebido['erro'] or '(sem error)'}\n"
                    f"Descricao: {recebido['erro_desc'] or '(sem descricao)'}\n\n"
                    "Volte para o terminal e veja a URL recebida."
                )
            ).encode("utf-8")
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    servidor = HTTPServer((host, port), CallbackHandler)
    print(f"Aguardando retorno em {redirect_uri}")
    while not recebido["code"] and not recebido["erro"]:
        servidor.handle_request()
    servidor.server_close()

    if recebido["erro"]:
        raise SystemExit(
            "Tiny retornou erro na autorizacao:\n"
            f"  error: {recebido['erro']}\n"
            f"  description: {recebido.get('erro_desc') or '(sem descricao)'}\n"
            f"  callback: {recebido.get('callback') or '(sem callback)'}"
        )
    if not recebido["code"]:
        raise SystemExit(
            "Nao recebi code no callback.\n"
            f"  callback: {recebido.get('callback') or '(sem callback)'}"
        )
    return recebido["code"]


def main():
    parser = argparse.ArgumentParser(description="Autoriza a API Tiny/Olist V3 e salva access/refresh token localmente.")
    parser.add_argument("--client-id", default=os.getenv("TINY_V3_CLIENT_ID", ""))
    parser.add_argument("--client-secret", default=os.getenv("TINY_V3_CLIENT_SECRET", ""))
    parser.add_argument("--redirect-uri", default=os.getenv("TINY_V3_REDIRECT_URI", "http://localhost:8765/callback"))
    parser.add_argument("--code", default="", help="Codigo recebido no redirect apos autorizar.")
    parser.add_argument("--listen", action="store_true", help="Abre servidor local e captura o code automaticamente.")
    parser.add_argument("--refresh", action="store_true", help="Renova usando refresh_token salvo.")
    parser.add_argument("--token-file", default=str(TOKEN_FILE))
    args = parser.parse_args()

    client_id = env_ou_arg(args.client_id, "TINY_V3_CLIENT_ID")
    client_secret = env_ou_arg(args.client_secret, "TINY_V3_CLIENT_SECRET")
    salvar_config(client_id, client_secret, args.redirect_uri)
    token_file = Path(args.token_file)

    if args.refresh:
        tokens = json.loads(token_file.read_text(encoding="utf-8"))
        novos = post_form({
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": tokens["refresh_token"],
        })
        salvar_tokens(novos, token_file)
        return

    if args.listen:
        print("Abra esta URL, autorize no Tiny e aguarde o terminal salvar o token:")
        print(montar_url_autorizacao(client_id, args.redirect_uri))
        code = aguardar_code_local(args.redirect_uri)
        tokens = post_form({
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": args.redirect_uri,
            "code": code,
        })
        salvar_tokens(tokens, token_file)
        return

    if not args.code:
        print("Abra esta URL, autorize e copie o parametro code do retorno:")
        print(montar_url_autorizacao(client_id, args.redirect_uri))
        return

    tokens = post_form({
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": args.redirect_uri,
        "code": args.code,
    })
    salvar_tokens(tokens, token_file)


if __name__ == "__main__":
    main()
