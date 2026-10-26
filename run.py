"""MediBridge local development launcher (optional convenience).

Launches the canonical FastAPI gateway and mobile web application.
Equivalent to:
    python -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload
"""

import sys
import uvicorn

if __name__ == "__main__":
    print("\n" + "=" * 68)
    print("  MediBridge Web App & REST API Gateway")
    print("=" * 68)
    print("  Mobile Web App:  http://127.0.0.1:8000/app/")
    print("  Root (Redirect): http://127.0.0.1:8000/")
    print("  API Docs:        http://127.0.0.1:8000/api/docs")
    print("  Health Check:    http://127.0.0.1:8000/api/health")
    print("=" * 68 + "\n")

    try:
        uvicorn.run(
            "api.main:app",
            host="127.0.0.1",
            port=8000,
            reload=True,
            log_level="info",
        )
    except KeyboardInterrupt:
        print("\nMediBridge server shut down cleanly.")
        sys.exit(0)
    except Exception as exc:
        print(f"\nMediBridge server error: {exc}", file=sys.stderr)
        sys.exit(1)
