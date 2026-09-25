"""
Centralised error handling for Medusa.

MedusaError is raised anywhere in the app. The global exception handler
converts it to a JSON response with {"detail": "<plain message>"} that
the frontend displays as-is.

HTTP status code map:
  400  bad input (malformed body, unknown fields, no source files)
  413  payload too large (zip, GitHub repo)
  415  wrong content type (not a zip)
  422  invalid URL / failed validation
  429  rate limited
  502  upstream failure (GitHub API unreachable / unexpected status)
  504  scan or download timeout
"""

from fastapi import Request
from fastapi.responses import JSONResponse


class MedusaError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


async def medusa_error_handler(request: Request, exc: MedusaError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"detail": exc.message})
