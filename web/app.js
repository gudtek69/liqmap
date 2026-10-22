"use strict";
// liqmap page logic. Loaded as an external file so the page CSP can forbid inline script/style.
// All API-derived values are inserted with textContent / DOM nodes (never innerHTML) and validated first.
const RANGE=.15;
const COIN_RE=/^[A-Z0-9]{1,15}$/;
let COINS=['BTC','ETH','SOL'];// default list; replaced by the coins present in data.json (set LIQMAP_COINS for fetcher.py)
let COIN=(location.hash.slice(1)||'BTC').toUpperCase();if(!COINS.includes(COIN))COIN='BTC';
const fin=x=>typeof x==='number'&&Number.isFinite(x);
const dec=p=>p>=1000?0:p>=100?1:p>=10?2:p>=1?3:p>=.1?4:5;
const fmtF=p=>fin(p)?p.toLocaleString(undefined,{minimumFractionDigits:dec(p),maximumFractionDigits:dec(p)}):'—';
const fmtK=p=>!fin(p)?'—':p>=10000?(p/1000).toFixed(1)+'K':p>=1000?(p/1000).toFixed(2)+'K':p.toFixed(dec(p));
const $=id=>document.getElementById(id),fmtUsd=v=>!fin(v)?'—':v>=1e9?'$'+(v/1e9).toFixed(2)+'B':v>=1e6?'$'+(v/1e6).toFixed(1)+'M':'$'+(v/1e3).toFixed(0)+'K';
// Tiny DOM builder: h('div',{class:'x',style:{width:'5%'}},'text',childNode). Strings always become text nodes.
function h(tag,attrs,...kids){const el=document.createElement(tag);
  for(const [k,v] of Object.entries(attrs||{})){if(v==null||v===false)continue;
    if(k==='class')el.className=v;else if(k==='style')Object.assign(el.style,v);/* CSSOM, allowed by CSP */
    else if(k==='data')Object.assign(el.dataset,v);else el.setAttribute(k,String(v))}
  for(const c of kids.flat()){if(c==null||c===false)continue;el.append(c instanceof Node?c:document.createTextNode(String(c)))}
  return el}
const put=(id,...kids)=>$(id).replaceChildren(...kids.flat().filter(c=>c!=null&&c!==false).map(c=>c instanceof Node?c:document.createTextNode(String(c))));
// ---- validation of anything that came over the network
const isRow=(r,n)=>Array.isArray(r)&&r.length>=n&&r.slice(0,n).every(fin);
function cleanPairs(a){return Array.isArray(a)?a.filter(r=>isRow(r,2)).map(r=>[r[0],r[1]]):null}
function cleanCoin(d){
  if(!d||typeof d!=='object'||!Array.isArray(d.candles))return null;
  const candles=d.candles.filter(r=>isRow(r,6)).map(r=>r.slice(0,6));
  const hl=d.hl;if(candles.length<5||!hl||!fin(hl.mark)||hl.mark<=0||!fin(hl.funding))return null;
  const src={};if(d.sources&&typeof d.sources==='object')for(const [k,v] of Object.entries(d.sources).slice(0,10))src[String(k).slice(0,40)]=String(v).slice(0,120);
  const out={ts:fin(d.ts)?d.ts:0,candles,hl:{mark:hl.mark,funding:hl.funding,oi_btc:fin(hl.oi_btc)?hl.oi_btc:null},sources:src};
  const oi=cleanPairs(d.okx_oi),ls=cleanPairs(d.okx_ls);if(oi&&oi.length)out.okx_oi=oi;if(ls&&ls.length)out.okx_ls=ls;
  return out}
function cleanAll(all){if(!all||typeof all!=='object'||!all.coins||typeof all.coins!=='object')return null;
  const coins={};for(const [k,v] of Object.entries(all.coins)){if(!COIN_RE.test(k))continue;const c=cleanCoin(v);if(c)coins[k]=c;else if(v&&typeof v==='object')coins[k]=null}
  return {coins}}
const PNUM=['mm','decay','bin_pct','range','smooth','min_dist','max_dist','ls_weight','candle_tilt','opp_min_rel','third_min_ratio','next_min_ratio'];
const CNUM=['m4_agree','m4_disagree','m1_agree','m1_disagree','funding','funding_min','strong','moderate'];
function validParams(q){const v2=q&&q.v2;return q&&typeof q==='object'&&Array.isArray(q.lev)&&q.lev.length&&q.lev.every(r=>isRow(r,2)&&r[0]>0)&&PNUM.every(k=>fin(q[k]))&&q.bin_pct>0&&q.range>0&&q.conf&&CNUM.every(k=>fin(q.conf[k]))&&!!v2&&fin(v2.target_frac)&&fin(v2.max_datr)&&fin(v2.min_asym)}
async function getJSON(url,opts){const ac=new AbortController(),t=setTimeout(()=>ac.abort(),15000);
  try{const r=await fetch(url,{...opts,signal:ac.signal});if(!r.ok)throw new Error('HTTP '+r.status);return await r.json()}finally{clearTimeout(t)}}
async function hl(b){return getJSON('https://api.hyperliquid.xyz/info',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b),cache:'no-store',credentials:'omit',referrerPolicy:'no-referrer'})}
let ALL=null,STATS=null;
async function loadSide(){try{const q=await getJSON('params.json?'+Date.now(),{cache:'no-store'});if(validParams(q))P=q}catch(e){}
  try{const s=await getJSON('stats.json?'+Date.now(),{cache:'no-store'});STATS=s&&typeof s==='object'?s:null}catch(e){STATS=null}}
