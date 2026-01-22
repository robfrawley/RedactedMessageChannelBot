from pathlib import Path

# ----------------------------------------------------------------------
# Base directory and environment file path
# ----------------------------------------------------------------------

# Base directory of the project (two levels up from this file)
BASE_DIR: Path = Path(__file__).resolve().parent.parent

# Path to the environment (.env) file
ENV_FILE_PATH: Path = BASE_DIR / ".env"
