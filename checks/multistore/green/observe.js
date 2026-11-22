(() => {
  const text = (element, selector) => element.querySelector(selector)?.innerText.trim() ?? '';
  const number = value => {
    const match = value.match(/[\d,]+/);
    return match ? Number(match[0].replaceAll(',', '')) : null;
  };
  function view() {
    const openDialog = document.querySelector('#item-dialog[open]');
    const stage = openDialog ? 'editor' :
      document.querySelector('.finish-panel') ? 'receipt' :
      document.querySelector('.payment-panel') ? 'payment' :
      document.querySelector('.review-panel') ? 'review' : 'menu';
    const rows = [...document.querySelectorAll('#cart-items article.cart-item')].map(row => [
      text(row, 'h3'), text(row, 'small'), number(text(row, 'output')), number(text(row, '.line-bottom strong'))
    ]);
    const reviewRows = [...document.querySelectorAll('.review-row')].map(row => {
      const details = text(row, 'p').split(' · ');
      const side = details.slice(2).join(' · ').replace(/ 포함$/, '');
      return [text(row, 'h3'), side ? details[0] + ' · ' + side : details[0],
              number(details[1] ?? ''), number(text(row, 'strong'))];
    });
    return {
      stage, heading: text(document, '#page-title'), rows,
      count: number(text(document, '#cart-count')), total: number(text(document, '#total-price')),
      dining: text(document, '.dining button[aria-pressed="true"]'),
      mode_summary: text(document, stage === 'receipt' ? '.selection-summary' : '.review-mode'),
      review_rows: reviewRows, review_total: number(text(document, '.review-total span:last-child')),
      method: text(document, '.selection-summary strong'),
      editor: openDialog ? {
        item: text(openDialog, '#item-title'),
        option: openDialog.querySelector('input:checked')?.value ?? '',
        quantity: number(text(openDialog, '#modal-quantity'))
      } : null
    };
  }
  const history = [], clicks = [], activations = [];
  function remember() {
    const current = view();
    if (JSON.stringify(history.at(-1)) !== JSON.stringify(current)) history.push(current);
  }
  function eventInfo(event, before) {
    const el = event.target.closest('button,label,input,select,a') ?? event.target;
    const raw = el.getAttribute('aria-label') || el.innerText?.trim() || '';
    const label = el.matches('button.payment-option') ? text(el, 'strong') : raw;
    return {at: performance.now(), trusted: event.isTrusted, stage: before.stage,
            label, raw_label: raw, before};
  }
  new MutationObserver(remember).observe(document.body, {
    childList:true, subtree:true, attributes:true, characterData:true
  });
  document.addEventListener('pointerdown', event => clicks.push(eventInfo(event, view())),
                            {capture:true, passive:true});
  document.addEventListener('click', event => {
    const before = view();
    if (before.stage === 'payment' || before.stage === 'receipt')
      activations.push(eventInfo(event, before));
  }, {capture:true, passive:true});
  remember();
  window.readStandProof = () => ({last:view(), history, clicks, activations});
})();