async function load(coin){
  let d=null;try{const all=cleanAll(await getJSON('data.json?'+Date.now(),{cache:'no-store'}));if(all){ALL=all;const ks=Object.keys(all.coins);if(ks.length)COINS=ks;d=all.coins[coin]||null}}catch(e){}
  if(!d||Date.now()-d.ts>5*60e3){ // browser-direct fallback: Hyperliquid has CORS
    const stale=!!d;
    const c=await hl({type:'candleSnapshot',req:{coin,interval:'1h',startTime:Date.now()-100*3600e3}});
    if(!Array.isArray(c))throw new Error('unexpected candle data');
    const mc=await hl({type:'metaAndAssetCtxs'});
    if(!Array.isArray(mc)||mc.length<2||!mc[0]||!Array.isArray(mc[0].universe)||!Array.isArray(mc[1]))throw new Error('unexpected market data');
    const a=mc[1][mc[0].universe.findIndex(u=>u&&u.name===coin)];if(!a)throw new Error('coin not found');
    d=cleanCoin({ts:Date.now(),sources:{hyperliquid:'ok (browser)',okx:stale?'stale data.json':'needs fetcher.py'},
      candles:c.map(x=>x&&[+x.t,+x.o,+x.h,+x.l,+x.c,+x.v]),hl:{oi_btc:+a.openInterest,funding:+a.funding,mark:+a.markPx}});
    if(!d)throw new Error('invalid data from API');
  }
  return d;
}
/*MODEL-START*/
let P={"version":1,"lev":[[5,.14],[10,.30],[25,.26],[50,.18],[100,.12]],"mm":.004,"decay":.985,"bin_pct":.0025,"range":.15,"smooth":3,"min_dist":.002,"max_dist":.10,"ls_weight":.6,"candle_tilt":.08,"opp_min_rel":.15,"third_min_ratio":.5,"next_min_ratio":.4,"conf":{"m4_agree":.2,"m4_disagree":-.15,"m1_agree":.1,"m1_disagree":-.05,"funding":.1,"funding_min":2e-6,"strong":.85,"moderate":.45},"v2":{"target_frac":.8,"max_datr":3,"min_asym":1.5}};
const nice=x=>{const e=Math.pow(10,Math.floor(Math.log10(x))),m=x/e;return e*(m<1.5?1:m<2.25?2:m<3.5?2.5:m<7.5?5:10)};
function model(d){
  const C=d.candles,spot=d.hl.mark,n=C.length;let dOI;
  if(d.okx_oi&&d.okx_oi.length>10){const m=new Map(d.okx_oi),last=d.okx_oi.at(-1)[1];const scale=d.hl.oi_btc!=null?(d.hl.oi_btc*spot+last)/last:1;
    dOI=C.map((c,i)=>{const a=m.get(c[0]);let b=m.get(c[0]+3600e3);if(b==null&&i===n-1)b=last;return a!=null&&b!=null?(b-a)*scale:0});d.oiMode='OKX OI Δ (scaled to HL+OKX)';}
  else{const avg=C.reduce((s,c)=>s+c[5],0)/n;dOI=C.map(c=>(c[5]-avg)*c[4]*0.3);d.oiMode='volume proxy (no OI history)';}
  const lsMap=new Map(d.okx_ls||[]);const pos=[];
  for(let i=0;i<n;i++){const [t,o,h,l,c]=C[i];for(const p of pos){p.size*=P.decay;if(!p.dead&&((p.side>0&&l<=p.liq)||(p.side<0&&h>=p.liq)))p.dead=1}
    const x=dOI[i],tp=(h+l+c)/3;
    if(x>0){const r=lsMap.get(t)??1.2;let lf=r/(1+r)*P.ls_weight+(1-P.ls_weight)/2+(c>o?P.candle_tilt:-P.candle_tilt);lf=Math.min(.8,Math.max(.2,lf));
      for(const [L,w] of P.lev){pos.push({side:1,size:x*lf*w,liq:tp*(1-1/L+P.mm)});pos.push({side:-1,size:x*(1-lf)*w,liq:tp*(1+1/L-P.mm)})}}
    else if(x<0){const tot=pos.reduce((s,p)=>s+(p.dead?0:p.size),0);if(tot>0){const f=Math.max(0,1+x/tot);for(const p of pos)p.size*=f}}}
  const bw=nice(spot*P.bin_pct),lo=Math.floor(spot*(1-P.range)/bw)*bw,nb=Math.ceil(spot*2*P.range/bw);
  const L=new Float64Array(nb),S=new Float64Array(nb);
  for(const p of pos){if(p.dead)continue;if((p.side>0&&p.liq>=spot)||(p.side<0&&p.liq<=spot))continue;const k=Math.floor((p.liq-lo)/bw);if(k>=0&&k<nb)(p.side>0?L:S)[k]+=p.size}
  const tot=Array.from(L,(v,i)=>v+S[i]),w=P.smooth;
  const sm=tot.map((_,i)=>{let s=0,ww=0;for(let j=-w;j<=w;j++){const k=i+j;if(k>=0&&k<nb){const q=w+1-Math.abs(j);s+=tot[k]*q;ww+=q}}return s/ww});
  let cl=[];for(let i=2;i<nb-2;i++)if(sm[i]>0&&sm[i]>=sm[i-1]&&sm[i]>sm[i+1]&&sm[i]>=sm[i-2]&&sm[i]>sm[i+2]){let s=0;for(let j=-4;j<=4;j++){const k=i+j;if(k>=0&&k<nb)s+=tot[k]}
    let pk=i;for(let j=-3;j<=3;j++){const k=i+j;if(k>=0&&k<nb&&tot[k]>tot[pk])pk=k}const price=lo+(pk+.5)*bw;cl.push({price,size:s,side:price>spot?'short':'long',dist:Math.abs(price-spot)/spot})}
  cl=cl.filter(c=>c.dist>P.min_dist&&c.dist<P.max_dist).sort((a,b)=>b.size-a.size);
  const max=cl[0]?.size||1;cl.forEach(c=>c.rel=c.size/max);
  const above=cl.filter(c=>c.price>spot).slice(0,4),below=cl.filter(c=>c.price<spot).slice(0,4);
  const score=c=>c.size/Math.sqrt(c.dist*100);const path=[];
  const first=[...above,...below].sort((a,b)=>score(b)-score(a))[0];
  if(first){path.push(first);const opp=(first.price>spot?below:above).filter(c=>c.rel>P.opp_min_rel).sort((a,b)=>score(b)-score(a));const sec=opp[0];
    if(sec){path.push(sec);const third=(sec.price>spot?above:below).filter(c=>c!==first&&Math.abs(c.price-spot)>Math.abs(sec.price-spot)&&c.size>sec.size*P.third_min_ratio).sort((a,b)=>score(b)-score(a))[0];if(third)path.push(third)}
    else{const nx=(first.price>spot?above:below).filter(c=>Math.abs(c.price-spot)>Math.abs(first.price-spot)&&c.size>first.size*P.next_min_ratio)[0];if(nx)path.push(nx)}}
  return {spot,lo,bw,nb,L,S,tot,cl,path};
}
function callCore(C,M,f){const t=M.path[0];if(!t)return null;const spot=M.spot,up=t.price>spot,pct=(t.price-spot)/spot*100,K=P.conf,dd=up?1:-1;
  let sc=t.rel/Math.sqrt(Math.max(.3,Math.abs(pct)));const l4=C.slice(-4),l1=C.at(-1);const m4=(l4.at(-1)[4]-l4[0][1])/l4[0][1],m1=(l1[4]-l1[1])/l1[1];
  sc+=(Math.sign(m4)===dd?K.m4_agree:K.m4_disagree)+(Math.sign(m1)===dd?K.m1_agree:K.m1_disagree);
  if(Math.abs(f)>K.funding_min)sc+=((f>0)!==up?K.funding:-K.funding);
  return {up,t,pct,dd:t.price-spot,m4,score:sc,conf:sc>K.strong?'strong':sc>K.moderate?'moderate':'weak'}}
