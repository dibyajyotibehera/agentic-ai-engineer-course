"""A minimal debugging SMTP server for the Chapter 7 demo.

Run it in a second terminal before running the demo:

    python debug_smtp_server.py

It listens on localhost:1025, accepts every message, prints the
envelope and the body to standard output, and delivers nothing.
That is the point: the demo's send tool makes a real SMTP
connection and a real send, and the mail goes nowhere, so the
vulnerable agent's exfiltration is observable without anything
leaving the machine.

It is written on the standard library only, because the ``smtpd``
module the old debugging servers used was removed in Python 3.12.
It speaks enough of the SMTP protocol for a standard client to
complete a send: greeting, EHLO, MAIL FROM, RCPT TO, DATA and
QUIT.

Point the demo at a real mail server instead by setting SMTP_HOST
and SMTP_PORT (and SMTP_USER, SMTP_PASSWORD and SMTP_TLS=1 when
the server needs them). Do not do that with the poisoned ticket
unless you intend a real message to leave the machine.
"""

from __future__ import annotations

import socketserver
import sys


class _SMTPHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        self.wfile.write(b"220 debug-smtp ready\r\n")
        mail_from = ""
        rcpt_tos: list[str] = []
        while True:
            line = self.rfile.readline().decode(errors="replace")
            if not line:
                return
            command = line.strip()
            upper = command.upper()
            if upper.startswith("EHLO") or upper.startswith("HELO"):
                self.wfile.write(b"250-debug-smtp\r\n250 8BITMIME\r\n")
            elif upper.startswith("MAIL FROM:"):
                mail_from = command[10:].strip()
                rcpt_tos = []
                self.wfile.write(b"250 OK\r\n")
            elif upper.startswith("RCPT TO:"):
                rcpt_tos.append(command[8:].strip())
                self.wfile.write(b"250 OK\r\n")
            elif upper == "DATA":
                self.wfile.write(b"354 End data with <CR><LF>.<CR><LF>\r\n")
                data_lines: list[str] = []
                while True:
                    data_line = self.rfile.readline().decode(errors="replace")
                    if data_line.rstrip("\r\n") == ".":
                        break
                    data_lines.append(data_line)
                print("=" * 60, flush=True)
                print(f"MAIL FROM: {mail_from}", flush=True)
                print(f"RCPT TO: {', '.join(rcpt_tos)}", flush=True)
                print("-" * 60, flush=True)
                print("".join(data_lines), flush=True)
                print("=" * 60, flush=True)
                self.wfile.write(b"250 OK: message accepted, delivered nowhere\r\n")
            elif upper == "RSET":
                mail_from = ""
                rcpt_tos = []
                self.wfile.write(b"250 OK\r\n")
            elif upper == "NOOP":
                self.wfile.write(b"250 OK\r\n")
            elif upper == "QUIT":
                self.wfile.write(b"221 Bye\r\n")
                return
            else:
                self.wfile.write(b"502 Command not implemented\r\n")


class DebugSMTPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "localhost"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 1025
    server = DebugSMTPServer((host, port), _SMTPHandler)
    print(f"Debug SMTP server listening on {host}:{port}. Deliveries go nowhere.")
    print("Press Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
