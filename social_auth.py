"""OAuth/OIDC helpers for Google, GitHub, and Apple social login."""

import os
import secrets
from urllib.parse import urlencode

import requests
from authlib.jose import JsonWebToken
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer


PROVIDERS = {
    "google": {
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "userinfo": "https://openidconnect.googleapis.com/v1/userinfo",
        "scope": "openid email profile",
    },
    "github": {
        "authorize": "https://github.com/login/oauth/authorize",
        "token": "https://github.com/login/oauth/access_token",
        "userinfo": "https://api.github.com/user",
        "scope": "read:user user:email",
    },
    "apple": {
        "authorize": "https://appleid.apple.com/auth/authorize",
        "token": "https://appleid.apple.com/auth/token",
        "scope": "name email",
    },
}


def _client_id(provider):
    return os.environ.get(f"{provider.upper()}_CLIENT_ID", "").strip()


def configured(provider):
    return provider in PROVIDERS and bool(_client_id(provider) and os.environ.get(f"{provider.upper()}_CLIENT_SECRET"))


def configured_providers():
    return [name for name in PROVIDERS if configured(name)]


def authorization_url(provider, redirect_uri, secret_key):
    if not configured(provider):
        raise ValueError(f"Social login {provider} belum dikonfigurasi.")
    nonce = secrets.token_urlsafe(20)
    state = URLSafeTimedSerializer(secret_key, salt="social-oauth").dumps({"provider": provider, "nonce": nonce})
    params = {
        "client_id": _client_id(provider), "redirect_uri": redirect_uri,
        "response_type": "code", "scope": PROVIDERS[provider]["scope"], "state": state,
    }
    if provider in ("google", "apple"):
        params["nonce"] = nonce
    if provider == "apple":
        params["response_mode"] = "form_post"
    return f"{PROVIDERS[provider]['authorize']}?{urlencode(params)}"


def _verify_state(provider, state, secret_key):
    try:
        payload = URLSafeTimedSerializer(secret_key, salt="social-oauth").loads(state, max_age=600)
    except (BadSignature, SignatureExpired) as exc:
        raise ValueError("OAuth state tidak valid atau sudah kedaluwarsa.") from exc
    if payload.get("provider") != provider:
        raise ValueError("OAuth provider tidak sesuai.")
    return payload


def _apple_claims(id_token):
    jwks = requests.get("https://appleid.apple.com/auth/keys", timeout=15).json()
    claims = JsonWebToken(["RS256"]).decode(id_token, jwks)
    claims.validate()
    return dict(claims)


def exchange_identity(provider, code, state, redirect_uri, secret_key):
    state_data = _verify_state(provider, state, secret_key)
    config = PROVIDERS[provider]
    response = requests.post(
        config["token"],
        data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "client_id": _client_id(provider),
            "client_secret": os.environ[f"{provider.upper()}_CLIENT_SECRET"],
        },
        headers={"Accept": "application/json"}, timeout=20,
    )
    response.raise_for_status()
    token = response.json()
    if provider == "apple":
        profile = _apple_claims(token["id_token"])
        if profile.get("nonce") != state_data["nonce"]:
            raise ValueError("Apple nonce tidak valid.")
        return {
            "subject": profile["sub"], "email": profile.get("email", ""),
            "email_verified": str(profile.get("email_verified", "false")).lower() == "true",
            "name": "", "avatar_url": "",
        }

    profile_response = requests.get(
        config["userinfo"], headers={"Authorization": f"Bearer {token['access_token']}"}, timeout=20
    )
    profile_response.raise_for_status()
    profile = profile_response.json()
    if provider == "github":
        email = profile.get("email")
        if not email:
            emails_response = requests.get(
                "https://api.github.com/user/emails",
                headers={"Authorization": f"Bearer {token['access_token']}"}, timeout=20,
            )
            emails_response.raise_for_status()
            emails = emails_response.json()
            selected = next((entry for entry in emails if entry.get("primary") and entry.get("verified")), None)
            email = selected.get("email") if selected else ""
        return {
            "subject": str(profile["id"]), "email": email, "email_verified": bool(email),
            "name": profile.get("name") or profile.get("login") or "",
            "avatar_url": profile.get("avatar_url") or "",
        }
    return {
        "subject": profile["sub"], "email": profile.get("email", ""),
        "email_verified": bool(profile.get("email_verified")),
        "name": profile.get("name") or "", "avatar_url": profile.get("picture") or "",
    }
