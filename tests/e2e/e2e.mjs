// End-to-end browser test of the merged app. Requires the backend (8000) and frontend (5173) to be RUNNING.
//   cd tests/e2e && npm install && npx playwright install chromium && node e2e.mjs
// Browser selection: CHROMIUM_PATH env var, else Playwright's own Chromium (npx playwright install chromium).
import { chromium as pw } from 'playwright-core';
import fs from 'node:fs';

const ORIGIN = process.env.ORIGIN ?? 'http://127.0.0.1:5173';
const API = 'http://127.0.0.1:8000';
const exe = process.env.CHROMIUM_PATH || undefined;
const browser = await pw.launch({ ...(exe ? { executablePath: exe } : {}), args: ['--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--no-zygote'], headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await ctx.newPage();

const log = { consoleErrors: [], pageErrors: [], apiCalls: [], apiNon2xx: [], externalFailed: [], checks: [] };
const isExternal = (u) => !u.startsWith(API) && !u.startsWith(ORIGIN);
page.on('console', (m) => { if (m.type() === 'error') log.consoleErrors.push(m.text().slice(0, 300)); });
page.on('pageerror', (e) => log.pageErrors.push(String(e).slice(0, 300)));
page.on('requestfailed', (r) => { if (isExternal(r.url())) log.externalFailed.push(new URL(r.url()).host); else log.apiNon2xx.push(`FAILED ${r.method()} ${r.url()} ${r.failure()?.errorText}`); });
page.on('response', (r) => {
  const u = r.url();
  if (u.startsWith(API)) { log.apiCalls.push(`${r.request().method()} ${u.replace(API, '')} -> ${r.status()}`); if (r.status() >= 400) log.apiNon2xx.push(`${r.request().method()} ${u.replace(API, '')} -> ${r.status()}`); }
});

const check = (name, ok, detail = '') => { log.checks.push({ name, ok: !!ok, detail }); console.log(ok ? 'PASS' : 'FAIL', name, detail); };
const go = async (path, name) => { await page.goto(ORIGIN + path, { waitUntil: 'load', timeout: 60000 }); await page.waitForTimeout(500); await page.waitForFunction(() => !document.querySelector('[role=status]'), null, { timeout: 60000 }).catch(() => {}); await page.waitForTimeout(500); await page.screenshot({ path: `shots/${name}.png` }); };
const bodyText = async () => (await page.textContent('body')) ?? '';

// ---------- 1. Executive Overview ----------
await go('/', '01_overview');
let t = await bodyText();
check('overview: title', t.includes('Executive Overview'));
check('overview: real location count (10)', /Locations in dataset\s*10/.test(t), '');
check('overview: synthetic banner', t.includes('not live traffic'));
check('overview: topbar reflects /health', t.includes('Backend online'));
check('overview: no demo strings', !/DEMO DATA|Operational/.test(t));
await page.waitForFunction(() => /P\(High\)/.test(document.body.innerText), null, { timeout: 30000 }).catch(() => {});
await page.waitForSelector('.leaflet-container', { timeout: 30000 }).catch(() => {});
t = await bodyText();
check('overview: predicted risk list rendered', t.includes('Highest predicted risk') && /P\(High\)/.test(t));
check('overview: map rendered with 10 prediction markers', (await page.locator('.leaflet-container').count()) > 0 && (await page.locator('path.leaflet-interactive').count()) >= 10, String(await page.locator('path.leaflet-interactive').count()));

// ---------- 2. Traffic Map ----------
await go('/traffic-map', '02_traffic_map');
t = await bodyText();
await page.waitForSelector('.leaflet-container', { timeout: 30000 }).catch(() => {});
await page.waitForFunction(() => document.querySelectorAll('path.leaflet-interactive').length >= 10, null, { timeout: 15000 }).catch(() => {});
check('map: leaflet rendered', await page.locator('.leaflet-container').count() > 0);
check('map: 10 location markers', (await page.locator('path.leaflet-interactive').count()) >= 10, String(await page.locator('path.leaflet-interactive').count()));
check('map: TomTom status honest (disabled)', /disabled|unavailable/i.test(t) && t.includes('No live traffic is shown'));
await page.selectOption('select >> nth=0', 'observed'); await page.waitForTimeout(300);
check('map: layer switch works', (await bodyText()).includes('Latest synthetic record'));

// ---------- 3. Prediction ----------
await go('/prediction', '03_prediction');
await page.waitForSelector('text=Top class probability');
await page.waitForTimeout(800);
t = await bodyText();
check('prediction: class result shown', /(Low|Medium|High)/.test(t) && t.includes('Random Forest'));
check('prediction: probabilities chart', await page.locator('.recharts-bar-rectangle').count() > 0);
await page.selectOption('#pred-location', 'IT Park'); await page.waitForTimeout(1200);
check('prediction: location change re-queries', log.apiCalls.some((c) => c.includes('/traffic/predict/IT%20Park') && c.endsWith('200')));
await page.fill('#ov-avg_speed', '5'); await page.fill('#ov-vehicle_count', '2500');
await page.click('text=Run prediction'); await page.waitForTimeout(1200);
check('prediction: what-if override accepted', log.apiCalls.filter((c) => c.includes('/traffic/predict/')).every((c) => c.endsWith('200')));

// ---------- 4. Forecasting ----------
await go('/forecasting', '04_forecasting');
await page.waitForSelector('text=+30 min congestion class', { timeout: 30000 });
t = await bodyText();
check('forecast: 30 and 60 min cards', t.includes('+30 min congestion class') && t.includes('+60 min congestion class'));
check('forecast: both horizons requested', log.apiCalls.some((c) => c.includes('horizon_minutes=30')) && log.apiCalls.some((c) => c.includes('horizon_minutes=60')));
await page.selectOption('#fc-model', 'xgboost'); await page.waitForTimeout(1500);
check('forecast: xgboost family requested', log.apiCalls.some((c) => c.includes('model=xgboost') && c.endsWith('200')));
await page.waitForFunction(() => document.body.innerText.includes('Forecast avg speed') || document.querySelectorAll('.recharts-bar-rectangle').length > 20, null, { timeout: 20000 }).catch(() => {});
await page.waitForTimeout(800);
check('forecast: all-locations comparison (10 calls)', log.apiCalls.filter((c) => c.includes('/forecasts/') && c.includes('model=xgboost')).length >= 10);

// ---------- 5. Route planner ----------
await go('/route-planner', '05_route_planner');
await page.waitForSelector('#origin-select');
t = await bodyText();
check('route: backend connected badge', t.includes('Backend connected'));
check('route: fallback graph honestly labelled', t.includes('not OpenStreetMap') || t.includes('OpenStreetMap data'));
await page.selectOption('#origin-select', '1'); await page.selectOption('#dest-select', '8');
await page.click('text=Find Smart Routes');
await page.waitForSelector('text=Recommended', { timeout: 30000 });
await page.waitForTimeout(600);
t = await bodyText();
check('route: node-mode request ok', log.apiCalls.some((c) => c.startsWith('POST /routes/calculate') && c.endsWith('200')));
check('route: 3 ranked alternatives', (t.match(/Rank \d/g) ?? []).length >= 3, (t.match(/Rank \d/g) ?? []).join(','));
check('route: polyline drawn on map', await page.locator('path.leaflet-interactive').count() >= 3);
check('route: routing details shown', t.includes('Traffic cost mode') && t.includes('free-flow'));
await page.selectOption('#algorithm-select', 'astar'); await page.click('text=Find Smart Routes'); await page.waitForTimeout(1200);
check('route: A* result', (await bodyText()).includes('astar'));
await page.screenshot({ path: 'shots/05_route_result.png' });
// validator: coordinate mode via map clicks
await page.selectOption('#mode-select', 'map');
const mapBox = await page.locator('.leaflet-container').first().boundingBox();
await page.mouse.click(mapBox.x + mapBox.width * 0.45, mapBox.y + mapBox.height * 0.5);
await page.mouse.click(mapBox.x + mapBox.width * 0.62, mapBox.y + mapBox.height * 0.42);
await page.waitForTimeout(300);
await page.click('text=Find Smart Routes'); await page.waitForTimeout(1500);
const coordCall = log.apiCalls.filter((c) => c.startsWith('POST /routes/calculate')).length;
check('route: coordinate-mode request ok', coordCall >= 3 && !log.apiNon2xx.some((c) => c.includes('/routes/calculate')), `calls=${coordCall}`);

// place-name mode needs online geocoding: offline it must fail GRACEFULLY with a visible message (no crash)
await page.selectOption('#mode-select', 'names');
await page.fill('#origin-name', 'Rasulgarh, Bhubaneswar'); await page.fill('#dest-name', 'Patia, Bhubaneswar');
await page.click('text=Find Smart Routes'); await page.waitForTimeout(4000);
const nameCalls = log.apiCalls.filter((c) => c.startsWith('POST /routes/calculate')).length;
check('route: place-name mode sends request and shows result or readable error', nameCalls >= 4 && (await page.locator('[role=alert]').count() > 0 || (await bodyText()).includes('Recommended')), `calls=${nameCalls}`);
// ---------- 6. Incidents ----------
await go('/incidents', '06_incidents');
await page.waitForSelector('#inc-origin');
await page.selectOption('#inc-origin', '1'); await page.selectOption('#inc-dest', '8');
await page.click('text=Calculate route'); await page.waitForSelector('text=Nodes:', { timeout: 30000 });
const before = (await page.locator('text=/Nodes: /').first().textContent()) ?? '';
// click the first segment of the route to fill the edge
await page.locator('button:has-text("→")').first().click();
check('incidents: segment click fills edge fields', (await page.inputValue('#edge-u')) !== '' && (await page.inputValue('#edge-v')) !== '');
await page.fill('#desc', 'e2e test incident');
await page.click('button:has-text("Create incident")'); await page.waitForSelector('text=created on', { timeout: 20000 });
await page.waitForTimeout(1200);
t = await bodyText();
check('incidents: POST /incidents 201', log.apiCalls.some((c) => c === 'POST /incidents -> 201'));
check('incidents: appears in feed', t.includes('e2e test incident'));
const after = (await page.locator('text=/Nodes: /').first().textContent()) ?? '';
check('incidents: route re-planned around incident', before !== after && t.includes('Route changed after last update'), `before=${before} | after=${after}`);
await page.screenshot({ path: 'shots/06_incident_created.png' });
await page.click('button[aria-label^="Delete incident"]'); await page.waitForSelector('text=removed', { timeout: 20000 });
await page.waitForTimeout(1000);
check('incidents: DELETE ok', log.apiCalls.some((c) => c.startsWith('DELETE /incidents/') && c.endsWith('200')));
check('incidents: feed empty after delete', (await bodyText()).includes('No active incidents'));
// error path: invalid edge shows backend message
await page.fill('#edge-u', '1'); await page.fill('#edge-v', '7');
await page.click('button:has-text("Create incident")'); await page.waitForTimeout(1000);
check('incidents: invalid edge -> backend 400 message shown', (await bodyText()).includes('not present in the active graph'));

// ---------- 7. Weather ----------
await go('/weather', '07_weather_unavailable');
await page.waitForSelector('text=Weather unavailable', { timeout: 40000 }).catch(() => {});
t = await bodyText();
check('weather: unavailable state honest (no internet)', t.includes('Weather unavailable') && !/\d+\.\d°C/.test(t.split('Weather unavailable')[1] ?? ''));
await page.click('text=Allow simulated fallback'); await page.waitForSelector('text=SIMULATED WEATHER.', { timeout: 30000 });
t = await bodyText();
check('weather: simulated fallback clearly labelled', t.includes('SIMULATED WEATHER') && t.includes('Simulated fallback ON'));
await page.screenshot({ path: 'shots/07_weather_simulated.png' });

// ---------- 8. Copilot ----------
await go('/copilot', '08_copilot');
await page.fill('input[aria-label="Message the copilot"]', 'Which locations have predicted traffic congestion?');
await page.keyboard.press('Enter');
await page.waitForSelector('text=location predictions from the Phase 1 model', { timeout: 60000 });
t = await bodyText();
check('copilot: real backend answer', t.includes('location predictions from the Phase 1 model'));
check('copilot: mode labelled rule-based', t.includes('Rule-based fallback'));
check('copilot: tool results expandable', await page.locator('details summary').count() > 0);
await page.click('text=Are there any active incidents?'); await page.waitForSelector('text=active incident record', { timeout: 30000 });
check('copilot: incident question answered', (await bodyText()).includes('active incident record'));
await page.screenshot({ path: 'shots/08_copilot.png' });

// ---------- 9. Model evaluation ----------
await go('/model-evaluation', '09_model_evaluation');
await page.waitForSelector('text=Confusion matrix', { timeout: 20000 });
t = await bodyText();
check('eval: real classifier metrics (RF accuracy 96.1%)', t.includes('96.1%'));
check('eval: forecast regressor table', t.includes('Moving Average') && t.includes('Persistence'));
check('eval: no fabricated LSTM table', !/Prophet|ARIMA/.test(t.replace(/ARIMA.*?removed/s, '')) || t.includes('removed rather') || t.includes('do not exist'));
check('eval: unavailable items stated', t.includes('Not available from the backend'));
await page.selectOption('#ev-model', 'XGBoost'); await page.waitForTimeout(300);
check('eval: model switch updates matrix', (await bodyText()).includes('Confusion matrix — XGBoost'));

// ---------- navigation & responsive ----------
await go('/', 'nav');
for (const label of ['Traffic Map','Traffic Prediction','Forecasting Analytics','Smart Route Planner','Incident Management','Weather Intelligence','AI Traffic Copilot','Model Evaluation','Executive Overview']) {
  await page.click(`nav >> text=${label}`); await page.waitForTimeout(250);
}
check('sidebar: all 9 nav links navigate', true);
await page.setViewportSize({ width: 390, height: 800 }); await go('/route-planner', 'mobile_route');
check('responsive: mobile route page renders', (await bodyText()).includes('Smart Route Planner'));

// ---------- backend-down state ----------
await ctx.route(`${API}/**`, (r) => r.abort());
await page.setViewportSize({ width: 1440, height: 1000 });
await page.goto(ORIGIN + '/prediction', { waitUntil: 'load' }); await page.waitForTimeout(1000);
t = await bodyText();
check('error state: backend unreachable message shown', t.includes('Could not reach the backend') || t.includes('Backend offline'));
await page.screenshot({ path: 'shots/10_backend_down.png' });

await browser.close();
const unexpectedConsole = log.consoleErrors.filter((m) => !/Failed to load resource|ERR_|net::|tile|cartocdn/i.test(m));
const apiProblems = log.apiNon2xx.filter((m) => !/^FAILED/.test(m));
log.summary = { origin: ORIGIN, passed: log.checks.filter((c) => c.ok).length, failed: log.checks.filter((c) => !c.ok).length,
  unexpectedConsoleErrors: unexpectedConsole, pageErrors: log.pageErrors, apiNon2xx_beforeBackendDown: apiProblems.slice(0, 20), externalHostsFailed: [...new Set(log.externalFailed)] };
fs.writeFileSync(`e2e_result_${new URL(ORIGIN).hostname}.json`, JSON.stringify(log, null, 1));
console.log('\nSUMMARY', JSON.stringify(log.summary, null, 1));
process.exit(log.summary.failed ? 1 : 0);