// v2 rules (mirrors tracker/liqmodel.py call_v2). Target is target_frac of the cluster distance.
// Suppress when the cluster is farther than max_datr × hourly ATR(14) (completed candles only) or the opposite
// cluster is < min_asym × as far. Non-finite ATR → "no ATR"; non-finite asymmetry → "bad data".
function hourlyAtr(C,n=14,now=Date.now()){const d=C.filter(c=>c[0]+3600e3<=now);if(d.length<n+1)return null;let s=0;
  for(let i=d.length-n;i<d.length;i++){const b=d[i],a=d[i-1];s+=Math.max(b[2]-b[3],Math.abs(b[2]-a[4]),Math.abs(b[3]-a[4]))}return s/n}
function callV2(C,M,cc,now){if(!cc)return null;const V=Object.assign({target_frac:.8,max_datr:3,min_asym:1.5},P.v2||{}),spot=M.spot,D=Math.abs(cc.t.price-spot);
  const opp=(M.cl||[]).filter(c=>cc.up?c.price<spot:c.price>spot).sort((a,b)=>b.size/Math.sqrt(b.dist*100)-a.size/Math.sqrt(a.dist*100))[0];
  const atr=hourlyAtr(C,14,now==null?Date.now():now),datr=(Number.isFinite(atr)&&atr>0)?D/atr:null,asym=opp&&D?Math.abs(opp.price-spot)/D:null,why=[],add=w=>{if(!why.includes(w))why.push(w)};
  if(!(Number.isFinite(V.target_frac)&&Number.isFinite(V.max_datr)&&Number.isFinite(V.min_asym)))add('bad data');
  if(datr===null||!Number.isFinite(datr))add('no ATR');else if(Number.isFinite(V.max_datr)&&datr>V.max_datr)add(`${datr.toFixed(1)}× ATR > ${V.max_datr}`);
  if(asym===null)add('no opposite cluster');else if(!Number.isFinite(asym))add('bad data');else if(Number.isFinite(V.min_asym)&&asym<V.min_asym)add(`opposite only ${asym.toFixed(2)}× as far`);
  const target=spot+(cc.up?1:-1)*V.target_frac*D;if(!Number.isFinite(target))add('bad data');
  return {pass:!why.length,why:why.join(', '),target,target_frac:V.target_frac,datr:Number.isFinite(datr)?datr:null,asym:Number.isFinite(asym)?asym:null}}
/*MODEL-END*/

