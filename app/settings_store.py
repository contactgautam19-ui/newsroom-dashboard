"""Persisted writer settings (channel voice + Anthropic key) in the settings
table. The API key is never returned in the clear — get_public_settings masks
it so the Ops UI can show it is configured without leaking the secret."""

from app import db

WRITER_MODEL_DEFAULT = "claude-opus-4-8"

# keys surfaced to the writer + Ops UI (the api key is masked on the way out)
_KEYS = ("channel_name", "voice_description", "sample_articles",
         "writer_model", "anthropic_api_key")


def get_setting(key: str, default=None):
    with db.connect() as con:
        row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with db.connect() as con:
        con.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def _mask_key(key: str) -> str:
    if not key:
        return ""
    return f"configured (…{key[-4:]})"


def get_public_settings() -> dict:
    return {
        "channel_name": get_setting("channel_name", ""),
        "voice_description": get_setting("voice_description", ""),
        "sample_articles": get_setting("sample_articles", ""),
        "writer_model": get_setting("writer_model", WRITER_MODEL_DEFAULT),
        "anthropic_api_key": _mask_key(get_setting("anthropic_api_key", "")),
    }
