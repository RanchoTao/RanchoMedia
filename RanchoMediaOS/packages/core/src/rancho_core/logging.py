import contextvars
import json
import logging

request_id_context: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)
LOG_FIELDS = (
    "request_id",
    "run_id",
    "publication_job_id",
    "publication_id",
    "platform",
    "adapter",
    "operation",
    "status",
)


class SafeJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        values = {field: getattr(record, field, None) for field in LOG_FIELDS}
        values["request_id"] = values["request_id"] or request_id_context.get()
        # Deliberately omit message arguments, exception text, request bodies, headers and URLs.
        return json.dumps({"level": record.levelname, **values})


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(SafeJsonFormatter())
    logger = logging.getLogger("rancho")
    logger.handlers = [handler]
    logger.setLevel(level)
    logger.propagate = False
