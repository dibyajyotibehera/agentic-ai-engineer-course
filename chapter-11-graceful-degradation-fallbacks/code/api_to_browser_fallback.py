"""API-to-browser fallback (Chapter 11, Exercise 5).

When the structured Application Programming Interface (API) a tool
normally uses is down, the same task can sometimes be done through
the browser instead -- here, by fetching a page over plain HTTP and
extracting typed data from it.  Both paths return the *identical*
typed structure, so downstream code cannot tell which path produced
the answer.

This exercise uses a page served locally by the standard library, so
no live website, browser automation library or API key is needed.
The pattern it practises is the one in Microsoft's Lesson 15
(https://microsoft.github.io/ai-agents-for-beginners/15-browser-use/):
Browser-Use for navigation, Playwright with the Chrome DevTools
Protocol for browser control, and Pydantic for structured extraction.
Here the browser is replaced by a local page fetch, which keeps the
exercise runnable anywhere while preserving the mechanism: detect the
API failure, fetch the page, extract typed data, validate it.

The browser path is deliberately narrow: it handles the common case
and refuses what it cannot verify, and it must never bypass
authentication, defeat a bot check, or do through the page what the
API refused for a policy reason.
Run:  python api_to_browser_fallback.py
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer

from circuit_breaker import ProviderError

PAGE_HTML = """<html><body>
<h1>Products</h1>
<table>
<tr><td>Widget A</td><td>12.50</td><td>4.5</td></tr>
<tr><td>Widget B</td><td>9.99</td><td>4.8</td></tr>
<tr><td>Widget C</td><td>19.00</td><td>4.1</td></tr>
</table>
</body></html>
"""

BROKEN_PAGE_HTML = """<html><body>
<h1>Products</h1>
<table>
<tr><td>Widget A</td><td>12.50</td><td>4.5</td></tr>
<tr><td>Widget B</td><td></td><td>4.8</td></tr>
</table>
</body></html>
"""

ROW_RE = re.compile(
    r"<tr><td>([^<]+)</td><td>([^<]*)</td><td>([^<]+)</td></tr>")


@dataclass(frozen=True)
class Product:
    """The typed structure both the API path and the browser path return."""

    name: str
    price: float
    rating: float

    def __post_init__(self) -> None:
        if self.price <= 0:
            raise ValueError(f"product {self.name!r} has no valid price")
        if not 0 <= self.rating <= 5:
            raise ValueError(f"product {self.name!r} has an invalid rating")


def extract_products(html: str) -> list[Product]:
    """Extract typed products, rejecting any row that cannot be validated.

    A row with a missing or non-numeric price is refused, never guessed
    at: the agent would rather lose one product than invent its price.
    """
    products: list[Product] = []
    for name, price_text, rating_text in ROW_RE.findall(html):
        try:
            price = float(price_text)
            rating = float(rating_text)
        except ValueError:
            print(f"  rejected row {name!r}: price {price_text!r} is not a number")
            continue
        try:
            products.append(Product(name.strip(), price, rating))
        except ValueError as exc:
            print(f"  rejected row {name!r}: {exc}")
    return products


def _make_server(html: str) -> HTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server naming
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # silence request logging
            pass

    return HTTPServer(("127.0.0.1", 0), Handler)


def serve_page(html: str):
    """Serve ``html`` locally; returns (server, url). Caller must shut down."""
    server = _make_server(html)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/"


class ProductApi:
    """Mock structured API: returns typed products, or fails with 503."""

    def __init__(self, fail_with_503: bool = False) -> None:
        self.fail_with_503 = fail_with_503
        self.calls = 0

    def list_products(self) -> list[Product]:
        self.calls += 1
        if self.fail_with_503:
            raise ProviderError(503, "product API unavailable")
        return [Product("Widget A", 12.50, 4.5),
                Product("Widget B", 9.99, 4.8),
                Product("Widget C", 19.00, 4.1)]


def fetch_via_browser(url: str) -> list[Product]:
    """The browser fallback: fetch the page, extract and validate."""
    import urllib.request

    with urllib.request.urlopen(url, timeout=10) as response:
        return extract_products(response.read().decode())


def cheapest_product(api: ProductApi, page_url: str) -> tuple[Product, str]:
    """API first; on a retryable API failure, fall back to the page.

    Returns the cheapest product and a label naming which path served
    it, so the degradation is visible in the log.
    """
    try:
        products = api.list_products()
        source = "api"
    except ProviderError as exc:
        if exc.status not in (429, 500, 502, 503, 504):
            raise
        print(f"API failed ({exc.status}); falling back to the browser page")
        products = fetch_via_browser(page_url)
        source = "browser (degraded)"
    if not products:
        raise RuntimeError("no valid products from either path")
    return min(products, key=lambda p: p.price), source


def demo() -> None:
    api = ProductApi()
    server, url = serve_page(PAGE_HTML)
    try:
        product, source = cheapest_product(api, url)
        print(f"API healthy: cheapest is {product.name} at {product.price} via {source}")

        api.fail_with_503 = True
        product, source = cheapest_product(api, url)
        print(f"API down: cheapest is {product.name} at {product.price} via {source}")

        # Break the page: one product loses its price.  It is rejected,
        # not guessed at, and the run is still degraded, not clean.
        server.shutdown()
        broken_server, broken_url = serve_page(BROKEN_PAGE_HTML)
        try:
            product, source = cheapest_product(api, broken_url)
            print(f"broken page: cheapest valid product is {product.name} "
                  f"at {product.price} via {source} (one row rejected)")
        finally:
            broken_server.shutdown()
    finally:
        server.shutdown()


if __name__ == "__main__":
    demo()