function v2View(r){
  const v=r&&r.v2;if(!r||!v)return null;
  // A published selection does not carry the cluster price, so there is no shadow line to draw.
  const shadow=!r.hideShadow&&r.t&&fin(r.t.price)&&r.t.price>0?r.t.price:null;
  if(!v.pass||!(fin(v.target)&&v.target>0)){
    if(r.fromSelection)return {pass:false,why:v.why||'no call',shadow:null,up:!!r.up};
    if(!shadow)return null;
    return {pass:false,why:v.why||'no call',shadow,up:!!r.up}}
  if(!r.fromSelection&&!shadow)return null;
  const dd=v.target-r.spot,pct=r.spot?dd/r.spot*100:0,frac=fin(v.target_frac)?Math.round(v.target_frac*100):null;
  return {pass:true,why:v.why,price:v.target,dd,pct,frac,shadow,up:!!r.up};
}
function draw(d,M,rec){
  const cv=$('c'),dpr=devicePixelRatio||1,W=cv.clientWidth,H=cv.clientHeight;cv.width=W*dpr;cv.height=H*dpr;const g=cv.getContext('2d');g.scale(dpr,dpr);
  const path=(rec&&rec.hideShadow)?(rec.path||[]):(rec&&Array.isArray(rec.path))?rec.path:M.path,view=v2View(rec);
  const marks=path.map(p=>p.price).filter(fin);if(view&&view.pass)marks.push(view.price);if(view&&fin(view.shadow))marks.push(view.shadow);if(!marks.length)marks.push(M.spot);
  const C=d.candles.slice(-96),padR=74,padB=26,fut=Math.round((W-padR)*.2),cw=W-padR-fut,histW=0;
  const lo=M.spot*(1-RANGE),hi=M.spot*(1+RANGE);let pmin=Math.min(...C.map(c=>c[3]),...marks),pmax=Math.max(...C.map(c=>c[2]),...marks);
  const pad=(pmax-pmin)*.25||M.spot*.01;pmin=Math.max(lo,pmin-pad);pmax=Math.min(hi,pmax+pad);if(!(pmax>pmin)){pmin=M.spot*.9;pmax=M.spot*1.1}
  const y=p=>(1-(p-pmin)/(pmax-pmin))*(H-padB);
  g.fillStyle='#06080c';g.fillRect(0,0,W,H);
  // heatmap
  const mx=Math.max(...M.tot);const ramp=v=>{v=Math.pow(v,.55);const s=[[6,8,12],[38,16,92],[30,90,170],[40,180,150],[200,220,60],[255,240,120]];const f=v*(s.length-1),i=Math.min(s.length-2,Math.floor(f)),t=f-i;return s[i].map((a,k)=>Math.round(a+(s[i+1][k]-a)*t))};
  for(let k=0;k<M.nb;k++){const p0=M.lo+k*M.bw;if(p0+M.bw<pmin||p0>pmax)continue;const v=M.tot[k]/mx;if(v<.01)continue;const [r,gg,b]=ramp(v);
    g.fillStyle=`rgba(${r},${gg},${b},${.25+.7*Math.pow(v,.5)})`;const y0=y(p0+M.bw),h=Math.max(1,y(p0)-y0);g.fillRect(0,y0,W-padR,h+.5)}
  // grid
  g.strokeStyle='rgba(255,255,255,.05)';g.fillStyle='#8a94a6';g.font='11px system-ui';g.textAlign='left';
  const step=nice((pmax-pmin)/10);for(let p=Math.ceil(pmin/step)*step;p<pmax;p+=step){g.beginPath();g.moveTo(0,y(p));g.lineTo(W-padR,y(p));g.stroke();g.fillText(fmtF(p),W-padR+6,y(p)+4)}
  // candles
  const bwc=cw/C.length;C.forEach((c,i)=>{const x=i*bwc+bwc/2,up=c[4]>=c[1];g.strokeStyle=g.fillStyle=up?'#26d07c':'#ff4d5e';g.beginPath();g.moveTo(x,y(c[2]));g.lineTo(x,y(c[3]));g.stroke();
    const t=y(Math.max(c[1],c[4])),b=y(Math.min(c[1],c[4]));g.fillRect(x-bwc*.35,t,bwc*.7,Math.max(1,b-t))});
  g.textAlign='center';g.fillStyle='#6b7486';for(let i=0;i<C.length;i+=24){const dt=new Date(C[i][0]);g.fillText(dt.toLocaleDateString([], {month:'short',day:'numeric'})+' '+dt.getHours()+':00',i*bwc+bwc/2,H-8)}
  // future zone + spot line
  g.fillStyle='rgba(255,210,63,.035)';g.fillRect(cw,0,fut,H-padB);g.fillStyle='#7d8798';g.fillText('PROJECTION',cw+fut/2,14);
  g.setLineDash([4,4]);g.strokeStyle='rgba(255,255,255,.55)';g.beginPath();g.moveTo(0,y(M.spot));g.lineTo(W-padR,y(M.spot));g.stroke();g.setLineDash([]);
  g.fillStyle='#fff';g.fillRect(W-padR,y(M.spot)-9,padR,18);g.fillStyle='#000';g.textAlign='left';g.font='bold 11px system-ui';g.fillText(fmtF(M.spot),W-padR+5,y(M.spot)+4);
  // path — the chosen cluster is the old-method target. With a v2 result it is drawn grey (shadow);
  // a passing call is the gold mark at target_frac of the way there.
  let px=cw,py=y(M.spot);const sx=fut/(Math.max(path.length,1)+.6);const placed={};
  const mark=(x0,y0,x1,y1,col,label,dash,kind)=>{g.strokeStyle=col;g.lineWidth=dash?1.5:2.5;g.shadowColor=dash?'transparent':col;g.shadowBlur=dash?0:8;
    if(dash)g.setLineDash([5,4]);g.beginPath();g.moveTo(x0,y0);g.lineTo(x1,y1);g.stroke();g.setLineDash([]);g.shadowBlur=0;
    const a=Math.atan2(y1-y0,x1-x0);g.fillStyle=col;g.beginPath();g.moveTo(x1,y1);g.lineTo(x1-12*Math.cos(a-.4),y1-12*Math.sin(a-.4));g.lineTo(x1-12*Math.cos(a+.4),y1-12*Math.sin(a+.4));g.closePath();g.fill();
    g.setLineDash([2,3]);g.strokeStyle=col;g.globalAlpha=dash?0.55:0.45;g.lineWidth=1;g.beginPath();g.moveTo(0,y1);g.lineTo(W-padR,y1);g.stroke();g.setLineDash([]);g.globalAlpha=1;
    g.font='bold 12px system-ui';const tw=g.measureText(label).width+12;let lyy=y1+(y1<y0?-22:8);
    // Target and shadow boxes are 17px tall. Shift the target when the two would sit under 18px apart.
    if(kind==='target'&&placed.shadow!=null&&Math.abs(placed.shadow-lyy)<18)lyy=placed.shadow+(lyy<placed.shadow?-18:18);
    if(kind==='shadow'||kind==='target')placed[kind]=lyy;
    g.fillStyle='rgba(10,12,16,.9)';g.fillRect(x1-tw/2,lyy,tw,17);g.strokeStyle=col;g.strokeRect(x1-tw/2,lyy,tw,17);g.fillStyle=col;g.textAlign='center';g.fillText(label,x1,lyy+12.5)};
  path.forEach((t,i)=>{const nx=cw+sx*(i+1),ny=y(t.price),shadow=!!(view&&fin(view.shadow)&&i===0);
    mark(px,py,nx,ny,shadow?'#7d8798':'#ffd23f',shadow?`shadow ${fmtK(t.price)}`:`${i+1}. ${fmtK(t.price)}`,shadow,shadow?'shadow':'');px=nx;py=ny});
  if(view&&view.pass){const nx=cw+sx*(fin(rec.v2.target_frac)?rec.v2.target_frac:1),ny=y(view.price);mark(cw,y(M.spot),nx,ny,'#ffd23f',`target ${fmtK(view.price)}`,false,'target')}
  const shadowBit=view&&fin(view.shadow)?`   shadow ${fmtK(view.shadow)}`:'';
  const tag=(rec&&rec.offline?' · tracker offline':'')+(rec&&rec.fallback?' · unfiltered page estimate':'');
  const head=view&&view.pass?`${fmtK(M.spot)} → ${fmtK(view.price)}${shadowBit}   (estimate)${tag}`
    :view?`No call (suppressed: ${view.why})${shadowBit}   (estimate)${tag}`
    :(path.length?fmtK(M.spot)+' → '+path.map(p=>fmtK(p.price)).join(' → '):'no clear target')+'   (estimate)'+tag;
  g.font='bold 13px system-ui';g.textAlign='left';g.fillStyle=view&&!view.pass?'#7d8798':'rgba(255,210,63,.95)';g.fillText(head,12,20);
  // legend
  const lx=12,ly=H-padB-22;for(let i=0;i<120;i++){const [r,gg,b]=ramp(i/119);g.fillStyle=`rgb(${r},${gg},${b})`;g.fillRect(lx+i,ly,1,8)}g.fillStyle='#8a94a6';g.font='10px system-ui';g.fillText('low    est. liquidation size    high',lx,ly-4);
}
function side(d,M,rec){
  $('px').textContent='$'+fmtF(M.spot);$('oi').textContent=d.hl.oi_btc!=null?fmtUsd(d.hl.oi_btc*M.spot):'n/a';
  $('fr').textContent=(d.hl.funding*100).toFixed(4)+'%';$('fr').className=d.hl.funding>=0?'ok':'bad';$('ls').textContent=d.okx_ls?d.okx_ls.at(-1)[1].toFixed(2):'n/a';
  $('up').textContent=new Date(d.ts).toLocaleTimeString();$('bw').textContent=fmtF(M.bw)+' (0.25%)';
  const view=v2View(rec),show=rec&&rec.hideShadow?(rec.path||[]):(rec&&rec.path&&rec.path.length)?rec.path:M.path;
  const note=(rec&&rec.offline?'tracker offline':'')+(rec&&rec.offline&&rec.fallback?' · ':'')+(rec&&rec.fallback?'unfiltered page estimate':'');
  const shadowSpan=view&&fin(view.shadow)?h('span',{class:'shadow'},'shadow '+fmtK(view.shadow)):null;
  if(view&&view.pass)put('path',fmtK(M.spot)+' → '+fmtK(view.price),shadowSpan?' ':'',shadowSpan,note?' ':'',note?h('span',{class:'small'},note):null);
  else if(view)put('path','No call (suppressed: '+view.why+')',shadowSpan?' ':'',shadowSpan,note?' ':'',note?h('span',{class:'small'},note):null);
  else put('path',show.length?fmtK(M.spot)+' → '+show.map(p=>fmtK(p.price)).join(' → '):'No dominant cluster',note?' ':'',note?h('span',{class:'small'},note):null);
  put('tg',show.map((t,i)=>h('div',{class:'t'},h('div',{class:'n'},i+1),
    h('div',null,h('b',{class:view&&fin(view.shadow)&&i===0?'shadow':''},'$'+fmtF(t.price)),' ',h('span',{class:'small'},`${t.side==='short'?'short liqs above':'long liqs below'} · ${(t.dist*100).toFixed(1)}% away`),
      h('div',{class:'bar'},h('i',{style:{width:Math.round(t.rel*100)+'%'}}))),
    h('div',{class:'small ar'},`${Math.round(t.rel*100)}% · ${fmtUsd(t.size)}`))));
  put('cl',M.cl.slice(0,8).sort((a,b)=>b.price-a.price).map(c=>h('tr',null,h('td',{class:c.side==='short'?'ok':'bad'},'$'+fmtF(c.price)),h('td',{class:'small'},c.side+' liqs'),h('td',null,Math.round(c.rel*100)+'%'))));
  put('src',Object.entries(d.sources).map(([k,v])=>h('tr',null,h('td',null,k),h('td',{class:String(v).startsWith('ok')?'ok':'bad'},String(v).slice(0,34)))),
    h('tr',null,h('td',null,'OI model'),h('td',{class:'small'},d.oiMode)));
}
function publishedSelection(coin){
  const all=isObj(STATS)&&isObj(STATS.selection)?STATS.selection:null;
  if(!all||!Object.prototype.hasOwnProperty.call(all,coin))return null;
  const s=all[coin];if(!isObj(s))return {offline:true};
  // null must not become 0. A coin with no call has no price to print.
  const shadow=fin(s.shadow)&&s.shadow>0?s.shadow:null;
  const v2_target=fin(s.v2_target)&&s.v2_target>0?s.v2_target:null;
  const ts=fin(s.ts)&&s.ts>0?s.ts:null;
  if(ts==null||Date.now()-ts>15*60*1000)return {offline:true};
  const pass=!!s.pass&&v2_target!=null;
  const why=typeof s.why==='string'&&s.why?s.why.slice(0,200):(pass?'ok':'no call');
  const conf=s.conf==='strong'||s.conf==='moderate'||s.conf==='weak'?s.conf:null;
  return {offline:false,shadow,target:pass?v2_target:null,pass,why,dir:s.dir==='down'?'down':'up',conf,
    rel:fin(s.rel)&&s.rel>=0?s.rel:null,m4:fin(s.m4)?s.m4:null,ts}}
