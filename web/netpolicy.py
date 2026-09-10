"""Where this plugin's HTTP client is allowed to connect, and the client that holds to it.

A URL this plugin fetches is chosen by the *model*, and often chosen out of a page the model
read a moment ago. That is a different situation from ``core/provider.py``, where the endpoint
is one the user configured and the danger is a redirect carrying their key somewhere else. Here
the danger is the request itself: ``http://169.254.169.254/latest/meta-data/iam/`` is a cloud
instance's credential store, ``http://127.0.0.1:9200/`` is whatever the developer is running,
and both answer a plain GET from a process running as the user. Anything that can put a URL in
front of this tool - the model, a page the model read, a repository's README - can ask for
those. So the address policy is the boundary, and it is on by default.

Three decisions worth stating, because each one is a place where a weaker version looks the
same from outside:

* **The check is on the resolved address, not on the hostname.** A hostname check is a
  substring test against text an attacker writes. ``http://127.0.0.1.nip.io/`` is a public
  name that resolves to loopback; so is any A record its owner cares to point at ``10.0.0.5``.
  There is no spelling of a host that a name-based denylist can be sure about, so this resolves
  the name with :func:`socket.getaddrinfo` and judges the addresses that come back.

* **The address that was judged is the address that is connected to.** Resolving, approving,
  and then handing the *name* to ``urllib`` means the name is resolved a second time, by a
  different call, and a DNS server that answers ``93.184.216.34`` the first time and
  ``127.0.0.1`` the second time has walked straight through the check. That is DNS rebinding,
  and it is the standard way an address check that "passes" is defeated. So the approved
  address is pinned: the socket connects to it, while ``Host:`` and the TLS certificate check
  keep the hostname, which is why :attr:`http.client.HTTPConnection._create_connection` is the
  seam used rather than rewriting the URL. Rewriting the URL to the IP would silently disable
  certificate verification for every https fetch.

* **Every redirect hop is judged again.** A public host answering ``302 Location:
  http://169.254.169.254/`` is the same request arriving at the same place with one extra step,
  and ``urllib`` follows it without asking. :class:`GuardedRedirects` refuses it, and refuses a
  scheme change out of http(s) while it is there.

What this does *not* cover, said plainly:

* A host inside your network with a **public** IP address is public as far as this is concerned.
  The address policy is about address ranges, not about your perimeter. ``denied_hosts`` is the
  knob for naming those, and it is a name check with all the weakness described above - useful
  for stopping an accident, not for stopping an attacker.
* An **open redirector or a proxy on an allowed host** reaches whatever it likes on your
  behalf. Nothing here can see past the host that answers.
* IPv6 transition addresses are unwrapped (``::ffff:``, 6to4, Teredo) because each embeds an
  IPv4 address that would otherwise be judged as the outer v6 address. That list is the ones
  ``ipaddress`` can name; a transition mechanism it cannot name would not be unwrapped.
"""
from __future__ import annotations

import contextlib
import http.client
import ipaddress
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from picoagent.core.text import safe_for_display

#: The only schemes this plugin will fetch. ``urllib``'s default opener also speaks ``file:``,
#: ``ftp:`` and ``data:``; a ``file:///etc/passwd`` would come back looking exactly like a page a
#: server had served. :func:`build_opener` here builds an :class:`urllib.request.OpenerDirector`
#: with only the handlers below rather than calling ``urllib.request.build_opener``, so those
#: three are not merely unreachable by a check that could be forgotten - they are not installed.
HTTP_SCHEMES = ("http", "https")

#: Read size for one pass of the body loop. Small enough that the wall-clock deadline is checked
#: often on a slow response, large enough not to matter on a fast one.
_CHUNK = 65536


class WebError(Exception):
    """Anything that stops a fetch producing a page: a refusal, a timeout, an HTTP error."""


class WebRefused(WebError):
    """The policy said no. Separate from :class:`WebError` so a caller can tell a rule from a fault."""


# --------------------------------------------------------------------------- addresses

