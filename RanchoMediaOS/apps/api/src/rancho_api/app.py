import hmac
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from rancho_core.domain import OWNER_ID, Clock, DomainError, SystemClock
from rancho_core.logging import configure_logging, request_id_context
from rancho_core.schemas import (
    AnalyticsView,
    BriefView,
    ContentView,
    EditVariant,
    ErrorBody,
    ErrorResponse,
    HealthResponse,
    IdeaView,
    MetricsCreate,
    MetricSnapshotView,
    PublicationCreate,
    PublicationView,
    ReviewDecision,
    StrategyView,
    SummaryView,
    TopicView,
    TrendCreate,
    TrendView,
    VariantView,
)
from rancho_core.service import MediaService
from rancho_core.settings import Settings
from rancho_db.database import Database
from rancho_mock.adapter import PersistentMockAdapter
from rancho_platform_sdk.contracts import (
    AuthenticationRequiredError,
    ExternalOutcomeUnknownError,
    ExternalPlatformError,
    UnsupportedCapabilityError,
)
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException
from starlette.responses import Response


def create_app(settings: Settings | None = None, clock: Clock | None = None) -> FastAPI:
    config, time = settings or Settings(), clock or SystemClock()
    database, mock_database = Database(config.database_url), Database(config.mock_database_url)
    adapter = PersistentMockAdapter(mock_database, time, config.mock_platform_seed)
    service = MediaService(database, adapter, time, environment=config.app_env)
    configure_logging(config.log_level)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        await service.seed()
        yield
        await database.close()
        await mock_database.close()

    application = FastAPI(
        title="Rancho Media OS — Phase 2 simulation",
        version="0.2.0",
        lifespan=lifespan,
        responses={
            400: {"model": ErrorResponse},
            401: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
        },
    )
    application.state.service = service
    application.state.adapter = adapter

    def error(code: str, message: str, status: int) -> JSONResponse:
        body = ErrorResponse(error=ErrorBody(code=code, message=message))
        return JSONResponse(body.model_dump(mode="json"), status_code=status)

    @application.exception_handler(DomainError)
    async def domain_error(request: Request, exception: DomainError) -> JSONResponse:
        return error(exception.code, exception.message, exception.status)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exception: RequestValidationError) -> JSONResponse:
        return error("VALIDATION_ERROR", "Request does not match the typed contract", 422)

    @application.exception_handler(UnsupportedCapabilityError)
    async def unsupported(request: Request, exception: UnsupportedCapabilityError) -> JSONResponse:
        return error("UNSUPPORTED_CAPABILITY", "Adapter operation unavailable", 409)

    @application.exception_handler(AuthenticationRequiredError)
    async def authentication_error(
        request: Request, exception: AuthenticationRequiredError
    ) -> JSONResponse:
        return error("AUTHENTICATION_REQUIRED", "Local fixture actor required", 401)

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, exception: HTTPException) -> JSONResponse:
        return error("HTTP_ERROR", "HTTP request unavailable", exception.status_code)

    @application.exception_handler(ExternalPlatformError)
    async def external_error(request: Request, exception: ExternalPlatformError) -> JSONResponse:
        code = (
            "OUTCOME_UNKNOWN"
            if isinstance(exception, ExternalOutcomeUnknownError)
            else "EXTERNAL_PLATFORM_ERROR"
        )
        return error(code, "External operation unavailable; inspect persisted job state", 503)

    @application.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exception: SQLAlchemyError) -> JSONResponse:
        return error("DATABASE_ERROR", "Database operation failed; inspect persisted state", 500)

    @application.exception_handler(Exception)
    async def unexpected_error(request: Request, exception: Exception) -> JSONResponse:
        return error("INTERNAL_ERROR", "Internal operation failed; inspect persisted state", 500)

    @application.middleware("http")
    async def request_log(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Ignore client-provided log IDs to avoid log injection.
        request_id = str(uuid4())
        context = request_id_context.set(request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            logging.getLogger("rancho.api").info(
                "api_request",
                extra={"operation": request.method, "status": str(response.status_code)},
            )
            return response
        finally:
            request_id_context.reset(context)

    async def actor(
        x_actor_id: Annotated[UUID | None, Header()] = None,
        x_rancho_api_token: Annotated[str | None, Header()] = None,
    ) -> MediaService:
        if x_actor_id != OWNER_ID:
            raise AuthenticationRequiredError()
        if config.app_env != "test" and (
            config.local_api_token is None
            or x_rancho_api_token is None
            or not hmac.compare_digest(x_rancho_api_token, config.local_api_token)
        ):
            raise AuthenticationRequiredError()
        return service

    ActiveService = Annotated[MediaService, Depends(actor)]

    @application.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        await adapter.health_check()
        return HealthResponse(
            database="sqlite_offline" if config.database_url.startswith("sqlite") else "postgresql"
        )

    @application.post("/trends", response_model=TrendView, status_code=201)
    async def create_trend(request: TrendCreate, active: ActiveService) -> TrendView:
        return await active.create_trend(request)

    @application.get("/trends", response_model=list[TrendView])
    async def trends(active: ActiveService) -> list[TrendView]:
        return await active.trends()

    @application.post("/trends/{trend_id}/ideas", response_model=IdeaView, status_code=201)
    async def ideas(trend_id: UUID, active: ActiveService) -> IdeaView:
        return await active.create_idea(trend_id)

    @application.post("/ideas/{idea_id}/briefs", response_model=BriefView, status_code=201)
    async def brief(idea_id: UUID, active: ActiveService) -> BriefView:
        return await active.create_brief(idea_id)

    @application.post("/briefs/{brief_id}/drafts", response_model=ContentView, status_code=201)
    async def draft(brief_id: UUID, active: ActiveService) -> ContentView:
        return await active.generate(brief_id)

    @application.get("/content", response_model=list[ContentView])
    async def content(active: ActiveService) -> list[ContentView]:
        return await active.content()

    @application.get("/content/{content_id}", response_model=ContentView)
    async def content_detail(content_id: UUID, active: ActiveService) -> ContentView:
        return (await active.content(content_id))[0]

    @application.get("/review", response_model=list[VariantView])
    async def review(active: ActiveService) -> list[VariantView]:
        return await active.review()

    @application.get("/review/{variant_id}", response_model=VariantView)
    async def review_detail(variant_id: UUID, active: ActiveService) -> VariantView:
        return (await active.review(variant_id))[0]

    @application.post("/review/{variant_id}/approve", response_model=VariantView)
    async def approve(
        variant_id: UUID, request: ReviewDecision, active: ActiveService
    ) -> VariantView:
        return await active.approve(variant_id, request)

    @application.post("/review/{variant_id}/reject", response_model=VariantView)
    async def reject(variant_id: UUID, active: ActiveService) -> VariantView:
        return await active.reject(variant_id)

    @application.post("/review/{variant_id}/edit", response_model=VariantView)
    async def edit(variant_id: UUID, request: EditVariant, active: ActiveService) -> VariantView:
        return await active.edit(variant_id, request)

    @application.post("/publications", response_model=PublicationView)
    async def publish(request: PublicationCreate, active: ActiveService) -> PublicationView:
        return await active.publish(request)

    @application.get("/publications", response_model=list[PublicationView])
    async def publications(active: ActiveService) -> list[PublicationView]:
        return await active.publications()

    @application.get("/publications/{job_id}", response_model=PublicationView)
    async def publication(job_id: UUID, active: ActiveService) -> PublicationView:
        return (await active.publications(job_id))[0]

    @application.post("/publications/{job_id}/reconcile", response_model=PublicationView)
    async def reconcile(job_id: UUID, active: ActiveService) -> PublicationView:
        return await active.reconcile(job_id)

    @application.post("/publications/{identifier}/metrics/mock", response_model=MetricSnapshotView)
    async def metrics(
        identifier: UUID, request: MetricsCreate, active: ActiveService
    ) -> MetricSnapshotView:
        return await active.observe(identifier, request.age_hours)

    @application.get("/analytics/publications/{identifier}", response_model=AnalyticsView)
    async def analytics(identifier: UUID, active: ActiveService) -> AnalyticsView:
        publication = await active.persisted_publication(identifier)
        return await active.analytics.publication(publication.id)

    @application.get("/analytics/summary", response_model=SummaryView)
    async def summary(active: ActiveService) -> SummaryView:
        return await active.analytics.summary()

    @application.post("/strategy/recompute", response_model=StrategyView)
    async def recompute(active: ActiveService) -> StrategyView:
        return await active.recompute()

    @application.get("/strategy/latest", response_model=StrategyView | None)
    async def strategy(active: ActiveService) -> StrategyView | None:
        return await active.latest_strategy()

    @application.get("/topics/next", response_model=list[TopicView])
    async def topics(active: ActiveService) -> list[TopicView]:
        return await active.topics()

    @application.post("/workflows/drain", response_model=list[PublicationView])
    async def drain(active: ActiveService) -> list[PublicationView]:
        return await active.drain()

    return application


app = create_app()
