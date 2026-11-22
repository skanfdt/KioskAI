async function loadCatalog() {
  const response = await fetch('./catalog.json', {cache: 'no-store'});
  if (!response.ok) throw new Error('메뉴를 불러올 수 없습니다.');
  return response.json();
}
let catalog;
try {
  catalog = await loadCatalog();
} catch (error) {
  document.getElementById('content').textContent = '메뉴를 불러오지 못했습니다. 새로고침해 주세요.';
  throw error;
}
const MENU = catalog.products;
const escapes = {'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'};
const html = value => String(value).replace(/[&<>"']/g, ch => escapes[ch]);
const $=s=>document.querySelector(s),money=n=>n.toLocaleString('ko-KR')+'원';
const state={category:'전체',mode:'매장에서 먹기',cart:[],stage:'menu',payment:null};
let editing=null,quantity=1,toastTimer,resetAll=false;
const storageKey='green-burger-kiosk-v1';
function choices(item) {
  return item.option_groups[0]?.choices ?? [{name:'기본', extra:0}];
}
function optionOf(item, name) {
  return choices(item).find(option => option.name === name);
}
function itemOf(row) { return MENU.find(item => item.id === row.id); }
function unit(row) { const item = itemOf(row); return item.price + optionOf(item, row.option).extra; }
function includes(row) { return optionOf(itemOf(row), row.option).includes || ''; }
try {
  const saved = JSON.parse(sessionStorage.getItem(storageKey));
  if (['매장에서 먹기', '포장하기'].includes(saved?.mode)) state.mode = saved.mode;
  if (Array.isArray(saved?.cart)) {
    state.cart = saved.cart.filter(row => row && itemOf(row) && optionOf(itemOf(row), row.option)
      && Number.isInteger(row.qty) && row.qty > 0 && row.qty <= 20);
  }
} catch {}
function total(){return state.cart.reduce((n,r)=>n+unit(r)*r.qty,0);}
function count(){return state.cart.reduce((n,r)=>n+r.qty,0);}
function label(row){return itemOf(row).name+(row.option==='기본'?'':' '+row.option);}
function persist(){try{sessionStorage.setItem(storageKey,JSON.stringify({mode:state.mode,cart:state.cart}));}catch{}}
function notify(text){$('#toast').textContent=text;$('#toast').classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').classList.remove('show'),2200);}
function go(stage){if(stage!=='menu'&&!state.cart.length)return;state.stage=stage;state.payment=null;render();$('#page-title').scrollIntoView({block:'start'});}
function render(){
  const titles={menu:['오늘은 어떤 메뉴가 좋으세요?','먹고 싶은 메뉴를 골라 나만의 한 끼를 담아보세요.'],review:['담으신 메뉴를 확인해 주세요.','메뉴와 수량, 이용 방법이 맞는지 한 번 더 확인하세요.'],payment:['어떤 방식으로 결제하시겠어요?','사용하실 결제 방식을 선택하면 연습이 완료됩니다.'],finished:['결제 방식 선택이 완료됐어요.','여기까지가 테스트입니다. 실제 결제는 진행되지 않았습니다.']};
  $('#page-title').textContent=titles[state.stage][0];$('#page-description').textContent=titles[state.stage][1];
  for(const step of ['menu','review','payment']){const el=$('#step-'+step);el.removeAttribute('aria-current');if((state.stage==='finished'?'payment':state.stage)===step)el.setAttribute('aria-current','step');}
  $('#dine-in').setAttribute('aria-pressed',String(state.mode==='매장에서 먹기'));$('#takeout').setAttribute('aria-pressed',String(state.mode==='포장하기'));
  $('#dine-in').disabled=$('#takeout').disabled=state.stage==='finished';
  if(state.stage==='menu')renderMenu();else if(state.stage==='review')renderReview();else if(state.stage==='payment')renderPayment();else renderFinished();
  renderCart();persist();
}
function renderMenu(){
 const list=MENU.filter(m=>state.category==='전체'||m.category===state.category);
 $('#content').innerHTML=`<nav class="categories" aria-label="메뉴 분류">${['전체',...catalog.categories].map(c=>`<button data-category="${html(c)}" aria-pressed="${state.category===c}">${html(c)}</button>`).join('')}</nav><div class="section-title"><h2>${state.category==='전체'?'맛있는 선택, 그린버거 메뉴':html(state.category)+' 메뉴'}</h2><small>${list.length}가지 메뉴</small></div><div class="products">${list.map(m=>`<button class="product" data-menu="${html(m.id)}" aria-label="${html(m.name)} 선택"><div class="food-art" style="--food-bg:${/^#[a-fA-F0-9]{6}$/.test(m.color)?m.color:'#eee'}" aria-hidden="true"><span>${html(m.emoji)}</span>${m.tag?`<small>${html(m.tag)}</small>`:''}</div><div class="product-body"><h3>${html(m.name)}</h3><p>${html(m.description)}</p><div class="price-row"><strong>${money(m.price)}</strong><span class="plus" aria-hidden="true">+</span></div></div></button>`).join('')}</div>`;
}
function renderCart(){
 $('#cart-count').textContent=count();$('#total-price').textContent=money(total());$('#clear-cart').disabled=!state.cart.length||state.stage==='finished';
 $('#cart-items').innerHTML=state.cart.length?state.cart.map((r,i)=>`<article class="cart-item"><div class="line-top"><h3>${html(itemOf(r).name)}</h3><button class="remove" data-remove="${i}" aria-label="${html(label(r))} 빼기" ${state.stage==='finished'?'disabled':''}>×</button></div><small>${html(r.option + (includes(r) ? ' · ' + includes(r) : ''))}</small><div class="line-bottom"><div class="stepper"><button data-minus="${i}" aria-label="${html(label(r))} 수량 줄이기" ${state.stage==='finished'?'disabled':''}>−</button><output aria-label="${html(label(r))} 수량">${r.qty}</output><button data-plus="${i}" aria-label="${html(label(r))} 수량 늘리기" ${r.qty>=20||state.stage==='finished'?'disabled':''}>+</button></div><strong>${money(unit(r)*r.qty)}</strong></div></article>`).join(''):'<div class="empty-cart"><div class="empty-symbol" aria-hidden="true">▧</div>아직 담은 메뉴가 없어요.<br>마음에 드는 메뉴를 선택해 주세요.</div>';
 const b=$('#next-step');b.disabled=!state.cart.length||['payment','finished'].includes(state.stage);b.textContent=!state.cart.length?'메뉴를 담아 주세요':state.stage==='menu'?`주문 확인 · ${money(total())}`:state.stage==='review'?'방식 고르기':'결제 방식 선택'+(state.stage==='finished'?' 완료':' 중');
}
function renderReview(){
 $('#content').innerHTML=`<section class="review-panel"><button class="back-button" data-go="menu">← 메뉴 더 담기</button><h2>주문 내역</h2><p class="review-mode">${state.mode} · 총 ${count()}개</p>${state.cart.map(r=>`<div class="review-row"><div><h3>${html(itemOf(r).name)}</h3><p>${html(r.option)} · ${r.qty}개${includes(r)?' · '+html(includes(r))+' 포함':''}</p></div><strong>${money(unit(r)*r.qty)}</strong></div>`).join('')}<div class="review-total"><span>총 주문 금액</span><span>${money(total())}</span></div></section>`;
}
const PAYMENTS=[['신용·체크카드','▤','실물 카드로 이용'],['간편결제','▣','카카오페이 · 네이버페이 등'],['모바일 상품권','▥','바코드 또는 쿠폰으로 이용'],['현금','₩','카운터에서 이용']];
function renderPayment(){
 $('#content').innerHTML=`<section class="payment-panel"><button class="back-button" data-go="review">← 주문 내역 다시 보기</button><h2>결제 방식 선택</h2><p class="review-mode">${state.mode} · 총 ${money(total())}</p><div class="payment-grid">${PAYMENTS.map(([name,icon,desc],i)=>`<button class="payment-option" data-payment="${i}" aria-label="${name==='간편결제'?'간편페이':name} 방식 선택"><span class="payment-icon" aria-hidden="true">${icon}</span><strong>${name}</strong><small>${desc}</small></button>`).join('')}</div><p class="notice">원하는 방식 하나를 선택해 주세요.<br>카드 삽입, 개인정보 입력, 금액 청구는 진행하지 않습니다.</p></section>`;
}
function renderFinished(){
 $('#content').innerHTML=`<section class="finish-panel"><div class="finish-check" aria-hidden="true">✓</div><p class="eyebrow">PRACTICE COMPLETE</p><h2>결제 방식 선택 완료</h2><p>주문 연습을 마쳤습니다.<br>실제 주문 접수나 결제는 이루어지지 않았습니다.</p><div class="selection-summary">선택한 방식<br><strong>${state.payment}</strong><br>${state.mode} · ${count()}개 · ${money(total())}</div><div class="actions"><button class="secondary" data-go="payment">다른 방식 선택</button><button class="primary" id="new-order">새 주문 연습하기</button></div></section>`;
}
function openItem(id){
 editing=MENU.find(m=>m.id===id);quantity=1;
 $('#item-detail').innerHTML=`<h2 id="item-title">${html(editing.name)}</h2><p>${html(editing.description)}</p><strong>${money(editing.price)}</strong>${editing.option_groups.length?`<fieldset class="option-group"><legend>${html(editing.option_groups[0].prompt || editing.option_groups[0].name)}</legend>${choices(editing).map(({name,extra,includes})=>`<label><input type="radio" name="option" value="${html(name)}" ${name===editing.option_groups[0].default?'checked':''}><span>${html(name)}${includes?' ('+html(includes)+')':''}</span><span class="option-price">${extra?'+'+money(extra):'추가금 없음'}</span></label>`).join('')}</fieldset>`:'<p>기본 옵션으로 준비됩니다.</p>'}`;
 updateModal();$('#item-dialog').showModal();
}
function selectedOption(){return $('#item-form input[name=option]:checked')?.value||'기본';}
function updateModal(){$('#modal-quantity').textContent=quantity;$('#modal-minus').disabled=quantity<=1;$('#modal-plus').disabled=quantity>=20;const extra=optionOf(editing,selectedOption()).extra;$('#add-item').textContent=`${money((editing.price+extra)*quantity)} 담기`;}
$('#item-form').addEventListener('change',updateModal);
$('#modal-minus').onclick=()=>{quantity=Math.max(1,quantity-1);updateModal();};$('#modal-plus').onclick=()=>{quantity=Math.min(20,quantity+1);updateModal();};$('#close-item').onclick=()=>$('#item-dialog').close();
$('#item-form').onsubmit=e=>{e.preventDefault();const option=selectedOption();const existing=state.cart.find(r=>r.id===editing.id&&r.option===option);if(existing&&existing.qty+quantity>20){notify('같은 메뉴와 옵션은 최대 20개까지 담을 수 있어요.');return;}if(existing)existing.qty+=quantity;else state.cart.push({id:editing.id,option,qty:quantity});$('#item-dialog').close();render();notify(editing.name+' '+quantity+'개를 담았어요.');};
$('#content').onclick=e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.category){state.category=b.dataset.category;render();}else if(b.dataset.menu)openItem(b.dataset.menu);else if(b.dataset.go)go(b.dataset.go);else if(b.dataset.payment!==undefined){state.payment=PAYMENTS[Number(b.dataset.payment)][0];state.stage='finished';render();}else if(b.id==='new-order'){state.cart=[];state.stage='menu';state.payment=null;state.category='전체';render();}};
$('#cart-items').onclick=e=>{const b=e.target.closest('button');if(!b||b.disabled||state.stage==='finished')return;for(const key of ['plus','minus','remove'])if(b.dataset[key]!==undefined){const i=Number(b.dataset[key]);if(key==='plus')state.cart[i].qty=Math.min(20,state.cart[i].qty+1);else if(key==='remove'||state.cart[i].qty===1)state.cart.splice(i,1);else state.cart[i].qty--;if(!state.cart.length)state.stage='menu';render();break;}};
$('#next-step').onclick=()=>go(state.stage==='menu'?'review':'payment');
$('#dine-in').onclick=()=>{state.mode='매장에서 먹기';render();};$('#takeout').onclick=()=>{state.mode='포장하기';render();};
$('#reset').onclick=()=>{resetAll=true;$('#reset-dialog').showModal();};$('#clear-cart').onclick=()=>{resetAll=false;$('#reset-dialog').showModal();};$('#keep-order').onclick=()=>$('#reset-dialog').close();$('#confirm-reset').onclick=()=>{state.cart=[];state.stage='menu';state.payment=null;if(resetAll){state.mode='매장에서 먹기';state.category='전체';}$('#reset-dialog').close();render();notify('새 주문을 시작할 수 있어요.');};
render();