def unwrap_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address):
    """The IPv4 address an IPv6 transition address embeds, or ``ip`` unchanged.

    ``::ffff:127.0.0.1`` is loopback wearing a v6 spelling, and ``IPv6Address.is_loopback`` is
    ``False`` for it - the v6 loopback is ``::1`` and nothing else. 6to4 (``2002::/16``) and
    Teredo (``2001::/32``) embed a v4 address the same way. Judging the outer address would let
    each of those name a private host in a form the checks below call global.
    """
    if not isinstance(ip, ipaddress.IPv6Address):
        return ip
    if ip.ipv4_mapped:
        return ip.ipv4_mapped
    if ip.sixtofour:
        return ip.sixtofour
    if ip.teredo:
        return ip.teredo[1]     # the client's own address; the server half is a public relay
    return ip


def address_refusal(raw) -> str | None:
    """Why this address must not be fetched, or ``None`` when it may be.

    Named categories rather than one ``is_global`` test, because the sentence a user reads has
    to say *which* rule refused them - "a link-local address" tells someone who typed a metadata
    URL what happened, where "not globally routable" does not. ``is_global`` is still the last
    line, so a range nobody thought to name is refused rather than allowed by omission.
    """
    ip = unwrap_address(raw)
    for test, description in (
            (ip.is_unspecified, "the unspecified address"),
            (ip.is_loopback, "a loopback address"),
            (ip.is_link_local, "a link-local address (169.254.0.0/16, fe80::/10) - "
                               "the range cloud instance metadata lives on"),
            (ip.is_multicast, "a multicast address"),
            (ip.is_private, "a private address (RFC1918 and friends)"),
            (ip.is_reserved, "a reserved address"),
            (not ip.is_global, "not a globally routable address")):
        if test:
            return description
    return None


@dataclass(frozen=True)
class AddressPolicy:
    """The rules, and the one call that applies all of them to a URL.

    ``allow_private`` is the blunt switch and is off by default: on, this plugin will fetch
    loopback, link-local and RFC1918 addresses, which is what a developer pointing the agent at
    their own dev server wants and is also exactly the capability an injected instruction wants.

    ``allow_hosts`` is the finer instrument: named hosts are exempt from the private-address
    rule and nothing else. It is a *name* check, so it inherits every weakness named at the top
    of this module - it is the user saying "this name is mine", which is a statement only they
    can make, and it is deliberately not how the general rule works.

    ``denied_hosts`` refuses a name outright, whatever it resolves to. It only ever refuses
    more, which is why it is the one setting a repository is allowed to add to.
    """
    allow_private: bool = False
    allow_hosts: frozenset[str] = frozenset()
    denied_hosts: frozenset[str] = frozenset()

    def check(self, url: str, pins: dict) -> str:
        """Approve ``url``, record the address to connect to in ``pins``, and return the host.

        Raises :class:`WebRefused` with the sentence the user should read. Every refusal names
        the URL, so a message about a redirect is not mistaken for one about the URL that was
        asked for.
        """
        shown = safe_for_display(url)
        if any(ord(character) < 0x20 or ord(character) == 0x7f for character in url):
            # A URL is a header value on the wire. urllib rejects some of this itself; refusing
            # it here means the message says what was wrong rather than surfacing as a ValueError
            # from inside the request machinery.
            raise WebRefused(f"refusing to fetch {shown}: the URL contains control characters")

        parts = urllib.parse.urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme not in HTTP_SCHEMES:
            found = f"its scheme is '{scheme}'" if scheme else "it names no scheme"
            raise WebRefused(f"refusing to fetch {shown}: only http and https are fetched, {found}")
        host = (parts.hostname or "").lower()
        if not host:
            raise WebRefused(f"refusing to fetch {shown}: it names no host")
        try:
            port = parts.port or {"http": 80, "https": 443}[scheme]
        except ValueError:
            raise WebRefused(f"refusing to fetch {shown}: its port is not a number") from None
        if host in self.denied_hosts:
            raise WebRefused(f"refusing to fetch {shown}: {host} is in denied_hosts")

        try:
            resolved = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise WebRefused(f"refusing to fetch {shown}: {host} could not be resolved "
                             f"({safe_for_display(str(exc))})") from None
        addresses = [entry[4][0] for entry in resolved]
        if not addresses:
            raise WebRefused(f"refusing to fetch {shown}: {host} resolved to no addresses")

        if not (self.allow_private or host in self.allow_hosts):
            for address in addresses:
                # Every address, not only the one that will be used. A name answering with a
                # public address and a private one is a name whose next lookup - a retry, a
                # second hop, a different resolver - may hand back the private half.
                refusal = address_refusal(ipaddress.ip_address(address.split("%")[0]))
                if refusal:
                    raise WebRefused(
                        f"refusing to fetch {shown}: {host} resolves to {address}, which is "
                        f"{refusal}. Set allow_private_addresses = true under [plugins.web] to "
                        f"allow this everywhere, or add {host!r} to allow_hosts to allow just it")
        pins[(host, port)] = addresses[0]
        return host


