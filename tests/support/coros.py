import json
from collections.abc import Callable
from datetime import date
from urllib.request import Request

from fit_to_md.infrastructure.coros_gateway import Response


def reply(body: object, status: int = 200) -> Response:
    return Response(status, {}, json.dumps(body).encode())


def rpc_reply(request_id: int, result: object) -> Response:
    return reply({"jsonrpc": "2.0", "id": request_id, "result": result})


def catalogue_reply(
    start: date,
    end: date,
    rows: list[tuple[str, int, str]],
    *,
    encoded: bool = False,
    count: int | None = None,
) -> Response:
    lines = [
        f"Sport Records — {start} to {end} ({len(rows) if count is None else count} records)",
        "=======================",
        "",
    ]
    for number, (label, sport, times) in enumerate(rows, start=1):
        lines.extend(
            (
                f"{number}. Private activity title",
                f"   Time Window: {times}",
                f"   LabelId: {label} | SportType: {sport}",
            )
        )
    text = "\n".join(lines)
    return rpc_reply(
        2,
        {
            "content": [
                {"type": "text", "text": json.dumps(text) if encoded else text}
            ],
            "isError": False,
        },
    )


class FakeTransport:
    def __init__(self, *responses: Response | Callable[[Request], Response]) -> None:
        self.responses = iter(responses)
        self.requests: list[Request] = []

    def send(
        self, request: Request, *, max_response_bytes: int = 2_000_000
    ) -> Response:
        self.requests.append(request)
        result = next(self.responses)
        return result(request) if callable(result) else result


class FakeVault:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.available = True
        self.writable = True

    def get_password(self, service: str, username: str) -> str | None:
        if not self.available:
            raise OSError("private backend error")
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        if not self.writable:
            raise OSError("private write error")
        self.values[service, username] = password

    def delete_password(self, service: str, username: str) -> None:
        del self.values[service, username]
