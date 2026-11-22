(() => {
  "use strict";

  const sections = {
    coffee: ["커피", "Coffee"],
    tea: ["차와 음료", "Tea & drinks"],
    bakery: ["베이커리", "Bakery"]
  };
  const drinks = ["따뜻하게", "아이스"];
  const menu = [
    { id: "americano", group: "coffee", name: "아메리카노", note: "견과류 향이 은은한 하우스 블렌드", price: 4300, temperatures: drinks },
    { id: "latte", group: "coffee", name: "카페라테", note: "에스프레소와 고소한 우유", price: 5200, temperatures: drinks },
    { id: "vanilla", group: "coffee", name: "바닐라빈 라테", note: "바닐라빈을 넣은 달콤한 라테", price: 5800, temperatures: drinks },
    { id: "coldbrew", group: "coffee", name: "콜드브루", note: "찬물로 천천히 우려낸 아이스 커피", price: 4900 },
    { id: "citron", group: "tea", name: "유자차", note: "유자 껍질의 향을 담은 과일차", price: 5000, temperatures: drinks },
    { id: "earlgrey", group: "tea", name: "얼그레이", note: "베르가모트 향이 맴도는 홍차", price: 4500, temperatures: drinks },
    { id: "grapefruit", group: "tea", name: "자몽 에이드", note: "자몽과 탄산수를 섞은 시원한 음료", price: 5500 },
    { id: "scone", group: "bakery", name: "버터 스콘", note: "바삭한 겉면과 부드러운 버터 향", price: 3800 },
    { id: "pound", group: "bakery", name: "레몬 파운드", note: "레몬 제스트를 넣어 구운 한 조각", price: 4500 },
    { id: "croissant", group: "bakery", name: "크루아상", note: "버터를 얇게 접어 낸 페이스트리", price: 4200 }
  ];
  const byId = new Map(menu.map(item => [item.id, item]));
  const tenders = ["카드", "간편결제", "현금"];
  const format = new Intl.NumberFormat("ko-KR");
  const won = value => format.format(value) + "원";
  const $ = selector => document.querySelector(selector);
  const main = $("#counter");
  const tray = $("#tray");
  const dialog = $("#customize");
  const store = "morrow-coffee.order.v1";
  const limit = 99;
  const session = { group: "coffee", page: "menu", items: [], tender: "" };
  try {
    const saved = JSON.parse(sessionStorage.getItem(store));
    if (saved && Array.isArray(saved.items)) {
      session.items = saved.items.filter(row => {
        const product = byId.get(row?.id);
        return product && (product.temperatures ?? [""]).includes(row.temperature) &&
          Number.isInteger(row.count) && row.count > 0 && row.count <= limit;
      });
      if (Object.hasOwn(sections, saved.group)) session.group = saved.group;
      if (tenders.includes(saved.tender)) session.tender = saved.tender;
      if (session.items.length && ["review", "tender", "receipt"].includes(saved.page)) {
        session.page = saved.page === "receipt" && !session.tender ? "tender" : saved.page;
      }
    }
  } catch { /* A new order also works when browser storage is unavailable. */ }

  let choice = null;
  let count = 1;
  const totalCount = () => session.items.reduce((sum, row) => sum + row.count, 0);
  const total = () => session.items.reduce((sum, row) => sum + byId.get(row.id).price * row.count, 0);
  function persist() {
    try { sessionStorage.setItem(store, JSON.stringify(session)); } catch { /* In-memory order remains usable. */ }
  }
  function announce(message) { $("#announcement").textContent = message; }
  function page(name) {
    session.page = name;
    render();
    main.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }
  function menuScreen() {
    const selected = session.group;
    const entries = menu.filter(item => item.group === selected);
    main.innerHTML = `
      <div class="intro"><div><h1>오늘의 메뉴</h1><small>메뉴를 눌러 옵션과 수량을 골라 주세요.</small></div><div class="coffee-photo" aria-hidden="true"></div></div>
      <div class="menu-body">
        <nav class="departments" aria-label="메뉴 분류">${Object.entries(sections).map(([key, [name]]) =>
          `<button type="button" data-group="${key}" aria-pressed="${key === selected}">${name}</button>`).join("")}</nav>
        <section aria-label="${sections[selected][0]} 메뉴"><div class="group-title"><h2>${sections[selected][0]}</h2><span lang="en">${sections[selected][1]}</span></div>
          <ul class="menu-list">${entries.map(item => `<li><button type="button" class="menu-item" data-item="${item.id}" aria-label="${item.name}, ${won(item.price)}">
            <span><strong>${item.name}</strong><span class="description">${item.note}</span></span>
            <span class="price">${format.format(item.price)}<span class="won">원</span></span><span class="plus" aria-hidden="true">+</span>
          </button></li>`).join("")}</ul>
        </section>
      </div>`;
    tray.className = "tray-shell";
    tray.innerHTML = `<div class="wrap tray-inner"><div class="tray-info"><span>담은 메뉴 ${totalCount()}개</span><strong>${won(total())}</strong></div>
      <button class="tray-button" type="button" data-page="review" ${session.items.length ? "" : "disabled"}>주문표 보기</button></div>`;
  }
  function rows(editable) {
    return session.items.map((row, index) => {
      const item = byId.get(row.id);
      const title = item.name + (row.temperature ? " " + row.temperature : "");
      return `<div class="bag-row"><div><strong>${item.name}</strong><span class="muted">${[row.temperature, row.count + "개"].filter(Boolean).join(" · ")}</span></div>
        <span class="subtotal">${won(item.price * row.count)}</span>
        ${editable ? `<div class="bag-controls"><div class="stepper">
          <button type="button" data-adjust="-1" data-row="${index}" aria-label="${title} 수량 줄이기" ${row.count === 1 ? "disabled" : ""}>−</button>
          <output>${row.count}</output><button type="button" data-adjust="1" data-row="${index}" aria-label="${title} 수량 늘리기" ${row.count === limit ? "disabled" : ""}>+</button></div>
          <button class="link-button" type="button" data-remove="${index}" aria-label="${title} 삭제">삭제</button></div>` : ""}
      </div>`;
    }).join("");
  }
  function checkoutScreen() {
    const isReceipt = session.page === "receipt";
    const isReview = session.page === "review";
    const title = isReview ? "내 주문표" : isReceipt ? "결제 방식 선택을 마쳤어요" : "결제 방식";
    const summary = `<div class="sum-line"><span>합계 · ${totalCount()}개</span><strong>${won(total())}</strong></div>`;
    const back = (target, label) => `<button type="button" class="link-button" data-page="${target}">← ${label}</button>`;
    const header = isReceipt ? "" : isReview ? back("menu", "메뉴로 돌아가기") : back("review", "주문표로 돌아가기");
    main.innerHTML = `<section class="flow"><div class="flow-head">${header}<h1>${title}</h1></div>
      ${isReview ? `<div class="sheet">${rows(true)}${summary}</div><div class="order-actions"><button type="button" class="secondary" data-page="menu">메뉴 더 담기</button><button type="button" class="primary advance" data-page="tender">결제 방식 고르기</button></div>`
        : isReceipt ? `<div class="sheet">${rows(false)}${summary}<p class="receipt-label">선택한 결제 방식 &nbsp; <span class="method-name">${session.tender}</span></p></div><div class="order-actions"><span class="note">연습 주문이며 실제 결제는 진행되지 않습니다.</span><button class="secondary" type="button" data-new>새 주문</button></div>`
        : `<div class="sheet">${rows(false)}${summary}</div><div class="pay-methods" aria-label="결제 방식 선택">${tenders.map(method => `<button type="button" class="pay-method" data-tender="${method}">${method}</button>`).join("")}</div><p class="note">연습 주문입니다. 결제 방식을 선택하면 체험이 끝납니다.</p>`}
      </section>`;
    tray.className = "";
    tray.replaceChildren();
  }
  function render() {
    if (session.page === "menu") menuScreen();
    else checkoutScreen();
    persist();
  }
  function updatePortion() {
    $("#drink-detail").textContent = choice.note;
    $("#portion").value = String(count);
    $("#fewer").disabled = count <= 1;
    $("#more").disabled = count >= limit;
    $("#pack").textContent = "주문표에 담기 · " + won(choice.price * count);
  }
  function customize(id) {
    choice = byId.get(id);
    count = 1;
    $("#drink-category").textContent = sections[choice.group][0];
    $("#drink-name").textContent = choice.name;
    $("#drink-choices").innerHTML = choice.temperatures ? `<fieldset><legend>음료 온도</legend><div class="chipset">${choice.temperatures.map((value, index) =>
      `<label><input type="radio" name="temperature" value="${value}" ${index === 0 ? "checked" : ""}><span>${value}</span></label>`).join("")}</div></fieldset>` : "";
    updatePortion();
    dialog.showModal();
  }
  document.addEventListener("click", event => {
    const button = event.target.closest("button");
    if (!button || button.disabled) return;
    const data = button.dataset;
    if (data.group) {
      session.group = data.group;
      render();
      main.querySelector(`[data-group="${data.group}"]`).focus({ preventScroll: true });
    } else if (data.item) customize(data.item);
    else if (data.page) page(data.page);
    else if (data.adjust) {
      const row = session.items[Number(data.row)];
      row.count = Math.min(limit, Math.max(1, row.count + Number(data.adjust)));
      render();
      announce(byId.get(row.id).name + " " + row.count + "개");
      const next = main.querySelector(`[data-row="${data.row}"][data-adjust="${data.adjust}"]:not(:disabled)`);
      if (next) next.focus({ preventScroll: true });
    } else if ("remove" in data) {
      session.items.splice(Number(data.remove), 1);
      if (!session.items.length) page("menu");
      else render();
      announce("메뉴를 주문표에서 삭제했습니다.");
    } else if (data.tender && tenders.includes(data.tender)) {
      session.tender = data.tender;
      page("receipt");
    } else if ("new" in data) {
      session.items = [];
      session.group = "coffee";
      session.tender = "";
      page("menu");
    }
  });
  dialog.querySelector(".close").addEventListener("click", () => dialog.close());
  $("#drink-choices").addEventListener("change", updatePortion);
  $("#fewer").addEventListener("click", () => { count = Math.max(1, count - 1); updatePortion(); });
  $("#more").addEventListener("click", () => { count = Math.min(limit, count + 1); updatePortion(); });
  $("#drink-form").addEventListener("submit", event => {
    event.preventDefault();
    const temperature = new FormData(event.currentTarget).get("temperature") || "";
    const row = session.items.find(item => item.id === choice.id && item.temperature === temperature);
    if (row && row.count + count > limit) {
      $("#drink-detail").textContent = "같은 메뉴와 옵션은 최대 " + limit + "개까지 담을 수 있어요. 현재 주문표에는 " + row.count + "개가 있습니다.";
      return;
    }
    if (row) row.count += count;
    else session.items.push({ id: choice.id, temperature, count });
    dialog.close();
    render();
    main.querySelector(`[data-item="${choice.id}"]`).focus({ preventScroll: true });
    announce(choice.name + "을 주문표에 담았습니다. 총 " + totalCount() + "개입니다.");
  });
  render();
})();
