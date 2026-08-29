from collections.abc import Callable
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlsplit


def _envelope(data: object = None, error: object = None) -> dict[str, object]:
    return {"data": data, "error": error}


def _request_handler(is_ready: Callable[[], bool]) -> type[BaseHTTPRequestHandler]:
    class WorkerHealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            path = urlsplit(self.path).path
            if path == "/health":
                self._respond(200, _envelope(data={"status": "ok"}))
            elif path == "/ready":
                if is_ready():
                    self._respond(200, _envelope(data={"status": "ready"}))
                else:
                    self._respond(
                        503,
                        _envelope(
                            error={
                                "code": "worker_not_ready",
                                "message": "The worker has not polled the query queue yet.",
                            }
                        ),
                    )
            else:
                self._respond(
                    404,
                    _envelope(
                        error={
                            "code": "not_found",
                            "message": "The requested resource was not found.",
                        }
                    ),
                )

        def _respond(self, status: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    return WorkerHealthHandler


class WorkerHealthServer:
    def __init__(self, port: int, *, is_ready: Callable[[], bool]) -> None:
        self._server = ThreadingHTTPServer(("0.0.0.0", port), _request_handler(is_ready))
        self._server.daemon_threads = True
        self._thread = Thread(target=self._server.serve_forever, name="worker-health", daemon=True)

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
