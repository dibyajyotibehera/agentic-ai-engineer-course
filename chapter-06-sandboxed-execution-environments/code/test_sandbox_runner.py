"""Tests for the Chapter 6 sandbox runner.

These tests cover the *local* resource-limited path, which runs
without Docker, and the allowlist proxy. The Docker path is tested
only when Docker is installed (the test is skipped otherwise), because
this environment may not have it.

Note what is deliberately NOT asserted here: the local path does not
isolate the filesystem or the network, so no test claims it does. The
tests assert resource limits and timeouts — the only guarantees the
local path actually provides.
"""

from __future__ import annotations

import http.server
import socket
import threading
import unittest
import urllib.request

import egress_proxy
import sandbox_runner
import untrusted_samples


class TestParseMemory(unittest.TestCase):
    def test_suffixes(self):
        self.assertEqual(sandbox_runner.parse_memory("256m"), 256 * 1024 ** 2)
        self.assertEqual(sandbox_runner.parse_memory("1g"), 1024 ** 3)
        self.assertEqual(sandbox_runner.parse_memory("512k"), 512 * 1024)
        self.assertEqual(sandbox_runner.parse_memory("1024"), 1024)


class TestInterpretExitCode(unittest.TestCase):
    def test_oom(self):
        self.assertIn("137", sandbox_runner.interpret_exit_code(137))

    def test_success(self):
        self.assertEqual(sandbox_runner.interpret_exit_code(0),
                         "completed normally")

    def test_signal(self):
        self.assertIn("9", sandbox_runner.interpret_exit_code(-9))


class TestLocalLimits(unittest.TestCase):
    def test_benign_completes(self):
        result = sandbox_runner.run_local_limited(
            untrusted_samples.benign(), "256m", timeout=30)
        self.assertEqual(result["returncode"], 0)
        self.assertIn("benign run completed", result["stdout"])

    def test_memory_hog_is_stopped_by_the_limit(self):
        # 64 MiB address-space limit against a sample that wants ~512 MiB.
        result = sandbox_runner.run_local_limited(
            untrusted_samples.memory_hog(), "64m", timeout=30)
        self.assertNotEqual(result["returncode"], 0)
        self.assertNotIn("the limit did not hold", result["stdout"])

    def test_cpu_spin_is_stopped_by_the_timeout(self):
        result = sandbox_runner.run_local_limited(
            untrusted_samples.cpu_spin(), "256m", timeout=3)
        self.assertEqual(result["killed"], "timeout")
        self.assertIsNone(result["returncode"])


@unittest.skipUnless(sandbox_runner.docker_available(),
                     "Docker is not installed in this environment")
class TestDockerPath(unittest.TestCase):
    def test_benign_completes_in_docker(self):
        result = sandbox_runner.run_in_docker(
            untrusted_samples.benign(), "256m", "1.0", timeout=60)
        self.assertEqual(result["returncode"], 0)
        self.assertIn("benign run completed", result["stdout"])

    def test_breakout_fails_in_docker(self):
        result = sandbox_runner.run_in_docker(
            untrusted_samples.breakout(), "256m", "1.0", timeout=60)
        # The host secret, the network and the metadata service must all
        # fail. (Reading the container's own /etc/passwd succeeding is
        # expected and is not a breakout — see the sample's comment.)
        self.assertIn("read /host-secret.txt: failed as expected",
                      result["stdout"])
        self.assertIn("reach example.com:80: failed as expected",
                      result["stdout"])
        self.assertIn("reach metadata 169.254.169.254: failed as expected",
                      result["stdout"])


class TestEgressProxy(unittest.TestCase):
    """The proxy allows one host and refuses everything else — proven
    against a local HTTP server, so no external network is needed."""

    @classmethod
    def setUpClass(cls):
        # A tiny "allowed" origin server on localhost.
        class Origin(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - stdlib handler name
                body = b"origin ok"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # noqa: A002, D102
                pass

        cls.origin = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Origin)
        cls.origin_port = cls.origin.server_address[1]
        threading.Thread(target=cls.origin.serve_forever, daemon=True).start()

        egress_proxy.ALLOWED_HOSTS = {"127.0.0.1"}
        cls.proxy = egress_proxy.ThreadingHTTPServer(
            ("127.0.0.1", 0), egress_proxy.AllowlistProxyHandler)
        cls.proxy_port = cls.proxy.server_address[1]
        threading.Thread(target=cls.proxy.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.proxy.shutdown()
        cls.origin.shutdown()

    def _fetch_via_proxy(self, url: str) -> int:
        proxy = urllib.request.ProxyHandler(
            {"http": f"http://127.0.0.1:{self.proxy_port}"})
        opener = urllib.request.build_opener(proxy)
        try:
            with opener.open(url, timeout=5) as resp:
                return resp.status
        except urllib.error.HTTPError as exc:
            return exc.code

    def test_allowed_host_reaches_the_origin(self):
        self.assertEqual(
            self._fetch_via_proxy(f"http://127.0.0.1:{self.origin_port}/"), 200)

    def test_other_host_is_refused_by_the_proxy(self):
        # Denied before any outbound connection is attempted, so this
        # needs no external network to pass.
        self.assertEqual(self._fetch_via_proxy("http://example.com/"), 403)


if __name__ == "__main__":
    unittest.main()
