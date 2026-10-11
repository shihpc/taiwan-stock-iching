// tests/explain_cases.mjs —— docs/P4-PREVIEW.md §6 F2：從 index.html 抽出說明層常數與純函式，在 node 裡跑固定案例、
// 斷言輸出逐字＝預期句。抽法同 taiwan-flows/tests/extract_js.mjs（宣告字串定位＋括號配對切片，vm 沙箱執行），
// 不需要改 index.html 結構。用法：node tests/explain_cases.mjs [index.html 路徑]（預設 repo 根的 index.html）。
// 由 tests/test_explain_js.py 以 subprocess 呼叫；node 不存在時該測試 skip。
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";

const HTML = process.argv[2] || path.join(import.meta.dirname, "..", "index.html");
const html = fs.readFileSync(HTML, "utf8");

/** 括號配對切片：從 start 起找第一個 open，配對到 depth 0（字串／樣板字面值內不算層級）。 */
function sliceBalanced(start, open, close) {
  const o = html.indexOf(open, start);
  if (o < 0) throw new Error(`找不到 ${open}`);
  let depth = 0, inStr = null;
  for (let i = o; i < html.length; i++) {
    const c = html[i];
    if (inStr) { if (c === "\\") { i++; continue; } if (c === inStr) inStr = null; }
    else if (c === '"' || c === "'" || c === "`") inStr = c;
    else if (c === open) depth++;
    else if (c === close) { depth--; if (depth === 0) return html.slice(start, i + 1); }
  }
  throw new Error(`括號未配對 @${start}`);
}
/** 抽 `const NAME = {…}` / `[…]`（多行物件／陣列字面值）。宣告字串必須唯一命中。 */
function pickConst(name) {
  const re = new RegExp(`^const ${name} = `, "mg");
  const hits = [...html.matchAll(re)];
  if (hits.length !== 1) throw new Error(`const ${name} 命中 ${hits.length} 次（需唯一）`);
  const start = hits[0].index;
  const eq = html.indexOf("=", start) + 1;
  const first = html.slice(eq).match(/[\[{]/);
  const open = first[0], close = open === "{" ? "}" : "]";
  return sliceBalanced(start, open, close) + ";";
}
/** 抽單行字串常數 `const NAME = "…";`（整行；宣告字串必須唯一命中）。 */
function pickLineConst(name) {
  const re = new RegExp(`^const ${name} = .*;[^\\n]*$`, "mg");
  const hits = [...html.matchAll(re)];
  if (hits.length !== 1) throw new Error(`const ${name} 命中 ${hits.length} 次（需唯一）`);
  return hits[0][0];
}
/** 抽 `function NAME(` 起至配對的 `}`。 */
function pickFunc(name) {
  const key = `function ${name}(`;
  const hits = html.split(key).length - 1;
  if (hits !== 1) throw new Error(`${key} 命中 ${hits} 次（需唯一）`);
  return sliceBalanced(html.indexOf(key), "{", "}");
}

const CONSTS = ["HORIZONS", "POS", "FACET", "TRI_NAME", "TRI", "TERMS", "ST_WORD",
  "TRI_ORDER", "TRI_ELEM", "GUIDE_INTRO",   // 後三個：§10 懂卦理
  "RANK_DISC_H", "RANK_DISC_S", "RANK_MK",   // §12.9 總分排序
  "MARKETS", "MK_LABEL", "IND_MISS", "IND_STRUCT", "IND_VSEQ", "IND_MSEQ", "IND_CONT", "IND_DIV", "IND_TERMS", "IND_COLS"];   // §13 PR-75d 本爻依據
const LINE_CONSTS = ["MV_OFF_TXT", "MV_SWITCH_TAG", "MV_MIX_TXT", "CAL_FALSE_HTML", "CAL_TRUE_HTML",   // §9 模型換版標示
  "KW_RE", "GUIDE_DIST_NOTE", "GUIDE_EXTRA_NOTE", "state",   // §10（state：guideListRows 讀 state.gq）
  "CODE_RE", "HOLD_KEY", "HOLD_SRC_TXT", "HOLD_NOTE_TXT", "HOLD_EMPTY_TXT", "HOLD_NO_H_TXT",   // §11 我的持股
  "RANK_XMKT_TXT", "RANK_RANK_NOTE", "RANK_TOP_N", "RANK_MK_SET", "RANK_IND_MAXLEN", "RANK_WARN_TXT", "RANK_MORE_TXT",   // §12.9 總分排序
  "IND_SCHEMA", "IND_HEAD", "IND_OFF_TXT", "IND_LIST_NOTE", "IND_KEY_RE", "RANK_IND_ON", "indFin", "indD", "indPct", "indLots", "indCat"];   // §13 PR-75d
const FUNCS = ["lineBit", "triSentence", "movingLines", "explainHex", "explainLine",
  "psVal", "tlPs", "psDiffers", "modelShiftAt", "modelShifts", "modelShiftText", "mvOffFor", "modelVersionInfo",
  "hexBits", "hexTri", "parseKw", "hexDist", "guideFacetRows", "guideTriRows", "kwLabel", "guideListRows",   // §10
  "hexFig", "holdingsCodes", "readHoldings", "holdRowHtml", "holdHtml",   // §11 我的持股（holdHtml 讀 DATA／state.h）
  "rankBs", "rankRows", "rankIndustries",   // §12.9 總分排序（純函式）
  "indN", "indSg", "indStr", "indMissTxt", "indShapeOk", "indRow", "indCtx", "indWin", "indTbl", "indMa",
  "indStockRows", "indMarketRows", "indRows", "indTableHtml"];   // §13 PR-75d 本爻依據（純函式）
// esc 與 index.html 同實作（該宣告跨兩行、不走 pickLineConst）；guideFacetRows／guideTriRows 需要它
const ESC_SRC = `const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));`;
const KWN_SRC = "const KW_NAME = {};";   // index.html 是 let（與 DATA／TL 同行宣告）；kwLabel 只在 name 缺時查它
const DATA_SRC = "var DATA = null;";      // §11 holdHtml／holdRowHtml 讀全域 DATA；var 掛在沙箱全域物件上，案例以 sb.DATA 餵 fixture
const src = [ESC_SRC, KWN_SRC, DATA_SRC, ...CONSTS.map(pickConst), ...LINE_CONSTS.map(pickLineConst), ...FUNCS.map(pickFunc)].join("\n\n");
const sb = { Array, Number, String, Object, isNaN, isFinite, Set, console, JSON };
vm.createContext(sb);
new vm.Script(src).runInContext(sb);
// const 是詞法綁定、不掛在 context 物件上（function 宣告才會），用一支表達式腳本讀回
const { FACET, TRI, TERMS, explainHex, explainLine, MV_OFF_TXT, MV_SWITCH_TAG, MV_MIX_TXT, CAL_FALSE_HTML, CAL_TRUE_HTML,
  tlPs, psDiffers, modelShiftAt, modelShifts, modelShiftText, mvOffFor, modelVersionInfo,
  TRI_NAME, TRI_ORDER, TRI_ELEM, GUIDE_INTRO, GUIDE_DIST_NOTE, GUIDE_EXTRA_NOTE, hexBits, hexTri, parseKw, hexDist } = new vm.Script(
  "({ FACET, TRI, TERMS, explainHex, explainLine, MV_OFF_TXT, MV_SWITCH_TAG, MV_MIX_TXT, CAL_FALSE_HTML, CAL_TRUE_HTML, " +
  "tlPs, psDiffers, modelShiftAt, modelShifts, modelShiftText, mvOffFor, modelVersionInfo, " +
  "TRI_NAME, TRI_ORDER, TRI_ELEM, GUIDE_INTRO, GUIDE_DIST_NOTE, GUIDE_EXTRA_NOTE, hexBits, hexTri, parseKw, hexDist })").runInContext(sb);
const { kwLabel, guideListRows, state } = new vm.Script("({ kwLabel, guideListRows, state })").runInContext(sb);
// §11 我的持股：純函式與區塊渲染（holdHtml 讀 sb.DATA 與 state.h）
const { holdingsCodes, holdHtml, HOLD_KEY, HOLD_SRC_TXT, HOLD_NOTE_TXT, HOLD_EMPTY_TXT, HOLD_NO_H_TXT } = new vm.Script(
  "({ holdingsCodes, holdHtml, HOLD_KEY, HOLD_SRC_TXT, HOLD_NOTE_TXT, HOLD_EMPTY_TXT, HOLD_NO_H_TXT })").runInContext(sb);
// §12.9 總分排序：純函式與揭露常數
const { rankRows, rankIndustries, RANK_DISC_H, RANK_DISC_S, RANK_XMKT_TXT, RANK_RANK_NOTE, RANK_WARN_TXT, RANK_MORE_TXT } = new vm.Script(
  "({ rankRows, rankIndustries, RANK_DISC_H, RANK_DISC_S, RANK_XMKT_TXT, RANK_RANK_NOTE, RANK_WARN_TXT, RANK_MORE_TXT })").runInContext(sb);
// §13 PR-75d 本爻依據：純函式與常數；fixture＝tests/fixtures/ind_23_market_2026-10-08.json（23.json 只留 2330／2317＋market.json 的 10-08 逐值副本）。
// 使用者 2026-10-11 裁示後：每爻是一張表（indRows 回 [[項目, 數值, 口徑／備註], …]），不顯示 ATR 倍數、借券賣出餘額用正名
const { indRows, indShapeOk, indTableHtml, IND_OFF_TXT, IND_MISS, IND_TERMS, IND_HEAD, IND_LIST_NOTE, IND_STRUCT, IND_VSEQ, IND_MSEQ, IND_CONT, IND_DIV, IND_COLS } = new vm.Script(
  "({ indRows, indShapeOk, indTableHtml, IND_OFF_TXT, IND_MISS, IND_TERMS, IND_HEAD, IND_LIST_NOTE, IND_STRUCT, IND_VSEQ, IND_MSEQ, IND_CONT, IND_DIV, IND_COLS })").runInContext(sb);
sb.LINE_NAMES = { stock: ["初 營運基礎", "二 價格趨勢", "三 相對動能", "四 量價確認", "五 籌碼供需", "上 外部環境"] };   // indTableHtml 的 r 模式讀它（index.html 由 FACET 推導）
const IND_FX = JSON.parse(fs.readFileSync(path.join(import.meta.dirname, "fixtures", "ind_23_market_2026-10-08.json"), "utf8"));
const indClone = k => JSON.parse(JSON.stringify(IND_FX[k]));
const IND_REF = { date: "2026-10-08", params_sha: IND_FX["23"].params_sha };
const ind6 = (j, kind, key, h, ref = IND_REF) => [0, 1, 2, 3, 4, 5].map(i => indRows(j, kind, key, h, i, ref));
const indR = (j, i, h = "short") => indRows(j, "stock", "2330", h, i, IND_REF);
// 改 2330 某期間子指標的 x 或輔助值（依 cols 欄名找位置；不寫死索引）
const indPatch = (j, h, xs, as) => { const r = j.stocks["2330"][h], c = j.cols[String(r[0])];
  for (const [n, v] of Object.entries(xs || {})) r[1][c.x.indexOf(n)] = v;
  for (const [n, v] of Object.entries(as || {})) r[2][c.a.indexOf(n)] = v; return j; };
// 欄位順序打亂＋cid 改號＋窗長改值：輸出只能隨 cols／win 變、不能依賴寫死的位置或 cid（D4）
const indShuffle = () => { const j = indClone("23"), r = j.stocks["2330"].short, c = j.cols[String(r[0])];
  const ox = c.x.map((n, k) => [n, r[1][k]]).reverse(), oa = c.a.map((n, k) => [n, r[2][k]]).reverse();
  j.cols = { "77": { h: "short", x: ox.map(e => e[0]), a: oa.map(e => e[0]) } };
  j.stocks = { "2330": { m: "twse", c: 2550, short: [77, ox.map(e => e[1]), oa.map(e => e[1])], swing: null, mid: null } };
  j.win["twse|short"]["2A.dist_ma_short"] = [7, "ATR", 2]; return j; };
// 2330 10-08 短線六爻表（初→上；每列 [項目, 數值, 口徑／備註]）＝§13.3 表的數字
const IND_2330_S = [
  [["營收資料月", "2026-08", "當日可得的最新月"], ["單月營收年增率", "+53.32%", ""], ["營收加速度", "+24.45 個百分點", "近 3 月合計年增率減前 3 月合計年增率"]],
  [["收盤距 5 日均線", "−0.35%", ""], ["收盤距 20 日均線", "+3.04%", ""], ["20 日均線 5 日變化", "+1.47%", ""], ["高低點結構", "未成明確型態", "看近 20 日、擺動點前後 2 根確認"]],
  [["10 日超額報酬", "+0.49 個百分點", "個股 +3.66%、加權指數 +3.17%"], ["5 日超額報酬", "−0.39 個百分點", "個股 +1.59%、加權指數 +1.99%"],
   ["10 日對同產業超額", "+2.66 個百分點", "同產業報酬中位數 +0.99%（96 檔，含本股）"], ["5 日超額加速度", "−1.27 個百分點", "近 5 日超額減前 5 日超額"]],
  [["當日量價情境", "其他", "當日量為前 20 日均量的 1.05 倍；當日跌 1.35%；回檔看近 5 日"], ["5 日平均收盤位置", "0.581", "0＝收在當日最低、1＝收在當日最高"],
   ["延續（基準 20 日、確認 3 日）", "確認窗未滿", "最近一次突破或跌破事件在 2 個交易日前"]],
  [["外資 5 日淨賣超", "11,282 張（占同期成交 10.90%）", "同期成交 103,481 張"], ["外資 3 日淨賣超", "15,139 張（占同期成交 24.86%）", "同期成交 60,888 張"],
   ["投信 5 日淨買超", "1,857 張（占同期成交 1.79%）", "同期成交 103,481 張"], ["投信 3 日淨買超", "1,014 張（占同期成交 1.67%）", "同期成交 60,888 張"],
   ["外資 5 日買超天數", "1 日", "5 日中外資買超的日數"], ["融資餘額 5 日變化", "+3.03%", "30,658 → 31,586 張；同期股價 +1.59%；融資增加且同期股價上漲，記中性"],
   ["借券賣出餘額 5 日變化", "−0.0085%", "14,198,514 → 11,985,514 股；變化量占發行股數"]],
  [["所屬大盤方向分數", "51.74", "上市・短線"], ["同產業 5 日報酬相對加權指數", "−2.79 個百分點", "同產業中位數 −0.80%（含本股）、加權指數 +1.99%"],
   ["產業內站上 20 日均線家數比", "70.8%", ""]],
];
// 使用者核准樣稿的語意（使用者 2026-10-11 裁示改表格後保留在二爻各列）：收盤距 5 日均線 −0.35%／距 20 日均線 +3.04%／20 日均線 5 日變化 +1.47%／高低點結構未成明確型態
const IND_APPROVED_ROWS = [["收盤距 5 日均線", "−0.35%"], ["收盤距 20 日均線", "+3.04%"], ["20 日均線 5 日變化", "+1.47%"], ["高低點結構", "未成明確型態"]];
// 迷你 stocks：同分（A 與 C 皆 60）、池外（P0 bs 最高 99 也不得列入）、bs 缺（字串 "70"、null、缺期間物件）、兩市場兩產業
const RK_ST = { "2002": { market: "twse", in_rank_pool: 1, short: { bs: 60 } }, "1001": { market: "twse", in_rank_pool: 1, short: { bs: 60 } },
  "3003": { market: "tpex", in_rank_pool: 1, short: { bs: 70.25 } }, "4004": { market: "tpex", in_rank_pool: 1, short: { bs: 50 } },
  "9990": { market: "twse", in_rank_pool: 0, short: { bs: 99 } }, "5005": { market: "twse", in_rank_pool: 1, short: { bs: "70" } },
  "6006": { market: "tpex", in_rank_pool: 1, short: { bs: null } }, "7007": { market: "twse", in_rank_pool: 1 },
  "8008": { market: "twse", in_rank_pool: 1, short: { bs: 60 } } };
const RK_NM = { "2002": ["乙", "甲業"], "1001": ["甲", "乙業"], "3003": ["丙", "甲業"], "4004": ["丁", "乙業"], "9990": ["外", "甲業"],
  "5005": ["戊", "甲業"], "6006": ["己", "乙業"], "7007": ["庚", "甲業"], "8008": ["辛", "甲業"] };
const rk = (mk, ind) => { const R = rankRows(RK_ST, RK_NM, "short", mk, ind); return JSON.stringify([R.rows.map(r => [r.code, r.bs, r.rank]), R.inPool, R.missing, R.outPool]); };
// 與 tests/test_page_playwright.py base_latest 同形的迷你 latest.json：2330 短線 bs=53.4（不得出現在持股區）、2317 排名池 0、
// 1101 短線正式卦待補（暫定 43）、3008 無 swing；9999 不在檔內
const HOLD_DATA = { date: "2026-09-26", names: { "2330": ["台積電", "半導體業"], "2317": ["鴻海", "其他電子業"], "1101": ["台泥", "水泥工業"], "3008": ["大立光", "光電業"] },
  stocks: {
    "2330": { market: "twse", in_rank_pool: 1, short: { kw: 1, name: "乾為天", kwp: 1, namep: "乾為天", lf: "111111", lp: "111111", st: "yyyyyy", bs: 53.4, ti: 61.8, to: 53.0 }, swing: { kw: 14, name: "火天大有", lf: "111101", lp: "111101", st: "yyyyny" } },
    "2317": { market: "twse", in_rank_pool: 0, short: { kw: 2, name: "坤為地", kwp: 2, namep: "坤為地", lf: "000000", lp: "000000", st: "nnnnnn" }, swing: { kw: 1, name: "乾為天", lf: "111111", lp: "111111", st: "yyyyyy" } },
    "1101": { market: "twse", in_rank_pool: 1, short: { kw: null, name: null, kwp: 43, namep: "澤天夬", lf: null, lp: "111110", st: "yyyyy-" }, swing: { kw: 14, name: "火天大有", lf: "111101", lp: "111101", st: "yyyyny" } },
    "3008": { market: "twse", in_rank_pool: 1, short: { kw: 43, name: "澤天夬", kwp: 43, namep: "澤天夬", lf: "111110", lp: "111110", st: "yyyyyn" } } } };
const HOLD_CODES = ["3008", "2330", "2317", "9999", "1101"];   // 刻意不依卦序、不依代號序（短線 kw 43／1／2／—／null）
const holdRows = (h, codes) => { sb.DATA = HOLD_DATA; state.h = h; const out = holdHtml(codes); state.h = "short";
  return { out, codes: [...out.matchAll(/<tr class="hrow" data-code="([^"]*)"/g)].map(m => m[1]),
    cells: [...out.matchAll(/<tr class="hrow"[^>]*>([\s\S]*?)<\/tr>/g)].map(m => m[1]) }; };
// 清單過濾：以 hexagram_text.json 建索引（同 guideIndex），設 state.gq 後取回 data-kw 清單
const listKws = q => { state.gq = q; return [...guideListRows(HXTEXT.map(hx => ({ kw: hx.king_wen, name: hx.name, tri: hexTri(hx) }))).matchAll(/class="glrow[^"]*" data-kw="(\d+)"/g)].map(m => Number(m[1])); };

// ---- §10 懂卦理的對照資料：spec/hexagrams64.json（事實來源）與 data/hexagram_text.json（前端實際讀的檔）----
const REPO = path.join(import.meta.dirname, "..");   // 對照檔一律讀本 repo（突變測試傳進來的 index.html 在 tmp 目錄）
const SPEC64 = JSON.parse(fs.readFileSync(path.join(REPO, "spec", "hexagrams64.json"), "utf8"));
const HXTEXT = JSON.parse(fs.readFileSync(path.join(REPO, "data", "hexagram_text.json"), "utf8")).hexagrams;
// G3「表由常數渲染、不另抄字串」的證明手法：把 guideFacetRows／guideTriRows 放進一個**只有哨兵常數**的沙箱執行，
// 輸出必須出現全部哨兵字串（且每個都被 esc 包過）；若函式裡抄了字面字串，哨兵就不會出現。
const SENT = `
const esc = s => "«" + String(s) + "»";
const POS = ["P0","P1","P2","P3","P4","P5"];
const FACET = { stock: [0,1,2,3,4,5].map(i => ({ name:"SN"+i, what:"SW"+i, yang:"SY"+i, yin:"SI"+i, window:"SD"+i })),
                market: [0,1,2,3,4,5].map(i => ({ name:"MN"+i, what:"MW"+i, yang:"MY"+i, yin:"MI"+i, window:"MD"+i })) };
const TRI_ORDER = ["T0","T1","T2","T3","T4","T5","T6","T7"];
const TRI_NAME = { "111":"T0", "110":"T1", "101":"T2", "100":"T3", "011":"T4", "010":"T5", "001":"T6", "000":"T7" };
const TRI_ELEM = { T0:"E0", T1:"E1", T2:"E2", T3:"E3", T4:"E4", T5:"E5", T6:"E6", T7:"E7" };
const TRI = { stock: Object.fromEntries(TRI_ORDER.map((t, i) => [t, ["SI" + i + "in", "SO" + i + "out"]])),
              market: Object.fromEntries(TRI_ORDER.map((t, i) => [t, ["MI" + i + "in", "MO" + i + "out"]])) };
`;
const sentSb = { Array, Number, String, Object, isNaN, console };
vm.createContext(sentSb);
new vm.Script(SENT + "\n" + pickFunc("guideFacetRows") + "\n" + pickFunc("guideTriRows")).runInContext(sentSb);
const { guideFacetRows: sentFacet, guideTriRows: sentTri } = new vm.Script("({ guideFacetRows, guideTriRows })").runInContext(sentSb);
const cjk = s => (String(s).match(/[\u4e00-\u9fff]/g) || []).length;

// ---- 案例 ----
const E = o => Object.assign({ kw: null, name: null, kwp: null, namep: null, lf: null, lp: null, st: "",
  sk: "0,0,0,0,0,0", l: [50, 50, 50, 50, 50, 50], unk: [0, 0, 0, 0, 0, 0], cov: "full" }, o);
const W = {   // window 字串（與 FACET 相同，展開只為讓預期句在測試裡肉眼可讀）
  s1: "短線最新單月年增；波段／中期近 3 月合計年增；加速度＝近 3 月減前 3 月",
  s2: "短線 MA5／MA20、波段 MA10／MA20、中期 MA20／MA60，乖離與斜率以 ATR14 為單位",
  s3: "短線 5／10 日、波段 10／20 日、中期 20／60 日；極端高分另掛過熱旗標",
  s5: "短線 3／5 日、波段 5／10 日、中期 10／20 日",
  m1: "短線 MA5／20、波段 MA10／20、中期 MA20／60；區間 20／20／60 日",
};
const e5 = E({ lf: "110111", st: "yynyyy" });
const e6 = E({ lf: "100000", st: "ynnnnn" });
const cases = [
  ["1 純陽（個股）", () => explainHex(E({ lf: "111111", st: "yyyyyy" }), "stock", null),
    "內卦乾：三面向皆有支撐；外卦乾：量價、籌碼、環境皆有支撐。"],
  ["2 純陰（個股）", () => explainHex(E({ lf: "000000", st: "nnnnnn" }), "stock", null),
    "內卦坤：三面向均未達門檻；外卦坤：三面向均未達門檻。"],
  ["3 含未知（內卦第三爻 unk=1，不查表、逐爻列）", () => explainHex(E({ lp: "111100", st: "yy-ynn", unk: [0, 0, 1, 0, 0, 0] }), "stock", null),
    "內卦含未知爻，僅列已知：初 營運基礎 有支撐、二 價格趨勢 有支撐、三 相對動能 未知；外卦震：量價有支撐，籌碼與環境不足。"],
  ["4 臨界（分數 50.0、爻態陽、連續 1 日）", () => explainLine(1, E({ lf: "111111", st: "yyyyyy", l: [70, 50, 70, 70, 70, 70], sk: "0,1,0,0,0,0" }), "stock", null),
    `看什麼：收盤對短、長均線的乖離，長均線斜率，已確認高低點結構（${W.s2}）。目前分數 50.0，臨界區，以正式爻態為準（目前爻態：陽）。候選變化：分數已連續 1 日站在翻轉門檻另一側，再 1 日仍站住即翻爻。`],
  ["5 動爻 陽→陰（三爻，前卦乾為天）", () => explainHex(e5, "stock", "yyyyyy", "乾為天"),
    "內卦兌：營運與趨勢有支撐，相對動能不足；外卦乾：量價、籌碼、環境皆有支撐。\n本次由乾為天的三爻・相對動能確認由陽→陰（轉弱）形成。"],
  ["6 動爻 陰→陽（初爻，前卦坤為地）", () => explainHex(e6, "stock", "nnnnnn", "坤為地"),
    "內卦震：營運有支撐，價格面尚未配合；外卦坤：三面向均未達門檻。\n本次由坤為地的初爻・營運基礎確認由陰→陽（轉強）形成。"],
  ["7 多爻同翻（初、三兩爻陽→陰，逐爻列）", () => explainHex(E({ lf: "010111", st: "nynyyy" }), "stock", "yyyyyy", "乾為天"),
    "內卦坎：趨勢有支撐，其餘兩面向不足；外卦乾：量價、籌碼、環境皆有支撐。\n本次由乾為天的初爻・營運基礎確認由陽→陰（轉弱）形成。\n本次由乾為天的三爻・相對動能確認由陽→陰（轉弱）形成。"],
  ["8 無前一日（prevSt null → 只有卦象句、無動爻句）", () => explainHex(e5, "stock", null),
    "內卦兌：營運與趨勢有支撐，相對動能不足；外卦乾：量價、籌碼、環境皆有支撐。"],
  ["9 reweighted（五爻 60.0 偏多區＋加註）", () => explainLine(4, E({ lf: "111111", st: "yyyyyy", cov: "reweighted", l: [70, 70, 70, 70, 60, 70] }), "stock", null),
    `看什麼：外資、投信淨買超占成交比重，外資買超天數，融資情境，借券賣出餘額變化（反向）（${W.s5}）。目前分數 60.0，偏多區，籌碼供需較有利上漲。爻態穩定，分數未站到翻轉門檻另一側。可選資料部分缺、已重配權重。`],
  ["10 大盤（兌／巽，大盤面向名）", () => explainHex(E({ lf: "110011", st: "yynnyy" }), "market", null),
    "內卦兌：趨勢與廣度有支撐，量價參與不足；外卦巽：期權與海外有支撐，現貨籌碼不足。"],
  ["11 lf null 用 lp（離／離）", () => explainHex(E({ lp: "101101", st: "y-yy-y" }), "stock", null),
    "內卦離：營運與動能有支撐，趨勢未配合；外卦離：量價與環境有支撐，籌碼未配合。"],
  ["12 同位元 110011 個股側（與案例 10 大盤側不同字串）", () => explainHex(E({ lf: "110011", st: "yynnyy" }), "stock", null),
    "內卦兌：營運與趨勢有支撐，相對動能不足；外卦巽：籌碼與環境有支撐，量價確認不足。"],
  ["13 每爻句 unk=1 → 資料缺句", () => explainLine(0, E({ unk: [1, 0, 0, 0, 0, 0], l: [null, 50, 50, 50, 50, 50] }), "stock", null),
    "必要資料缺，本爻不計、不補陰、不累計確認天數。"],
  ["14 每爻句 翻轉當日（三爻 40.0、sk 0、前一日為陽）→ 動爻句、無候選句", () => explainLine(2, E({ lf: "110111", st: "yynyyy", l: [70, 70, 40, 70, 70, 70] }), "stock", "yyyyyy"),
    `看什麼：對所屬指數的超額報酬（長短視窗）、對同業中位數的超額、超額加速度（${W.s3}）。目前分數 40.0，偏空區，相對動能支撐不足或偏弱。今日完成翻轉（動爻）。`],
  ["15 大盤每爻句 yin（初爻 30.0）", () => explainLine(0, E({ lf: "011111", st: "nyyyyy", l: [30, 70, 70, 70, 70, 70] }), "market", null),
    `看什麼：指數對短、長均線的乖離，均線斜率，指數在區間中的位置（${W.m1}）。目前分數 30.0，偏空區，趨勢支撐不足或偏弱。爻態穩定，分數未站到翻轉門檻另一側。`],
  ["16 前一日只差在「-」→ 不是動爻、無動爻句", () => explainHex(E({ lf: "111111", st: "yyyyyy" }), "stock", "yyy-yy", "乾為天"),
    "內卦乾：三面向皆有支撐；外卦乾：量價、籌碼、環境皆有支撐。"],
  ["17 lf／lp 皆 null → 退回 st；外卦含「-」列為未知", () => explainHex(E({ st: "yyy-nn" }), "stock", null),
    "內卦乾：三面向皆有支撐；外卦含未知爻，僅列已知：四 量價確認 未知、五 籌碼供需 未達門檻、上 外部環境 未達門檻。"],
  ["18 動爻但前一日正式卦名缺（prevName 省略）", () => explainHex(e6, "stock", "nnnnnn"),
    "內卦震：營運有支撐，價格面尚未配合；外卦坤：三面向均未達門檻。\n本次由前一日（正式卦待補）的初爻・營運基礎確認由陰→陽（轉強）形成。"],
  ["19 每爻句 翻轉判定與 sk 無關（sk=3 仍是動爻，不出候選句）", () => explainLine(2, E({ lf: "110111", st: "yynyyy", l: [70, 70, 40, 70, 70, 70], sk: "0,0,3,0,0,0" }), "stock", "yyyyyy"),
    `看什麼：對所屬指數的超額報酬（長短視窗）、對同業中位數的超額、超額加速度（${W.s3}）。目前分數 40.0，偏空區，相對動能支撐不足或偏弱。今日完成翻轉（動爻）。`],
  ["20 每爻句 個股初爻 yang（60.0）", () => explainLine(0, E({ lf: "111111", st: "yyyyyy", l: [60, 70, 70, 70, 70, 70] }), "stock", null),
    `看什麼：月營收年增、營收加速度；中期另看 EPS 年增、毛利率季變化、領先同業幅度（${W.s1}）。目前分數 60.0，偏多區，營運基礎較有利上漲。爻態穩定，分數未站到翻轉門檻另一側。`],
  // ---- 尾句三態（裁決 2026-09-19：sk＝已連續站到翻轉門檻另一側的天數，翻爻當日歸 0）----
  ["21 sk=1 未翻轉 → 候選變化句（含「再 1 日」）", () => explainLine(3, E({ lf: "111111", st: "yyyyyy", l: [70, 70, 70, 44.0, 70, 70], sk: "0,0,0,1,0,0" }), "stock", "yyyyyy"),
    "看什麼：成交量／均量與當日漲跌、回檔的情境表（中期改看漲跌日成交量比率與 OBV 斜率），收盤位置，突破後是否站穩（短線 5 日、波段 10 日、中期 20 日；突破基準 20／60／120 日、確認 3／5／10 日）。目前分數 44.0，偏空區，量價確認支撐不足或偏弱。候選變化：分數已連續 1 日站在翻轉門檻另一側，再 1 日仍站住即翻爻。"],
  ["22 sk=0 未翻轉 → 穩定句", () => explainLine(3, E({ lf: "111111", st: "yyyyyy", l: [70, 70, 70, 66.0, 70, 70] }), "stock", "yyyyyy"),
    "看什麼：成交量／均量與當日漲跌、回檔的情境表（中期改看漲跌日成交量比率與 OBV 斜率），收盤位置，突破後是否站穩（短線 5 日、波段 10 日、中期 20 日；突破基準 20／60／120 日、確認 3／5／10 日）。目前分數 66.0，偏多區，量價確認較有利上漲。爻態穩定，分數未站到翻轉門檻另一側。"],
  ["23 翻轉當日 sk=0 → 動爻句、無候選句", () => explainLine(3, E({ lf: "111011", st: "yyynyy", l: [70, 70, 70, 40.0, 70, 70] }), "stock", "yyyyyy"),
    "看什麼：成交量／均量與當日漲跌、回檔的情境表（中期改看漲跌日成交量比率與 OBV 斜率），收盤位置，突破後是否站穩（短線 5 日、波段 10 日、中期 20 日；突破基準 20／60／120 日、確認 3／5／10 日）。目前分數 40.0，偏空區，量價確認支撐不足或偏弱。今日完成翻轉（動爻）。"],
  ["24 sk=2 未翻轉（理論上不出現）→ 不炸、寫「再 1 日」", () => explainLine(3, E({ lf: "111111", st: "yyyyyy", l: [70, 70, 70, 44.0, 70, 70], sk: "0,0,0,2,0,0" }), "stock", null),
    "看什麼：成交量／均量與當日漲跌、回檔的情境表（中期改看漲跌日成交量比率與 OBV 斜率），收盤位置，突破後是否站穩（短線 5 日、波段 10 日、中期 20 日；突破基準 20／60／120 日、確認 3／5／10 日）。目前分數 44.0，偏空區，量價確認支撐不足或偏弱。候選變化：分數已連續 2 日站在翻轉門檻另一側，再 1 日仍站住即翻爻。"],
  // ---- §9 模型換版標示（docs/P4-PREVIEW.md §9 W3／P2／P3／P4／P1）：JSON.stringify 比較，逐字＝預期 ----
  ["25 W3 降級：timeline 無 ps（舊檔）／長度不合／非陣列 → tlPs null；元素非字串或空字串 → null", () => JSON.stringify([
      tlPs({ dates: ["a", "b"], series: {} }), tlPs({ dates: ["a", "b"], ps: ["x"] }), tlPs({ dates: ["a"], ps: "x" }), tlPs(null),
      tlPs({ dates: ["a", "b", "c", "d"], ps: ["x", null, 7, ""] })]),
    JSON.stringify([null, null, null, null, ["x", null, null, null]])],
  ["26 P2 無換版（同值、全 null、ps 缺席）→ 無說明句", () => JSON.stringify([
      modelShiftText(["d1", "d2", "d3"], ["A", "A", null]), modelShiftText(["d1", "d2"], [null, null]), modelShiftText(["d1"], null)]),
    JSON.stringify(["", "", ""])],
  ["27 P2 中途換版一次 → 逐字說明句", () => modelShiftText(["2026-09-22", "2026-09-23", "2026-09-24"], ["c7385e78cb9f", "8ca174ee8bc7", "8ca174ee8bc7"]),
    "近 3 日含模型換版：2026-09-23 起改用新參數版本（params_sha c7385e78cb9f → 8ca174ee8bc7）。換版前後的卦象不可直接比較，換卦可能來自模型調整而非市場變化。"],
  ["28 P2 兩次換版、中間夾 null（跳過 null 比較、逐次列出）", () => modelShiftText(["d1", "d2", "d3", "d4", "d5"], ["A", null, "B", "B", "C"]),
    "近 5 日含模型換版：d3 起改用新參數版本（params_sha A → B）；d5 起改用新參數版本（params_sha B → C）。換版前後的卦象不可直接比較，換卦可能來自模型調整而非市場變化。"],
  ["29 P3 psDiffers：皆非 null 且不同才 true；任一 null／空字串 → false", () => JSON.stringify([
      psDiffers("A", "B"), psDiffers("A", "A"), psDiffers(null, "B"), psDiffers("A", null), psDiffers("", "B"), psDiffers(undefined, undefined)]),
    JSON.stringify([true, false, false, false, false, false])],
  ["30 P4 mvOffFor：前一日 null → false；今日兩來源皆同 → false；任一來源不同 → true；今日兩來源皆 null → false", () => JSON.stringify([
      mvOffFor(["B", "B"], null), mvOffFor(["A", "A"], "A"), mvOffFor(["B", "B"], "A"), mvOffFor([null, "B"], "A"),
      mvOffFor(["A", "B"], "A"), mvOffFor([null, null], "A"), mvOffFor(null, "A")]),
    JSON.stringify([false, false, true, true, true, false, false])],
  ["31 P4 動爻抑制：前一日 st 有翻爻但模型版本不同 → 不出動爻句、改一句不比較", () => explainHex(e5, "stock", "yyyyyy", "乾為天", true),
    "內卦兌：營運與趨勢有支撐，相對動能不足；外卦乾：量價、籌碼、環境皆有支撐。\n今日與前一日模型版本不同，不比較動爻。"],
  ["32 動爻 陽→陰：mvOff=false 時維持現行（P4 對照組，與案例 5 同）", () => explainHex(e5, "stock", "yyyyyy", "乾為天", false),
    "內卦兌：營運與趨勢有支撐，相對動能不足；外卦乾：量價、籌碼、環境皆有支撐。\n本次由乾為天的三爻・相對動能確認由陽→陰（轉弱）形成。"],
  ["34 P3 modelShiftAt：相鄰不同→true；任一 null／空字串→false；相同→false；ps 缺席／i=0／越界→false", () => JSON.stringify([
      modelShiftAt(["A", "B"], 1), modelShiftAt(["A", null, "B"], 1), modelShiftAt(["A", null, "B"], 2), modelShiftAt(["A", ""], 1),
      modelShiftAt(["A", "A"], 1), modelShiftAt(null, 1), modelShiftAt(["A", "B"], 0), modelShiftAt(["A", "B"], 2)]),
    JSON.stringify([true, false, false, false, false, false, false, false])],
  ["33 P1 modelVersionInfo：缺欄 → null；各市場 1 個；某市場 2 個 → mixed；空陣列保留、非字串濾掉", () => JSON.stringify([
      modelVersionInfo(undefined), modelVersionInfo([]),
      modelVersionInfo({ twse: ["p2-score-engine-2.a"], tpex: ["p2-score-engine-2.b"] }),
      modelVersionInfo({ twse: ["p2-score-engine-1.a", "p2-score-engine-2.a"], tpex: [7, ""] })]),
    JSON.stringify([null, null,
      [{ mk: "twse", label: "上市", vals: ["p2-score-engine-2.a"], mixed: false }, { mk: "tpex", label: "上櫃", vals: ["p2-score-engine-2.b"], mixed: false }],
      [{ mk: "twse", label: "上市", vals: ["p2-score-engine-1.a", "p2-score-engine-2.a"], mixed: true }, { mk: "tpex", label: "上櫃", vals: [], mixed: false }]])],
  // ---- §10 懂卦理（docs/P4-PREVIEW.md §10）----
  ["35 G1 hexBits／hexTri：由 hexagram_text.json 爻題反推的位元與上下卦，64 卦全部＝spec/hexagrams64.json（不符者列出）", () => JSON.stringify(
      HXTEXT.map(hx => { const t = hexTri(hx), sp = SPEC64.find(x => x.king_wen === hx.king_wen);
        return (t && sp && t.bits === sp.lines_bottom_up.join("") && t.lower === sp.lower && t.upper === sp.upper) ? null : hx.king_wen; }).filter(x => x !== null)
      .concat(HXTEXT.length === 64 ? [] : ["n=" + HXTEXT.length])),
    "[]"],
  ["36 G1 hexBits 形狀不合 → null（缺 lines／只有 5 爻／爻題不含九六／null）", () => JSON.stringify([
      hexBits(null), hexBits({ lines: HXTEXT[0].lines.slice(0, 5) }), hexBits({ lines: HXTEXT[0].lines.map(l => ({ title: "初", text: l.text })) }), hexBits({}),
      hexBits({ lines: [{ title: "初九" }, { title: "六二" }, { title: "九三" }, { title: "六四" }, { title: "九五" }, { title: "上六" }] })]),
    JSON.stringify([null, null, null, null, "101010"])],
  ["37 G5 parseKw 白名單：1..64 整數字串（數字亦可）→ 值；0／65／99／小數／前導 0／空白／字母／空／null → null", () => JSON.stringify([
      parseKw("32"), parseKw("1"), parseKw("64"), parseKw(32), parseKw("0"), parseKw("65"), parseKw("99"), parseKw("3.5"), parseKw("032"),
      parseKw(" 3"), parseKw("abc"), parseKw(""), parseKw(null), parseKw(undefined), parseKw("1e1")]),
    JSON.stringify([32, 1, 64, 32, null, null, null, null, null, null, null, null, null, null, null])],
  ["38 G4 hexDist：合成 4 檔（含 kw null、缺某期間、in_rank_pool 0）→ 逐 horizon 計數與排名池計數逐字＝手算", () => JSON.stringify(hexDist({
      "2330": { in_rank_pool: 1, short: { kw: 1 }, swing: { kw: 14 }, mid: { kw: 34 } },
      "2317": { in_rank_pool: 0, short: { kw: 2 }, swing: { kw: 1 }, mid: { kw: null } },
      "1101": { in_rank_pool: 1, short: { kw: null }, swing: { kw: 14 }, mid: { kw: 34 } },
      "3008": { in_rank_pool: 1, short: { kw: 43 }, mid: { kw: 34 } } })),
    JSON.stringify({
      short: { rows: 4, undef: 1, undefPool: 1, count: { "1": 1, "2": 1, "43": 1 }, pool: { "1": 1, "43": 1 } },
      swing: { rows: 3, undef: 0, undefPool: 0, count: { "1": 1, "14": 2 }, pool: { "14": 2 } },
      mid:   { rows: 4, undef: 1, undefPool: 0, count: { "34": 3 }, pool: { "34": 3 } } })],
  ["39 G4 hexDist 不變式 Σcount＋undef＝rows；非物件期間／stocks 缺／kw 越界（99）計未定", () => {
      const d = hexDist({ a: { in_rank_pool: 1, short: { kw: 99 }, swing: "x", mid: null }, b: { short: { kw: 5 } }, c: 7, d: { in_rank_pool: "1", short: { kw: 5 } } });
      const sum = h => Object.values(d[h].count).reduce((x, y) => x + y, 0) + d[h].undef;
      return JSON.stringify([sum("short") === d.short.rows, sum("swing") === d.swing.rows, sum("mid") === d.mid.rows, d.short, hexDist(null).mid]); },
    JSON.stringify([true, true, true, { rows: 3, undef: 1, undefPool: 1, count: { "5": 2 }, pool: { "5": 1 } }, { rows: 0, undef: 0, undefPool: 0, count: {}, pool: {} }])],
  ["40 G5 TRI_ORDER＝乾兌離震巽坎艮坤＝TRI_NAME 位元 111→000 遞減（不是 Object.keys 的 震離兌乾…）", () => {
      const bitsOf = Object.fromEntries(Object.entries(TRI_NAME).map(([b, n]) => [n, b]));
      return JSON.stringify([TRI_ORDER.join(""), TRI_ORDER.map(n => bitsOf[n]), new Set(TRI_ORDER).size]); },
    JSON.stringify(["乾兌離震巽坎艮坤", ["111", "110", "101", "100", "011", "010", "001", "000"], 8])],
  ["41 G2 TRI_ELEM 八個自然象＝spec/hexagrams64.json 純卦卦名「X為Y」的 Y（lower＝upper＝X）", () => JSON.stringify(
      TRI_ORDER.map(t => { const sp = SPEC64.find(x => x.lower === t && x.upper === t); return sp ? sp.name === t + "為" + TRI_ELEM[t] : false; })),
    JSON.stringify([true, true, true, true, true, true, true, true])],
  ["42 G3 guideFacetRows 由 POS／FACET 常數渲染（哨兵沙箱：兩側 6×5 個哨兵全部出現且各被 esc 包過；上爻列在前）", () => {
      const out = { stock: sentFacet("stock"), market: sentFacet("market") };
      const miss = [];
      for (const [k, pre] of [["stock", "S"], ["market", "M"]]) for (let i = 0; i < 6; i++) for (const f of ["N", "W", "Y", "I", "D"])
        if (!out[k].includes("«" + pre + f + i + "»")) miss.push(pre + f + i);
      for (let i = 0; i < 6; i++) if (!out.stock.includes("«P" + i + "»")) miss.push("P" + i);
      return JSON.stringify([miss, out.stock.indexOf("«SN5»") < out.stock.indexOf("«SN0»"), (out.stock.match(/<tr>/g) || []).length]); },
    JSON.stringify([[], true, 6])],
  ["43 G3 guideTriRows 由 TRI_ORDER／TRI_NAME／TRI_ELEM／TRI 常數渲染（哨兵沙箱：8 列、順序＝TRI_ORDER、位元／象／四句全為哨兵）", () => {
      const out = sentTri(); const miss = [];
      const bits = ["111", "110", "101", "100", "011", "010", "001", "000"];
      for (let i = 0; i < 8; i++) for (const w of ["T" + i, bits[i], "E" + i, "SI" + i + "in", "SO" + i + "out", "MI" + i + "in", "MO" + i + "out"])
        if (!out.includes("«" + w + "»")) miss.push(w);
      return JSON.stringify([miss, (out.match(/<tr>/g) || []).length, out.indexOf("«T0»") < out.indexOf("«T7»")]); },
    JSON.stringify([[], 8, true])],
  ["44 G2 卦理入門：五段、≤600 漢字、段題固定；G4 頂部說明句＝規格 G4 原句", () => JSON.stringify([
      GUIDE_INTRO.length, cjk(GUIDE_INTRO.map(x => x[1]).join("")) <= 600, GUIDE_INTRO.map(x => x[0]).join("/"), GUIDE_DIST_NOTE]),
    JSON.stringify([5, true, "陰陽爻/八卦/上下卦組成六十四卦/爻位的名稱/本站的動爻與換卦", "僅為當日卦象計數，不是選股清單、不代表方向。"])],
  ["45 G5 kwLabel：kw 在 1..64 才掛 .kwlink（data-kw＝parseKw 值）；99／\"abc\"／0 不掛、仍顯示卦名；null → 正式卦待補", () => JSON.stringify([
      kwLabel(1, "乾為天"), /kwlink|data-kw/.test(kwLabel(99, "第九十九")), kwLabel(99, "第九十九").includes("第九十九"),
      /kwlink|data-kw/.test(kwLabel("abc", "x")), /kwlink|data-kw/.test(kwLabel(0, "x")), kwLabel(null, "x"), kwLabel("64", "火水未濟").includes('data-kw="64"')]),
    JSON.stringify(['<span class="n kwlink" data-kw="1" role="link" tabindex="0" title="查看卦理（懂卦理分頁）">乾為天<small>第 1 卦</small></span>',
      false, true, false, false, '<span class="n">正式卦待補</span>', true])],
  ["46 G5 guideListRows 過濾：空字串→64；\" 天 \"（trim）→15 筆＝含「天」的卦；\"天\"→同；\"既濟\"→[63]；\"天雷\"→[25]；卦序 \"3\"→[3]；\"無此卦\"→[]", () => JSON.stringify([
      listKws("").length, listKws(" 天 "), listKws("天"), listKws("既濟"), listKws("天雷"), listKws("3"), listKws("無此卦"), listKws(" 3 ")]),
    JSON.stringify([64, [1, 5, 6, 9, 10, 11, 12, 13, 14, 25, 26, 33, 34, 43, 44], [1, 5, 6, 9, 10, 11, 12, 13, 14, 25, 26, 33, 34, 43, 44], [63], [25], [3], [], [3]])],
  ["47 G5 guideListRows 是「含」不是「開頭」：\"天\" 15 筆中 8 筆不以「天」開頭（1 乾為天、5 水天需…）", () => JSON.stringify(
      listKws("天").filter(k => !HXTEXT.find(h => h.king_wen === k).name.startsWith("天"))),
    JSON.stringify([1, 5, 9, 11, 14, 26, 34, 43])],
  // ---- §11 我的持股（docs/P4-PREVIEW.md §11）：唯讀 pm_holdings 的純函式與區塊渲染 ----
  ["48 H3 holdingsCodes：只讀 c、trim().toUpperCase() 後過 CODE_RE、去重、順序＝原順序；壞筆（c 非字串／7 碼／3 碼／注入字串／null／數字／缺 c）靜默略過；sh／cost 有無皆不影響", () => JSON.stringify([
      holdingsCodes(JSON.stringify([{ c: "3008", sh: 8848, cost: 123.45 }, { c: "2330", sh: null, cost: null }, { c: "2317" }, { c: " 00631l " }, { c: 1234 }, { c: "1234567" }, { c: "123" },
        { c: "<img src=x onerror=1>" }, null, 7, "2330", { sh: 1 }, { c: "2330" }, { c: "2317 " }, { c: "" }])),
      holdingsCodes(JSON.stringify([{ c: "2317" }, { c: "2330" }])), holdingsCodes(JSON.stringify([{ c: "2330" }, { c: "2317" }]))]),
    JSON.stringify([["3008", "2330", "2317", "00631L"], ["2317", "2330"], ["2330", "2317"]])],
  ["49 H3 holdingsCodes 壞輸入 → []（壞 JSON／物件／null 字串／null／undefined／空字串／字串／數字／空陣列）", () => JSON.stringify([
      holdingsCodes("{bad"), holdingsCodes("{}"), holdingsCodes('{"c":"2330"}'), holdingsCodes("null"), holdingsCodes(null), holdingsCodes(undefined),
      holdingsCodes(""), holdingsCodes('"2330"'), holdingsCodes("7"), holdingsCodes("[]")]),
    JSON.stringify([[], [], [], [], [], [], [], [], [], []])],
  ["50 H5／H6 holdHtml：列順序＝清單原順序（不排序）；股名／正式卦名（kwLabel 可點）／六爻圖／未定＋暫定卦／不在分數檔／該期間無列／未達流動性門檻；不含 bs／ti／to 值；空清單一句提示、無表格；區塊內無按鈕／輸入框", () => {
      const S = holdRows("short", HOLD_CODES), W = holdRows("swing", HOLD_CODES), E = holdRows("short", []);
      const cell = (R, code) => R.cells[R.codes.indexOf(code)];
      const bits = html => [...html.matchAll(/<div class="yao (yang|yin|und)">/g)].map(m => ({ yang: "1", yin: "0", und: "-" })[m[1]]).join("");
      return JSON.stringify([
        S.codes, W.codes,
        cell(S, "3008").includes("大立光") && cell(S, "3008").includes('data-kw="43"') && cell(S, "3008").includes("澤天夬") && bits(cell(S, "3008")) === "111110",
        cell(S, "2330").includes('data-kw="1"') && bits(cell(S, "2330")) === "111111" && cell(S, "2330").includes("台積電"),
        cell(S, "2317").includes("未達流動性門檻（60 日成交值 &lt;3,000 萬）") && cell(S, "2317").includes("坤為地") && !cell(S, "2330").includes("未達流動性門檻"),
        cell(S, "9999").includes("不在最新分數檔（2026-09-26）內") && !/hexfig|kwlink/.test(cell(S, "9999")) && /<td><\/td>/.test(cell(S, "9999")),
        cell(S, "1101").includes("正式卦待補") && cell(S, "1101").includes("暫定卦：") && cell(S, "1101").includes('data-kw="43"') && bits(cell(S, "1101")) === "11111-",
        cell(W, "3008").includes(HOLD_NO_H_TXT) && !/hexfig|kwlink/.test(cell(W, "3008")) && cell(W, "2330").includes("火天大有") && cell(W, "1101").includes('data-kw="14"'),
        !/53\.4|61\.8|53\.0|\bbs\b/.test(S.out + W.out), S.out.includes(HOLD_NOTE_TXT) && S.out.includes(HOLD_SRC_TXT) && S.out.includes("期間 <b>短線</b>") && W.out.includes("期間 <b>波段</b>"),
        E.out.includes(HOLD_EMPTY_TXT) && !E.out.includes("<table") && !E.out.includes(HOLD_NOTE_TXT),
        !/<button|<input|新增|刪除/.test(S.out + E.out), (S.out.match(/<tr class="hrow"/g) || []).length]); },
    JSON.stringify([HOLD_CODES, HOLD_CODES, true, true, true, true, true, true, true, true, true, true, 5])],
  ["51 H3 holdingsCodes 對 postmkt 實際寫法（裸陣列 [{c,sh,cost}]）與含小寫／空白的手動輸入：只取 c；重複代號（大小寫不同）只留第一筆", () => JSON.stringify(
      holdingsCodes(JSON.stringify([{ c: "2330", sh: 1000, cost: 580.5 }, { c: "00878", sh: 20000, cost: 21.3 }, { c: "2330 ", sh: 5, cost: 1 }, { c: "00631l", sh: null, cost: null }, { c: "00631L" }]))),
    JSON.stringify(["2330", "00878", "00631L"])],
  ["52 §12.9 rankRows 全部：bs 高→低、同分並列名次（competition：1,2,2,2,5）、同分依代號升冪；池外（bs 99）不列入；字串 bs／null／缺期間算缺值",
    () => rk("all", ""), JSON.stringify([[["3003", 70.25, 1], ["1001", 60, 2], ["2002", 60, 2], ["8008", 60, 2], ["4004", 50, 5]], 8, 3, 1])],
  ["53 §12.9 rankRows 市場篩選上櫃：名次在篩選後範圍內重算、計數也只算篩選範圍", () => rk("tpex", ""),
    JSON.stringify([[["3003", 70.25, 1], ["4004", 50, 2]], 3, 1, 0])],
  ["54 §12.9 rankRows 上市＋產業甲業：同分三檔變兩檔仍並列；產業以 names[code][1] 全等比對", () => rk("twse", "甲業"),
    JSON.stringify([[["2002", 60, 1], ["8008", 60, 1]], 4, 2, 1])],
  ["55 §12.9 rankIndustries：去重、升冪、只取 stocks 內 in_rank_pool===1 的代號（只有池外股的「丁業」與不在 stocks 的「丙業」皆不列）", () => JSON.stringify(rankIndustries(
      Object.assign({ "9991": { market: "twse", in_rank_pool: 0, short: { bs: 10 } } }, RK_ST), Object.assign({ "0000": ["不在", "丙業"], "9991": ["池外", "丁業"] }, RK_NM))),
    JSON.stringify(["乙業", "甲業"])],
  // ---- §13 PR-75d 本爻依據（indRows；fixture＝10-08 的 23.json／market.json 逐值副本）----
  ["56 §13 2330 短線二爻表＝核准樣稿語意四列（收盤距 5 日均線 −0.35%／距 20 日均線 +3.04%／20 日均線 5 日變化 +1.47%／高低點結構未成明確型態）；六爻皆不出 ATR 倍數、不附免責句",
    () => JSON.stringify(indR(IND_FX["23"], 1).map(r => [r[0], r[1]])) === JSON.stringify(IND_APPROVED_ROWS)
      && !/ATR|描述性資料|非買賣訊號/.test(JSON.stringify(ind6(IND_FX["23"], "stock", "2330", "short"))), true],
  ["57 §13 2330 短線六爻表全文（數字＝§13.3 表；外資淨賣超不出雙重負號、量價情境標當日、借券賣出餘額正名）",
    () => JSON.stringify(ind6(IND_FX["23"], "stock", "2330", "short")), JSON.stringify(IND_2330_S)],
  ["58 §13 大盤上市短線六爻表（market.json；S&P 500 距均線整列不列（使用者裁示）；VIX 缺值代碼 H → 歷史不足、項目帶窗長；美元兌台幣以觀測日計）", () => JSON.stringify(ind6(IND_FX.market, "market", "twse", "short")), JSON.stringify([
    [["加權指數收盤", "49,313.44", "指數無還原"], ["收盤距 5 日均線", "−0.23%", ""], ["收盤距 20 日均線", "+3.41%", ""], ["20 日均線 5 日變化", "+1.50%", ""],
     ["20 日區間位置", "0.882", "收盤在區間中的位置：0＝區間最低、1＝區間最高"]],
    [["廣度母體", "1,079 家", ""], ["站上 5 日均線家數比", "49.1%", ""], ["站上 20 日均線家數比", "57.5%", ""], ["當日上漲家數比", "39.6%", ""],
     ["10 日新高減新低家數比", "+2.78%", "占廣度母體"], ["10 日騰落線偏離", "−0.43 個標準差", "騰落線對其 10 日均線"]],
    [["當日成交金額比", "1.048 倍", "除以前 20 日均額"], ["當日上漲股成交金額占比", "37.5%", ""],
     ["10 日新高低與量能", "其他（非新高量縮、新低量增、新低量縮）", "新高／新低以近 10 日收盤計，量能對 10 日均額"]],
    [["外資 5 日淨賣超", "占市場成交金額 0.41%", ""], ["投信 5 日淨賣超", "占市場成交金額 0.28%", ""], ["外資 10 日買超天數", "5 日", ""], ["全市場融資餘額 5 日變化", "+2.63%", ""]],
    [["外資台指期淨未平倉（百分位窗 250 日）", "−83,194 口", ""], ["淨未平倉 5 日變化", "−3,640 口", ""], ["期貨基差", "+0.07%", "近 60 日中位數 +0.147%"], ["VIX（百分位窗 250 日）", "歷史不足", ""]],
    [["美股對齊日", "2026-10-07", "台北當日 08:00 前已收盤的最近美股交易日"], ["S&P 500 5 個美股交易日報酬", "+1.96%", ""],
     ["費城半導體 5 個美股交易日報酬", "+3.46%", ""], ["美元兌台幣 5 個觀測日變化", "−0.14%", "匯率對齊日 2026-10-07"]]])],
  ["59 §13 D3 date 或 params_sha 與 latest.json 不同 → 六爻皆 IND_OFF_TXT（不出任何數字）", () => JSON.stringify([
      ind6(IND_FX["23"], "stock", "2330", "short", { date: "2026-10-07", params_sha: IND_REF.params_sha }),
      ind6(IND_FX.market, "market", "tpex", "mid", { date: IND_REF.date, params_sha: "000000000000" })].flat().every(t => t === IND_OFF_TXT)), "true"],
  ["60 §13 D3 形狀不合／無此代號／該期間 null／cols 期間不符／市場不在 win → null（靜默）", () => JSON.stringify([
      indR(Object.assign(indClone("23"), { schema: 2 }), 1),
      indR(Object.assign(indClone("23"), { cols: null }), 1),
      indR(Object.assign(indClone("23"), { stocks: "x" }), 1),
      indRows(IND_FX["23"], "stock", "9999", "short", 1, IND_REF),
      indR((() => { const j = indClone("23"); j.stocks["2330"].swing = null; return j; })(), 1, "swing"),
      indR((() => { const j = indClone("23"); j.cols[String(j.stocks["2330"].short[0])].h = "mid"; return j; })(), 1),
      indR((() => { const j = indClone("23"); j.stocks["2330"].m = "otc"; return j; })(), 1),
      indRows(IND_FX["23"], "market", "twse", "short", 1, IND_REF),
      indR(null, 1)]), JSON.stringify([null, null, null, null, null, null, null, null, null])],
  ["61 §13 D4 缺值代碼轉白話、項目照帶窗長（E 無信用交易資格／Z 分母為零／H 歷史不足／M 缺資料／表外代碼→原因未登錄）；依數值的備註不出、靜態口徑照留", () => {
      const j = indPatch(indClone("23"), "short", { "5D.margin_scenario": "E", "1A.revenue_accel": "Z", "2A.dist_ma_short": "H", "5E.short_sale_change": "M", "6A.market_direction": "Q", "3A.excess_long": "H" });
      return JSON.stringify([indR(j, 0)[2], indR(j, 1)[0], indR(j, 2)[0], indR(j, 4).slice(5), indR(j, 5)[0]]); }, JSON.stringify([
    ["營收加速度", "分母為零", "近 3 月合計年增率減前 3 月合計年增率"], ["收盤距 5 日均線", "歷史不足", ""], ["10 日超額報酬", "歷史不足", ""],
    [["融資餘額 5 日變化", "無信用交易資格", ""], ["借券賣出餘額 5 日變化", "缺資料", ""]], ["所屬大盤方向分數", "原因未登錄", "上市・短線"]])],
  ["62 §13 D4 x 為 null 的情境類改用輔助值：結構／量價情境／延續各狀態（事件距今 0 日＝發生在當日）／融資情境序；序號表外或缺 → 不列或資料不足", () => JSON.stringify([
      ["HH+HL", "LH+LL", "insufficient_swings", "weird"].map(v => JSON.stringify(indR(indPatch(indClone("23"), "short", {}, { "2.struct": v }), 1)[3] || null)),
      [1, 2, 3, 4, null].map(v => indR(indPatch(indClone("23"), "short", {}, { "4.seq": v }), 3)[0][1]),
      ["no_event", "breakout_held", "breakout_failed", "breakdown_unrecovered", "breakdown_recovered"].map(v => indR(indPatch(indClone("23"), "short", {}, { "4.cont": v }), 3)[2].slice(1)),
      indR(indPatch(indClone("23"), "short", {}, { "4.cont_off": 0 }), 3)[2][2],
      [1, 3, 4].map(v => indR(indPatch(indClone("23"), "short", {}, { "5.mseq": v }), 4)[5][2].split("；").pop())]),
    JSON.stringify([
      [JSON.stringify(["高低點結構", "高點、低點皆墊高", "看近 20 日、擺動點前後 2 根確認"]), JSON.stringify(["高低點結構", "高點、低點皆走低", "看近 20 日、擺動點前後 2 根確認"]),
       JSON.stringify(["高低點結構", "擺動點不足，未判定", "看近 20 日、擺動點前後 2 根確認"]), "null"],
      ["縮量回檔", "上漲放量", "下跌放量", "一般縮量", "資料不足"],
      [["無突破或跌破事件", ""], ["突破後守住", "基準為前 20 日收盤高低點，看第 3 日收盤"], ["突破後未守住", "基準為前 20 日收盤高低點，看第 3 日收盤"],
       ["跌破後未收復", "基準為前 20 日收盤高低點，看第 3 日收盤"], ["跌破後收復", "基準為前 20 日收盤高低點，看第 3 日收盤"]],
      "最近一次突破或跌破事件發生在當日",
      ["融資變化近零，記中性", "融資增加、同期股價未上漲", "融資減少"]])],
  ["64 §13 外資／投信淨額子指標缺值（歷史不足／缺資料）→ 項目寫中性「淨買賣超」，不預設「淨買超」；大盤同規則", () => {
      const j = indPatch(indClone("23"), "short", { "5A.foreign_strength_long": "H", "5B.trust_strength_short": "M" }, { "5.f_l": 500, "5.t_s": -20 });
      const m = indClone("market"), r = m.rows["twse|short"], c = m.cols[String(r[0])];
      r[1][c.x.indexOf("4A.foreign_net_ratio")] = "H"; r[1][c.x.indexOf("4B.trust_net_ratio")] = "Z";
      return JSON.stringify([indR(j, 4).slice(0, 4).map(x => x.slice(0, 2)), indRows(m, "market", "twse", "short", 3, IND_REF).slice(0, 2)]); }, JSON.stringify([
    [["外資 5 日淨買賣超", "歷史不足"], ["外資 3 日淨賣超", "15,139 張（占同期成交 24.86%）"], ["投信 5 日淨買超", "1,857 張（占同期成交 1.79%）"], ["投信 3 日淨買賣超", "缺資料"]],
    [["外資 5 日淨買賣超", "歷史不足", ""], ["投信 5 日淨買賣超", "分母為零", ""]]])],
  ["63 §13 D4 欄位一律經 cols／win：欄序整個反轉＋cid 改號 77 → 六爻表不變；win 窗長 5→7 → 項目名跟著變", () => {
      const t = ind6(indShuffle(), "stock", "2330", "short"), e = JSON.parse(JSON.stringify(IND_2330_S));
      e[1][0][0] = "收盤距 7 日均線";
      return JSON.stringify(t) === JSON.stringify(e); }, true],
];

let fail = 0;
for (const [name, fn, exp] of cases) {
  let got;
  try { got = fn(); } catch (e) { got = `THROW ${e.message}`; }
  const ok = got === exp;
  if (!ok) fail++;
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${ok ? "" : `\n  got: ${JSON.stringify(got)}\n  exp: ${JSON.stringify(exp)}`}`);
}

// ---- 結構斷言：TRI 個股側逐字＝v1.2.2:103-114；FACET／TERMS 形狀；用字檢核（S2-5）----
const SPEC_TRI_STOCK = {   // spec/stock-iching-plan-v1.2.2.md:105-112 兩欄逐字
  "乾": ["三面向皆有支撐", "量價、籌碼、環境皆有支撐"],
  "兌": ["營運與趨勢有支撐，相對動能不足", "量價與籌碼有支撐，環境不足"],
  "離": ["營運與動能有支撐，趨勢未配合", "量價與環境有支撐，籌碼未配合"],
  "震": ["營運有支撐，價格面尚未配合", "量價有支撐，籌碼與環境不足"],
  "巽": ["價格面有支撐，營運尚未配合", "籌碼與環境有支撐，量價確認不足"],
  "坎": ["趨勢有支撐，其餘兩面向不足", "籌碼有支撐，量價與環境不足"],
  "艮": ["相對動能有支撐，營運與趨勢不足", "環境有支撐，個股交易條件不足"],
  "坤": ["三面向均未達門檻", "三面向均未達門檻"],
};
// 免責卡內文逐字（§9 P5 只准把校準那一句包進 #calTxt、依資料換字）。2026-10-10 裁定 #74 PR-74c（docs/P4-PREVIEW.md §12.9）：
// 改寫兩處過時語（原「未經回測驗證」「不建吉凶排名」）；使用者指示「非資訊性資料都可以隱藏」→ 常駐只留單行最低限度，其餘（含 #calTxt）收進「說明」details。比對對象含 details 內文
const DISC_ORIG = `
  <span class="discmin"><b>預覽版</b>・<b>非買賣訊號</b>・<b>AI 研判、非保證</b></span>
  <details id="discMore"><summary>說明</summary>
    <p>本站不依卦象吉凶排名。回測：總分已跑訓練段與驗證段。驗證段短線未通過門檻、波段與中期證據不足（訓練段短線與波段亦未通過），保留段未驗證；目前沒有任何期間通過預先登錄的判定門檻。各期間數字見「總分排序」分頁。</p>
    <p>本站把個股與大盤的量化狀態對應到 64 卦六爻。陰陽不是買賣指令；爻態只是量化狀態的描述（陽＝較有利上漲、陰＝支撐不足或偏弱、未定＝資料不足）。「總分排序」分頁只依總分排列。</p>
    <p>校準：參數未校準。此句依資料檔 calibrated 欄位顯示（「資料版本資訊」內同列出該值）；「部分參數已校準」指部分參數已依訓練段校準、其餘仍為未校準的起點值；尚未校準的參數在校準後數字會變。</p>
  </details>
`;
const FORBID = ["機率", "勝率", "看多", "看空", "買進", "賣出", "多頭", "空頭", "吉", "凶", "趨勢反轉", "亢龍有悔", "轉弱", "轉強", "上行", "回撤", "衍生品", "期貨選擇權"];
// 精確片語白名單（使用者 2026-10-11 裁示：「借券賣出餘額」是正式名稱、不得改寫）：掃描前只拿掉這幾個完整片語，「賣出」本身仍是禁用字
const FORBID_OK = ["借券賣出餘額"];
const forbidHits = (text, list) => { const t = FORBID_OK.reduce((s, p) => s.split(p).join("\u0000"), String(text)); return list.filter(w => t.includes(w)); };
const structural = [
  ["TRI.stock 逐字＝v1.2.2:103-114", JSON.stringify(TRI.stock) === JSON.stringify(SPEC_TRI_STOCK)],
  ["TRI.market 8 卦；乾內卦／坤兩欄是不點名面向的通用句（兩側同字），其餘每格都與個股側不同字串", Object.keys(TRI.market).length === 8
    && TRI.market["坤"][0] === "三面向均未達門檻" && TRI.market["乾"][0] === "三面向皆有支撐"
    && Object.keys(TRI.stock).every(k => k === "坤" || (TRI.stock[k][1] !== TRI.market[k][1] && (k === "乾" || TRI.stock[k][0] !== TRI.market[k][0])))],
  ["FACET 個股／大盤各 6 爻、yang／yin＝面向名（期權另加「市場條件」）＋固定尾綴、名稱兩側互不相同", ["stock", "market"].every(k => FACET[k].length === 6
    && FACET[k].every(f => (f.yang === f.name + "較有利上漲" || f.yang === f.name + "市場條件較有利上漲") && (f.yin === f.name + "支撐不足或偏弱" || f.yin === f.name + "市場條件支撐不足或偏弱") && f.what && f.window))
    && FACET.stock.every((f, i) => f.name !== FACET.market[i].name)],
  ["TERMS 六條＝正式卦／暫定卦／動爻／遲滯／分數區間／資料不足", TERMS.map(t => t[0]).join("/") === "正式卦/暫定卦/動爻/遲滯/分數區間/資料不足" && TERMS.every(t => t[1].length > 0)],
  ["靜態文案零禁用詞（含「轉弱／轉強」：它們只能由動爻句產生；「借券賣出餘額」以精確片語放行）", forbidHits(JSON.stringify([FACET, TRI, TERMS]), FORBID).length === 0],
  ["非動爻輸出零「轉弱／轉強」", !cases.filter(c => !/動爻 |動爻但|多爻同翻/.test(c[0])).some(c => /轉弱|轉強/.test(c[2]))],
  ["翻轉當日的每爻句不含「候選變化」、未翻轉的不含「動爻」", !/候選變化/.test(cases[13][2]) && !/候選變化/.test(cases[22][2]) && !/動爻/.test(cases[20][2]) && !/動爻/.test(cases[21][2])],
  ["TERMS 遲滯條＝裁定 #53 C1 文", TERMS[3][1] === "連續兩日過門檻才翻爻的確認緩衝機制（陰→陽 ≥55、陽→陰 ≤45）；只站住一日的爻稱候選變化"],
  // ---- §9 ----
  ["§9 新增字串零禁用詞（S2-5＋#53 清單）", !FORBID.some(w => JSON.stringify([MV_OFF_TXT, MV_SWITCH_TAG, MV_MIX_TXT, CAL_FALSE_HTML, CAL_TRUE_HTML,
    modelShiftText(["d1", "d2"], ["A", "B"])]).includes(w))],
  ["§9 P5 #calTxt 靜態預設＝CAL_FALSE_HTML（讀不到資料時的最保守敘述與 calibrated=false 時逐字相同）",
    (html.match(/<span id="calTxt">([\s\S]*?)<\/span>。/) || [])[1] === CAL_FALSE_HTML],
  ["§9 P5／§12.9 免責卡除 #calTxt 外一字不動（與 DISC_ORIG 逐字相同；DISC_ORIG＝PR-74c 改寫後的版本）", (() => {
    const m = html.match(/<div class="disc" id="disc">([\s\S]*?)<\/div>/);
    return !!m && m[1].replace(/<span id="calTxt">[\s\S]*?<\/span>/, CAL_FALSE_HTML) === DISC_ORIG;
  })()],
  // ---- §10 懂卦理 ----
  ["§10 G7 新增說明文字零禁用詞（S2-5＋#53，含「轉弱／轉強」）：卦理入門／分布說明／用九用六註／自然象", !FORBID.some(w => JSON.stringify([GUIDE_INTRO, GUIDE_DIST_NOTE, GUIDE_EXTRA_NOTE, TRI_ELEM]).includes(w))],
  ["§10 G5 TRI_ORDER 宣告為陣列字面值（原始碼不含 Object.keys）", /^const TRI_ORDER = \[/m.test(html) && !/const TRI_ORDER = .*Object\.keys/.test(html)],
  ["§10 G5／§12.9 tab 白名單＝market／stock／guide／rank、hash kw 只由 parseKw 進出", /const TABS = new Set\(\["market","stock","guide","rank"\]\)/.test(html) && /const kw = parseKw\(q\.get\("kw"\)\)/.test(html)],
  ["§10 G1 卦頁與分布段不引用任何本站分數欄位（guideHexHtml 原始碼不含 .bs／.ti／.to／.l［）", (() => {
    const f = pickFunc("guideHexHtml"); return !/\.(bs|ti|to|sk)\b|\.l\[|in_rank_pool|DATA\./.test(f); })()],
  // ---- §11 我的持股 ----
  ["§11 H2 全檔（去註解後）對本機儲存只准 getItem(HOLD_KEY)：無 setItem／removeItem／clear／方括號存取／Storage 原型；HOLD_KEY 宣告唯一", (() => {
    const code = html.replace(/<!--[\s\S]*?-->/g, "").replace(/\/\/[^\n]*/g, "");
    const uses = [...code.matchAll(/(?<![A-Za-z_$])localStorage\b([^\n;]*)/g)].map(m => m[1]);   // lookbehind 不排除 `.`：window./self./globalThis. 前綴也計入
    return uses.length >= 1 && uses.every(u => /^\.getItem\(HOLD_KEY\)/.test(u)) && !/\bStorage\b/.test(code)
      && !/\b(window|self|globalThis)\.localStorage\.(setItem|removeItem|clear)\b/.test(code)
      && (code.match(/^const HOLD_KEY = "pm_holdings";$/mg) || []).length === 1; })()],
  ["§11 H2／H5 持股區原始碼只讀 c、不碰 sh／cost、不引用任何分數欄（bs／ti／to／l／sk）、不排序不篩選（無 sort／filter／reverse）", (() => {
    const f = [pickFunc("holdingsCodes"), pickFunc("readHoldings"), pickFunc("holdRowHtml"), pickFunc("holdHtml")].join("\n");
    return !/\.(sh|cost|bs|ti|to|sk)\b|["'](sh|cost)["']|\.l\[|\.(sort|filter|reverse)\(/.test(f); })()],
  ["§11 H7 持股區新增字串（常數＋渲染輸出）零禁用詞，另加「名單／查看／候選／買／賣」；字串常數一律經 esc() 進 innerHTML", (() => {
    const extra = ["名單", "查看", "候選", "買", "賣"];
    const S = holdRows("short", HOLD_CODES), W = holdRows("mid", HOLD_CODES), E = holdRows("short", []);
    // 渲染輸出去標籤後檢（可見文字；kwLabel 既有的 title="查看卦理…" 屬性是 §10 的既有字串、不是本批新增）
    const txt = JSON.stringify([HOLD_SRC_TXT, HOLD_NOTE_TXT, HOLD_EMPTY_TXT, HOLD_NO_H_TXT].concat([S.out, W.out, E.out].map(o => o.replace(/<[^>]*>/g, ""))));
    const f = pickFunc("holdRowHtml") + pickFunc("holdHtml");
    const consts = ["HOLD_SRC_TXT", "HOLD_NOTE_TXT", "HOLD_EMPTY_TXT", "HOLD_NO_H_TXT"];
    return !FORBID.concat(extra).some(w => txt.includes(w)) && HOLD_KEY === "pm_holdings"
      && consts.every(k => (f.match(new RegExp(k, "g")) || []).length >= 1 && (f.match(new RegExp(k, "g")) || []).length === (f.match(new RegExp("esc\\(" + k + "\\)", "g")) || []).length); })()],
  ["§11 H4 hash 白名單不變（code 只由 CODE_RE 進出、無持股清單鍵）；列 data-code 由 esc 包過、點列走 CODE_RE 再進 state", (() => {
    const click = html.slice(html.indexOf('closest("tr.hrow[data-code]")'), html.indexOf('closest("tr.hrow[data-code]")') + 400);
    return /const code = String\(q\.get\("code"\) \|\| ""\)\.trim\(\)\.toUpperCase\(\);\s*if \(CODE_RE\.test\(code\)\) out\.code = code;/.test(html)
      && !/q\.get\("hold/.test(html) && /data-code="\$\{esc\(code\)\}"/.test(pickFunc("holdRowHtml")) && /CODE_RE\.test\(c\)/.test(click); })()],
  ["§2 P7／§12.9 頂列收合：常駐 #stats 只寫資料日＋modelMixHtml（警示格）；其餘格與 #siteVer 在 details#statsMore（無 open 屬性）內；警示格不進 details", (() => {
    const f = pickFunc("renderStats");
    return /el\.innerHTML = `<span class="stat">資料日 <b>\$\{esc\(d\.date\)\}<\/b><\/span>` \+ modelMixHtml\(d\.model_version\);/.test(f)
      && /more\.innerHTML = \[/.test(f) && !/mvmix/.test(pickFunc("modelStatsHtml")) && /mvmix/.test(pickFunc("modelMixHtml"))
      && /<details class="statsmore" id="statsMore"><summary>資料版本資訊<\/summary>[^\n]*id="statsMoreIn"[^\n]*id="siteVer" hidden><\/span><\/div><\/details>/.test(html)
      && !/<details class="statsmore" id="statsMore"[^>]*\bopen\b/.test(html); })()],
  // ---- §12.9 總分排序 ----
  ["§12.9 C2 rankRows／rankBs 排序鍵只有 bs（＋代號）：原始碼不引用 overheated／ti／to／l／sk／kw／st／flags", (() => {
    const f = pickFunc("rankRows") + pickFunc("rankBs");
    return !/overheat|\.(ti|to|sk|kw|kwp|st|l|lf|lp|flags|cov)\b|\.l\[/.test(f) && /\.bs\b/.test(f); })()],
  ["§12.9 C5 揭露常數三期間齊、各含「總分高不代表後續報酬較高」、短線含兩市場負 IC；跨市場句逐字", ["short", "swing", "mid"].every(k => typeof RANK_DISC_H[k] === "string"
    && RANK_DISC_H[k].endsWith("總分高不代表後續報酬較高。")) && /IC 為負（上市 −0\.\d{3}、上櫃 −0\.\d{3}）/.test(RANK_DISC_H.short)
    && RANK_XMKT_TXT === "IC 分市場計算，跨市場混排的可比性未經驗證。"],
  ["§12.9 C1／C5 揭露常數（完整句＋常駐短語）＋名次說明零禁用詞（S2-5＋#53），另加「候選／推薦／強勢／選多空／連續確認天數／報酬較低」", (() => {
    const extra = ["候選", "推薦", "強勢", "選多空", "連續確認天數", "報酬較低", "較低者"];
    return !FORBID.concat(extra).some(w => JSON.stringify([RANK_DISC_H, RANK_DISC_S, RANK_XMKT_TXT, RANK_RANK_NOTE]).includes(w)); })()],
  ["§12.9 版面整理：常駐短語三期間齊、各以期間名起頭且含「驗證段」（常駐只有它，分頁免責／完整句／完整計數／名次定義都在 details#rankDetail 內）；分頁免責＝僅為總分排序／非買賣訊號／非候選名單；details 免責含 保留段未驗證／不依卦象吉凶／AI 研判、非保證；舊 RANK_WARN_HTML 大框已移除",
    [["short", "短線："], ["swing", "波段："], ["mid", "中期："]].every(([k, p]) => typeof RANK_DISC_S[k] === "string" && RANK_DISC_S[k].startsWith(p) && RANK_DISC_S[k].includes("驗證段"))
    && ["僅為總分排序", "非買賣訊號", "非候選名單"].every(w => RANK_WARN_TXT.includes(w))
    && ["保留段未驗證", "不依卦象吉凶", "AI 研判、非保證"].every(w => RANK_MORE_TXT.includes(w)) && !/RANK_WARN_HTML|id="rankWarn"/.test(html)
    && (() => { const f = pickFunc("rankHtml"), d = f.indexOf('<details id="rankDetail"');
      return d > 0 && f.indexOf('class="rk-w"') > d && f.indexOf('class="rk-h"') > d && f.indexOf('class="rk-c"') > d && f.indexOf('class="rk-n"') > d
        && f.indexOf('class="rk-s"') < d && !/<details id="rankDetail" open/.test(f); })()],
  ["§2 P1／§12.9 #disc 常駐（details 之前）＝單行「預覽版・非買賣訊號・AI 研判、非保證」、不含 #calTxt；details#discMore（summary「說明」、無 open 屬性）內含 不依卦象吉凶排名／回測無期間通過預先登錄的判定門檻／保留段未驗證／陰陽定義／#calTxt；.sub 已移除", (() => {
    const m = html.match(/<div class="disc" id="disc">([\s\S]*?)<details id="discMore"><summary>說明<\/summary>([\s\S]*?)<\/details>/);
    if (!m) return false;
    const vis = m[1].replace(/<[^>]*>/g, "").trim(), more = m[2];
    return vis === "預覽版・非買賣訊號・AI 研判、非保證" && !m[1].includes("calTxt")
      && ["本站不依卦象吉凶排名", "目前沒有任何期間通過預先登錄的判定門檻", "保留段未驗證", "陽＝較有利上漲、陰＝支撐不足或偏弱、未定＝資料不足", '<span id="calTxt">'].every(w => more.includes(w))
      && !/<details id="discMore"[^>]*\bopen\b/.test(html) && !/class="sub"/.test(html); })()],
  ["§12.9 C7 總分排序段落不寫本機儲存、不發請求（rankHtml／rankExpHtml／rankRowHtml 原始碼無 localStorage／sessionStorage／fetch）", (() => {
    const f = pickFunc("rankHtml") + pickFunc("rankExpHtml") + pickFunc("rankRowHtml") + pickFunc("rankRows");
    return !/localStorage|sessionStorage|fetch\(|XMLHttpRequest/.test(f); })()],
];
// ---- §13 PR-75d 本爻依據 ----
const IND_FORBID = FORBID.concat(["偏強", "偏弱", "越高越好", "越低越好", "描述性資料", "權重", "clipped", "undefined", "NaN", "null", "Infinity"]);
const indAll = (() => {   // 真實分片（data/web/ind/*.json，每日班會更新）全部個股／大盤 × 三期間 × 六爻：零禁用字、零 ATR 倍數（無例外）、不出現 undefined／NaN 等破字
  const dir = path.join(REPO, "data", "web", "ind"); let n = 0, hit = [], empty = 0;
  if (!fs.existsSync(dir)) return { n: 0, hit: ["no dir"], empty };
  for (const f of fs.readdirSync(dir).filter(f => f.endsWith(".json"))) {
    const j = JSON.parse(fs.readFileSync(path.join(dir, f), "utf8")), ref = { date: j.date, params_sha: j.params_sha };
    const keys = f === "market.json" ? [["market", "twse"], ["market", "tpex"]] : Object.keys(j.stocks || {}).map(k => ["stock", k]);
    for (const [kind, key] of keys) for (const h of ["short", "swing", "mid"]) for (let i = 0; i < 6; i++) {
      const r = indRows(j, kind, key, h, i, ref); n++;
      if (r === null) { empty++; continue; }
      if (!Array.isArray(r) || !r.every(x => Array.isArray(x) && x.length === 3 && x.every(c => typeof c === "string") && x[0] && x[1])) { hit.push([f, key, h, i, "shape"]); continue; }
      const t = JSON.stringify(r);
      const w = forbidHits(t, IND_FORBID.concat(["ATR", "S&P 500 距"]))[0]; if (w && hit.length < 5) hit.push([f, key, h, i, w]);
    }
  }
  return { n, hit, empty };
})();
structural.push(
  ["§13 D5 真實分片全部輸出（個股＋大盤 × 三期間 × 六爻）每列皆 [項目, 數值, 口徑] 字串、零禁用字（借券賣出餘額以精確片語放行）、零「偏強／偏弱／越高越好／權重／描述性資料」、零 ATR 倍數（無例外；S&P 500 距均線整列不列）、零 undefined／NaN／null 破字", indAll.n > 1000 && indAll.hit.length === 0],
  ["§13 D5 本爻依據常數（缺值白話／類別白話／名詞說明／標頭／不一致句）零禁用字；IND_TERMS 含還原價口徑、產業中位數含本股＋裁定 #73 B、融資記中性、借券賣出餘額以股、融資張（推論）、張數四捨五入註記",
    forbidHits(JSON.stringify([IND_MISS, IND_STRUCT, IND_VSEQ, IND_MSEQ, IND_CONT, IND_DIV, IND_TERMS, IND_HEAD, IND_LIST_NOTE, IND_OFF_TXT, IND_COLS]), FORBID.concat(["偏強", "偏弱", "越高越好", "越低越好", "描述性資料"])).length === 0
    && (() => { const t = JSON.stringify(IND_TERMS); return ["還原（後復權）價", "含本股", "裁定 #73 B", "記中性", "借券賣出餘額與發行股數以股計", "以張計（推論", "四捨五入到整張"].every(w => t.includes(w)) && !t.includes("借券餘額"); })()],
  ["§13 D6 依據表每格與爻名一律經 esc 進 innerHTML（indTableHtml）；indSlot 的 data-* 一律 esc", (() => {
    const f = pickFunc("indTableHtml"), g = pickFunc("indSlot");
    return /\$\{esc\(it\)\}/.test(f) && /\$\{esc\(v\)\}/.test(f) && (f.match(/\$\{esc\(n\)\}/g) || []).length === 2 && /esc\(\(LINE_NAMES/.test(f) && /esc\(IND_HEAD\)/.test(f)
      && !/\$\{(it|v|n|ik|kind|key|h|i)\}/.test(f + g)
      && (() => { const out = indTableHtml([["<b>a</b>", "<i>1</i>", "<u>n</u>"]], "stock", 0, ""); return !/<[biu]>/.test(out) && out.includes("&lt;b&gt;a&lt;/b&gt;"); })(); })()],
  ["§13 D1 依據表欄位＝項目｜數值｜口徑／備註（表頭由 IND_COLS 渲染，口徑欄 .ind-n 與項目下方 .ind-n2 同一段文字）", JSON.stringify(IND_COLS) === JSON.stringify(["項目", "數值", "口徑／備註"])
    && (() => { const out = indTableHtml(IND_2330_S[1], "stock", 1, ""); return (out.match(/<th /g) || []).length === 3 && (out.match(/<tr>/g) || []).length === 5
      && out.includes(">項目</th>") && out.includes(">口徑／備註</th>") && out.includes('<span class="ind-n2">看近 20 日、擺動點前後 2 根確認</span>'); })()],
  ["§13 D2 只有 indEnsure 發請求（fetchFresh 同源、鍵先過 IND_KEY_RE）；indRows／indSlot／indListHtml／rankExpHtml 不 fetch、不寫儲存", (() => {
    const e = pickFunc("indEnsure"), pure = ["indRows", "indSlot", "indInner", "indListHtml", "indFill", "rankExpHtml", "indStockRows", "indMarketRows", "indTableHtml"].map(pickFunc).join("\n");
    return /if \(!IND_KEY_RE\.test\(ik\) \|\| IND\[ik\]\) return;/.test(e) && /fetchFresh\("data\/web\/ind\/" \+ ik \+ "\.json"\)/.test(e)
      && !/fetch\(|fetchFresh|localStorage|sessionStorage|XMLHttpRequest/.test(pure) && !/localStorage|sessionStorage/.test(e); })()],
  ["§13 禁用字白名單是精確片語：「借券賣出餘額」放行，其他含「賣出」的句子（含「借券賣出」「融資賣出」）仍被擋；全站畫面不得把借券賣出餘額寫成「借券餘額」",
    forbidHits("借券賣出餘額 5 日變化", FORBID).length === 0 && forbidHits("融資賣出增加", FORBID).includes("賣出") && forbidHits("借券賣出增加", FORBID).includes("賣出")
    && forbidHits("借券賣出餘額增加、外資賣出", FORBID).includes("賣出") && JSON.stringify(FORBID_OK) === JSON.stringify(["借券賣出餘額"])
    && !html.replace(/<!--[\s\S]*?-->/g, "").replace(/\/\/[^\n]*/g, "").includes("借券餘額") && FACET.stock[4].what.includes("借券賣出餘額變化（反向）")],
);
for (const [name, ok] of structural) { if (!ok) fail++; console.log(`${ok ? "PASS" : "FAIL"} [結構] ${name}`); }
console.log(`\n=== explain_cases: ${cases.length} 案例 + ${structural.length} 結構斷言，FAIL ${fail} ===`);
process.exit(fail ? 1 : 0);
