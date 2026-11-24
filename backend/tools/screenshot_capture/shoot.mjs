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
if (MODE === 'all') {
  await shot('01_overview');
  await nav('Traffic Map'); await shot('02_traffic_map');
  await nav('Traffic Prediction'); await shot('03_prediction');
  await nav('Forecasting Analytics'); await shot('04_forecasting');
  await nav('Route Planner'); await clickBtn('Find best route'); await shot('05_route_planner');
  await nav('Incident Lab'); await clickBtn('Add incident'); await shot('06_incident_lab');
  await nav('Weather'); await clickBtn('Fetch current weather'); await shot('07_weather');
  await nav('AI Copilot');
  await page.waitForSelector('[data-testid="stChatInputTextArea"]'); await page.type('[data-testid="stChatInputTextArea"]', 'Which locations are likely to be congested next?'); await page.keyboard.press('Enter'); await settle(6000);
  await shot('08_copilot');
  await nav('Model Evaluation'); await shot('09_model_evaluation');
} else {
  await shot('01_overview'); await nav('Traffic Map'); await shot('02_traffic_map');
  await nav('Route Planner'); await clickBtn('Find best route'); await shot('03_route_planner');
}
console.log('JS PAGE ERRORS:', jsErrors.length, jsErrors.slice(0,3));
console.log('library assets served locally:', served.length, '| external requests blocked:', [...blocked].slice(0,8));
await browser.close();
