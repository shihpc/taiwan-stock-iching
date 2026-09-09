"""股市易經 P2 資料層。

套件不做任何 import 時的副作用（不讀 token、不連網、不開 DB），
以便 `plan`／離線測試在沒有 FINMIND_TOKEN 的環境跑得起來。
"""
