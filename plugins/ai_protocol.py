"""Small protocol helpers shared by AI translation and the settings panel."""

from urllib.parse import urlsplit


def ollama_native_chat_endpoint(base_url):
    """Return Ollama's native chat endpoint for its local default port."""
    try:
        parsed = urlsplit(str(base_url or "").strip())
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() != "http"
        or host not in ("localhost", "127.0.0.1", "::1")
        or port != 11434
    ):
        return None
    authority = "[%s]:%s" % (host, port) if ":" in host else "%s:%s" % (host, port)
    return "http://%s/api/chat" % authority


def clean_ollama_content(content):
    """Remove Qwen/Ollama thinking delimiters from the start of model output."""
    if not isinstance(content, str):
        return content
    text = content.strip()
    while text.startswith("</think>"):
        text = text[len("</think>"):].lstrip()
    if text.startswith("<think>"):
        _thinking, marker, remainder = text.partition("</think>")
        if marker:
            text = remainder.lstrip()
    return text.strip()
