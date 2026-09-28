'use strict';
const $ = id => document.getElementById(id);
const titles = {work:'Ищу работу', rental:'Ищу жильё', help:'Вам тут помогут'};
let token='', section='home', cards=[], cardIndex=0, busy=false, info=null;
let savedCards=[], savedLoaded=false, savedView=false, lastView=null, progressTimer=null;
let materialCard=null, highlightedMaterial=null;
let pendingAction=null;
const bridge = () => window.WebApp;

function error(message) { $('error').textContent=message; $('error').hidden=!message; }
function linkElement(label, url) {
  const a=document.createElement('a'); a.textContent=label;
  try { const u=new URL(url); if(!['http:','https:'].includes(u.protocol)) return document.createTextNode(label); a.href=u.href; } catch { return document.createTextNode(label); }
  a.target='_blank'; a.rel='noopener noreferrer'; return a;
}
function richText(container, text) {
  // Only Markdown links are interpreted. All provider/user text stays text, never HTML.
  const pattern=/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g; let start=0;
  for(const match of text.matchAll(pattern)) { container.append(document.createTextNode(text.slice(start,match.index)),linkElement(match[1],match[2])); start=match.index+match[0].length; }
  container.append(document.createTextNode(text.slice(start)));
}
async function requestJSON(path, options={}, timeoutMs=130000) {
  const controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),timeoutMs);
  try {
    const response=await fetch(path,{...options,signal:controller.signal});
    if(response.status===401) $('reopen').hidden=false;
    let data;
    try { data=await response.json(); }
    catch(e) { if(e.name==='AbortError') throw e; throw new Error('Сервер не смог ответить. Попробуйте ещё раз через несколько секунд.'); }
    if(!response.ok) throw new Error(typeof data.detail==='string'?data.detail:'Не получилось выполнить запрос. Попробуйте ещё раз.');
    return data;
  } catch(e) {
    if(e.name==='AbortError') throw new Error('Ответ не пришёл вовремя. Проверьте интернет и повторите поиск.');
    if(e instanceof TypeError) throw new Error('Не удалось связаться с сервером. Проверьте интернет и попробуйте ещё раз.');
    throw e;
  } finally { clearTimeout(timer); }
}
async function api(path, body, idempotencyKey='') {
  return requestJSON(path,{method:'POST',headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{}),...(idempotencyKey?{'Idempotency-Key':idempotencyKey}:{})},body:JSON.stringify(body)});
}
function homeView() {
  $('material').hidden=true;materialCard=null;
  savedView=false; $('saved').hidden=true; selectTab(false);
  document.body.classList.remove('search-open');
  section='home'; cards=[]; $('landing').hidden=false; $('search').hidden=true; $('share-panel').hidden=true;
  $('share-section').hidden=true; bridge()?.BackButton?.hide();
}
function selectTab(saved) {
  $('tab-home').setAttribute('aria-current',saved?'false':'page');
  $('tab-saved').setAttribute('aria-current',saved?'page':'false');
}
function setBusy(value, body={}) {
  busy=value; clearTimeout(progressTimer);
  const target=body.section||section;
  const text=body.section?(body.section==='home'?'Открываем разделы…':'Открываем поиск…'):
    ({work:'Секундочку, подбираем вакансии по твоему запросу…',rental:'Секундочку, подбираем жильё по твоему запросу…',help:'Секундочку, ищем места, где тебе могут помочь…'}[target]||'Открываем приложение…');
  const status=$('search').hidden?$('page-status'):$('status');
  $('status').textContent=''; $('page-status').textContent='';
  if(value) {
    status.textContent=text;
    progressTimer=setTimeout(()=>{status.textContent=text+' Ответ занимает чуть больше времени. Можно подождать здесь.';},12000);
  }
  $('search').classList.toggle('busy',value); $('landing').classList.toggle('busy',value);
  $('search').setAttribute('aria-busy',String(value));
  $('tab-home').disabled=value; $('tab-saved').disabled=value;
  $('wishes').disabled=value; $('submit').disabled=value;
}
async function act(body) {
  if(busy || !token) return;
  error(''); setBusy(true,body);
  const input=JSON.stringify(body);
  if(!pendingAction || pendingAction.input!==input)pendingAction={input,key:crypto.randomUUID()};
  try { const result=await api('/api/action',body,pendingAction.key);pendingAction=null; render(result); if(body.text||body.section) $('wishes').value=''; }
  catch(e) {error(e.message);} finally {setBusy(false);}
}
function render(view) {
  $('material').hidden=true;materialCard=null;
  lastView=view; savedView=false; $('saved').hidden=true; selectTab(false);
  if(view.section==='home') {homeView();return;}
  section=view.section;
  document.body.classList.add('search-open');
  $('landing').hidden=true; $('search').hidden=false; $('share-panel').hidden=true;
  $('section-title').textContent=titles[section];
  $('summary-box').hidden=!view.summary; $('summary').textContent=view.summary;
  $('input-label').textContent=section==='help'?'Напишите город или ответ на вопрос':'Напишите пожелания или ответ на вопрос';
  $('wishes').placeholder=section==='help'?'Например, Тула':'Город, пожелания и важные условия…';
  $('notices').replaceChildren();
  let noticeContainer=$('notices');
  if(view.cards.length) {
    const details=document.createElement('details');const summary=document.createElement('summary');
    summary.textContent='Пояснения и ограничения поиска';details.append(summary);$('notices').append(details);noticeContainer=details;
  }
  for(const notice of view.notices) {if(!notice.text)continue;const p=document.createElement('p');p.className='notice';richText(p,notice.text);noticeContainer.append(p);}
  $('controls').replaceChildren();
  const unique=new Set();
  for(const action of view.controls) {
    const key=action.payload||action.url;if(unique.has(key))continue;unique.add(key);
    if(action.url) {$('controls').append(linkElement(action.label,action.url));continue;}
    const b=document.createElement('button'); b.textContent=action.label;b.dataset.more=String(Boolean(action.more));b.onclick=()=>act({payload:action.payload});$('controls').append(b);
  }
  cards=view.cards;cardIndex=0;$('carousel').hidden=!cards.length;if(cards.length)renderCard();
  bridge()?.BackButton?.show();
}
function renderCard() {
  const item=cards[cardIndex];if(!item)return;
  $('count').textContent=`${cardIndex+1} из ${cards.length}`;
  fillCard($('card'),item);
  $('previous').disabled=cardIndex===0;$('next').disabled=cardIndex===cards.length-1;
  document.querySelectorAll('[data-more="true"]').forEach(b=>b.hidden=cardIndex!==cards.length-1);
}
function fillCard(container,item,isSaved=false) {
  container.replaceChildren();
  const badge=document.createElement('span');badge.className='badge '+item.status;
  badge.textContent=isSaved?'Сохранено · '+new Date(item.saved_at).toLocaleDateString('ru-RU')+(item.updated?' · Обновлено':''):item.section==='advice'?'Полезный ответ':item.status==='unverified'?'Есть неподтверждённые условия':item.status==='information'?'Условия обращения — в описании':'Соответствие подтверждено данными источника';
  const h=document.createElement('h2');h.textContent=item.title;
  const price=document.createElement('div');price.className='price';price.textContent=(item.price||'').replace(/^Зарплата:\s*/, '');
  const subtitle=document.createElement('div');subtitle.className='subtitle';subtitle.textContent=item.subtitle||'';
  const warning=document.createElement('p');warning.className='warning';warning.textContent=item.warnings||'';warning.hidden=!item.warnings;
  const body=document.createElement('div');body.className='card-body';
  let text=item.text;if(text.startsWith(item.title+'\n'))text=text.slice(item.title.length+1);
  richText(body,text);
  const actions=document.createElement('div');actions.className='card-actions';
  for(const a of item.actions)if(a.url)actions.append(linkElement(a.label,a.url));
  const details=document.createElement('details');details.className='card-details';
  const toggle=document.createElement('summary');toggle.textContent=item.section==='advice'?'Открыть ответ':'Подробнее';details.append(toggle,body);
  if(item.section==='advice'&&(!isSaved||item.node_id===highlightedMaterial))details.open=true;
  if((item.section||section)==='help'&&!isSaved) {details.open=true;}
  const heading=document.createElement('div');heading.className='card-heading';heading.append(h);
  if(item.favorite_id) {
    const heart=document.createElement('button');heart.className='favorite';
    const selected=savedCards.some(c=>c.favorite_id===item.favorite_id);
    heart.textContent=selected?'♥':'♡';heart.setAttribute('aria-pressed',String(selected));
    heart.setAttribute('aria-label',selected?'Убрать из Моего':'Сохранить в Моё');
    heart.title=selected?'Убрать из Моего':'Сохранить в Моё';
    heart.onclick=()=>toggleFavorite(item,heart);heading.append(heart);
  }
  container.append(heading,price,subtitle,actions,badge,warning);
  if(item.section==='advice'&&!item.unavailable) {
    const shareButton=document.createElement('button');shareButton.className='quiet';shareButton.textContent='Поделиться ответом ↗';
    shareButton.onclick=()=>shareMaterial(item);container.append(shareButton);
  }
  if(isSaved&&item.section!=='advice') {
    const note=document.createElement('p');note.className='saved-note';
    note.textContent=item.section==='help'?'Перед визитом уточни часы работы и условия помощи.':'Предложение могло измениться. Проверь актуальность по ссылке.';
    container.append(note);
  }
  container.append(details);
}
async function loadSaved() {
  const data=await requestJSON('/api/favorites',{headers:{Authorization:'Bearer '+token}},10000);
  savedCards=data.cards;savedLoaded=true;
}
async function toggleFavorite(item,button) {
  button.disabled=true;error('');
  try {
    if(!savedLoaded)await loadSaved();
    const saved=!savedCards.some(c=>c.favorite_id===item.favorite_id);
    const data=await api('/api/favorites',{card_id:item.favorite_id,saved});
    savedCards=data.cards;savedLoaded=true;
    if(savedView)renderSaved();else if(materialCard)fillCard($('material-card'),materialCard);else renderCard();
  } catch(e) {error(e.message);} finally {button.disabled=false;}
}
function renderSaved() {
  if(highlightedMaterial&&!savedCards.some(c=>c.node_id===highlightedMaterial)) {
    highlightedMaterial=null;$('saved-confirmation').hidden=true;
  }
  $('saved-count').textContent=savedCards.length?String(savedCards.length):'';
  $('saved-list').replaceChildren();
  if(!savedCards.length) {
    const empty=document.createElement('div');empty.className='saved-empty';
    const icon=document.createElement('span');icon.className='empty-heart';icon.textContent='♡';icon.setAttribute('aria-hidden','true');
    const heading=document.createElement('h2');heading.textContent='Оставь здесь то, что понравилось';
    const text=document.createElement('p');text.textContent='Сохрани вакансию, жильё, место помощи или полезный ответ из бота. Всё будет ждать тебя здесь.';
    const back=document.createElement('button');back.className='quiet';back.textContent='Перейти к поиску';back.onclick=showMain;
    empty.append(icon,heading,text,back);$('saved-list').append(empty);return;
  }
  const ordered=[...savedCards].sort((a,b)=>Number(b.node_id===highlightedMaterial)-Number(a.node_id===highlightedMaterial));
  for(const item of ordered) {
    const article=document.createElement('article');article.className='saved-card';
    if(item.node_id&&item.node_id===highlightedMaterial)article.classList.add('just-saved');
    const label=document.createElement('p');label.className='saved-category';label.textContent=item.section==='advice'?'Полезный ответ из бота':titles[item.section];
    fillCard(article,item,true);article.prepend(label);$('saved-list').append(article);
  }
}
async function showSaved(focus=null) {
  if(busy||!token)return;
  $('material').hidden=true;materialCard=null;highlightedMaterial=typeof focus==='string'?focus:null;
  $('saved-confirmation').hidden=!highlightedMaterial;
  $('saved-confirmation').textContent=highlightedMaterial?'Сохранено в Моё. Ответ уже здесь ↓':'';
  error('');savedView=true;document.body.classList.remove('search-open');
  $('landing').hidden=true;$('search').hidden=true;$('saved').hidden=false;$('share-panel').hidden=true;
  selectTab(true);bridge()?.BackButton?.show();
  $('saved-list').textContent='Загружаем сохранённое…';
  try {await loadSaved();if(savedView)renderSaved();}
  catch(e) {if(savedView){$('saved-list').textContent='';error(e.message);}}
}
async function openMaterial(nodeId,save=false) {
  error('');setBusy(true);$('page-status').textContent=save?'Сохраняем ответ в Моё…':'Открываем полезный ответ…';
  try {
    const result=await api('/api/material',{node_id:nodeId,save});
    setBusy(false);
    if(save){await showSaved(nodeId);return;}
    materialCard=result.card;savedView=false;highlightedMaterial=null;
    document.body.classList.remove('search-open');
    $('landing').hidden=true;$('search').hidden=true;$('saved').hidden=true;$('material').hidden=false;
    $('share-panel').hidden=true;selectTab(false);bridge()?.BackButton?.show();
    fillCard($('material-card'),materialCard);
  } catch(e) {error(e.message);} finally {setBusy(false);}
}
function shareMaterial(item) {
  if(info.preview){error('Отправка ссылки доступна в MAX после подключения приложения.');return;}
  shareLink(item.title,`https://max.ru/${info.bot_name}?startapp=view_${item.node_id}`);
}
function showMain() {
  if(busy)return;error('');
  if(lastView&&lastView.section!=='home')render(lastView);else homeView();
}
function move(delta) {if(busy)return;const next=Math.max(0,Math.min(cards.length-1,cardIndex+delta));if(next!==cardIndex){cardIndex=next;renderCard();}}
$('previous').onclick=()=>move(-1);$('next').onclick=()=>move(1);
$('card').onkeydown=e=>{if(e.key==='ArrowLeft'){e.preventDefault();move(-1);}if(e.key==='ArrowRight'){e.preventDefault();move(1);}};
let touch=null;
$('card').addEventListener('pointerdown',e=>{if(e.pointerType==='touch')touch={x:e.clientX,y:e.clientY};});
$('card').addEventListener('pointercancel',()=>touch=null);
$('card').addEventListener('pointerup',e=>{if(!touch)return;const dx=e.clientX-touch.x,dy=e.clientY-touch.y;touch=null;if(Math.abs(dx)>55&&Math.abs(dx)>Math.abs(dy)*1.5)move(dx<0?1:-1);});
document.querySelectorAll('[data-section]').forEach(b=>b.onclick=()=>act({section:b.dataset.section}));
$('home').onclick=$('back').onclick=()=>act({section:'home'});
$('tab-home').onclick=showMain;$('tab-saved').onclick=showSaved;
$('delete-account').onclick=async()=>{
  if(!window.confirm('Удалить все сохранения и напоминания? Это действие нельзя отменить.'))return;
  try {
    await requestJSON('/api/account',{method:'DELETE',headers:{Authorization:'Bearer '+token}},10000);
    token='';savedCards=[];savedLoaded=false;$('reopen').hidden=false;
    $('saved-list').textContent='Сохранения и напоминания удалены из рабочей базы. Открой приложение заново, чтобы начать новый сеанс.';
  } catch(e) {error(e.message);}
};
$('material-back').onclick=()=>showSaved();
$('composer').onsubmit=e=>{e.preventDefault();const text=$('wishes').value.trim();if(text)act({text});};
$('edit').onclick=()=>{$('wishes').placeholder='Что нужно изменить? Остальные условия сохраним.';$('wishes').focus();$('composer').scrollIntoView({block:'center',behavior:'smooth'});};
$('share').onclick=()=>{$('share-panel').hidden=!$('share-panel').hidden;$('share-section').hidden=section==='home'||savedView||Boolean(materialCard);if(!$('share-panel').hidden)$('share-panel').scrollIntoView({block:'center',behavior:'smooth'});};
$('share-close').onclick=()=>$('share-panel').hidden=true;
function share(target) {
  if(info.preview){error('Это локальный предпросмотр. Ссылка для отправки станет доступна после подключения приложения к MAX.');return;}
  const link=`https://max.ru/${info.bot_name}?startapp=${target}`;
  const text=target==='home'?'Точка опоры — поиск работы, жилья и мест, где могут помочь.':`Точка опоры — ${titles[target].toLowerCase()}.`;
  shareLink(text,link);
}
function shareLink(text,link) {
  try {
    if(bridge()?.shareMaxContent) Promise.resolve(bridge().shareMaxContent({text,link})).catch(()=>error('Не удалось открыть отправку. Попробуйте ещё раз в MAX.'));
    else window.open('https://max.ru/:share?text='+encodeURIComponent(text+'\n'+link),'_blank','noopener,noreferrer');
  } catch {error('Не удалось открыть отправку. Попробуйте ещё раз в MAX.');}
}
$('share-section').onclick=()=>share(section);$('share-app').onclick=()=>share('home');
$('reopen').onclick=()=>window.location.reload();
async function start() {
  setBusy(true);
  try {
    info=await requestJSON('/api/info');$('preview').hidden=!info.preview;
    // MAX UI 0.5.0 also follows prefers-color-scheme; no undocumented Bridge events.
    const sampleTheme=info.preview?new URLSearchParams(location.search).get('theme'):null;
    if(['light','dark'].includes(sampleTheme))document.documentElement.dataset.theme=sampleTheme;
    const colourMedia=matchMedia('(prefers-color-scheme: dark)');
    const updateColour=()=>document.querySelector('meta[name="theme-color"]').setAttribute('content',getComputedStyle(document.documentElement).getPropertyValue('--surface').trim());
    colourMedia.addEventListener('change',updateColour);updateColour();
    const fragment=new URLSearchParams(location.hash.slice(1));
    const init=bridge()?.initData||fragment.get('WebAppData')||'';
    const launch=await api('/api/session',{init_data:init});token=launch.session;
    $('share').disabled=false;
    bridge()?.BackButton?.onClick(()=>materialCard?showSaved():savedView?showMain():act({section:'home'}));
    const signed=new URLSearchParams(init);
    const target= signed.get('start_param')||bridge()?.initDataUnsafe?.start_param||(info.preview?new URLSearchParams(location.search).get('section'):null);
    // A favorites failure must not prevent searching; retry when the user saves.
    try {await loadSaved();} catch {}
    homeView();setBusy(false);if(Object.hasOwn(titles,target))await act({section:target});
    if(typeof target==='string'&&/^(save|view)_[A-Za-z][A-Za-z0-9_-]{0,79}$/.test(target))await openMaterial(target.slice(5),target.startsWith('save_'));
  } catch(e) {error(e.message);$('reopen').hidden=false;} finally {setBusy(false);}
}
start();