function sameCluster(spot,local,pub){
  if(!local||!local.t||!fin(local.t.price)||!pub||!pub.pass||!fin(pub.target))return false;
  if(!!local.up!==(pub.dir==='up'))return false;
  const frac=fin(P.v2&&P.v2.target_frac)?P.v2.target_frac:.8;
  const expect=spot+(local.up?1:-1)*frac*Math.abs(local.t.price-spot);
  return fin(expect)&&Math.abs(expect-pub.target)/pub.target<=.0025}
function call(coin,d,M){
  const raw=M.path,spot=M.spot,C=d.candles,K='liqmap_v1_'+coin;let st={};try{st=JSON.parse(localStorage.getItem(K))||{}}catch(e){}
  if(typeof st!=='object'||Array.isArray(st))st={};if(!Array.isArray(st.hits))st.hits=[];st.hits=st.hits.filter(x=>x&&fin(x.p)&&fin(x.t));
  if(fin(st.target)&&fin(st.since)&&fin(st.from)){const after=C.filter(c=>c[0]+3600e3>st.since);const up=st.target>st.from;
    if(after.some(c=>up?c[2]>=st.target:c[3]<=st.target)||(up?spot>=st.target:spot<=st.target)){st.hits=[{p:st.target,t:Date.now()},...st.hits].slice(0,5);st.target=null}}
  // A fresh selection is the tracker's call, so pass/suppress match the tracker. Missing or older than 15 min: unfiltered page estimate.
  const path=raw,r={coin,spot,hits:st.hits,path,f:d.hl.funding,fromSelection:false,hideShadow:false,offline:false,fallback:false,agree:false};
  const cc=callCore(C,{spot,path},r.f);
  if(cc){Object.assign(r,cc);r.v2=callV2(C,{spot,cl:M.cl},cc)}
  const local={up:!!r.up,t:r.t,path:(r.path||[]).slice(),conf:r.conf,m4:r.m4};
  const pub=publishedSelection(coin),frac=fin(P.v2&&P.v2.target_frac)?P.v2.target_frac:.8;
  const track=px=>{if(fin(px)&&(!fin(st.target)||Math.abs(st.target-px)/px>.0025)){st.target=px;st.from=spot;st.since=Date.now()}};
  if(pub&&pub.offline){r.offline=true;r.fallback=true;
    const live=r.v2&&r.v2.pass&&fin(r.v2.target)?r.v2.target:null;if(live)track(live)}
  else if(pub){r.fromSelection=true;r.hideShadow=true;r.up=pub.dir==='up';r.agree=sameCluster(spot,local,pub);
    if(pub.pass&&pub.target){const dist=spot?Math.abs(pub.target-spot)/spot:0;
      r.t={price:pub.target,rel:r.agree&&pub.rel!=null?pub.rel:0,size:0,dist,side:r.up?'short':'long'};
      r.pct=spot?(pub.target-spot)/spot*100:0;r.dd=pub.target-spot;
      r.v2={pass:true,why:pub.why,target:pub.target,target_frac:frac};
      r.path=[{price:pub.target,rel:r.t.rel,size:0,dist,side:r.t.side}];
      if(r.agree&&local.path[1])r.path.push(local.path[1]);
      r.conf=r.agree?pub.conf:null;r.m4=r.agree&&fin(pub.m4)?pub.m4:null;track(pub.target)}
    else{r.t=null;r.path=[];r.v2={pass:false,why:pub.why,target:null,target_frac:frac};r.conf=null;r.m4=null;r.agree=false}}
  else{r.fallback=true;const live=r.v2&&r.v2.pass&&fin(r.v2.target)?r.v2.target:null;if(live)track(live)}
  try{localStorage.setItem(K,JSON.stringify(st))}catch(e){}
  return r;
}
function banner(r){
  const bn=$('bn'),t=r.t,path=r.path||[],view=v2View(r);
  const bits=[];if(r.offline)bits.push('tracker offline');if(r.fallback)bits.push('unfiltered page estimate');
  const note=bits.length?h('span',{class:'small'},bits.join(' · ')):null;
  const confOk=r.conf==='strong'||r.conf==='moderate'||r.conf==='weak';
  const showDetail=!r.fromSelection||r.agree;
  if(r.fromSelection&&view&&!view.pass){bn.className='banner';$('ba').textContent='↔';
    put('bc',`NOW (${r.coin}): No call (suppressed: ${view.why})`);
    put('bs',h('span',{class:'amber'},'Estimate, not financial advice'),note?' · ':null,note);
    $('bf').className='conf weak';$('bf').textContent='no call'}
  else if(!t){bn.className='banner';$('ba').textContent='↔';$('bc').textContent=`NOW (${r.coin}): No dominant liquidation cluster — chop / wait`;
    put('bs',h('span',{class:'amber'},'Estimate, not financial advice'),note?' · ':null,note);
    $('bf').className='conf weak';$('bf').textContent='weak'}
  else if(!view||!view.pass){const why=(view&&view.why)||'no call';
    bn.className='banner';$('ba').textContent='↔';
    put('bc',`NOW (${r.coin}): No call (suppressed: ${why})`);
    put('bs',view&&fin(view.shadow)?h('span',{class:'shadow'},`Shadow (old target) $${fmtF(view.shadow)}`):null,view&&fin(view.shadow)?` (${r.pct>0?'+':''}${r.pct.toFixed(1)}%) · `:null,h('span',{class:'amber'},'Estimate, not financial advice'),note?' · ':null,note);
    $('bf').className='conf weak';$('bf').textContent='no call';}
  else{const up=view.up,px=view.price,dd=view.dd,pct=view.pct;
    const nx=showDetail&&path[1]?[`, then ${path[1].price>px?'UP':'DOWN'} to `,h('b',{class:path[1].price>px?'ok':'bad'},'$'+fmtK(path[1].price))]:[];
    bn.className='banner '+(up?'up':'dn');$('ba').textContent=up?'▲':'▼';
    put('bc',`NOW (${r.coin}): ${up?'Model leans UP, toward shorts at':'Model leans DOWN, toward longs at'} `,h('b',null,'$'+fmtK(px)),` (${pct>0?'+':''}${pct.toFixed(1)}%)`,nx);
    const detail=[];
    if(view.frac!=null)detail.push(` · ${view.frac}% of cluster distance`);
    if(showDetail&&t&&fin(t.rel))detail.push(` · cluster strength ${Math.round(t.rel*100)}%`);
    if(showDetail&&fin(r.m4))detail.push(` · 4h momentum ${(r.m4*100).toFixed(2)}%`);
    put('bs','Next target ',h('b',null,'$'+fmtF(px)),' · distance ',h('b',null,`${dd>0?'+':'−'}$${fmtF(Math.abs(dd))}`),' (',h('b',null,pct.toFixed(2)+'%'),')',
      detail,` · funding ${(r.f*100).toFixed(4)}%/h · `,h('span',{class:'amber'},'Estimate, not financial advice'),
      fin(view.shadow)?h('br'):null,fin(view.shadow)?h('span',{class:'shadow'},`Shadow (old target) $${fmtF(view.shadow)}`):null,
      note?h('br'):null,note);
    if(confOk){$('bf').className='conf '+r.conf;$('bf').textContent=r.conf}
    else{$('bf').className='conf weak';$('bf').textContent='—'}}
  const x=r.hits[0];
  if(x)put('bh','Last target hit: ',h('b',null,'$'+fmtF(x.p)),h('br'),new Date(x.t).toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}));
  else put('bh','No target hit yet',h('br'),h('span',{class:'tiny'},'tracked in this browser'));
}
const CALLS={};
const pc=v=>!fin(v)?'—':(v*100).toFixed(0)+'%';
const nstr=v=>fin(v)?String(v):'—';
const isObj=x=>x&&typeof x==='object';
function statCells(c){const s=isObj(STATS)&&isObj(STATS.coins)&&STATS.coins[c];if(!isObj(s))return [h('td',{class:'small'},'—'),h('td',{class:'small'},'—')];
  const f=x=>isObj(x)&&fin(x.n)&&x.n?[pc(x.hit_rate),' ',h('span',{class:'small'},`(${x.n}${fin(x.avg_tth_h)?' · '+x.avg_tth_h.toFixed(1)+'h':''})`)]:[h('span',{class:'small'},'—')];
  return [h('td',null,f(s.backtest)),h('td',null,f(s.live))]}
