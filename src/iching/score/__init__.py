"""六爻計分引擎（P2；`docs/P2-KICKOFF.md` §8）。**純函式、不碰 DB**——DB 讀取層在 `iching.score_io`。

- `transform`  ：S／clip_3d／N／L／P_hist、`Missing`、`Ind`
- `indicators` ：MA／ATR14／OBV／擺動點等技術量
- `aggregate`  ：族／爻／方向分數聚合與缺值重配（B1.7）
- `params`     ：參數字典（B2.8 欄位；P1 起點值、`calibrated=False`；每市場一份 `ParamSet`）
- `market`     ：大盤六爻 B1.1–B1.6 ＋ 旗標 B1.8
- `stock`      ：個股六爻 B2.1–B2.6
- `hexagram`   ：六爻 ↔ king_wen（B4）、暫定爻態、遲滯一步、基本狀態
- `assemble`   ：組 `scores_db_row`（鍵讀 `spec/dimensions.json`）
"""
from .aggregate import FamilyResult, LineResult, SubResult
from .assemble import MARKET_STOCK_ID, assemble_row, row_key, scores_db_row_keys
from .hexagram import (basic_state, line_flip_direction, from_king_wen_paths, hysteresis_step, king_wen_from_lines,
                       lines_from_king_wen, lines_from_scores, load_hexagrams, to_king_wen)
from .market import MarketInputs, MarketScores, market_flags, score_market
from .params import NON_PARAM_CONSTANTS, ParamSet, Rules, build_params
from .stock import StockInputs, StockScores, score_stock
from .transform import Ind, L, Missing, N, P_hist, S, S_clip, clip_3d, normalize

__all__ = [
    "FamilyResult", "LineResult", "SubResult", "MARKET_STOCK_ID", "assemble_row", "row_key", "scores_db_row_keys",
    "basic_state", "line_flip_direction", "from_king_wen_paths", "hysteresis_step", "king_wen_from_lines",
    "lines_from_king_wen", "lines_from_scores", "load_hexagrams", "to_king_wen",
    "MarketInputs", "MarketScores", "market_flags", "score_market", "NON_PARAM_CONSTANTS", "ParamSet", "Rules", "build_params",
    "StockInputs", "StockScores", "score_stock", "Ind", "L", "Missing", "N", "P_hist", "S", "S_clip", "clip_3d", "normalize",
]
