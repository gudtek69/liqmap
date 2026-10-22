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
function validParams(q){return q&&typeof q==='object'&&Array.isArray(q.lev)&&q.lev.length&&q.lev.every(r=>isRow(r,2)&&r[0]>0)&&PNUM.every(k=>fin(q[k]))&&q.bin_pct>0&&q.range>0&&q.conf&&CNUM.every(k=>fin(q.conf[k]))}
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
let P={"version":1,"lev":[[5,.14],[10,.30],[25,.26],[50,.18],[100,.12]],"mm":.004,"decay":.985,"bin_pct":.0025,"range":.15,"smooth":3,"min_dist":.002,"max_dist":.10,"ls_weight":.6,"candle_tilt":.08,"opp_min_rel":.15,"third_min_ratio":.5,"next_min_ratio":.4,"conf":{"m4_agree":.2,"m4_disagree":-.15,"m1_agree":.1,"m1_disagree":-.05,"funding":.1,"funding_min":2e-6,"strong":.85,"moderate":.45}};
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
// v2 rules (2026-10-22, mirrors tracker/liqmodel.py call_v2): target at 80% of the cluster distance; suppress if the cluster is
// farther than 3x hourly ATR(14) (completed candles only) or the opposite cluster is < 1.5x as far. 24h horizon unchanged.
function hourlyAtr(C,n=14){const now=Date.now(),d=C.filter(c=>c[0]+3600e3<=now);if(d.length<n+1)return null;let s=0;
  for(let i=d.length-n;i<d.length;i++){const b=d[i],a=d[i-1];s+=Math.max(b[2]-b[3],Math.abs(b[2]-a[4]),Math.abs(b[3]-a[4]))}return s/n}
function callV2(C,M,cc){if(!cc)return null;const V=Object.assign({target_frac:.8,max_datr:3,min_asym:1.5},P.v2||{}),spot=M.spot,D=Math.abs(cc.t.price-spot);
  const opp=(M.cl||[]).filter(c=>cc.up?c.price<spot:c.price>spot).sort((a,b)=>b.size/Math.sqrt(b.dist*100)-a.size/Math.sqrt(a.dist*100))[0];
  const atr=hourlyAtr(C),datr=atr?D/atr:null,asym=opp&&D?Math.abs(opp.price-spot)/D:null,why=[];
  if(datr===null)why.push('no ATR');else if(datr>V.max_datr)why.push(`${datr.toFixed(1)}× ATR > ${V.max_datr}`);
  if(asym===null)why.push('no opposite cluster');else if(asym<V.min_asym)why.push(`opposite only ${asym.toFixed(2)}× as far`);
  return {pass:!why.length,why:why.join(', '),target:spot+(cc.up?1:-1)*V.target_frac*D,datr,asym}}
/*MODEL-END*/

