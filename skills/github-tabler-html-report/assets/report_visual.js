/* Progressive enhancement only: all report sections, rows and evidence ship in HTML. */
(() => {
  'use strict';
  const body = document.body;
  const sections = [...document.querySelectorAll('.report-section')];
  const groupButtons = [...document.querySelectorAll('[data-group-button]')];
  const index = document.querySelector('.full-index');
  let activeGroup = 'all';
  let printSnapshot = null;

  function showGroup(group) {
    activeGroup = group;
    sections.forEach(section => { section.hidden = group !== 'all' && section.dataset.group !== group; });
    groupButtons.forEach(button => {
      const active = button.dataset.groupButton === group;
      button.setAttribute('aria-pressed', String(active));
      button.classList.toggle('active', active);
    });
    if (index) index.open = group === 'all' && window.matchMedia('(min-width:981px)').matches;
  }

  function filterTable(input) {
    const table = document.getElementById(input.dataset.tableSearch);
    if (!table) return;
    const rows = [...table.tBodies].flatMap(tbody => [...tbody.rows]);
    const needle = input.value.trim().toLocaleLowerCase();
    let shown = 0;
    rows.forEach(row => {
      const matches = row.textContent.toLocaleLowerCase().includes(needle);
      row.hidden = !matches;
      if (matches) shown += 1;
    });
    const count = document.getElementById('count-' + table.id);
    const empty = document.getElementById('empty-' + table.id);
    if (count) count.textContent = `显示 ${shown} / ${rows.length} 行`;
    if (empty) empty.hidden = shown !== 0;
  }

  function revealHash(scroll = true) {
    let id;
    try { id = decodeURIComponent(location.hash.slice(1)); } catch (_) { id = location.hash.slice(1); }
    if (!id) return false;
    const target = document.getElementById(id);
    if (!target) return false;
    const section = target.closest('.report-section');
    if (section) showGroup(section.dataset.group);
    const table = target.closest('table');
    if (table) {
      document.querySelectorAll('[data-table-search]').forEach(input => {
        if (input.dataset.tableSearch === table.id) { input.value = ''; filterTable(input); }
      });
    }
    let ancestor = target;
    while (ancestor && ancestor !== body) {
      if (ancestor.tagName === 'DETAILS') ancestor.open = true;
      ancestor = ancestor.parentElement;
    }
    if (scroll) requestAnimationFrame(() => target.scrollIntoView({block: 'start'}));
    return true;
  }

  function beforePrint() {
    if (printSnapshot) return;
    printSnapshot = {
      group: activeGroup,
      hidden: [...document.querySelectorAll('[hidden]')],
      details: [...document.querySelectorAll('details')].map(detail => [detail, detail.open]),
      scrollX: window.scrollX,
      scrollY: window.scrollY
    };
    sections.forEach(section => { section.hidden = false; });
    document.querySelectorAll('table tr[hidden]').forEach(row => { row.hidden = false; });
    document.querySelectorAll('details').forEach(detail => { detail.open = true; });
    body.classList.add('printing');
  }

  function afterPrint() {
    if (!printSnapshot) return;
    const snapshot = printSnapshot;
    printSnapshot = null;
    showGroup(snapshot.group);
    snapshot.hidden.forEach(element => { element.hidden = true; });
    snapshot.details.forEach(([detail, open]) => { detail.open = open; });
    body.classList.remove('printing');
    window.scrollTo(snapshot.scrollX, snapshot.scrollY);
  }

  groupButtons.forEach(button => button.addEventListener('click', () => {
    const group = button.dataset.groupButton;
    showGroup(group);
    if (group === 'all') {
      // Explicit full view removes reading filters; print restoration calls showGroup only.
      document.querySelectorAll('[data-table-search]').forEach(input => {
        input.value = '';
        filterTable(input);
      });
      document.querySelectorAll('table tr[hidden]').forEach(row => { row.hidden = false; });
      document.querySelectorAll('details').forEach(detail => { detail.open = true; });
    }
  }));
  ['theme', 'density'].forEach(kind => {
    const buttons = [...document.querySelectorAll(`[data-${kind}-button]`)];
    buttons.forEach(button => button.addEventListener('click', () => {
      body.dataset[kind] = button.dataset[kind + 'Button'];
      buttons.forEach(item => item.setAttribute('aria-pressed', String(item === button)));
    }));
  });
  document.querySelectorAll('[data-table-search]').forEach(input => input.addEventListener('input', () => filterTable(input)));
  document.querySelectorAll('[data-print-report]').forEach(button => button.addEventListener('click', () => {
    beforePrint();
    try { window.print(); } finally { afterPrint(); }
  }));
  window.addEventListener('beforeprint', beforePrint);
  window.addEventListener('afterprint', afterPrint);
  window.addEventListener('hashchange', () => revealHash());
  // Repeated clicks on the same fragment still reopen any manually closed ancestors.
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href^="#"]');
    if (link && link.getAttribute('href') === location.hash) revealHash();
  });
  document.querySelectorAll('details[data-initial-open="false"]').forEach(detail => { detail.open = false; });
  body.classList.add('js');
  const elevator = document.querySelector('.elevator');
  const firstGroup = groupButtons.find(button => button.dataset.groupButton !== 'all');
  // 有左侧电梯时默认看完整报告：电梯负责定位，分组按钮保留为筛选。
  showGroup(elevator ? 'all' : (firstGroup ? firstGroup.dataset.groupButton : 'all'));
  revealHash();

  if (elevator) {
    const lifts = [...elevator.querySelectorAll('a.lift[href^="#"]')].filter(a => !a.classList.contains('top'));
    const targets = lifts.map(a => {
      let id; try { id = decodeURIComponent(a.getAttribute('href').slice(1)); } catch (_) { id = a.getAttribute('href').slice(1); }
      return [a, document.getElementById(id)];
    }).filter(([, el]) => el);
    const bar = elevator.querySelector('.elevator-progress i');
    elevator.addEventListener('click', event => {
      const link = event.target.closest('a.lift');
      if (!link) return;
      event.preventDefault();
      event.stopPropagation();  // 不让旧目录的"按分组显示"逻辑接管：电梯永远在完整报告里跳转
      const href = link.getAttribute('href');
      let id; try { id = decodeURIComponent(href.slice(1)); } catch (_) { id = href.slice(1); }
      const target = document.getElementById(id);
      if (!target) return;
      if (target.closest('.report-section') && target.closest('.report-section').hidden) showGroup('all');
      history.replaceState(null, '', href);
      lifts.forEach(a => a.classList.toggle('on', a === link));
      target.scrollIntoView({behavior: 'smooth', block: 'start'});
      setTimeout(spy, 700);
    });
    let ticking = false;
    function spy() {
      ticking = false;
      const line = window.innerHeight * 0.28;
      let current = null;
      let nearestTop = -Infinity;
      for (const [a, el] of targets) {
        if (el.closest('[hidden]')) continue;
        // Navigation is grouped by reading purpose, not by document position.
        // Choose the nearest visible heading above the reading line, regardless
        // of the link order; a break here used to pin the highlight at M07.
        const top = el.getBoundingClientRect().top;
        if (top <= line && top > nearestTop) { current = a; nearestTop = top; }
      }
      if (!current && targets.length) current = targets[0][0];
      lifts.forEach(a => a.classList.toggle('on', a === current));
      if (current) {
        const box = elevator;
        const r = current.getBoundingClientRect(), br = box.getBoundingClientRect();
        if (r.top < br.top + 40 || r.bottom > br.bottom - 40) box.scrollTop += r.top - br.top - 60;
      }
      if (bar) {
        const max = document.documentElement.scrollHeight - window.innerHeight;
        bar.style.width = (max > 0 ? Math.min(100, window.scrollY / max * 100) : 100) + '%';
      }
    }
    window.addEventListener('scroll', () => { if (!ticking) { ticking = true; requestAnimationFrame(spy); } }, {passive: true});
    window.addEventListener('resize', spy);
    spy();
  }
})();
