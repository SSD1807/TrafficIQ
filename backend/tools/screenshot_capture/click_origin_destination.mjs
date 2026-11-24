import chromium from '@sparticuz/chromium'; import puppeteer from 'puppeteer-core'; import fs from 'fs'; import path from 'path';
const [,, BASE, OUTDIR, PREFIX, MODE] = process.argv;   // MODE: all | overview-map
fs.mkdirSync(OUTDIR, {recursive:true});
const NM = '/tmp/shot/node_modules'; const blocked = new Set(); const served = [];
function localFor(url){
  let m;
  if ((m = url.match(/^https:\/\/cdn\.jsdelivr\.net\/npm\/((?:@[^/]+\/)?[^@/]+)@[^/]+\/(.+)$/))) {
    const pkg = m[1], rest = m[2]; const cands = [path.join(NM,pkg,rest), path.join(NM,pkg,rest.replace('.min.js','.js')), path.join(NM,pkg,'dist',rest)];
    return cands.find(fs.existsSync);
  }
  if ((m = url.match(/cdnjs\.cloudflare\.com\/ajax\/libs\/Leaflet\.awesome-markers\/[^/]+\/(.+)$/))) { const f=path.join(NM,'leaflet.awesome-markers','dist',m[1]); return fs.existsSync(f)?f:undefined; }
  if ((m = url.match(/cdnjs\.cloudflare\.com\/ajax\/libs\/leaflet-locatecontrol\/[^/]+\/(.+)$/))) { const f=path.join(NM,'leaflet.locatecontrol','dist',m[1]); return fs.existsSync(f)?f:undefined; }
  if (url.startsWith('https://code.jquery.com/jquery-3.7.1.min.js')) return path.join(NM,'jquery/dist/jquery.min.js');
  return undefined;
}
const ctype = f => f.endsWith('.css')?'text/css':f.endsWith('.js')?'application/javascript':f.endsWith('.woff2')?'font/woff2':f.endsWith('.png')?'image/png':f.endsWith('.svg')?'image/svg+xml':'application/octet-stream';
const browser = await puppeteer.launch({args:[...chromium.args,'--no-sandbox','--lang=en-US'], executablePath: await chromium.executablePath(), headless:'shell'});
const page = await browser.newPage(); await page.setViewport({width:1440,height:1000,deviceScaleFactor:1});
await page.setExtraHTTPHeaders({'Accept-Language':'en-US,en'}); await page.setRequestInterception(true);
page.on('request', req => {
  const url = req.url();
  if (url.startsWith(BASE) || url.startsWith('data:') || url.startsWith('blob:') || url.startsWith('about:')) return req.continue();
  const f = localFor(url);
  if (f) { served.push(url); return req.respond({status:200, contentType:ctype(f), body:fs.readFileSync(f)}); }
  if (/basemaps\.cartocdn\.com|tile\.openstreetmap\.org/.test(url)) return req.respond({status:200, contentType:'image/png', body:fs.readFileSync(url.includes('dark_all')?'/tmp/shot/tile_dark.png':'/tmp/shot/tile_light.png')});
  if (/glyphicons\.css/.test(url)) return req.respond({status:200, contentType:'text/css', body:''});
  blocked.add(url); return req.abort();
});
const jsErrors=[]; page.on('pageerror', e=>jsErrors.push(String(e).slice(0,200)));
const sleep = ms => new Promise(r=>setTimeout(r,ms));
async function settle(extra=2500){ await sleep(extra); for (let i=0;i<60;i++){ const busy = await page.evaluate(()=>!!document.querySelector('[data-testid="stStatusWidget"]')); if(!busy) break; await sleep(500);} await sleep(1200); }
async function nav(name){ await page.evaluate(n=>{ const l=[...document.querySelectorAll('[data-testid="stSidebar"] label')].find(x=>x.innerText.trim()===n); if(!l) throw new Error('nav not found '+n); l.click(); }, name); await settle(); }
async function clickBtn(text){ const ok = await page.evaluate(t=>{ const b=[...document.querySelectorAll('button')].find(x=>x.innerText.trim().includes(t)); if(!b) return false; b.scrollIntoView(); b.click(); return true; }, text); if(!ok) console.log('BUTTON NOT FOUND', text); await settle(3500); return ok; }
async function shot(name){
  const h = await page.evaluate(()=>{ const m=document.querySelector('[data-testid="stMain"]')||document.body; return m.scrollHeight; });
  await page.setViewport({width:1440,height:Math.min(Math.max(h+30,900),3600),deviceScaleFactor:1}); await sleep(1200);
  const f = path.join(OUTDIR, `${PREFIX}_${name}.png`); await page.screenshot({path:f}); console.log('saved', f, 'height', h);
  await page.setViewport({width:1440,height:1000,deviceScaleFactor:1});
}
await page.goto(BASE, {waitUntil:'networkidle2', timeout:120000}); await settle(6000);

await nav('Traffic Map'); await sleep(3000);
async function setMode(label){ await page.evaluate(t=>{ const l=[...document.querySelectorAll('[data-testid="stMain"] label')].find(x=>x.innerText.trim()===t); if(!l) throw new Error('no '+t); l.click(); }, label); await settle(2500); }
async function clickMap(fx, fy){ const box = await page.evaluate(()=>{ const f=document.querySelector('[data-testid="stMain"] iframe'); f.scrollIntoView({block:'center'}); const r=f.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height}; }); await sleep(600);
  const b2 = await page.evaluate(()=>{ const r=document.querySelector('[data-testid="stMain"] iframe').getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height}; });
  await page.mouse.click(b2.x + b2.w*fx, b2.y + b2.h*fy); await settle(3500); }
const txt = ()=>page.evaluate(()=>[...document.querySelectorAll('[data-testid="stMain"] p')].map(p=>p.innerText).filter(t=>/Origin:|Destination:/.test(t)));
await setMode('Origin'); await clickMap(0.30, 0.55); console.log('AFTER ORIGIN CLICK  :', JSON.stringify(await txt()));
await setMode('Destination'); await clickMap(0.75, 0.30); console.log('AFTER DEST CLICK    :', JSON.stringify(await txt()));
await clickBtn('Find routes'); await sleep(2000);
const table = await page.evaluate(()=>[...document.querySelectorAll('[data-testid="stMain"] [data-testid="stDataFrame"]')].length);
const res = await page.evaluate(()=>document.body.innerText.includes('Times use a simulated congestion scenario'));
console.log('ROUTE RESULT SHOWN  :', res, '| dataframes on page:', table);
await shot('map_click_route');
console.log('JS PAGE ERRORS:', jsErrors.length, jsErrors.slice(0,3));
await browser.close();
