"use strict";
const fs = require("fs");
const nodePath = require("path");
const NOW = Number(process.argv[2]);
if (!Number.isFinite(NOW)) throw new Error("pass now ms");
Date.now = () => NOW;

class Node {}
class El extends Node {
  constructor() { super(); this.childNodes = []; this.className = ""; this.style = {}; this.dataset = {}; this.textContent = ""; }
  append(...xs) { this.childNodes.push(...xs); }
  replaceChildren(...xs) { this.childNodes = xs.flat(); }
  setAttribute(k, v) { this[k] = String(v); }
}
class TextNode extends Node { constructor(s) { super(); this.nodeType = 3; this.data = String(s); } }
const texts = [];
const textPos = [];
const g = {
  scale() {}, fillRect() {}, strokeRect() {}, beginPath() {}, moveTo() {}, lineTo() {}, stroke() {}, fill() {}, closePath() {}, setLineDash() {},
  measureText() { return { width: 48 }; },
  fillText(s, x, y) { texts.push(String(s)); textPos.push({ s: String(s), y }); },
};
const canvas = { clientWidth: 900, clientHeight: 640, width: 0, height: 0, getContext: () => g };
const els = { c: canvas };
function getElementById(id) { if (!els[id]) els[id] = new El(); return els[id]; }
global.Node = Node;
global.devicePixelRatio = 1;
global.document = { createElement: () => new El(), createTextNode: s => new TextNode(s), getElementById };
global.localStorage = { getItem() { return null; }, setItem() {} };

function text(n) {
  if (n == null || n === false) return "";
  if (typeof n === "string" || typeof n === "number") return String(n);
  if (n.nodeType === 3) return n.data;
  if (n.childNodes && n.childNodes.length) return n.childNodes.map(text).join("");
  return n.textContent || "";
}
function cut(src, a, b) {
  const i = src.indexOf(a), j = src.indexOf(b, i + a.length);
  if (i < 0 || j < 0) throw new Error("missing " + a + " .. " + b);
  return src.slice(i, j);
}
const src = fs.readFileSync(nodePath.join(__dirname, "../web/app.js"), "utf8");
const api = new Function([
  "const RANGE=.15; let COINS=['BTC','ETH']; let COIN='BTC';",
  cut(src, "const fin=", "async function loadSide"),
  cut(src, "let P=", "/*MODEL-END*/"),
  cut(src, "function v2View", "document.addEventListener"),
  "return {validParams,hourlyAtr,callV2,call,banner,draw,side,overview,CALLS,get STATS(){return STATS},set STATS(v){STATS=v},get P(){return P},set P(v){P=v}};",
].join("\n"))();

