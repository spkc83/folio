"""The vocabulary: the metrics and dimensions that people and the model can name."""
from fastapi import APIRouter, Depends

from living_reports.api.deps import Stores, stores

router = APIRouter(prefix="/api")


@router.get("/vocabulary")
def vocabulary(s: Stores = Depends(stores)) -> dict:
    return s.graph.vocabulary().model_dump(mode="json")
