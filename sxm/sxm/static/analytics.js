/* SXM Analytics: pick any set of channels and every number, chart and table recombines instantly. */
(function () {
  'use strict';
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const META = {
    views: { label: 'Views', fmt: 'num' },
    watch_h: { label: 'Watch time', fmt: 'hours' },
    subs: { label: 'Subscribers', fmt: 'num' },
    likes: { label: 'Likes', fmt: 'num' },
    revenue: { label: 'Revenue', fmt: 'money' },
  };
  const F = {
    num: (v) => Math.round(v).toLocaleString(),
    hours: (v) => v.toLocaleString(undefined, { maximumFractionDigits: v < 100 ? 1 : 0 }) + ' h',
    money: (v) => '$' + v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
  };
  const short = (v, fmt) => {
    const a = Math.abs(v), sign = v < 0 ? '-' : '';
    const pre = fmt === 'money' ? '$' : '';
    if (a >= 1e6) return sign + pre + (a / 1e6).toFixed(a >= 1e7 ? 0 : 1).replace(/\.0$/, '') + 'M';
    if (a >= 1e3) return sign + pre + (a / 1e3).toFixed(a >= 1e4 ? 0 : 1).replace(/\.0$/, '') + 'K';
    return sign + pre + (fmt === 'money' ? a.toFixed(a < 10 && a ? 2 : 0) : (Number.isInteger(a) ? a : a.toFixed(1)));
  };
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const day = (iso, opt) => new Date(iso + 'T12:00').toLocaleDateString(undefined, opt || { month: 'short', day: 'numeric' });
  const sum = (a) => a.reduce((x, y) => x + y, 0);

  function roll(el, to, fmt) {  // numbers roll to their new value
    if (!el) return;
    const f = F[fmt] || F.num, from = parseFloat(el.dataset.v || '0');
    el.dataset.v = to;
    if (reduce || from === to) { el.textContent = f(to); return; }
    const t0 = performance.now(), dur = 850;
    const tick = (t) => {
      const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 4);
      el.textContent = f(from + (to - from) * e);
      if (k < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }
  function deltaHtml(cur, prev) {
    if (!prev) return cur ? '<span class="ax-d new">New</span>' : '';
    const p = (cur - prev) / Math.abs(prev) * 100;
    if (Math.abs(p) < 0.05) return '<span class="ax-d flat">0%</span>';
    const up = p > 0;
    return `<span class="ax-d ${up ? 'up' : 'down'}"><svg viewBox="0 0 10 10"><path d="${up ? 'M5 2 9 7H1z' : 'M5 8 1 3h8z'}"/></svg>${Math.abs(p) >= 100 ? Math.round(Math.abs(p)) : Math.abs(p).toFixed(1)}%</span>`;
  }
  function slideSeg(seg) {  // the pill slides under the picked option
    const on = seg.querySelector('button.on'), ind = seg.querySelector('.ax-seg-ind');
    if (!on || !ind) return;
    ind.style.width = on.offsetWidth + 'px';
    ind.style.transform = `translateX(${on.offsetLeft}px)`;
  }
  const smooth = (pts) => {  // monotone-ish curve through the points
    if (!pts.length) return '';
    let d = `M${pts[0][0].toFixed(1)},${pts[0][1].toFixed(1)}`;
    for (let i = 1; i < pts.length; i++) {
      const p = pts[i - 1], q = pts[i], cx = (p[0] + q[0]) / 2;
      d += ` C${cx.toFixed(1)},${p[1].toFixed(1)} ${cx.toFixed(1)},${q[1].toFixed(1)} ${q[0].toFixed(1)},${q[1].toFixed(1)}`;
    }
    return d;
  };
  function niceMax(v) {
    if (v <= 0) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(v))), n = v / p;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
  }

  function init(root) {
    const boot = window.SXM_DASH, box = root.querySelector('#ax');
    if (!boot || !box) return;
    let D = boot.d, days = boot.days;
    const ids = D.channels.map((c) => c.id);
    let sel = new Set(boot.sel && boot.sel.length ? boot.sel.filter((i) => ids.includes(i)) : ids);
    if (!sel.size) sel = new Set(ids);
    let metric = 'views', mode = 'total', compare = true, sortKey = 'views', sortDir = -1;
    const $ = (s) => box.querySelector(s);

    const chosen = () => D.channels.filter((c) => sel.has(c.id));
    const total = (key, half, list) => {
      const out = new Array(D.labels.length).fill(0);
      (list || chosen()).forEach((c) => c[half][key].forEach((v, i) => { out[i] += v; }));
      return out;
    };
    const allSelected = () => sel.size === ids.length;

    // ---------- channel picker ----------
    function paintChips() {
      box.querySelectorAll('.ax-chip[data-ch]').forEach((b) => {
        const on = sel.has(+b.dataset.ch);
        b.classList.toggle('on', on); b.setAttribute('aria-pressed', on);
      });
      $('.ax-chip.all').classList.toggle('on', allSelected());
    }
    box.querySelectorAll('.ax-chip[data-ch]').forEach((b) => b.addEventListener('click', () => {
      const id = +b.dataset.ch;
      if (sel.has(id) && sel.size === 1) { b.classList.remove('shake'); void b.offsetWidth; b.classList.add('shake'); return; }
      sel.has(id) ? sel.delete(id) : sel.add(id);
      b.classList.remove('pop'); void b.offsetWidth; b.classList.add('pop');
      update();
    }));
    $('.ax-chip.all').addEventListener('click', () => { sel = new Set(ids); update(); });

    // ---------- KPI cards ----------
    function paintKpis() {
      const list = chosen();
      Object.keys(META).forEach((k) => {
        const card = box.querySelector(`.ax-kpi[data-metric="${k}"]`);
        if (!card) return;
        const cur = total(k, 'cur'), prev = total(k, 'prev');
        let val = sum(cur), sub = `vs previous ${days} days`;
        let d = deltaHtml(sum(cur), sum(prev));
        if (k === 'subs') {
          val = sum(list.map((c) => c.subscribers || 0));
          const net = sum(cur);
          sub = `${net >= 0 ? '+' : ''}${Math.round(net).toLocaleString()} in ${days} days`;
          d = deltaHtml(net, sum(prev));
        }
        if (k === 'revenue') {
          const camp = allSelected() ? (D.campaign_income || 0) : 0;
          val += camp;
          if (camp) sub = `${F.money(sum(cur))} YouTube · ${F.money(camp)} campaigns`;
        }
        roll(card.querySelector('.ax-kpi-val'), val, META[k].fmt);
        card.querySelector('.ax-delta').innerHTML = d;
        card.querySelector('.ax-kpi-sub').textContent = sub;
        sparkline(card.querySelector('.ax-kpi-spark'), cur, prev);
      });
    }
    function sparkline(svg, cur, prev) {
      const n = cur.length, W = 120, H = 32;
      const mx = Math.max(1, ...cur, ...prev.map((v) => Math.max(0, v)));
      const mn = Math.min(0, ...cur);
      const y = (v) => 3 + (1 - (v - mn) / (mx - mn || 1)) * (H - 6);
      const pts = (a) => a.map((v, i) => [n < 2 ? 0 : i / (n - 1) * W, y(v)]);
      const c = smooth(pts(cur)), p = smooth(pts(prev));
      if (!svg.firstChild) {
        svg.innerHTML = `<defs><linearGradient id="kg${Math.random().toString(36).slice(2, 7)}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="currentColor" stop-opacity=".28"/><stop offset="1" stop-color="currentColor" stop-opacity="0"/></linearGradient></defs>
          <path class="pv"/><path class="ar"/><path class="ln"/>`;
        svg.querySelector('.ar').setAttribute('fill', `url(#${svg.querySelector('linearGradient').id})`);
      }
      setD(svg.querySelector('.pv'), p);
      setD(svg.querySelector('.ar'), `${c} L${W},${H} L0,${H} Z`);
      setD(svg.querySelector('.ln'), c);
    }
    function setD(el, d) { el.style.d = `path("${d}")`; el.setAttribute('d', d); }

    box.querySelectorAll('.ax-kpi').forEach((card) => card.addEventListener('click', () => {
      metric = card.dataset.metric;
      box.querySelectorAll('.ax-kpi').forEach((c) => c.classList.toggle('on', c === card));
      paintChart(true);
    }));

    // ---------- combined views + share bar ----------
    function paintCombined() {
      const list = chosen(), views = list.map((c) => sum(c.cur.views)), all = sum(views);
      roll($('#ax-combined'), all, 'num');
      $('#ax-combined-sub').textContent = allSelected() ? `across all ${ids.length} channels · ${days} days`
        : `across ${list.length} of ${ids.length} channels · ${days} days`;
      const bar = $('#ax-share');
      D.channels.forEach((c) => {
        let seg = bar.querySelector(`[data-seg="${c.id}"]`);
        if (!seg) {
          seg = document.createElement('span'); seg.dataset.seg = c.id; seg.style.setProperty('--c', c.color);
          bar.appendChild(seg);
        }
        const v = sel.has(c.id) ? sum(c.cur.views) : 0;
        seg.style.flexGrow = all ? v / all * 100 : (sel.has(c.id) ? 1 : 0);
        seg.title = `${c.name}: ${F.num(v)} views`;
      });
      $('#ax-share-legend').innerHTML = list.map((c, i) => {
        const v = views[i];
        return `<span style="--c:${c.color}"><i></i>${esc(c.name)} <b>${all ? (v / all * 100).toFixed(1) : '0'}%</b></span>`;
      }).join('');
    }

    // ---------- main chart ----------
    const chart = $('#ax-chart'), tip = chart.querySelector('.ax-tip');
    let W = 1000, H = 320; const P = { l: 48, r: 12, t: 14, b: 30 };
    let geo = null;
    function paintChart(changed) {
      const fmt = META[metric].fmt, list = chosen(), n = D.labels.length;
      W = Math.max(300, chart.clientWidth || 1000); H = Math.max(200, chart.clientHeight || 320);  // real pixels, so text stays crisp
      const cur = total(metric, 'cur'), prev = total(metric, 'prev');
      $('#ax-chart-label').textContent = META[metric].label + (metric === 'subs' ? ' gained' : '') + ` · last ${days} days`;
      roll($('#ax-chart-total'), sum(cur), fmt);
      $('#ax-chart-delta').innerHTML = deltaHtml(sum(cur), sum(prev)) + `<span class="ax-k">vs previous ${days} days</span>`;
      const stack = mode === 'stack' && list.length > 1;
      const layers = stack ? list.map((c) => ({ c, v: c.cur[metric] })) : [];
      const top = stack ? cur : cur;
      const hi = niceMax(Math.max(1, ...top, ...(compare ? prev : [])));
      const lo = Math.min(0, ...cur);
      const x = (i) => P.l + (n < 2 ? 0 : i / (n - 1)) * (W - P.l - P.r);
      const y = (v) => P.t + (1 - (v - lo) / (hi - lo || 1)) * (H - P.t - P.b);
      const base = y(Math.max(lo, 0));
      let svg = chart.querySelector('svg');
      if (!svg) {
        chart.insertAdjacentHTML('afterbegin', `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="ax-svg">
          <defs><linearGradient id="axg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#22d3ee" stop-opacity=".32"/><stop offset="1" stop-color="#22d3ee" stop-opacity="0"/></linearGradient></defs>
          <g class="grid"></g><g class="xl"></g><g class="layers"></g>
          <path class="prev"/><path class="area"/><path class="line" pathLength="1"/>
          <line class="cross" y1="${P.t}" y2="${H - P.b}"/><circle class="dot" r="5" cx="-50" cy="-50"/></svg>`);
        svg = chart.querySelector('svg');
        let rz = 0; addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(() => document.body.contains(chart) && paintChart(false), 150); });
        svg.addEventListener('pointermove', hover);
        svg.addEventListener('pointerleave', () => { tip.hidden = true; svg.classList.remove('hovering'); });
      }
      svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
      const cr0 = svg.querySelector('.cross'); cr0.setAttribute('y1', P.t); cr0.setAttribute('y2', H - P.b);
      svg.querySelector('.grid').innerHTML = [0, .25, .5, .75, 1].map((k) => {
        const v = lo + (hi - lo) * k, yy = y(v);
        return `<line x1="${P.l}" x2="${W - P.r}" y1="${yy}" y2="${yy}"/><text x="${P.l - 10}" y="${yy + 4}">${short(v, fmt)}</text>`;
      }).join('');
      const step = Math.max(1, Math.ceil(n / Math.max(3, Math.min(7, Math.floor(W / 110)))));
      svg.querySelector('.xl').innerHTML = D.labels.map((s, i) => (i % step && i !== n - 1) || (i === n - 1 && (n - 1) % step && (n - 1) % step < step / 2)
        ? '' : `<text x="${x(i)}" y="${H - 8}" text-anchor="${i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'}">${day(s)}</text>`).join('');
      const line = smooth(cur.map((v, i) => [x(i), y(v)]));
      setD(svg.querySelector('.line'), line);
      setD(svg.querySelector('.area'), `${line} L${x(n - 1)},${base} L${x(0)},${base} Z`);
      setD(svg.querySelector('.prev'), smooth(prev.map((v, i) => [x(i), y(v)])));
      svg.querySelector('.prev').classList.toggle('off', !compare);
      svg.classList.toggle('stacked', stack);
      // stacked layers, one colour per channel
      const g = svg.querySelector('.layers');
      let acc = new Array(n).fill(0);
      const want = new Set(layers.map((l) => String(l.c.id)));
      g.querySelectorAll('path').forEach((p) => { if (!want.has(p.dataset.ch)) p.remove(); });
      layers.forEach((l) => {
        const lowPts = acc.map((v, i) => [x(i), y(v)]);
        acc = acc.map((v, i) => v + l.v[i]);
        const upPts = acc.map((v, i) => [x(i), y(v)]);
        let p = g.querySelector(`path[data-ch="${l.c.id}"]`);
        if (!p) { p = document.createElementNS('http://www.w3.org/2000/svg', 'path'); p.dataset.ch = l.c.id; g.appendChild(p); }
        p.style.setProperty('--c', l.c.color);
        const back = lowPts.slice().reverse().map((q) => `L${q[0].toFixed(1)},${q[1].toFixed(1)}`).join(' ');
        setD(p, `${smooth(upPts)} ${back} Z`);
      });
      if (changed && !reduce) { svg.classList.remove('draw'); void svg.getBoundingClientRect(); svg.classList.add('draw'); }
      geo = { x, y, cur, prev, list, fmt, n };
    }
    function hover(e) {
      if (!geo) return;
      const svg = chart.querySelector('svg'), r = svg.getBoundingClientRect();
      const fx = (e.clientX - r.left) / r.width * W;
      const i = Math.max(0, Math.min(geo.n - 1, Math.round((fx - P.l) / (W - P.l - P.r) * (geo.n - 1))));
      const px = geo.x(i), py = geo.y(geo.cur[i]);
      svg.classList.add('hovering');
      const cr = svg.querySelector('.cross'), dot = svg.querySelector('.dot');
      cr.setAttribute('x1', px); cr.setAttribute('x2', px); dot.setAttribute('cx', px); dot.setAttribute('cy', py);
      const f = F[geo.fmt];
      const rows = geo.list.length > 1 ? geo.list.map((c) => `<span class="row"><i style="--c:${c.color}"></i>${esc(c.name)}<b>${f(c.cur[metric][i])}</b></span>`).join('') : '';
      const pd = new Date(D.labels[i] + 'T12:00'); pd.setDate(pd.getDate() - geo.n);
      tip.innerHTML = `<span class="d">${day(D.labels[i], { weekday: 'short', month: 'short', day: 'numeric' })}</span>
        <span class="big">${f(geo.cur[i])}</span>
        ${compare ? `<span class="pv">${f(geo.prev[i])} on ${pd.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</span>` : ''}${rows}`;
      tip.hidden = false;
      const left = px / W * r.width, tw = tip.offsetWidth;
      tip.style.transform = `translate(${Math.min(r.width - tw, Math.max(0, left + (left > r.width / 2 ? -tw - 16 : 16)))}px, ${Math.max(0, py / H * r.height - 40)}px)`;
    }
    const modeSeg = $('#ax-mode');
    modeSeg.querySelectorAll('button').forEach((b) => b.addEventListener('click', () => {
      mode = b.dataset.mode;
      modeSeg.querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === b));
      slideSeg(modeSeg); paintChart(true);
    }));
    $('#ax-compare').addEventListener('change', (e) => { compare = e.target.checked; paintChart(false); });

    // ---------- channel table ----------
    function paintTable() {
      const rows = chosen().map((c) => {
        const v = sum(c.cur.views), pv = sum(c.prev.views);
        return { c, name: c.name.toLowerCase(), views: v, delta: pv ? (v - pv) / pv : (v ? 9e9 : 0), pv,
          watch: sum(c.cur.watch_h), subscribers: c.subscribers || 0, net: sum(c.cur.subs) };
      });
      rows.sort((a, b) => (a[sortKey] > b[sortKey] ? 1 : a[sortKey] < b[sortKey] ? -1 : 0) * sortDir);
      const max = Math.max(1, ...rows.map((r) => r.views));
      $('#ax-table tbody').innerHTML = rows.map((r, i) => `<tr style="--i:${i}">
        <td><span class="ax-tc"><i style="--c:${r.c.color}"></i>${r.c.avatar ? `<img src="${esc(r.c.avatar)}" alt="" referrerpolicy="no-referrer">` : `<span class="ax-av">${esc(r.c.name[0])}</span>`}
          <span><b>${esc(r.c.name)}</b><span class="ax-bar"><i style="--w:${(r.views / max * 100).toFixed(1)}%;--c:${r.c.color}"></i></span></span></span></td>
        <td class="r num">${F.num(r.views)}</td><td class="r">${deltaHtml(r.views, r.pv)}</td>
        <td class="r num">${r.watch.toLocaleString(undefined, { maximumFractionDigits: 1 })}</td>
        <td class="r num">${F.num(r.subscribers)}</td>
        <td class="r num ${r.net > 0 ? 'pos' : r.net < 0 ? 'neg' : ''}">${r.net > 0 ? '+' : ''}${F.num(r.net)}</td>
        <td class="r"><svg class="ax-mini" viewBox="0 0 80 24" preserveAspectRatio="none" style="--c:${r.c.color}"><path d="${miniPath(r.c.cur.views)}"/></svg></td></tr>`).join('');
      box.querySelectorAll('#ax-table th[data-sort]').forEach((th) => th.classList.toggle('on', th.dataset.sort === sortKey));
    }
    const miniPath = (a) => {
      const mx = Math.max(1, ...a);
      return smooth(a.map((v, i) => [a.length < 2 ? 0 : i / (a.length - 1) * 80, 22 - v / mx * 20]));
    };
    box.querySelectorAll('#ax-table th[data-sort]').forEach((th) => th.addEventListener('click', () => {
      const k = th.dataset.sort;
      sortDir = sortKey === k ? -sortDir : (k === 'name' ? 1 : -1); sortKey = k;
      th.dataset.dir = sortDir; paintTable();
    }));

    // ---------- weekdays + lifetime ----------
    function paintWeek() {
      const cur = total('views', 'cur'), bins = [0, 0, 0, 0, 0, 0, 0], cnt = [0, 0, 0, 0, 0, 0, 0];
      D.labels.forEach((s, i) => { const w = (new Date(s + 'T12:00').getDay() + 6) % 7; bins[w] += cur[i]; cnt[w]++; });
      const avg = bins.map((b, i) => cnt[i] ? b / cnt[i] : 0), mx = Math.max(1, ...avg), best = avg.indexOf(Math.max(...avg));
      const names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
      const wk = $('#ax-week');
      if (!wk.children.length) wk.innerHTML = names.map((nm) => `<div class="ax-wb"><span class="v"></span><span class="bcol"><i></i></span><span class="n">${nm}</span></div>`).join('');
      [...wk.children].forEach((el, i) => {
        el.classList.toggle('best', i === best && avg[i] > 0);
        el.querySelector('i').style.height = (avg[i] / mx * 100).toFixed(1) + '%';
        el.querySelector('.v').textContent = short(Math.round(avg[i]), 'num');
        el.title = `${names[i]}: ${F.num(avg[i])} average views`;
      });
      const list = chosen();
      roll($('#ax-life-views'), sum(list.map((c) => c.total_views || 0)), 'num');
      roll($('#ax-life-subs'), sum(list.map((c) => c.subscribers || 0)), 'num');
      roll($('#ax-life-videos'), sum(list.map((c) => c.videos || 0)), 'num');
    }

    // ---------- top videos ----------
    function paintVideos() {
      const color = Object.fromEntries(D.channels.map((c) => [c.id, c.color]));
      const vids = D.videos.filter((v) => sel.has(v.channel_id)).slice(0, 10);
      const mx = Math.max(1, ...vids.map((v) => v.period_views || v.views || 0));
      $('#ax-videos').innerHTML = vids.length ? vids.map((v, i) => {
        const pv = v.period_views || 0;
        return `<a class="ax-vid" href="https://www.youtube.com/watch?v=${encodeURIComponent(v.video_id)}" target="_blank" rel="noopener" style="--i:${i}">
          <span class="rk">${i + 1}</span>
          <span class="th">${v.thumb ? `<img src="${esc(v.thumb)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : ''}${v.duration_s && v.duration_s <= 180 ? '<em>Short</em>' : ''}</span>
          <span class="tx"><b>${esc(v.title)}</b><span class="ax-k"><i style="--c:${color[v.channel_id]}"></i>${esc(v.channel_name)}${v.published ? ' · ' + day(String(v.published).slice(0, 10), { month: 'short', day: 'numeric', year: 'numeric' }) : ''}</span>
            <span class="ax-bar"><i style="--w:${(pv / mx * 100).toFixed(1)}%;--c:${color[v.channel_id]}"></i></span></span>
          <span class="st"><b>${F.num(pv)}</b><span class="ax-k">${F.num(v.views || 0)} all time</span></span></a>`;
      }).join('') : '<div class="ax-k ax-pad">No videos for these channels yet.</div>';
    }

    // ---------- date range ----------
    const rangeSeg = $('#ax-range');
    rangeSeg.querySelectorAll('button').forEach((b) => b.addEventListener('click', async () => {
      if (b.classList.contains('on')) return;
      rangeSeg.querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === b));
      slideSeg(rangeSeg);
      box.classList.add('loading');
      try {
        const j = await (await fetch('/api/analytics?days=' + b.dataset.days)).json();
        D = j; days = j.days;
        box.querySelector('#ax-refresh-form [name=back]').value = '/analytics?days=' + days;
        update(true);
      } catch (_) {}
      box.classList.remove('loading');
    }));

    function update(changed) {
      paintChips(); paintCombined(); paintKpis(); paintChart(changed !== false); paintTable(); paintWeek(); paintVideos();
      const q = new URLSearchParams({ days });
      if (!allSelected()) q.set('ch', [...sel].join(','));
      history.replaceState(history.state, '', '/analytics?' + q);
    }
    update(true);
    requestAnimationFrame(() => { slideSeg(rangeSeg); slideSeg(modeSeg); });
    addEventListener('resize', () => { slideSeg(rangeSeg); slideSeg(modeSeg); });

    // While a sync runs, keep checking and fold the new numbers in when it's done.
    clearInterval(window.__axTimer);
    if (D.syncing) {
      window.__axTimer = setInterval(async () => {
        if (!document.getElementById('ax')) return clearInterval(window.__axTimer);
        try {
          const j = await (await fetch('/api/analytics?days=' + days)).json();
          if (!j.syncing) {
            clearInterval(window.__axTimer);
            D = j; update(true);
            box.querySelector('.ax-live').classList.remove('busy');
            $('#ax-status').textContent = `Live from ${D.channels.length} channel${D.channels.length === 1 ? '' : 's'} · just updated`;
            const rb = box.querySelector('.ax-icon-btn'); rb.classList.remove('spin'); rb.disabled = false;
            if (window.SXM_toast) window.SXM_toast('Numbers updated');
          }
        } catch (_) {}
      }, 2500);
    }
  }
  window.SXMAnalytics = init;
})();
