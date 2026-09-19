import unicodedata
import re


def deep_clean_text(text: str) -> str:
    if not text:
        return ""
    try:
        text = text.encode("utf-16", "surrogatepass").decode("utf-16", "ignore")
    except Exception:
        text = text.encode("utf-8", "ignore").decode("utf-8", "ignore")
    try:
        text = unicodedata.normalize("NFKC", text)
    except Exception:
        pass
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = text.strip()
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        text = text.encode("utf-8", errors="ignore").decode("utf-8")
    return text