function track(){const el=$('trk');if(!isObj(STATS)){put('trk',h('div',{class:'small'},'No stats.json yet — run tracker.py / optimize.py.'));$('pv').textContent='v'+nstr(P.version)+' (params.json)';return}
  $('pv').textContent='v'+nstr(STATS.params_version);const b=isObj(STATS.backtest)?STATS.backtest:null,o=b&&isObj(b.overall)?b.overall:null,c=(isObj(STATS.coins)&&isObj(STATS.coins[COIN]))?STATS.coins[COIN]:{};
  const row=(k,x)=>isObj(x)&&fin(x.n)&&x.n?h('tr',null,h('td',null,k),h('td',null,pc(x.hit_rate)),h('td',{class:'small'},'n='+x.n),h('td',{class:'small'},fin(x.avg_tth_h)?x.avg_tth_h.toFixed(1)+'h':'—'),h('td',{class:'small'},fin(x.expectancy_r)?(x.expectancy_r>=0?'+':'')+x.expectancy_r.toFixed(2)+'R':''))
    :h('tr',null,h('td',null,k),h('td',{colspan:4,class:'small'},'no samples yet'));
  const all=o&&isObj(o.all)?o.all:null;
  const cal=x=>x&&isObj(x.by_conf)?['strong','moderate','weak'].map(k=>{const q=isObj(x.by_conf[k])?x.by_conf[k]:{};return h('tr',null,h('td',null,h('span',{class:'conf sm '+k},k)),h('td',null,pc(q.hit_rate)),h('td',{class:'small',colspan:3},'n='+nstr(q.n)))}):[];
  const reason=b&&isObj(b.decision)&&typeof b.decision.reason==='string'?b.decision.reason.slice(0,90):'';
  el.replaceChildren(
    h('table',null,row(COIN+' backtest',c.backtest),row(COIN+' live',c.live),o?row('All · validation',o.val):null,
      all&&fin(all.mirror_hit_rate)?h('tr',null,h('td',{class:'small'},'Random baseline'),h('td',null,pc(all.mirror_hit_rate)),h('td',{class:'small',colspan:3},'mirror (same dist, opposite dir)')):null),
    h('div',{class:'small cal-h'},'Confidence calibration (backtest, all coins)'),h('table',null,cal(all)),
    h('div',{class:'small mt6'},'Hit = target before 1.5× adverse move, ≤24h. '+(b?`Optimizer: ${b.changed?'params updated':'params kept'} — ${reason}`:'')))}
