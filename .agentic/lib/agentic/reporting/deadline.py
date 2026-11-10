"""Monotonic HTTPS deadline, including DNS, TLS, send, headers and body.

Socket inactivity limits alone cannot stop a peer dripping headers or bytes.
The deadline shuts down each live socket, including sockets retained by a
response's makefile. DNS alone may outlive its wait; it holds no credential,
socket or callback capable of sending a request.
"""
from http.client import HTTPSConnection
import socket
import threading
from time import monotonic
from urllib.request import HTTPSHandler


class RequestDeadline:
    def __init__(self, seconds):
        self.seconds = seconds
        self.end = monotonic() + seconds
        self.lock = threading.Lock()
        self.sockets = []
        self.expired = False
        self.timer = threading.Timer(seconds, self.expire)

    def remaining(self):
        remaining = self.end - monotonic()
        if self.expired or remaining <= 0:
            raise TimeoutError('REPORT_HTTP_DEADLINE')
        return min(self.seconds, remaining)

    @staticmethod
    def stop(sock):
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    def watch(self, sock):
        with self.lock:
            if not self.expired:
                self.sockets.append(sock)
                return
        self.stop(sock)
        raise TimeoutError('REPORT_HTTP_DEADLINE')

    def expire(self):
        with self.lock:
            self.expired = True
            sockets = list(self.sockets)
        for sock in sockets:
            self.stop(sock)

    def resolve(self, host, port):
        result, completed = [], threading.Event()

        def lookup():
            try:
                result.append(socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM))
            except OSError:
                result.append(None)
            finally:
                completed.set()

        # OS DNS has no portable timeout. This isolated, credential-free lookup
        # cannot connect later when the request has already been refused.
        threading.Thread(target=lookup, name='awf-dns', daemon=True).start()
        if not completed.wait(self.remaining()) or not result[0]:
            raise TimeoutError('REPORT_HTTP_DNS_UNAVAILABLE')
        self.remaining()
        return result[0]

    def __enter__(self):
        self.timer.start()
        return self

    def __exit__(self, *args):
        self.timer.cancel()
        self.timer.join()
        self.expire()


class DeadlineConnection(HTTPSConnection):
    def __init__(self, host, *, deadline, **kwargs):
        super().__init__(host, **kwargs)
        self.deadline = deadline

    def connect(self):
        deadline = self.deadline
        for family, kind, protocol, _, address in deadline.resolve(self.host, self.port):
            sock = socket.socket(family, kind, protocol)
            deadline.watch(sock)
            try:
                sock.settimeout(deadline.remaining())
                sock.connect(address)
                self.sock = sock
                break
            except OSError:
                sock.close()
        else:
            raise OSError('REPORT_HTTP_CONNECT_UNAVAILABLE')
        # Register TLS before the handshake so a dripping handshake is bounded.
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host,
                                              do_handshake_on_connect=False)
        deadline.watch(self.sock)
        self.sock.settimeout(deadline.remaining())
        self.sock.do_handshake()
        self.sock.settimeout(deadline.remaining())


class DeadlineHTTPSHandler(HTTPSHandler):
    def __init__(self, deadline):
        super().__init__()
        self.deadline = deadline

    def https_open(self, request):
        def connection(host, **kwargs):
            return DeadlineConnection(host, deadline=self.deadline, **kwargs)
        return self.do_open(connection, request, context=self._context)
