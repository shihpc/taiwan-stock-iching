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
  "TRI_ORDER", "TRI_ELEM", "GUIDE_INTRO"];   // 後三個：§10 懂卦理
const LINE_CONSTS = ["MV_OFF_TXT", "MV_SWITCH_TAG", "MV_MIX_TXT", "CAL_FALSE_HTML", "CAL_TRUE_HTML",   // §9 模型換版標示
  "KW_RE", "GUIDE_DIST_NOTE", "GUIDE_EXTRA_NOTE", "state",   // §10（state：guideListRows 讀 state.gq）
  "CODE_RE", "HOLD_KEY", "HOLD_SRC_TXT", "HOLD_NOTE_TXT", "HOLD_EMPTY_TXT", "HOLD_NO_H_TXT"];   // §11 我的持股
const FUNCS = ["lineBit", "triSentence", "movingLines", "explainHex", "explainLine",
  "psVal", "tlPs", "psDiffers", "modelShiftAt", "modelShifts", "modelShiftText", "mvOffFor", "modelVersionInfo",
  "hexBits", "hexTri", "parseKw", "hexDist", "guideFacetRows", "guideTriRows", "kwLabel", "guideListRows",   // §10
  "hexFig", "holdingsCodes", "readHoldings", "holdRowHtml", "holdHtml"];   // §11 我的持股（holdHtml 讀 DATA／state.h）
