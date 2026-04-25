"""astrapi_mirror._cli – Console-Script-Einstiegspunkt.

Start:
    astrapi-mirror --work-dir /opt/astrapi-mirror --port 5002
    astrapi-mirror --work-dir /opt/astrapi-mirror --port 5002 --debug
"""
from astrapi_core.system.paths import run_app


def main() -> None:
    run_app("astrapi_mirror._app:app", "astrapi-mirror", default_port=5002)


if __name__ == "__main__":
    main()
