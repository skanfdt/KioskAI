() => {
  const text = (selector, root = document) => root.querySelector(selector)?.innerText.trim() || "";
  const won = (label) => {
    const match = /^([\d,]+)\s*원$/.exec(label);
    return match ? Number(match[1].replaceAll(",", "")) : null;
  };
  const pages = new Map([
    ["오늘의 메뉴", "menu"],
    ["내 주문표", "review"],
    ["결제 방식", "payment"],
    ["결제 방식 선택을 마쳤어요", "receipt"],
  ]);

  function read() {
    const heading = text("main h1");
    const dialog = document.querySelector("#customize[open]");
    const stage = dialog ? "editor" : pages.get(heading) || "unknown";
    const rows = Array.from(document.querySelectorAll(".bag-row"), (row) => {
      const description = text(".muted", row);
      const pieces = /^(?:(.*?)\s*·\s*)?(\d+)개$/.exec(description);
      return [text("strong", row), pieces ? (pieces[1] || "").trim() : description,
              pieces ? Number(pieces[2]) : null, won(text(".subtotal", row))];
    });
    const countLabel = text(".sum-line span") || text(".tray-info span");
    const countMatch = /(?:^|\s)(\d+)개$/.exec(countLabel);
    let editor = null;
    if (dialog) {
      editor = {
        item: text("#drink-name", dialog),
        option: dialog.querySelector('input[type="radio"]:checked')?.value || "",
        quantity: Number(text("#portion", dialog)),
        amount: won(text("#pack", dialog).split("·").at(-1).trim())
      };
    }
    return {stage, heading, rows, count: countMatch ? Number(countMatch[1]) : null,
            total: won(text(".sum-line strong") || text(".tray-info strong")),
            method: text(".method-name"), editor};
  }

  const history = [], clicks = [], activations = [];
  function sample() {
    const value = read();
    if (JSON.stringify(history.at(-1)) !== JSON.stringify(value)) history.push(value);
    return value;
  }
  new MutationObserver(sample).observe(document.body, {
    subtree: true, childList: true, characterData: true, attributes: true
  });
  function mark(event) {
    const before = sample();
    const control = event.target.closest("button,input,label");
    const label = control?.getAttribute("aria-label") || (control?.innerText || control?.value || "").trim();
    const item = {at: performance.now(), stage: before.stage, label, trusted: event.isTrusted, before};
    if (event.type === "pointerdown") clicks.push(item);
    else if (before.stage === "payment" || before.stage === "receipt") activations.push(item);
  }
  document.addEventListener("pointerdown", mark, {capture:true, passive:true});
  document.addEventListener("click", mark, {capture:true, passive:true});
  sample();
  window.readStandProof = () => ({last: sample(), history, clicks, activations});
}
