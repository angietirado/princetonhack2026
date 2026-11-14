"""Check a running Docker stack using only the Python standard library.

The defaults target published host ports. Inside the backend container, pass
--frontend-url http://frontend:8080 and the backend's container URL.
"""

import argparse
import json
import re
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen


def read(url, content_types=None):
    with urlopen(url, timeout=10) as response:
        assert response.status == 200, (url, response.status)
        if content_types is not None:
            content_type = response.headers.get_content_type()
            assert content_type in content_types, (url, content_type)
        return response.read()


def assert_not_found(url):
    # HEAD checks access controls without retrieving sensitive file contents.
    request = Request(url, method="HEAD")
    try:
        with urlopen(request, timeout=10) as response:
            status = response.status
    except HTTPError as error:
        status = error.code
        error.close()
    assert status == 404, (url, status)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--frontend-url", default="http://127.0.0.1:5173",
        help="Frontend base URL (inside Docker: http://frontend:8080).",
    )
    parser.add_argument(
        "--backend-url", default="http://127.0.0.1:8765",
        help="Backend base URL; override with its container URL inside Docker.",
    )
    args = parser.parse_args(argv)
    frontend = args.frontend_url.rstrip("/")
    backend = args.backend_url.rstrip("/")
    html = read(frontend + "/", {"text/html"})
    assert re.search(rb"<title>Ledgerly\b", html), "Ledgerly title is missing"
    for element_id in ("overview-view", "transactions-view", "workspace-switcher", "balance-value"):
        assert f'id="{element_id}"'.encode() in html, f"Missing Ledgerly element: {element_id}"

    scripts = re.findall(rb'<script[^>]+src="([^"]+)"', html)
    assert any(urlsplit(src.decode()).path.endswith("/app.js") or src == b"app.js" for src in scripts), \
        "Ledgerly app.js is missing"
    for script in scripts:
        script_url = urljoin(frontend + "/", script.decode())
        javascript = read(script_url, {"application/javascript", "text/javascript"})
        assert javascript.strip(), (script_url, "Empty JavaScript")
        assert not javascript.lstrip().lower().startswith(b"<!doctype html"), script_url
        if urlsplit(script_url).path.endswith("/app.js"):
            assert b"document." in javascript, "Ledgerly JavaScript content is missing"

    stylesheet_links = re.findall(rb'<link[^>]+href="([^"]+)"', html)
    stylesheets = [href for href in stylesheet_links if urlsplit(href.decode()).path.endswith("styles.css")]
    assert stylesheets, "Ledgerly styles.css is missing"
    for stylesheet in stylesheets:
        stylesheet_url = urljoin(frontend + "/", stylesheet.decode())
        css = read(stylesheet_url, {"text/css"})
        assert b".app-shell" in css, "Ledgerly stylesheet content is missing"

    assert read(frontend + "/a-client-route", {"text/html"}) == html
    for path in ("/.env", "/.git/config"):
        assert_not_found(frontend + path)
    assert json.loads(read(frontend + "/health"))["status"] == "ok"
    route = "/api/v1/spending/summary"
    direct = json.loads(read(backend + route))
    assert direct == json.loads(read(frontend + route))
    assert direct["source"] == "fixture"
    assert direct["included_count"] > 0
    assert direct["net_spending_cents"] == sum(row["amount_cents"] for row in direct["categories"])
    dashboard = json.loads(read(frontend + "/api/v1/dashboard"))
    assert dashboard["summary"] == direct
    assert dashboard["gross_spending_cents"] - dashboard["refunds_cents"] == direct["net_spending_cents"]
    monthly_total = sum(row["net_spending_cents"] for row in dashboard["monthly_spending"])
    assert monthly_total == direct["net_spending_cents"]
    assert json.loads(read(backend + "/openapi.json"))["info"]["title"] == "Spending Companion API"
    request = Request(frontend + "/webhooks/photon", data=b"{}", method="POST",
                      headers={"Content-Type": "application/json"})
    try:
        urlopen(request, timeout=10)
        raise AssertionError("Unconfigured Photon webhook unexpectedly accepted a request")
    except HTTPError as error:
        assert error.code == 503, error.code
        assert "not configured" in json.loads(error.read())["detail"]
    print("Docker smoke checks passed: Ledgerly HTML/assets, SPA routing, hidden paths, "
          "API proxy, summary, dashboard totals, docs, webhook.")


if __name__ == "__main__":
    main()
