'use strict';
const menu = __MENU_DATA__;
const main = document.querySelector('main');
const money = n => n.toLocaleString('ko-KR') + '원';
const button = (label, cmd, className='') => '<button type="button" data-action="'+cmd+'" class="'+className+'">'+label+'</button>';
let cart = [];
let order = null;
let stage = 'menu';
let tender = '';
function summary(){
  const count=cart.reduce((n,row)=>n+row.quantity,0);
  const amount=cart.reduce((n,row)=>n+row.unit*row.quantity,0);
  return '<p class="sum"><span>총 '+count+'개</span><strong>'+money(amount)+'</strong></p>';
}
function menuPage(){
  const groups = menu.categories.map(cat => '<h2>'+cat+'</h2><ul class="rack">'+menu.products.filter(p=>p.category===cat).map(p=>'<li><button class="tile '+(p.option_groups.length?'fruit':'bake')+'" data-open="'+p.id+'"><span>'+p.name+'</span><span>'+money(p.price)+'</span></button></li>').join('')+'</ul>').join('');
  return '<h1>메뉴</h1>'+groups+'<section class="footer"><h2>주문표</h2>'+summary()+'<button data-action="review" class="primary" '+(cart.length?'':'disabled')+'>주문표 보기</button></section>';
}
function choices(){
  return order.product.option_groups.map((g,i)=>'<fieldset><legend>'+g.name+'</legend>'+g.choices.map((c,j)=>'<label><input type="radio" name="g'+i+'" data-group="'+i+'" value="'+j+'" '+(order.indices[i]===j?'checked':'')+'><span>'+c.name+'</span>'+(c.extra?'<small>+'+money(c.extra)+'</small>':'')+'</label>').join('')+'</fieldset>').join('');
}
function subtotal(){return order.product.option_groups.reduce((v,g,i)=>v+g.choices[order.indices[i]].extra,order.product.price)*order.quantity;}
function updateDraft(){
  main.querySelector('output').textContent=order.quantity;
  main.querySelector('.commit strong').textContent=money(subtotal());
  main.querySelector('[data-action="minus"]').disabled=order.quantity<=1;
  main.querySelector('[data-action="plus"]').disabled=order.quantity>=50;
}
function draftPage(){
  return '<div class="right"><h1>'+order.product.name+'</h1>'+button('뒤로가기','menu')+'</div><section class="pane">'+choices()+'<h2>수량</h2><div class="qty"><button data-action="minus" aria-label="수량 줄이기">−</button><output aria-label="선택 수량"></output><button data-action="plus" aria-label="수량 늘리기">+</button></div><div class="commit"><span>상품 금액</span><strong></strong>'+button('주문표에 담기','add','primary')+'</div></section>';
}
function ticket(){
  return '<section class="ticket"><table><caption>주문 내역</caption><thead><tr><th>상품</th><th>옵션</th><th>수량</th><th>금액</th></tr></thead><tbody>'+cart.map(row=>'<tr><td>'+row.item+'</td><td>'+(row.options.join(' · ')||'기본')+'</td><td>'+row.quantity+'개</td><td>'+money(row.quantity*row.unit)+'</td></tr>').join('')+'</tbody></table><strong>합계</strong>'+summary()+'</section>';
}
function render(next){
 stage=next;main.dataset.stage=next;
 if(next==='menu')main.innerHTML=menuPage();
 if(next==='editor'){main.innerHTML=draftPage();updateDraft();}
 if(next==='review')main.innerHTML='<h1>주문표 확인</h1>'+ticket()+'<div class="actions">'+button('상품 더 담기','menu')+button('결제 방식 선택','payment','primary')+'</div>';
 if(next==='payment')main.innerHTML='<h1>결제 방식 선택</h1><div class="actions">'+['카드','현금','간편결제'].map(label=>'<button data-method="'+label+'">'+label+'</button>').join('')+'</div>'+ticket()+button('주문표로 돌아가기','review');
 if(next==='receipt')main.innerHTML='<h1>결제 방식 선택 완료</h1><p class="method">선택한 결제 방식 · '+tender+'</p>'+ticket();
 window.scrollTo(0,0);
}
main.addEventListener('click',e=>{
  const b=e.target.closest('button');
  if(!b||b.disabled)return;
  if(b.dataset.open){
    const product=menu.products.find(p=>p.id===b.dataset.open);
    order={product,indices:product.option_groups.map(g=>g.choices.findIndex(c=>c.name===g.default)),quantity:1};
    render('editor');return;
  }
  const act=b.dataset.action;
  if(act==='minus'||act==='plus'){order.quantity+=act==='plus'?1:-1;updateDraft();return;}
  if(act==='add'){
    const options=order.product.option_groups.map((g,i)=>g.choices[order.indices[i]].name);
    const same=cart.find(row=>row.item===order.product.name&&row.options.join('|')===options.join('|'));
    if(same)same.quantity+=order.quantity;
    else cart.push({item:order.product.name,quantity:order.quantity,options,unit:subtotal()/order.quantity});
    order=null;render('menu');return;
  }
  if(b.dataset.method&&stage==='payment'){tender=b.dataset.method;render('receipt');return;}
  if(['menu','review','payment'].includes(act))render(act);
});
main.addEventListener('change',e=>{
  if(!e.target.matches('[data-group]'))return;
  order.indices[Number(e.target.dataset.group)]=Number(e.target.value);updateDraft();
});
render('menu');
