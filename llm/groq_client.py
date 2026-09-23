"""A minimal client for Groq's OpenAI-compatible chat-completions endpoint.

Stdlib only (`urllib.request`), matching the rest of this project's
no-new-dependency discipline. This file has exactly one job: send a prompt,
get text back. It performs no parsing of the response and no verification --
those stay in `llm/miner.py` and `verify/`, so that the untrusted-proposal
boundary described in the paper (Section III) is a real module boundary in
the code, not just a sentence.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass

from llm.env import load_env, require

CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
MODELS_URL = "https://api.groq.com/openai/v1/models"
# Groq's catalog changes over time and varies by account; this is not a
# stable guarantee. `list_models()` below queries what's actually available
# rather than trusting this constant blindly.
DEFAULT_MODEL = "openai/gpt-oss-120b"


def _ssl_context() -> ssl.SSLContext:
    """A verified TLS context that doesn't depend on the OS cert store.

    The python.org macOS build ships with an empty cert bundle at
    `.../etc/openssl/cert.pem` unless the user has separately run
    "Install Certificates.command" -- a well-known trap that otherwise
    surfaces as CERTIFICATE_VERIFY_FAILED on the first outbound HTTPS call a
    project makes. Using `certifi`'s bundle when it's importable sidesteps
    that without asking anyone to run a script outside this repo; if
    `certifi` isn't installed, fall back to whatever the interpreter's
    default verification finds (which is exactly the previous behaviour).
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


class GroqError(Exception):
    pass


@dataclass
class Completion:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    raw: dict


class GroqClient:
    def __init__(self, api_key: str | None = None, model: str | None = None,
                 timeout: float = 60.0):
        self.api_key = api_key or require(
            "GROQ_API_KEY", "Get one at https://console.groq.com/keys.")
        load_env()
        self.model = model or os.environ.get("GROQ_MODEL") or DEFAULT_MODEL
        self.timeout = timeout

    def _request(self, url: str, *, method: str = "GET",
                body: bytes | None = None) -> dict:
        # Groq's endpoint sits behind Cloudflare, which blocks requests with
        # no User-Agent (or urllib's bare default one) as bot traffic before
        # the request ever reaches Groq's own auth check -- surfacing as an
        # opaque 403 "error code: 1010" that has nothing to do with the key.
        headers = {"Authorization": f"Bearer {self.api_key}",
                  "Accept": "application/json",
                  "User-Agent": "ProveIt-C/0.1 (+https://github.com)"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(
                    req, timeout=self.timeout, context=_ssl_context()) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise GroqError(f"HTTP {e.code} from Groq: {detail[:500]}") from None
        except urllib.error.URLError as e:
            raise GroqError(f"network error reaching Groq: {e.reason}") from None
        except json.JSONDecodeError as e:
            raise GroqError(f"unexpected response shape: {e}") from None

    def list_models(self) -> list[str]:
        """What's actually available to this key, right now.

        Groq's catalog changes and varies by account, so `DEFAULT_MODEL` is a
        starting guess, not a promise -- this is the ground truth.
        """
        payload = self._request(MODELS_URL)
        return sorted(m["id"] for m in payload.get("data", []))

    def complete(self, system: str, user: str, *, temperature: float = 0.0,
                max_tokens: int = 3000, seed: int = 0) -> Completion:
        """One request, one response. Temperature 0 and a fixed seed by
        default, per the reproducibility rule this project applies
        everywhere else: every number should be regeneratable."""
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "seed": seed,
        }).encode("utf-8")

        payload = self._request(CHAT_URL, method="POST", body=body)

        try:
            text = payload["choices"][0]["message"]["content"]
            usage = payload.get("usage", {})
        except (KeyError, IndexError) as e:
            raise GroqError(f"malformed response: missing {e}") from None

        return Completion(
            text=text, model=payload.get("model", self.model),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            raw=payload)
