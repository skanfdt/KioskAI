(() => {
 const main=document.querySelector('main');
 const txt=(ctx,sel)=>ctx?.querySelector(sel)?.innerText.trim()||'';
 const n=x=>{const match=x.match(/[\d,]+/);return match?Number(match[0].replaceAll(',','')):null;};
 function view(){
   const stage=main.dataset.stage;
   const rows=[...main.querySelectorAll('.ticket tr')].slice(1).map(tr=>{
     const cols=[...tr.querySelectorAll('td')].map(td=>td.innerText.trim());
     return [cols[0],cols[1],n(cols[2]),n(cols[3])];});
   return {stage,heading:txt(main,'h1'),rows,count:n(txt(main,'.sum span')),total:n(txt(main,'.sum strong')),method:txt(main,'.method'),
     editor:stage==='editor'?{options:[...main.querySelectorAll('input:checked')].map(c=>txt(c.closest('label'),'span')),quantity:n(txt(main,'output')),amount:n(txt(main,'.commit strong'))}:null};
 }
 const history=[],clicks=[],activations=[];
 const remember=()=>{const v=view();if(JSON.stringify(v)!==JSON.stringify(history.at(-1)))history.push(v);};
 new MutationObserver(remember).observe(main,{subtree:true,attributes:true,characterData:true,childList:true});
 document.addEventListener('pointerdown',event=>{
    const el=event.target.closest('button,label')||event.target;
    clicks.push({at:performance.now(),trusted:event.isTrusted,stage:view().stage,label:el.getAttribute('aria-label')||el.innerText,before:view()});
 },{capture:true,passive:true});
 remember();
 document.addEventListener('click',event=>{
    const before=view();
    if(!['payment','receipt'].includes(before.stage))return;
    const el=event.target.closest('button')||event.target;
    activations.push({at:performance.now(),trusted:event.isTrusted,stage:before.stage,label:el.getAttribute('aria-label')||el.innerText,before});
 },{capture:true,passive:true});
 window.readStandProof=()=>({last:view(),history,clicks,activations});
})()
