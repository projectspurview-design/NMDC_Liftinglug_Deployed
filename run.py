"""Portable headless entry point. Execute inside the DX-RT Python environment."""
import sys
from app import main

if __name__ == "__main__":
    sys.argv[1:1] = ["--host", "127.0.0.1"]
    main()
