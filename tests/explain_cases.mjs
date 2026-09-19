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
/** 抽 `function NAME(` 起至配對的 `}`。 */
function pickFunc(name) {
  const key = `function ${name}(`;
  const hits = html.split(key).length - 1;
  if (hits !== 1) throw new Error(`${key} 命中 ${hits} 次（需唯一）`);
  return sliceBalanced(html.indexOf(key), "{", "}");
}

const CONSTS = ["POS", "FACET", "TRI_NAME", "TRI", "TERMS", "ST_WORD"];
const FUNCS = ["lineBit", "triSentence", "movingLines", "explainHex", "explainLine"];
const src = [...CONSTS.map(pickConst), ...FUNCS.map(pickFunc)].join("\n\n");
const sb = { Array, Number, String, Object, isNaN, console };
vm.createContext(sb);
new vm.Script(src).runInContext(sb);
// const 是詞法綁定、不掛在 context 物件上（function 宣告才會），用一支表達式腳本讀回
const { FACET, TRI, TERMS, explainHex, explainLine } = new vm.Script("({ FACET, TRI, TERMS, explainHex, explainLine })").runInContext(sb);

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
];
for (const [name, ok] of structural) { if (!ok) fail++; console.log(`${ok ? "PASS" : "FAIL"} [結構] ${name}`); }
console.log(`\n=== explain_cases: ${cases.length} 案例 + ${structural.length} 結構斷言，FAIL ${fail} ===`);
process.exit(fail ? 1 : 0);
