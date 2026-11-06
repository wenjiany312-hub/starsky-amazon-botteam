(() => {
  'use strict';
  const rows = [...document.querySelectorAll('#keyword-table tbody tr')];
  const buttons = [...document.querySelectorAll('[data-filter]')];
  const search = document.getElementById('keyword-search');
  let filter = 'usable';
  let poolLabel = '';
  function refresh() {
    const query = search ? search.value.toLocaleLowerCase().trim() : '';
    let count = 0;
    for (const row of rows) {
      const match = filter.startsWith('pool:') ? (row.dataset.in || '').split(' ').includes(filter.slice(5))
        : filter === 'all' || (filter === 'copy' ? row.dataset.copy === '1' : filter === 'new_ads' ? row.dataset.ads === '1' : row.dataset.status === filter);
      row.hidden = !match || !row.textContent.toLocaleLowerCase().includes(query);
      if (!row.hidden) count++;
    }
    const label = document.getElementById('filter-count');
    if (label) label.textContent = filter.startsWith('pool:') ? `只看「${poolLabel}」：${count} 个词（点上方按钮切回其他筛选）` : `显示 ${count} / ${rows.length} 个唯一词`;
    const empty = document.getElementById('empty-filter');
    if (empty) empty.hidden = count > 0;
    buttons.forEach(b => b.setAttribute('aria-pressed', String(b.dataset.filter === filter)));
  }
  buttons.forEach(button => button.addEventListener('click', () => { filter = button.dataset.filter; refresh(); }));
  document.querySelectorAll('[data-show-pool]').forEach(button => button.addEventListener('click', () => {
    filter = 'pool:' + button.dataset.showPool;
    poolLabel = button.dataset.label;
    if (search) search.value = '';
    refresh();
    go('worksheet');
  }));
  if (search) search.addEventListener('input', refresh);
  refresh();
  document.querySelectorAll('[data-copy-target]').forEach(button => button.addEventListener('click', async () => {
    const target = document.getElementById(button.dataset.copyTarget);
    const status = document.getElementById('copy-status');
    try {
      if (!target || !target.textContent.trim() || target.textContent === '未提供') throw new Error('missing');
      const text = target.textContent;
      if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(text);
      else {
        const area = document.createElement('textarea');
        area.value = text; area.style.position = 'fixed'; area.style.left = '-9999px';
        document.body.appendChild(area); area.select();
        const copied = document.execCommand('copy'); area.remove();
        if (!copied) throw new Error('clipboard unavailable');
      }
      status.textContent = '已复制英文；审阅备注未复制。';
    } catch (_) { status.textContent = '未能复制，请选中英文正文复制。'; }
    window.setTimeout(() => { status.textContent = ''; }, 3500);
  }));
  document.querySelectorAll('[data-review-request]').forEach(button => button.addEventListener('click', () => {
    const source = document.getElementById(button.dataset.reviewRequest);
    const status = document.getElementById('copy-status');
    try {
      const request = JSON.parse(source.textContent);
      const url = URL.createObjectURL(new Blob([JSON.stringify(request, null, 2)], {type:'application/json'}));
      const link = document.createElement('a'); link.href = url; link.download = 'listing-review-request.json';
      document.body.appendChild(link); link.click(); link.remove(); URL.revokeObjectURL(url);
      status.textContent = '已生成审核请求；须先读取线上旧值并由本人确认。未写入。';
    } catch (_) { status.textContent = '审核请求生成失败；未写入。'; }
  }));
  const layout = document.getElementById('layout');
  const toc = document.getElementById('toc');
  const pin = document.getElementById('toc-pin');
  const toggle = document.getElementById('toc-toggle');
  const store = {
    get(key) { try { return window.localStorage.getItem(key); } catch (_) { return null; } },
    set(key, value) { try { window.localStorage.setItem(key, value); } catch (_) { /* private mode: keep default */ } }
  };
  function applyPin(on) {
    toc.classList.toggle('unpinned', !on);
    pin.setAttribute('aria-pressed', String(on));
    pin.textContent = on ? '锁定' : '未锁定';
  }
  function applyCollapse(collapsed) {
    layout.classList.toggle('collapsed', collapsed);
    toggle.setAttribute('aria-expanded', String(!collapsed));
    toggle.textContent = collapsed ? '目录' : '收起';
  }
  if (toc && pin && toggle && layout) {
    applyPin(store.get('s2-toc-pin') !== '0');
    applyCollapse(store.get('s2-toc-collapsed') === '1');
    pin.addEventListener('click', () => { const on = pin.getAttribute('aria-pressed') !== 'true'; applyPin(on); store.set('s2-toc-pin', on ? '1' : '0'); });
    toggle.addEventListener('click', () => { const c = !layout.classList.contains('collapsed'); applyCollapse(c); store.set('s2-toc-collapsed', c ? '1' : '0'); });
  }
  function go(id) {
    const target = document.getElementById(id);
    if (!target) return;
    for (let node = target; node; node = node.parentElement) if (node.tagName === 'DETAILS') node.open = true;
    target.scrollIntoView({ block: 'start' });
    window.setTimeout(spy, 0);
  }
  document.querySelectorAll('.toc a[href^="#"]').forEach(link => link.addEventListener('click', event => {
    event.preventDefault();
    const id = link.getAttribute('href').slice(1);
    go(id);
    history.replaceState(null, '', '#' + id);
  }));
  if (location.hash) go(decodeURIComponent(location.hash.slice(1)));
  document.querySelectorAll('[data-fold-all]').forEach(button => button.addEventListener('click', () => {
    const open = button.dataset.foldAll === 'open';
    document.querySelectorAll('main details.fold').forEach(d => { d.open = open; });
  }));
  const entries = [...document.querySelectorAll('.toc-list > li')];
  const blocks = entries.map(li => document.getElementById(li.dataset.for)).filter(Boolean);
  function spy() {
    let current = blocks[0];
    for (const block of blocks) if (block.getBoundingClientRect().top <= 120) current = block;
    entries.forEach(li => li.classList.toggle('active', Boolean(current) && li.dataset.for === current.id));
  }
  if (blocks.length) { window.addEventListener('scroll', spy, { passive: true }); spy(); }
  window.addEventListener('beforeprint', () => document.querySelectorAll('details').forEach(d => { d.open = true; }));
})();
