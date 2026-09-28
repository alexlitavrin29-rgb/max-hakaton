const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('project/miniapp/static/app.js', 'utf8');

// Minimal browser boundary; run the real script and its event handlers.
function launch(failure, target='') {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      value: '', hidden: true, disabled: false, textContent: '', dataset: {},
      classList: {add() {}, remove() {}, toggle() {}},
      addEventListener() {}, setAttribute() {},
      children: [], append(...items) {this.children.push(...items);}, prepend(item) {this.children.unshift(item);},
      replaceChildren(...items) {this.children = items;},
    });
    return elements.get(id);
  }
  let calls = 0, aborted = 0, saved=[];
  const material={title:'Потерян паспорт',text:'Полный ответ со ссылками',section:'advice',node_id:'doc_passport_q3',status:'information',actions:[],favorite_id:'a'.repeat(64)};
  const context = vm.createContext({
    document: {getElementById: element, body: element('body'), documentElement: element('html'),
      querySelectorAll: () => [], querySelector: () => element('meta'),
      createElement: tag => element(Symbol(tag)), createTextNode: text => ({textContent: text})},
    window: {}, location: {search: target?'?section='+target:'', hash: ''}, URL, URLSearchParams, AbortController, TypeError,
    crypto: require('node:crypto').webcrypto,
    matchMedia: () => ({addEventListener() {}}), getComputedStyle: () => ({getPropertyValue: () => ''}),
    setTimeout: fn => setTimeout(fn, 10), clearTimeout,
    fetch: async (path, options) => {
      calls++;
      if (path === '/api/info') return {ok: true, json: async () => ({preview: true})};
      if (path === '/api/session') return {ok: true, json: async () => ({session: 'test'})};
      if (path === '/api/favorites') {calls--;return {ok: true, json: async () => ({cards: saved})};}
      if (path === '/api/material') {
        if(JSON.parse(options.body).save)saved=[{...material,saved_at:'2026-09-27T12:00:00Z'}];
        return {ok:true,json:async()=>({card:material})};
      }
      if (failure === 'network') throw new TypeError('Failed to fetch');
      if (failure === 'json') return {ok: false, status: 502, json: async () => {throw new SyntaxError('private html');}};
      return new Promise((resolve, reject) => {
        options?.signal?.addEventListener('abort', () => {
          aborted++;
          const error = new Error('aborted'); error.name = 'AbortError'; reject(error);
        });
      });
    },
  });
  vm.runInContext(source, context);
  return {context, element, counts: () => ({calls, aborted})};
}

test('save link opens My with the complete saved answer', async () => {
  const app=launch('network','save_doc_passport_q3');
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(app.element('saved').hidden,false);
  assert.equal(app.element('saved-count').textContent,'1');
  assert.match(app.element('saved-confirmation').textContent,/Сохранено/);
  assert.equal(app.element('saved-list').children[0].children.at(-1).open,true);
});

test('shared answer opens for reading without adding it to My', async () => {
  const app=launch('network','view_doc_passport_q3');
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(app.element('material').hidden,false);
  await vm.runInContext('showSaved()',app.context);
  assert.equal(app.element('saved-count').textContent,'');
});

test('sharing passes a read-only material link and no dialogue data', async () => {
  const app=launch('network');
  await new Promise(resolve=>setImmediate(resolve));
  app.context.sent=[];
  vm.runInContext("info={preview:false,bot_name:'test_bot'};window.WebApp={shareMaxContent:value=>sent.push(value)};shareMaterial({node_id:'doc_passport_q3',title:'Потерян паспорт',text:'private dialogue'})",app.context);
  assert.equal(app.context.sent[0].link,'https://max.ru/test_bot?startapp=view_doc_passport_q3');
  assert.equal(app.context.sent[0].text,'Потерян паспорт');
  assert.ok(!JSON.stringify(app.context.sent).includes('private'));
});

test('search progress names the section, stays visible, and clears after failure', async () => {
  const app=launch('timeout');
  await new Promise(resolve=>setImmediate(resolve));
  app.element('search').hidden=false;
  vm.runInContext("section='rental'",app.context);
  const done=vm.runInContext("act({text:'Томск до 30000'})",app.context);
  assert.match(app.element('status').textContent,/жильё/);
  assert.equal(app.element('tab-saved').disabled,true);
  await done;
  assert.equal(app.element('status').textContent,'');
  assert.equal(app.element('tab-saved').disabled,false);
});

test('saved view keeps search conditions and cards when returning', async () => {
  const app=launch('network');
  await new Promise(resolve=>setImmediate(resolve));
  vm.runInContext("render({section:'work',summary:'Томск',notices:[],controls:[],cards:[]})",app.context);
  app.element('wishes').value='до 60000';
  await vm.runInContext('showSaved()',app.context);
  assert.equal(app.element('saved').hidden,false);
  vm.runInContext('showMain()',app.context);
  assert.equal(app.element('summary').textContent,'Томск');
  assert.equal(app.element('wishes').value,'до 60000');
  assert.equal(app.element('saved').hidden,true);
});

for (const price of ['18 000 ₽', '17 000.0 ₽', 'от 18 000 ₽', '1 200 000 ₽', 'не указана', 'Зарплата: 60 000–80 000 ₽']) {
  test(`card preserves server price ${price}`, () => {
    const app = launch('network');
    app.context.testPrice = price;
    vm.runInContext("cards=[{title:'Квартира',price:testPrice,text:'Квартира\\nЦена: '+testPrice,status:'confirmed',actions:[]}];cardIndex=0;renderCard()", app.context);
    const displayed = app.element('card').children.find(item => item.className === 'price');
    assert.equal(displayed.textContent, price.replace(/^Зарплата:\s*/, ''));
  });
}

for (const failure of ['timeout', 'network', 'json']) {
  test(`request ${failure} releases input and preserves unsent conditions`, async () => {
    const app = launch(failure);
    await new Promise(resolve => setImmediate(resolve));
    app.element('wishes').value = 'Томск до 30000';
    const done = vm.runInContext("act({text: 'Томск до 30000'})", app.context);
    await Promise.race([done, new Promise((_, reject) => setTimeout(() => reject(new Error('request stuck')), 100))]);
    assert.equal(app.element('submit').disabled, false);
    assert.equal(app.element('wishes').value, 'Томск до 30000');
    assert.match(app.element('error').textContent, /[А-Яа-я]/);
    assert.equal(app.counts().calls, 3, 'a failed action must not be replayed automatically');
    if (failure === 'timeout') assert.equal(app.counts().aborted, 1);
  });
}