# --------------------------------------------------------------------------- pinned connections

def _pinning(base, pins: dict):
    """A connection factory that dials the vetted address and keeps the hostname for everything else.

    ``do_open`` calls this with the ``host[:port]`` from the request, so the lookup happens after
    ``http.client`` has split the two and filled in the default port. A host with no entry in
    ``pins`` is a host nothing approved - a handler added by mistake, a redirect that got past
    the guard - and the answer is a refusal rather than a connection, because a client that
    quietly falls back to ``socket.create_connection`` is a client with no address policy at all.
    """
    def make(host, **kwargs):
        conn = base(host, **kwargs)
        address = pins.get((conn.host.lower(), conn.port))
        if address is None:
            raise WebRefused(f"refusing to connect to {safe_for_display(conn.host)}:{conn.port}: "
                             "no address for it was approved")
        create = conn._create_connection    # documented in http.client as the seam for exactly this
        conn._create_connection = (
            lambda target, timeout, source: create((address, target[1]), timeout, source))
        return conn
    return make


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, pins: dict):
        super().__init__()
        self._pins = pins

    def http_open(self, req):
        return self.do_open(_pinning(http.client.HTTPConnection, self._pins), req)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, pins: dict):
        super().__init__()
        self._pins = pins

    def https_open(self, req):
        # ``context=self._context`` is urllib's own default context: certificates verified,
        # hostname checked. The hostname it checks is ``conn.host``, which the pin leaves alone.
        return self.do_open(_pinning(http.client.HTTPSConnection, self._pins), req,
                            context=self._context)


