from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.core.response_wrapper import ApiResponse


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def request_validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        raw = exc.errors()
        simplified = [
            {
                'location': [str(x) for x in e.get('loc', ())],
                'message': str(e.get('msg', '')),
                'type': str(e.get('type', '')),
            }
            for e in raw
        ]
        parts = []
        for item in simplified:
            loc = '.'.join(item['location']) if item['location'] else 'request'
            parts.append(f'{loc}: {item["message"]}')
        message = '; '.join(parts) if parts else 'Validation failed'

        body = ApiResponse.fail(
            message=message,
            data={'validation_errors': simplified},
        ).model_dump()
        return JSONResponse(status_code=422, content=body)
