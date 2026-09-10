"""HTML to the text a person would have read, with :mod:`html.parser` and nothing else.

The job is narrow on purpose. A page's markup is not what the model needs and is most of what
the page weighs: tags, inline scripts, stylesheets and SVG paths would fill a context window
with nothing worth reading, and ``<script>`` in particular is the one part of a page whose text
is *certain* not to be prose. So elements whose contents are code are dropped whole, the rest is
flattened, and block-level tags become line breaks so a list still reads as a list.

What this is not: it is not a renderer, and the text it produces is not "what was visible".
Nothing here evaluates CSS, so text hidden with ``display: none``, white-on-white text, and
anything a script would have written into the page all come out the same way visible prose does
- either present in the source and therefore extracted, or absent from the source and therefore
missing. That matters for the injection question and is stated again in the README: a page can
carry instructions a human reader never sees, and this extractor will hand them to the model
along with the prose.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

#: Elements whose content is code, styling or a graphic rather than reading matter. Their text
#: is dropped entirely - not flattened - because a stylesheet flattened into prose is noise the
#: model has to read past, and an inline script is noise that also looks like instructions.
DROPPED = frozenset({"script", "style", "noscript", "template", "svg", "math",
                     "canvas", "iframe", "object", "embed", "applet"})

#: Elements that end a line. A list whose items ran together, or a table whose cells did, is
#: harder to read than the page was, and the model is being asked to read it.
BLOCK = frozenset({
    "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt", "fieldset",
    "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header",
    "hr", "li", "main", "nav", "ol", "option", "p", "pre", "section", "table", "tbody",
    "td", "tfoot", "th", "thead", "tr", "ul",
})

_HORIZONTAL_RUN = re.compile(r"[ \t   ]+")
_BLANK_RUN = re.compile(r"\n{3,}")


class _Reader(HTMLParser):
    """Collects the text of a document, minus :data:`DROPPED`, with :data:`BLOCK` as line ends."""

    def __init__(self) -> None:
        # convert_charrefs is the default and is wanted: ``&amp;`` should read as ``&``. It is
        # also the reason a numeric reference for a lone surrogate is not a problem here -
        # ``html.unescape`` maps every invalid code point to U+FFFD rather than building a
        # character that no codec can encode. The tool still runs the final encodability pass,
        # because that guarantee belongs to a line of code rather than to this paragraph.
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self._dropped_depth = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in DROPPED:
            self._dropped_depth += 1
            return
        if tag == "title":
            self._in_title = True
        if tag in BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in DROPPED:
            # Floored at zero: a document with a stray ``</script>`` and no opening tag must not
            # push this negative, because a negative depth reads as "inside nothing" for every
            # dropped element that follows and the drop stops working for the rest of the page.
            self._dropped_depth = max(0, self._dropped_depth - 1)
            return
        if tag == "title":
            self._in_title = False
        if tag in BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._dropped_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
            return
        self.parts.append(data)


def to_text(html: str) -> tuple[str, str]:
    """``(title, text)`` for a document. Malformed markup is read as far as it goes.

    ``HTMLParser`` is forgiving by design and does not raise on unclosed tags or stray angle
    brackets, which is what a fetched page usually is. A document truncated by the size cap is
    the ordinary case, not an error case, and parses the same way.
    """
    reader = _Reader()
    reader.feed(html)
    reader.close()
    return collapse("".join(reader.title_parts)), collapse("".join(reader.parts))


def collapse(text: str) -> str:
    """Runs of horizontal whitespace to one space, runs of blank lines to one, ends trimmed.

    Source indentation is the typesetter's, not the author's: a paragraph indented eight spaces
    inside six nested ``<div>``s is one paragraph. Vertical space is kept, one blank line at
    most, because it is the only structure left once the tags are gone.
    """
    lines = [_HORIZONTAL_RUN.sub(" ", line).strip() for line in text.splitlines()]
    return _BLANK_RUN.sub("\n\n", "\n".join(lines)).strip()
