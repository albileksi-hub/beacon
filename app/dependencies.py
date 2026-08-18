from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db import get_db

# Annotated dependencies keep FastAPI's injection out of function defaults,
# which keeps both linters and type checkers happy.
DbSession = Annotated[Session, Depends(get_db)]
