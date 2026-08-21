from fastapi import APIRouter

from app.api.routes import (
    agents,
    auth,
    backtest,
    dashboard,
    market,
    risk,
    signal_performance,
    trades,
)

api_router = APIRouter()

api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
api_router.include_router(agents.router, prefix="/agents", tags=["agents"])
api_router.include_router(trades.router, prefix="/trades", tags=["trades"])
api_router.include_router(backtest.router, prefix="/backtest", tags=["backtest"])
api_router.include_router(risk.router, prefix="/risk", tags=["risk"])
api_router.include_router(market.router, prefix="/market", tags=["market"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(
    signal_performance.router, prefix="/signal-performance", tags=["signal-performance"]
)
