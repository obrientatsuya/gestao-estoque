from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE_URL = "https://api.tiny.com.br/public-api/v3"
TOKEN_URL = "https://accounts.tiny.com.br/realms/tiny/protocol/openid-connect/token"
AUTH_URL = "https://accounts.tiny.com.br/realms/tiny/protocol/openid-connect/auth"


class TinyClient:
    def __init__(self, root: Path, token_dir: Path | None = None):
        self.root = root
        self.token_file = (token_dir or root) / "tiny_v3_tokens.json"
        self.config_file = root / "tiny_v3_config.json"
        self.lock = threading.Lock()
        if token_dir:
            token_dir.mkdir(parents=True, exist_ok=True)
            bundled_token = root / "tiny_v3_tokens.json"
            if not self.token_file.is_file() and bundled_token.is_file():
                shutil.copy2(bundled_token, self.token_file)

    def _load(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def _tokens(self) -> dict:
        tokens = self._load(self.token_file)
        if not tokens:
            raise RuntimeError("Tiny nao autorizado. Execute a autorizacao do Tiny no Coordenadas.")
        return tokens

    def config(self) -> dict:
        return self._load(self.config_file)

    def authorization_url(self) -> str:
        config = self.config()
        query = urlencode({
            "client_id": config.get("client_id", ""),
            "redirect_uri": config.get("redirect_uri", ""),
            "scope": "openid email offline_access",
            "response_type": "code",
        })
        return f"{AUTH_URL}?{query}"

    def exchange_code(self, code: str) -> dict:
        config = self.config()
        tokens = self._post_token({
            "grant_type": "authorization_code",
            "client_id": config.get("client_id", ""),
            "client_secret": config.get("client_secret", ""),
            "redirect_uri": config.get("redirect_uri", ""),
            "code": code,
        })
        self._save_tokens(tokens)
        return tokens

    def ensure_token(self) -> dict:
        return self._refresh(self._tokens())

    def _post_token(self, fields: dict) -> dict:
        request = Request(
            TOKEN_URL,
            data=urlencode(fields).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urlopen(request, timeout=40) as response:
                return json.loads(response.read().decode())
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Tiny OAuth HTTP {error.code}: {detail}") from error

    def _save_tokens(self, tokens: dict) -> None:
        tokens = dict(tokens)
        tokens["obtido_em"] = int(time.time())
        tokens["expires_at"] = tokens["obtido_em"] + int(tokens.get("expires_in", 0))
        self.token_file.write_text(json.dumps(tokens, ensure_ascii=False, indent=2), encoding="utf-8")

    def _refresh(self, tokens: dict) -> dict:
        if int(tokens.get("expires_at", 0)) - time.time() > 90:
            return tokens
        with self.lock:
            tokens = self._tokens()
            if int(tokens.get("expires_at", 0)) - time.time() > 90:
                return tokens
            config = self._load(self.config_file)
            client_id = os.getenv("TINY_V3_CLIENT_ID") or config.get("client_id")
            client_secret = os.getenv("TINY_V3_CLIENT_SECRET") or config.get("client_secret")
            if not client_id or not client_secret:
                raise RuntimeError("Credenciais do Tiny V3 nao encontradas.")
            tokens = self._post_token({
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": tokens["refresh_token"],
            })
            self._save_tokens(tokens)
            return tokens

    def request(self, method: str, path: str) -> dict:
        tokens = self._refresh(self._tokens())
        request = Request(
            BASE_URL + path,
            headers={"Authorization": f"Bearer {tokens['access_token']}", "Accept": "application/json"},
            method=method,
        )
        try:
            with urlopen(request, timeout=45) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Tiny HTTP {error.code}: {detail}") from error

    def product_by_sku(self, sku: str) -> dict | None:
        query = urlencode({"codigo": sku, "limit": 100, "offset": 0})
        listing = self.request("GET", f"/produtos?{query}")
        listing_body = listing.get("body") if isinstance(listing.get("body"), dict) else listing
        products = listing_body.get("itens") or []
        product = next((item for item in products if str(item.get("sku", "")).strip().upper() == sku.upper()), None)
        if not product:
            return None
        response = self.request("GET", f"/produtos/{product['id']}")
        detail = response.get("body") if isinstance(response.get("body"), dict) else response
        stock = detail.get("estoque") or {}
        return {
            "id": detail.get("id") or product.get("id"),
            "sku": detail.get("sku") or product.get("sku") or sku,
            "description": detail.get("descricao") or product.get("descricao") or sku,
            "location": str(stock.get("localizacao") or "").strip(),
        }