function draw(d,M){
  const cv=$('c'),dpr=devicePixelRatio||1,W=cv.clientWidth,H=cv.clientHeight;cv.width=W*dpr;cv.height=H*dpr;const g=cv.getContext('2d');g.scale(dpr,dpr);
  const C=d.candles.slice(-96),padR=74,padB=26,fut=Math.round((W-padR)*.2),cw=W-padR-fut,histW=0;
  const lo=M.spot*(1-RANGE),hi=M.spot*(1+RANGE);let pmin=Math.min(...C.map(c=>c[3]),...M.path.map(p=>p.price)),pmax=Math.max(...C.map(c=>c[2]),...M.path.map(p=>p.price));
  const pad=(pmax-pmin)*.25;pmin=Math.max(lo,pmin-pad);pmax=Math.min(hi,pmax+pad);
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
  // path
  let px=cw,py=y(M.spot);const sx=fut/(M.path.length+.6);
  M.path.forEach((t,i)=>{const nx=cw+sx*(i+1),ny=y(t.price);g.strokeStyle='#ffd23f';g.lineWidth=2.5;g.shadowColor='#ffd23f';g.shadowBlur=8;g.beginPath();g.moveTo(px,py);g.lineTo(nx,ny);g.stroke();g.shadowBlur=0;
    const a=Math.atan2(ny-py,nx-px);g.fillStyle='#ffd23f';g.beginPath();g.moveTo(nx,ny);g.lineTo(nx-12*Math.cos(a-.4),ny-12*Math.sin(a-.4));g.lineTo(nx-12*Math.cos(a+.4),ny-12*Math.sin(a+.4));g.closePath();g.fill();
    g.setLineDash([2,3]);g.strokeStyle='rgba(255,210,63,.5)';g.lineWidth=1;g.beginPath();g.moveTo(0,ny);g.lineTo(W-padR,ny);g.stroke();g.setLineDash([]);
    const lbl=`${i+1}. ${fmtK(t.price)}`;g.font='bold 12px system-ui';const tw=g.measureText(lbl).width+12,lyy=ny+(t.price>M.spot?-22:8);
    g.fillStyle='rgba(10,12,16,.9)';g.fillRect(nx-tw/2,lyy,tw,17);g.strokeStyle='#ffd23f';g.strokeRect(nx-tw/2,lyy,tw,17);g.fillStyle='#ffd23f';g.textAlign='center';g.fillText(lbl,nx,lyy+12.5);
    px=nx;py=ny;g.lineWidth=1});
  g.font='bold 13px system-ui';g.textAlign='left';g.fillStyle='rgba(255,210,63,.95)';g.fillText((M.path.length?fmtK(M.spot)+' → '+M.path.map(p=>fmtK(p.price)).join(' → '):'no clear target')+'   (estimate)',12,20);
  // legend
  const lx=12,ly=H-padB-22;for(let i=0;i<120;i++){const [r,gg,b]=ramp(i/119);g.fillStyle=`rgb(${r},${gg},${b})`;g.fillRect(lx+i,ly,1,8)}g.fillStyle='#8a94a6';g.font='10px system-ui';g.fillText('low    est. liquidation size    high',lx,ly-4);
}
function side(d,M){
  $('px').textContent='$'+fmtF(M.spot);$('oi').textContent=d.hl.oi_btc!=null?fmtUsd(d.hl.oi_btc*M.spot):'n/a';
  $('fr').textContent=(d.hl.funding*100).toFixed(4)+'%';$('fr').className=d.hl.funding>=0?'ok':'bad';$('ls').textContent=d.okx_ls?d.okx_ls.at(-1)[1].toFixed(2):'n/a';
  $('up').textContent=new Date(d.ts).toLocaleTimeString();$('bw').textContent=fmtF(M.bw)+' (0.25%)';
  $('path').textContent=M.path.length?fmtK(M.spot)+' → '+M.path.map(p=>fmtK(p.price)).join(' → '):'No dominant cluster';
  put('tg',M.path.map((t,i)=>h('div',{class:'t'},h('div',{class:'n'},i+1),
    h('div',null,h('b',null,'$'+fmtF(t.price)),' ',h('span',{class:'small'},`${t.side==='short'?'short liqs above':'long liqs below'} · ${(t.dist*100).toFixed(1)}% away`),
      h('div',{class:'bar'},h('i',{style:{width:Math.round(t.rel*100)+'%'}}))),
    h('div',{class:'small ar'},`${Math.round(t.rel*100)}% · ${fmtUsd(t.size)}`))));
  put('cl',M.cl.slice(0,8).sort((a,b)=>b.price-a.price).map(c=>h('tr',null,h('td',{class:c.side==='short'?'ok':'bad'},'$'+fmtF(c.price)),h('td',{class:'small'},c.side+' liqs'),h('td',null,Math.round(c.rel*100)+'%'))));
  put('src',Object.entries(d.sources).map(([k,v])=>h('tr',null,h('td',null,k),h('td',{class:String(v).startsWith('ok')?'ok':'bad'},String(v).slice(0,34)))),
    h('tr',null,h('td',null,'OI model'),h('td',{class:'small'},d.oiMode)));
}
function call(coin,d,M){
  const P=M.path,spot=M.spot,C=d.candles,K='liqmap_v1_'+coin;let st={};try{st=JSON.parse(localStorage.getItem(K))||{}}catch(e){}
  if(typeof st!=='object'||Array.isArray(st))st={};if(!Array.isArray(st.hits))st.hits=[];st.hits=st.hits.filter(x=>x&&fin(x.p)&&fin(x.t));
  if(fin(st.target)&&fin(st.since)&&fin(st.from)){const after=C.filter(c=>c[0]+3600e3>st.since);const up=st.target>st.from;
    if(after.some(c=>up?c[2]>=st.target:c[3]<=st.target)||(up?spot>=st.target:spot<=st.target)){st.hits=[{p:st.target,t:Date.now()},...st.hits].slice(0,5);st.target=null}}
  const hits=st.hits;const path=P.filter(t=>!hits.some(x=>Date.now()-x.t<12*3600e3&&Math.abs(x.p-t.price)/t.price<.0025));
  const r={coin,spot,hits,path,f:d.hl.funding};
  const cc=callCore(C,{spot,path},r.f);
  if(cc){Object.assign(r,cc);r.v2=callV2(C,{spot,cl:M.cl},cc);if(!fin(st.target)||Math.abs(st.target-cc.t.price)/cc.t.price>.0025){st.target=cc.t.price;st.from=spot;st.since=Date.now()}}
  try{localStorage.setItem(K,JSON.stringify(st))}catch(e){}
  return r;
}
function banner(r){
  const bn=$('bn'),t=r.t,path=r.path;
  if(!t){bn.className='banner';$('ba').textContent='↔';$('bc').textContent=`NOW (${r.coin}): No dominant liquidation cluster — chop / wait`;$('bs').textContent='Estimate, not financial advice.';$('bf').className='conf weak';$('bf').textContent='weak'}
  else{const up=r.up;const nx=path[1]?[`, then ${path[1].price>t.price?'UP':'DOWN'} to `,h('b',{class:path[1].price>t.price?'ok':'bad'},'$'+fmtK(path[1].price))]:[];
    bn.className='banner '+(up?'up':'dn');$('ba').textContent=up?'▲':'▼';
    put('bc',`NOW (${r.coin}): ${up?'Model leans UP, toward shorts at':'Model leans DOWN, toward longs at'} `,h('b',null,'$'+fmtK(t.price)),` (${r.pct>0?'+':''}${r.pct.toFixed(1)}%)`,nx);
    put('bs','Next target ',h('b',null,'$'+fmtF(t.price)),' · distance ',h('b',null,`${r.dd>0?'+':'−'}$${fmtF(Math.abs(r.dd))}`),' (',h('b',null,r.pct.toFixed(2)+'%'),
      `) · cluster strength ${Math.round(t.rel*100)}% · 4h momentum ${(r.m4*100).toFixed(2)}% · funding ${(r.f*100).toFixed(4)}%/h · `,h('span',{class:'amber'},'Estimate, not financial advice'));
    if(r.v2)put('bs',...Array.from($('bs').childNodes),h('br'),r.v2.pass?['v2 call: target ',h('b',null,'$'+fmtF(r.v2.target)),' (80% of cluster distance; old target shown above as shadow)']:['v2: ',h('b',{class:'amber'},'no call'),` — suppressed (${r.v2.why}); old-method target above is shadow only`]);
    $('bf').className='conf '+r.conf;$('bf').textContent=r.conf;}
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
  put('tabs',COINS.map(c=>{const r=CALLS[c];const a=r&&r.t?(r.up?h('i',{class:'ok'},'▲'):h('i',{class:'bad'},'▼')):h('i',{class:'mut'},'·');
    return h('button',{data:{c},class:c===COIN?'on':''},a,c,h('span',{class:'small'},r?'$'+fmtF(r.spot):''))}));
  const head=h('tr',null,['Coin','Price','Move','Next target','Distance','Then','Confidence','Last hit'].map(x=>h('th',null,x)),
    h('th',{title:'Backtest: hit target before 1.5x adverse move within 24h'},'Backtest hit (n)'),h('th',null,'Live hit (n)'));
  put('ovt',head,COINS.map(c=>{const r=CALLS[c];if(!r)return h('tr',{class:'r',data:{c}},h('td',null,h('b',null,c)),h('td',{colspan:9,class:'small'},'no data'));
    const t=r.t;
    const mid=t?[h('td',{class:r.up?'u':'d'},h('b',null,r.up?'▲ leans UP · shorts':'▼ leans DOWN · longs')),h('td',null,'$'+fmtF(t.price)),h('td',{class:r.up?'u':'d'},`${r.pct>0?'+':''}${r.pct.toFixed(2)}%`),
      h('td',{class:'small'},r.path[1]?'→ $'+fmtK(r.path[1].price):'—'),h('td',null,h('span',{class:'conf sm2 '+r.conf},r.conf))]:[h('td',{colspan:5,class:'small'},'no dominant cluster')];
    return h('tr',{class:'r'+(c===COIN?' on':''),data:{c}},h('td',null,h('b',null,c)),h('td',null,'$'+fmtF(r.spot)),mid,
      h('td',{class:'small'},r.hits[0]?'$'+fmtF(r.hits[0].p)+' '+new Date(r.hits[0].t).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}):'—'),statCells(c))}));
}
document.addEventListener('click',e=>{const el=e.target.closest('[data-c]');if(!el)return;const c=el.dataset.c;if(!COIN_RE.test(c))return;COIN=c;location.hash=COIN;tick()});

let last;async function tick(){try{await loadSide();const d=await load(COIN);const M=model(d);last=[d,M];window.__model=M;$('cn').textContent=COIN;draw(d,M);side(d,M);CALLS[COIN]=call(COIN,d,M);banner(CALLS[COIN]);
  if(ALL&&ALL.coins)for(const c of COINS){if(c===COIN||!ALL.coins[c])continue;try{CALLS[c]=call(c,ALL.coins[c],model(ALL.coins[c]))}catch(e){}}
  overview();try{track()}catch(e){put('trk',h('div',{class:'small'},'stats.json unreadable'))}window.__calls=CALLS}catch(e){$('path').textContent='Data error: '+String(e&&e.message||e).slice(0,200)}}
tick();setInterval(tick,60e3);addEventListener('resize',()=>last&&draw(...last));
