const assert = require('node:assert/strict');
const {chromium} = require('playwright');

const base = process.env.TOCHKA_PREVIEW_URL || 'http://127.0.0.1:18866';

(async () => {
  const browser = await chromium.launch({headless: process.env.PLAYWRIGHT_HEADLESS === '1'});
  try {
    const page = await browser.newPage();
    await page.route('https://st.max.ru/**', route => route.abort());
    await page.goto(base);
    await page.locator('#preview').waitFor({state: 'visible'});
    await page.locator('[data-section="help"]').click();
    await page.locator('#search').waitFor({state: 'visible'});
    await page.waitForTimeout(500);
    await page.locator('#wishes').fill('Казань');
    await page.locator('#submit').click();
    await page.locator('#controls button').filter({hasText: 'Город Казань'}).waitFor();
    await page.waitForTimeout(500);
    await page.locator('#controls button').filter({hasText: 'Город Казань'}).click();
    await page.locator('#controls button').filter({hasText: 'Еда'}).waitFor();
    await page.waitForTimeout(500);
    await page.locator('#controls button').filter({hasText: 'Еда'}).click();
    await page.locator('#card .favorite').waitFor();
    const title = await page.locator('#card h2').innerText();
    assert.ok(title);
    if (await page.locator('#card .favorite').getAttribute('aria-pressed') !== 'true')
      await page.locator('#card .favorite').click();
    await page.locator('#card .favorite[aria-pressed="true"]').waitFor();
    await page.locator('#tab-saved').click();
    await page.locator('#saved-list .saved-card').first().waitFor();
    assert.match(await page.locator('#saved-list').innerText(), new RegExp(title));
    await page.locator('#tab-home').click();
    await page.locator('#card').waitFor({state: 'visible'});
    assert.match(await page.locator('#card').innerText(), new RegExp(title));

    await page.waitForTimeout(500);
    let failed = false;
    const dropOnce = async route => {
      if (!failed) {failed = true; await route.abort('failed');}
      else await route.continue();
    };
    await page.route('**/api/action', dropOnce);
    await page.locator('#wishes').fill('Набережные Челны');
    await page.locator('#submit').click();
    await page.locator('#error').filter({hasText: 'Не удалось связаться'}).waitFor();
    assert.equal(await page.locator('#wishes').inputValue(), 'Набережные Челны');
    await page.unroute('**/api/action', dropOnce);
    const retryResponse = page.waitForResponse(response => response.url().endsWith('/api/action') && response.request().method() === 'POST');
    await page.locator('#submit').click();
    assert.equal((await retryResponse).status(), 200);
    await page.waitForFunction(() => document.querySelector('#wishes').value === '' && !document.querySelector('#submit').disabled);
    assert.equal(await page.locator('#error').innerText(), '');
    await page.evaluate(() => document.querySelector('#tab-saved').click());
    await page.locator('#saved-list .saved-card').first().waitFor();
    page.once('dialog', dialog => dialog.accept());
    await page.locator('#delete-account').click();
    await page.locator('#reopen').waitFor({state: 'visible'});
    console.log('browser preview: search, card, My, return, network retry, account cleanup passed');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