class GuardedRedirects(urllib.request.HTTPRedirectHandler):
    """Re-runs the whole policy on every hop, and refuses to carry a credential off its origin.

    Two rules, and they answer two different attacks.

    The address rule is the one this plugin exists to have: a public page that answers ``302
    Location: http://10.0.0.5/`` has asked for an internal fetch, and ``urllib`` would make it.
    Checking only the URL the model supplied would mean the check is one HTTP response away
    from being bypassed by anybody who controls a public host.

    The origin rule is ``core/provider.py``'s, for its reason rather than by analogy: urllib
    re-sends every header that is not about the body, so a search endpoint answering with a
    redirect is handed the API key - and the query string, which is the user's own words. It
    applies only to a credentialed request (``web_search`` with a key configured), because
    ``web_fetch`` carries nothing worth stealing and following a redirect across hosts is
    ordinary behaviour for a page fetch.
    """

    def __init__(self, policy: AddressPolicy, pins: dict, hops: list,
                 max_redirects: int = 5, same_origin_only: bool = False):
        super().__init__()
        self._policy, self._pins, self._hops = policy, pins, hops
        self._same_origin_only = same_origin_only
        self.max_redirections = max_redirects

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            self._check(req, newurl)
        except WebRefused:
            # ``fp`` is the live 3xx and urllib reads and closes it only after this returns, so
            # the path that never returns is the path that has to close it. Same reasoning, and
            # the same suppression, as ``provider._SameOriginRedirects``: a socket that objects
            # on the way down must not replace the refusal with an I/O error.
            with contextlib.suppress(Exception):
                fp.close()
            raise
        self._hops.append(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

    def _check(self, req, newurl: str) -> None:
        origin, target = _origin(req.full_url), _origin(newurl)
        if self._same_origin_only and (origin is None or target is None or origin != target):
            raise WebRefused(
                f"refusing to follow a redirect from {safe_for_display(req.full_url)} to "
                f"{safe_for_display(newurl)}: this request carries your search credentials and "
                "your query, and urllib would send both to a host you did not configure")
        self._policy.check(newurl, self._pins)


def _origin(url: str) -> tuple[str, str, int | None] | None:
    """``(scheme, host, port)``, with the port filled in from the scheme, or ``None``.

    A ``Location`` header is written by whoever answered, so a port that is not a number is a
    shape this needs an answer for: ``None``, which the caller reads as "not the same origin as
    anything" - it checks for it rather than comparing, because ``None == None`` would otherwise
    make two unparseable URLs the same origin.
    """
    parts = urllib.parse.urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    return scheme, (parts.hostname or "").lower(), port or {"http": 80, "https": 443}.get(scheme)


# --------------------------------------------------------------------------- fetching

@dataclass
class Fetched:
    """One successful response, already bounded and already decoded."""
    url: str                    # the URL that finally answered, after any redirects
    status: int
    content_type: str
    charset: str
    text: str
    byte_count: int
    truncated: bool
    hops: list[str] = field(default_factory=list)


#: Content types this plugin turns into text. Everything else - an image, a tarball, a PDF - is
#: refused rather than decoded into mojibake and put in front of the model. A response with no
#: ``Content-Type`` at all is treated as ``text/plain``, which is what ``curl`` assumes and what
#: an unconfigured static server sends.
TEXT_TYPES = ("text/", "application/json", "application/xml", "application/xhtml+xml",
              "application/javascript", "application/rss+xml", "application/atom+xml")


def is_texty(content_type: str) -> bool:
    lowered = content_type.lower()
    return lowered.startswith(TEXT_TYPES) or lowered.endswith(("+json", "+xml"))


@dataclass(frozen=True)
class Fetcher:
    """A GET with a size cap, a wall-clock deadline, and :class:`AddressPolicy` on every hop."""
    policy: AddressPolicy
    max_bytes: int = 2_000_000
    timeout: float = 20.0
    max_redirects: int = 5
    same_origin_only: bool = False
    user_agent: str = "picoagent-web-plugin/0.1 (+https://github.com/opscontinuum/picoagent-plugins)"

    def get(self, url: str, headers: dict[str, str] | None = None) -> Fetched:
        """Fetch ``url``. Raises :class:`WebError` for anything that is not a readable response.

        Blocking, on purpose: ``urllib`` is, and the tool calling this runs it in an executor
        the way ``provider.list_models`` does, rather than this module pretending to be async.
        """
        pins: dict = {}
        hops: list[str] = []
        self.policy.check(url, pins)
        request = urllib.request.Request(
            url, method="GET",
            headers={"User-Agent": self.user_agent,
                     "Accept": "text/html,application/xhtml+xml,text/plain,application/json;q=0.9,*/*;q=0.1",
                     "Accept-Encoding": "identity",   # no transparent decompression: a 2MB cap on
                                                      # compressed bytes is not a cap on what we decode
                     **(headers or {})})
        opener = _build_opener(self.policy, pins, hops, self.max_redirects, self.same_origin_only)
        deadline = time.monotonic() + self.timeout
        try:
            with opener.open(request, timeout=self.timeout) as response:
                encoding = (response.headers.get("Content-Encoding") or "identity").lower()
                if encoding not in ("identity", ""):
                    # ``Accept-Encoding: identity`` asked for none, and urllib does not
                    # decompress. A server that compressed anyway would have its bytes decoded
                    # as if they were characters, which is mojibake dressed up as a page, and
                    # the size cap would be counting compressed bytes rather than text.
                    raise WebError(f"refusing to read {safe_for_display(response.url)}: it "
                                   f"answered with Content-Encoding: {safe_for_display(encoding)} "
                                   "after being asked for none, so the body is not text")
                content_type = response.headers.get_content_type() or "text/plain"
                if not is_texty(content_type):
                    raise WebError(f"refusing to read {safe_for_display(response.url)}: it answered "
                                   f"with {safe_for_display(content_type)}, which is not text. "
                                   "This tool returns readable text, not bytes")
                raw, truncated = _read_capped(response, self.max_bytes, deadline)
                charset = _charset(response)
                return Fetched(url=response.url, status=response.status, content_type=content_type,
                               charset=charset, text=raw.decode(charset, errors="replace"),
                               byte_count=len(raw), truncated=truncated, hops=hops)
        except WebError:
            raise
        except urllib.error.HTTPError as exc:
            # The body of an error page is untrusted content with nothing to delimit it against,
            # so the status line is all that is reported. A page worth reading answers 200.
            with contextlib.suppress(Exception):
                exc.close()
            raise WebError(f"{safe_for_display(url)} answered HTTP {exc.code} "
                           f"{safe_for_display(str(exc.reason))}") from None
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            raise WebError(f"could not fetch {safe_for_display(url)}: "
                           f"{type(exc).__name__}: {safe_for_display(str(exc))}") from None


def _charset(response) -> str:
    """The declared charset, or UTF-8. A charset nothing can look up is not a reason to fail.

    Only the HTTP header is read. An HTML ``<meta charset>`` is deliberately not honoured: it
    would mean decoding the document twice and letting page content pick the codec, and every
    byte sequence decodes under ``errors="replace"`` anyway. The cost is mojibake on a page that
    declares its encoding only in the markup, and that is a legibility bug, not a security one.
    """
    declared = response.headers.get_content_charset()
    if not declared:
        return "utf-8"
    try:
        import codecs
        codecs.lookup(declared)
    except (LookupError, ValueError, TypeError):
        return "utf-8"
    return declared


def _read_capped(response, max_bytes: int, deadline: float) -> tuple[bytes, bool]:
    """Read at most ``max_bytes`` bytes, giving up at ``deadline``.

    Two bounds because they stop two different things. The cap stops a response that is simply
    enormous - an unbounded ``read()`` is a memory fault the caller cannot recover from, and a
    server does not have to be hostile to serve a 4GB file. The deadline stops a response that
    arrives slowly forever: a socket timeout is per-recv, so a server dribbling one byte every
    second resets it on every byte and holds the tool open for as long as it likes.

    ``read1`` rather than ``read`` is what makes the deadline real. ``read(n)`` blocks until it
    has ``n`` bytes or the body ends, so the check between iterations would not run until the
    slow server had finished being slow; ``read1`` comes back with whatever has arrived.

    ``Content-Length`` is not consulted. It is a claim by the sender, and a sender who is lying
    about the size is exactly the sender the cap is for.
    """
    chunks: list[bytes] = []
    total = 0
    while total <= max_bytes:
        if time.monotonic() >= deadline:
            raise WebError(f"gave up reading the response after {total} bytes: it did not finish "
                           "within the configured timeout")
        chunk = response.read1(min(_CHUNK, max_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    body = b"".join(chunks)
    return body[:max_bytes], total > max_bytes


def _build_opener(policy: AddressPolicy, pins: dict, hops: list,
                  max_redirects: int, same_origin_only: bool) -> urllib.request.OpenerDirector:
    """An opener with the handlers this plugin wants and no others.

    Built by hand rather than with ``urllib.request.build_opener`` for two reasons. The default
    set includes ``FileHandler``, ``FTPHandler`` and ``DataHandler``, and a scheme check that is
    the only thing standing between a model-chosen URL and ``file:///etc/shadow`` is a check
    that must never be forgotten at a call site; not installing them means there is nothing to
    forget. And ``ProxyHandler`` reads ``http_proxy`` from the environment, which would send
    every request to a host this module never resolved and never approved - the address policy
    would still run, and would be judging an address nothing connects to.
    """
    opener = urllib.request.OpenerDirector()
    opener.add_handler(_PinnedHTTPHandler(pins))
    if hasattr(urllib.request, "HTTPSHandler"):     # absent from a Python built without ssl
        opener.add_handler(_PinnedHTTPSHandler(pins))
    opener.add_handler(GuardedRedirects(policy, pins, hops, max_redirects, same_origin_only))
    opener.add_handler(urllib.request.HTTPErrorProcessor())
    opener.add_handler(urllib.request.HTTPDefaultErrorHandler())
    opener.add_handler(urllib.request.UnknownHandler())
    return opener
