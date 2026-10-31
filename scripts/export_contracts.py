import json
from pathlib import Path

from backend.api.app import create_app
from backend.contracts import Scenario


def main():
    directory = Path("backend/contracts")
    (directory / "openapi.json").write_text(json.dumps(create_app().openapi(), indent=2) + "\n", encoding="utf-8")
    (directory / "scenario.schema.json").write_text(json.dumps(Scenario.model_json_schema(), indent=2) + "\n",
                                                     encoding="utf-8")
    print("Published OpenAPI and Scenario JSON Schema, version 1")


if __name__ == "__main__":
    main()
