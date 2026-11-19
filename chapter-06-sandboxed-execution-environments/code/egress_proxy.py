"""An egress allowlist proxy for Exercise 3.

The proxy is the *only* way out of the sandbox: the container runs on
an internal Docker network with no direct route to the internet, and
its only egress is through this proxy, which forwards a request only
when the destination host is on the allowlist. Everything else gets
HTTP 403, and the refusal is logged, so you can prove the policy was
enforced by the proxy rather than by the code giving up.

This is a teaching implementation in the Python standard library, not
a production proxy. It handles plain HTTP requests and HTTPS
``CONNECT`` tunnels (for a tunnel it checks the host, then relays raw
bytes without inspecting the encrypted traffic — exactly as a real
forward proxy does). A production version would add authentication,
per-tenant allowlists, TLS (Transport Layer Security) interception
only where that is lawful and agreed, rate limits, and audit logging
to durable storage.

Usage::

    python egress_proxy.py --allow pypi.org --port 8899

Then, in a second terminal, create the internal network and run the
sandbox on it (these are the exact commands for Exercise 3)::

    docker network create --internal --subnet 172.30.14.0/24 sandbox-net
    docker run --rm -i --network sandbox-net \\
        -e HTTP_PROXY=http://<host-ip>:8899 -e HTTPS_PROXY=http://<host-ip>:8899 \\
        -e http_proxy=http://<host-ip>:8899 -e https_proxy=http://<host-ip>:8899 \\
        --memory 256m --memory-swap 256m --cpus 1.0 --pids-limit 64 \\
        --read-only --user 10001:10001 --cap-drop ALL \\
        agent-sandbox:ch06 python -c "import untrusted_samples"  # see note below

Note: ``<host-ip>`` must be an address the container can reach on the
internal network — the Docker gateway address of ``sandbox-net``
(``docker network inspect sandbox-net``), not ``localhost``, because
``localhost`` inside the container is the container itself. The
``egress_probe`` sample is run through ``sandbox_runner.py`` by
passing the probe code on standard input; the simplest path is to
copy ``untrusted_samples.py`` into the image (it is already there via
the Dockerfile) and run ``python -c "from untrusted_samples import
egress_probe; exec(egress_probe())"`` inside the container.

Run ``python egress_proxy.py --print-docker-commands`` to print the
Docker commands with the current allowlist filled in.
"""

from __future__ import annotations

import argparse
import select
import socket
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ALLOWED_HOSTS: set[str] = set()


def host_allowed(host: str) -> bool:
    """Exact-match allowlist check (no wildcards, no suffix matching)."""
    if not host:
        return False
    return host.split(":")[0].lower() in ALLOWED_HOSTS


class AllowlistProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A002 - stdlib name
        # Log to stdout in a greppable form: ALLOW / DENY + host.
        print(f"[proxy] {fmt % args}", flush=True)

    def _deny(self, host: str) -> None:
        body = f"egress denied: {host} is not on the allowlist\n".encode()
        self.send_response(403)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        print(f"[proxy] DENY {host} from {self.client_address[0]}", flush=True)

    def _relay_tunnel(self, upstream: socket.socket) -> None:
        """Relay bytes both ways until either side closes."""
        sockets = [self.connection, upstream]
        try:
            while True:
                readable, _, _ = select.select(sockets, [], [], 30)
                if not readable:
                    break
                for sock in readable:
                    data = sock.recv(65536)
                    if not data:
                        return
                    (upstream if sock is self.connection else self.connection).sendall(data)
        except OSError:
            pass
        finally:
            upstream.close()

    def do_CONNECT(self) -> None:  # noqa: N802 - stdlib handler name
        host = self.path.split(":")[0]
        if not host_allowed(host):
            self._deny(host)
            return
        print(f"[proxy] ALLOW CONNECT {self.path}", flush=True)
        try:
            upstream = socket.create_connection((host, 443), timeout=10)
        except OSError as exc:
            self.send_error(502, f"cannot connect to {host}: {exc}")
            return
        self.send_response(200, "Connection Established")
        self.end_headers()
        self._relay_tunnel(upstream)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler name
        self._handle_plain_http()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler name
        self._handle_plain_http()

    def _handle_plain_http(self) -> None:
        parts = urlsplit(self.path)
        host = parts.netloc or self.headers.get("Host", "")
        if not host_allowed(host):
            self._deny(host)
            return
        print(f"[proxy] ALLOW {self.command} {self.path}", flush=True)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        req = urllib.request.Request(self.path, data=body, method=self.command)
        for key, value in self.headers.items():
            if key.lower() not in ("host", "content-length", "connection"):
                req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
                self.send_response(resp.status)
                for key, value in resp.headers.items():
                    if key.lower() not in ("connection", "transfer-encoding"):
                        self.send_header(key, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        except Exception as exc:  # noqa: BLE001 - report the upstream failure
            self.send_error(502, f"upstream error for {host}: {exc}")


def print_docker_commands(port: int) -> None:
    print("# 1. Create the internal network (no route to the outside):")
    print("docker network create --internal --subnet 172.30.14.0/24 sandbox-net")
    print()
    print("# 2. Find the gateway address the container can use to reach this proxy:")
    print("docker network inspect sandbox-net --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}'")
    print()
    print("# 3. Run the sandbox on that network, proxy as its only egress:")
    print(f"docker run --rm -i --network sandbox-net \\")
    print(f"  -e HTTP_PROXY=http://<gateway>:{port} -e HTTPS_PROXY=http://<gateway>:{port} \\")
    print(f"  -e http_proxy=http://<gateway>:{port} -e https_proxy=http://<gateway>:{port} \\")
    print("  --memory 256m --memory-swap 256m --cpus 1.0 --pids-limit 64 \\")
    print("  --read-only --user 10001:10001 --cap-drop ALL \\")
    print("  agent-sandbox:ch06 python -c \"from untrusted_samples import egress_probe; exec(egress_probe())\"")


def main() -> int:
    parser = argparse.ArgumentParser(description="Egress allowlist proxy.")
    parser.add_argument("--allow", action="append", default=["pypi.org"],
                        help="host to allow (repeatable; default: pypi.org)")
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--print-docker-commands", action="store_true",
                        help="print the Docker commands for Exercise 3 and exit")
    args = parser.parse_args()

    global ALLOWED_HOSTS  # noqa: PLW0603 - set once at startup
    ALLOWED_HOSTS = {h.lower() for h in args.allow}
    if args.print_docker_commands:
        print_docker_commands(args.port)
        return 0

    server = ThreadingHTTPServer(("0.0.0.0", args.port), AllowlistProxyHandler)
    print(f"[proxy] listening on :{args.port}, allowlist: {sorted(ALLOWED_HOSTS)}",
          flush=True)
    print("[proxy] only hosts on the allowlist are reachable; all else gets 403",
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[proxy] shutting down", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