const H = 3600e3;
function bars(n, high) {
  const out = [];
  for (let i = 0; i < n; i++) out.push([NOW - (n - i) * H, 1000, high ? high(i, n) : 1005, 995, 1000, 1]);
  return out;
}
const C = bars(20);
const path = [
  { price: 1040, size: 50, rel: 1, dist: 0.04, side: "short" },
  { price: 1020, size: 40, rel: 0.8, dist: 0.02, side: "short" },
];
const opp = { price: 960, size: 80, rel: 0.7, dist: 0.04, side: "long" };
const cl = path.concat([opp]);
function prices(xs) { return xs.map(c => c.price); }
const ccOf = pth => {
  const body = cut(src, "function callCore", "function hourlyAtr");
  const fn = new Function("P", "C", "path", body + "\nreturn callCore(C,{spot:1000,path},0);");
  return fn(api.P, C, pth);
};
const kept = [path[1]];
const unf = ccOf(path), fil = ccOf(kept);
const params = JSON.parse(fs.readFileSync(nodePath.join(__dirname, "../tracker/params.json"), "utf8"));
const bad = JSON.parse(JSON.stringify(params)); delete bad.v2.target_frac;
const nanP = JSON.parse(JSON.stringify(params)); nanP.v2.max_datr = NaN;
const cluster = { price: 1100, rel: 0.8, size: 10, dist: 0.1, side: "short" };
const base = { coin: "BTC", spot: 1000, up: true, pct: 10, dd: 100, m4: 0.01, f: 0.0001, conf: "strong", hits: [], path: [cluster, { price: 1200, rel: 0.4, size: 4, dist: 0.2, side: "short" }], t: cluster };
function shot(rec, candles) {
  texts.length = 0;
  textPos.length = 0;
  const M = { spot: 1000, path: rec.path, cl: rec.path, tot: [1], nb: 1, lo: 900, bw: 20, bwNote: 1 };
  const d = { candles: candles || C, hl: { funding: 0.0001, oi_btc: 1, mark: 1000 }, sources: { hyperliquid: "ok" }, ts: NOW, oiMode: "test" };
  api.banner(rec); api.draw(d, M, rec); api.side(d, M, rec);
  return { bc: text(els.bc), bs: text(els.bs), bf: els.bf.textContent, bn: els.bn.className, chart: texts.slice(), path: text(els.path), labels: textPos.filter(t => t.s.startsWith("target ") || t.s.startsWith("shadow ")) };
}
const passRec = Object.assign({}, base, { v2: { pass: true, why: "ok", target: 1080, target_frac: 0.75 } });
const supRec = Object.assign({}, base, { v2: { pass: false, why: "no ATR", target: 1080, target_frac: 0.8 } });
const passShot = shot(passRec);
const supShot = shot(supRec);
const near = { price: 1000.4, rel: 1, size: 10, dist: 0.0004, side: "short" };
const wide = bars(20).map((c, i) => i === 10 ? [c[0], 1000, 8000, 100, 1000, 1] : c.slice());
const closeRec = { coin: "BTC", spot: 1000, up: true, pct: 0.04, dd: 0.4, m4: 0, f: 0, conf: "weak", hits: [], path: [near], t: near, v2: { pass: true, why: "ok", target: 1000.2, target_frac: 0.5 } };
const closeShot = shot(closeRec, wide);
const labelGap = closeShot.labels.length >= 2 ? Math.abs(closeShot.labels[0].y - closeShot.labels[1].y) : null;
const mkt = { candles: C, hl: { funding: 0 } };
const book = { spot: 1000, path, cl };
api.STATS = null;
const localCall = api.call("BTC", mkt, book);
const localShot = shot(localCall);
const fresh = { dir: "up", pass: true, why: "ok", conf: "strong", rel: 0.42, m4: 0.05, ts: NOW };
api.STATS = { selection: { BTC: Object.assign({ v2_target: 1016, shadow: 1100 }, fresh) } };
const called = api.call("BTC", mkt, book);
const calledShot = shot(called);
api.STATS = { selection: { BTC: Object.assign({ v2_target: 1032 }, fresh) } };
const agreed = api.call("BTC", mkt, book);
const agreedShot = shot(agreed);
api.STATS = { selection: { BTC: { dir: "up", shadow: null, pass: true, why: "ok", v2_target: null, conf: "strong", rel: 0.42, m4: 0.05, ts: NOW } } };
const nullShadow = api.call("BTC", mkt, book);
const nullShot = shot(nullShadow);
api.STATS = { selection: { BTC: { dir: "up", shadow: null, pass: true, why: "ok", v2_target: 1016, ts: NOW } } };
const nullShadowPass = api.call("BTC", mkt, book);
const nullPassShot = shot(nullShadowPass);
api.STATS = { selection: { BTC: Object.assign({ v2_target: 1016 }, fresh, { ts: NOW - 16 * 60 * 1000 }) } };
const stale = api.call("BTC", mkt, book);
const staleShot = shot(stale);
api.CALLS.BTC = passRec; api.CALLS.ETH = supRec; api.overview();

const out = {
  atr: api.hourlyAtr(C, 14, NOW),
  unfiltered: api.callV2(C, { spot: 1000, cl }, unf, NOW),
  filtered: api.callV2(C, { spot: 1000, cl }, fil, NOW),
  nan: api.callV2(bars(20, () => NaN), { spot: 1000, cl }, unf, NOW),
  flat: api.callV2(bars(20, () => 1000).map(c => (c[2] = c[3] = c[4] = 1000, c)), { spot: 1000, cl }, unf, NOW),
  asym: api.callV2(C, { spot: 1000, cl: path.concat([{ price: -Infinity, size: 80, rel: 0.5, dist: 0.04 }]) }, unf, NOW),
  local_price: localCall.t && localCall.t.price, local_pass: !!(localCall.v2 && localCall.v2.pass),
  local_text: localShot.bc + localShot.bs + localShot.path,
  call_price: called.t && called.t.price, call_pass: !!(called.v2 && called.v2.pass), call_target: called.v2 && called.v2.target,
  call_text: calledShot.bc + calledShot.bs + calledShot.path + calledShot.chart.join("\n"),
  call_badge: calledShot.bf,
  agree_badge: agreedShot.bf, agree_text: agreedShot.bs + agreedShot.bc,
  null_text: nullShot.bc + nullShot.bs + nullShot.path + nullShot.chart.join("\n"),
  null_pass_text: nullPassShot.bc + nullPassShot.bs + nullPassShot.path + nullPassShot.chart.join("\n"),
  null_pass_target: nullShadowPass.v2 && nullShadowPass.v2.target,
  stale_price: stale.t && stale.t.price, stale_text: staleShot.bc + staleShot.bs + staleShot.path,
  label_gap: labelGap,
  valid_ok: api.validParams(params), valid_missing: api.validParams(bad), valid_nan: api.validParams(nanP),
  banner_pass: { bc: passShot.bc, bs: passShot.bs, bf: passShot.bf, bn: passShot.bn },
  banner_sup: { bc: supShot.bc, bs: supShot.bs, bf: supShot.bf, bn: supShot.bn },
  chart_pass: passShot.chart, chart_sup: supShot.chart, path_pass: passShot.path,
  overview: text(els.ovt),
};
process.stdout.write(JSON.stringify(out));
