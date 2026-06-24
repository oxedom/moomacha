import uvicorn

from control_plane.app import create_app
from control_plane.config import Settings

settings = Settings()
app = create_app(settings=settings)


def main() -> None:
    from control_plane.observability.logging_config import configure_logging
    configure_logging(json_logs=settings.log_json)
    uvicorn.run(app, host="0.0.0.0", port=settings.port)


if __name__ == "__main__":
    main()
