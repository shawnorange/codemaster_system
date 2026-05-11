from __future__ import annotations

from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit


ALLOWED_HTML_TAGS = {
    "a",
    "b",
    "blockquote",
    "br",
    "code",
    "div",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "li",
    "ol",
    "p",
    "pre",
    "strong",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
}
VOID_HTML_TAGS = {"br", "hr"}
BLOCKED_HTML_TAGS = {"script", "style"}
ALLOWED_HTML_ATTRS = {
    "a": {"href", "title", "target"},
    "div": {"class"},
    "th": {"colspan", "rowspan", "scope"},
    "td": {"colspan", "rowspan"},
}
ALLOWED_URL_SCHEMES = {"", "http", "https", "mailto"}
ALLOWED_SCOPE_VALUES = {"row", "col", "rowgroup", "colgroup"}
ALLOWED_CLASS_VALUES = {
    "diagram",
    "important",
    "page",
    "tip",
}


def sanitize_rich_html(raw_html: object) -> str:
    sanitizer = _RichHTMLSanitizer()
    sanitizer.feed(str(raw_html or ""))
    sanitizer.close()
    return sanitizer.get_html().strip()


class _RichHTMLSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._parts: list[str] = []
        self._blocked_depth = 0

    def get_html(self) -> str:
        return "".join(self._parts)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        if normalized_tag in BLOCKED_HTML_TAGS:
            self._blocked_depth += 1
            return
        if self._blocked_depth or normalized_tag not in ALLOWED_HTML_TAGS:
            return
        rendered_attrs = self._build_attrs(normalized_tag, attrs)
        if normalized_tag in VOID_HTML_TAGS:
            self._parts.append(f"<{normalized_tag}{rendered_attrs}>")
            return
        self._parts.append(f"<{normalized_tag}{rendered_attrs}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if normalized_tag in BLOCKED_HTML_TAGS:
            if self._blocked_depth > 0:
                self._blocked_depth -= 1
            return
        if self._blocked_depth or normalized_tag not in ALLOWED_HTML_TAGS or normalized_tag in VOID_HTML_TAGS:
            return
        self._parts.append(f"</{normalized_tag}>")

    def handle_data(self, data: str) -> None:
        if self._blocked_depth:
            return
        self._parts.append(escape(data))

    def handle_entityref(self, name: str) -> None:
        if self._blocked_depth:
            return
        self._parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._blocked_depth:
            return
        self._parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        return

    def _build_attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> str:
        allowed_attr_names = ALLOWED_HTML_ATTRS.get(tag, set())
        clean_attrs: dict[str, str] = {}
        for attr_name, raw_value in attrs:
            normalized_name = (attr_name or "").lower().strip()
            if normalized_name not in allowed_attr_names:
                continue
            cleaned_value = self._sanitize_attr(tag, normalized_name, raw_value)
            if cleaned_value:
                clean_attrs[normalized_name] = cleaned_value
        if tag == "a":
            href = clean_attrs.get("href", "")
            if not href:
                clean_attrs.pop("href", None)
                clean_attrs.pop("target", None)
            elif clean_attrs.get("target") == "_blank":
                clean_attrs["rel"] = "noopener noreferrer"
        return "".join(f' {key}="{escape(value, quote=True)}"' for key, value in clean_attrs.items())

    def _sanitize_attr(self, tag: str, attr_name: str, raw_value: str | None) -> str:
        value = str(raw_value or "").strip()
        if not value:
            return ""
        if tag == "a" and attr_name == "href":
            return _sanitize_url(value)
        if tag == "a" and attr_name == "target":
            return "_blank" if value == "_blank" else ""
        if attr_name in {"colspan", "rowspan"}:
            return value if value.isdigit() and int(value) > 0 else ""
        if attr_name == "scope":
            return value if value in ALLOWED_SCOPE_VALUES else ""
        if attr_name == "class":
            return _sanitize_class_names(value)
        return value


def _sanitize_url(value: str) -> str:
    if value.startswith("//"):
        return ""
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in ALLOWED_URL_SCHEMES:
        return ""
    return value


def _sanitize_class_names(value: str) -> str:
    class_names = []
    for raw_name in value.split():
        class_name = raw_name.strip()
        if class_name in ALLOWED_CLASS_VALUES:
            class_names.append(class_name)
    return " ".join(class_names)
