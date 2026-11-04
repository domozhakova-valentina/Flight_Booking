"""Генерация gRPC-кода обоих сервисов: python proto/generate.py."""

import argparse
from importlib.resources import files
from pathlib import Path

from grpc_tools import protoc


ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("services", nargs="*", help="booking и/или flight; по умолчанию оба")
    services = parser.parse_args().services or ["booking", "flight"]
    if any(service not in ("booking", "flight") for service in services):
        parser.error("Допустимы только booking и flight")
    for service in services:
        output = ROOT / "services" / service
        # Виртуальный путь даёт корректный импорт app.generated.flight_pb2
        # в сгенерированном gRPC-модуле без ручного исправления кода.
        result = protoc.main([
            "grpc_tools.protoc",
            f"-Iapp/generated={ROOT / 'proto'}",
            f"-I{files('grpc_tools') / '_proto'}",
            f"--python_out={output}",
            f"--grpc_python_out={output}",
            "app/generated/flight.proto",
        ])
        if result != 0:
            raise SystemExit(result)
        (output / "app" / "generated" / "__init__.py").touch()
        print(f"Generated: services/{service}/app/generated/")


if __name__ == "__main__":
    main()