function overview(){
  put('tabs',COINS.map(c=>{const r=CALLS[c],view=v2View(r);const a=view&&view.pass?(view.up?h('i',{class:'ok'},'▲'):h('i',{class:'bad'},'▼')):h('i',{class:'mut'},'·');
    return h('button',{data:{c},class:c===COIN?'on':''},a,c,h('span',{class:'small'},r?'$'+fmtF(r.spot):''))}));
  const head=h('tr',null,['Coin','Price','Move','Next target','Distance','Then','Confidence','Last hit'].map(x=>h('th',null,x)),
    h('th',{title:'Backtest: hit target before 1.5x adverse move within 24h'},'Backtest hit (n)'),h('th',null,'Live hit (n)'));
  put('ovt',head,COINS.map(c=>{const r=CALLS[c];if(!r)return h('tr',{class:'r',data:{c}},h('td',null,h('b',null,c)),h('td',{colspan:9,class:'small'},'no data'));
    const view=v2View(r),confOk=r.conf==='strong'||r.conf==='moderate'||r.conf==='weak';
    const showThen=(!r.fromSelection||r.agree)&&r.path&&r.path[1];
    const tag=(r.offline?'tracker offline':'')+(r.offline&&r.fallback?' · ':'')+(r.fallback?'unfiltered page estimate':'');
    const mid=view&&!view.pass?[h('td',{class:'small'},'No call (suppressed: '+view.why+(tag?' · '+tag:'')+')'),
        fin(view.shadow)?h('td',{class:'shadow'},'$'+fmtF(view.shadow)):h('td',{class:'small'},'—'),
        fin(view.shadow)?h('td',{class:'shadow'},`${r.pct>0?'+':''}${r.pct.toFixed(2)}%`):h('td',{class:'small'},'—'),
        fin(view.shadow)?h('td',{class:'small shadow'},'shadow'):h('td',{class:'small'},tag||'—'),
        h('td',null,h('span',{class:'conf sm2 weak'},'no call'))]
      :view&&view.pass?[h('td',{class:view.up?'u':'d'},h('b',null,view.up?'▲ leans UP · shorts':'▼ leans DOWN · longs')),
        h('td',null,'$'+fmtF(view.price),fin(view.shadow)?h('div',{class:'small shadow'},'shadow $'+fmtF(view.shadow)):(tag?h('div',{class:'small'},tag):null)),
        h('td',{class:view.up?'u':'d'},`${view.pct>0?'+':''}${view.pct.toFixed(2)}%`),
        h('td',{class:'small'},showThen?'→ $'+fmtK(r.path[1].price):'—'),
        h('td',null,confOk?h('span',{class:'conf sm2 '+r.conf},r.conf):h('span',{class:'small'},'—'))]
      :[h('td',{colspan:5,class:'small'},(tag?tag+' — ':'')+'no dominant cluster')];
    return h('tr',{class:'r'+(c===COIN?' on':''),data:{c}},h('td',null,h('b',null,c)),h('td',null,'$'+fmtF(r.spot)),mid,
      h('td',{class:'small'},r.hits[0]?'$'+fmtF(r.hits[0].p)+' '+new Date(r.hits[0].t).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}):'—'),statCells(c))}));
}
document.addEventListener('click',e=>{const el=e.target.closest('[data-c]');if(!el)return;const c=el.dataset.c;if(!COIN_RE.test(c))return;COIN=c;location.hash=COIN;tick()});

let last;async function tick(){try{await loadSide();const d=await load(COIN);const M=model(d);const rec=call(COIN,d,M);last=[d,M,rec];window.__model=M;$('cn').textContent=COIN;draw(d,M,rec);side(d,M,rec);CALLS[COIN]=rec;banner(rec);
  if(ALL&&ALL.coins)for(const c of COINS){if(c===COIN||!ALL.coins[c])continue;try{CALLS[c]=call(c,ALL.coins[c],model(ALL.coins[c]))}catch(e){}}
  overview();try{track()}catch(e){put('trk',h('div',{class:'small'},'stats.json unreadable'))}window.__calls=CALLS}catch(e){$('path').textContent='Data error: '+String(e&&e.message||e).slice(0,200)}}
tick();setInterval(tick,60e3);addEventListener('resize',()=>last&&draw(...last));
