"""Run the ClashFlow Management Panel: python -m clashflow."""

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "clashflow.main:app",
        host=os.environ.get("CLASHFLOW_BIND", "0.0.0.0"),
        port=int(os.environ.get("CLASHFLOW_PORT", "8080")),
    )


if __name__ == "__main__":
    main()