// esc 與 index.html 同實作（該宣告跨兩行、不走 pickLineConst）；guideFacetRows／guideTriRows 需要它
const ESC_SRC = `const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));`;
const KWN_SRC = "const KW_NAME = {};";   // index.html 是 let（與 DATA／TL 同行宣告）；kwLabel 只在 name 缺時查它
const DATA_SRC = "var DATA = null;";      // §11 holdHtml／holdRowHtml 讀全域 DATA；var 掛在沙箱全域物件上，案例以 sb.DATA 餵 fixture
const src = [ESC_SRC, KWN_SRC, DATA_SRC, ...CONSTS.map(pickConst), ...LINE_CONSTS.map(pickLineConst), ...FUNCS.map(pickFunc)].join("\n\n");
const sb = { Array, Number, String, Object, isNaN, console };
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
    `看什麼：外資、投信淨買超占成交比重，外資買超天數，融資情境，借券餘額變化（反向）（${W.s5}）。目前分數 60.0，偏多區，籌碼供需較有利上漲。爻態穩定，分數未站到翻轉門檻另一側。可選資料部分缺、已重配權重。`],
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
// 2026-09-26（§9 P5）前 index.html 的免責卡內文逐字；P5 只准把校準那一句包進 #calTxt、依資料換字
const DISC_ORIG = `
  <b>預覽版</b>・本頁所有卦象與六爻皆<b>未經回測驗證</b>、<b>參數未校準</b>（calibrated=false），數字在校準後會變。
  <b>陰陽不是買賣指令</b>，本站<b>不建吉凶排名</b>；爻態只是量化狀態的描述（陽＝較有利上漲、陰＝支撐不足或偏弱、未定＝資料不足），
  屬 <b>AI 研判、非保證</b>。
`;
const FORBID = ["機率", "勝率", "看多", "看空", "買進", "賣出", "多頭", "空頭", "吉", "凶", "趨勢反轉", "亢龍有悔", "轉弱", "轉強", "上行", "回撤", "衍生品", "期貨選擇權"];
const structural = [
  ["TRI.stock 逐字＝v1.2.2:103-114", JSON.stringify(TRI.stock) === JSON.stringify(SPEC_TRI_STOCK)],
  ["TRI.market 8 卦；乾內卦／坤兩欄是不點名面向的通用句（兩側同字），其餘每格都與個股側不同字串", Object.keys(TRI.market).length === 8
    && TRI.market["坤"][0] === "三面向均未達門檻" && TRI.market["乾"][0] === "三面向皆有支撐"
    && Object.keys(TRI.stock).every(k => k === "坤" || (TRI.stock[k][1] !== TRI.market[k][1] && (k === "乾" || TRI.stock[k][0] !== TRI.market[k][0])))],
  ["FACET 個股／大盤各 6 爻、yang／yin＝面向名（期權另加「市場條件」）＋固定尾綴、名稱兩側互不相同", ["stock", "market"].every(k => FACET[k].length === 6
    && FACET[k].every(f => (f.yang === f.name + "較有利上漲" || f.yang === f.name + "市場條件較有利上漲") && (f.yin === f.name + "支撐不足或偏弱" || f.yin === f.name + "市場條件支撐不足或偏弱") && f.what && f.window))
    && FACET.stock.every((f, i) => f.name !== FACET.market[i].name)],
  ["TERMS 六條＝正式卦／暫定卦／動爻／遲滯／分數區間／資料不足", TERMS.map(t => t[0]).join("/") === "正式卦/暫定卦/動爻/遲滯/分數區間/資料不足" && TERMS.every(t => t[1].length > 0)],
  ["靜態文案零禁用詞（含「轉弱／轉強」：它們只能由動爻句產生）", !FORBID.some(w => JSON.stringify([FACET, TRI, TERMS]).includes(w))],
  ["非動爻輸出零「轉弱／轉強」", !cases.filter(c => !/動爻 |動爻但|多爻同翻/.test(c[0])).some(c => /轉弱|轉強/.test(c[2]))],
  ["翻轉當日的每爻句不含「候選變化」、未翻轉的不含「動爻」", !/候選變化/.test(cases[13][2]) && !/候選變化/.test(cases[22][2]) && !/動爻/.test(cases[20][2]) && !/動爻/.test(cases[21][2])],
  ["TERMS 遲滯條＝裁定 #53 C1 文", TERMS[3][1] === "連續兩日過門檻才翻爻的確認緩衝機制（陰→陽 ≥55、陽→陰 ≤45）；只站住一日的爻稱候選變化"],
  // ---- §9 ----
  ["§9 新增字串零禁用詞（S2-5＋#53 清單）", !FORBID.some(w => JSON.stringify([MV_OFF_TXT, MV_SWITCH_TAG, MV_MIX_TXT, CAL_FALSE_HTML, CAL_TRUE_HTML,
    modelShiftText(["d1", "d2"], ["A", "B"])]).includes(w))],
  ["§9 P5 #calTxt 靜態預設＝CAL_FALSE_HTML（讀不到資料時的最保守敘述與 calibrated=false 時逐字相同）",
    (html.match(/<span id="calTxt">([\s\S]*?)<\/span>。/) || [])[1] === CAL_FALSE_HTML],
  ["§9 P5 免責卡除 #calTxt 外一字不動（與 2026-09-26 前原文逐字相同）", (() => {
    const m = html.match(/<div class="disc" id="disc">([\s\S]*?)<\/div>/);
    return !!m && m[1].replace(/<span id="calTxt">[\s\S]*?<\/span>/, CAL_FALSE_HTML) === DISC_ORIG;
  })()],
  // ---- §10 懂卦理 ----
  ["§10 G7 新增說明文字零禁用詞（S2-5＋#53，含「轉弱／轉強」）：卦理入門／分布說明／用九用六註／自然象", !FORBID.some(w => JSON.stringify([GUIDE_INTRO, GUIDE_DIST_NOTE, GUIDE_EXTRA_NOTE, TRI_ELEM]).includes(w))],
  ["§10 G5 TRI_ORDER 宣告為陣列字面值（原始碼不含 Object.keys）", /^const TRI_ORDER = \[/m.test(html) && !/const TRI_ORDER = .*Object\.keys/.test(html)],
  ["§10 G5 tab 白名單含 guide、hash kw 只由 parseKw 進出", /const TABS = new Set\(\["market","stock","guide"\]\)/.test(html) && /const kw = parseKw\(q\.get\("kw"\)\)/.test(html)],
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
];
for (const [name, ok] of structural) { if (!ok) fail++; console.log(`${ok ? "PASS" : "FAIL"} [結構] ${name}`); }
console.log(`\n=== explain_cases: ${cases.length} 案例 + ${structural.length} 結構斷言，FAIL ${fail} ===`);
process.exit(fail ? 1 : 0);
