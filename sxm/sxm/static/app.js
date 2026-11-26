/* SXM: smooth navigation without a framework.
   Links and forms load in the background and swap the page in with a
   cross-fade, so the dashboard never flashes white or jumps. OAuth links,
   new tabs and downloads still behave normally. */
(() => {
  const main = () => document.getElementById('main');
  const bar = document.getElementById('progress');
  const canBoost = (url) => url.origin === location.origin &&
    !/^\/(oauth|media|static|api|login|download\/)/.test(url.pathname);
  let busy = 0;

  function progress(on) {
    busy += on ? 1 : -1;
    bar.classList.toggle('on', busy > 0);
  }

  function toast(text, kind) {
    if (!text) return;
    const t = document.createElement('div');
    t.className = 'toast ' + (kind || '');
    t.textContent = text;
    document.getElementById('toasts').appendChild(t);
    requestAnimationFrame(() => t.classList.add('in'));
    setTimeout(() => { t.classList.remove('in'); setTimeout(() => t.remove(), 300); }, 4200);
  }

  function runScripts(root) {
    root.querySelectorAll('script').forEach((old) => {
      const s = document.createElement('script');
      [...old.attributes].forEach((a) => s.setAttribute(a.name, a.value));
      s.textContent = old.textContent;
      old.replaceWith(s);
    });
  }

  // ---------- little satisfying things ----------
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const fmt = {
    num: (v) => Math.round(v).toLocaleString(),
    dec: (v) => v.toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 }),
    money: (v) => '$' + v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
  };

  function countUp(el, to) {
    const f = fmt[el.dataset.fmt] || fmt.num;
    const from = parseFloat(el.dataset.shown || '0');
    el.dataset.shown = to;
    if (reduce || from === to) { el.textContent = f(to); return; }
    const t0 = performance.now(), dur = 900;
    const tick = (t) => {
      const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3);
      el.textContent = f(from + (to - from) * e);
      if (k < 1) requestAnimationFrame(tick);
      else if (from !== 0) { el.classList.remove('bump'); void el.offsetWidth; el.classList.add('bump'); }
    };
    requestAnimationFrame(tick);
  }

  function spark(svg) {
    const pts = JSON.parse(svg.dataset.spark || '[]');
    if (pts.length < 2) { svg.remove(); return; }
    const max = Math.max(...pts, 1);
    const xy = pts.map((v, i) => [i / (pts.length - 1) * 100, 26 - v / max * 24]);
    const line = xy.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
    svg.innerHTML = `<path class="area" d="${line} L100 28 L0 28 Z"/><path class="line" d="${line}" pathLength="1"/>`;
  }

  function confetti(x, y) {
    if (reduce) return;
    const c = document.createElement('canvas');
    c.className = 'confetti';
    c.width = innerWidth; c.height = innerHeight;
    document.body.appendChild(c);
    const g = c.getContext('2d');
    const colors = ['#22d3ee', '#5eead4', '#38bdf8', '#fbbf24', '#f472b6', '#34d399'];
    const bits = Array.from({ length: 90 }, () => ({
      x, y, vx: (Math.random() - 0.5) * 12, vy: -Math.random() * 12 - 4, r: Math.random() * 6 + 3,
      c: colors[(Math.random() * colors.length) | 0], a: Math.random() * 6, va: (Math.random() - 0.5) * 0.4,
    }));
    let f = 0;
    (function draw() {
      g.clearRect(0, 0, c.width, c.height);
      bits.forEach((b) => {
        b.vy += 0.35; b.x += b.vx; b.y += b.vy; b.a += b.va;
        g.save(); g.translate(b.x, b.y); g.rotate(b.a); g.fillStyle = b.c;
        g.fillRect(-b.r / 2, -b.r / 4, b.r, b.r / 2); g.restore();
      });
      if (++f < 90) requestAnimationFrame(draw); else c.remove();
    })();
  }

  // Swipe deck on Review: drag, arrow keys or buttons; the card flies off before submitting.
  function fly(form, dir) {
    const btn = form.querySelector(`[data-dir="${dir}"]`);
    if (!btn || form.classList.contains('gone')) return;
    form.classList.add('gone', dir === 'right' ? 'fly-right' : 'fly-left');
    if (dir === 'right') { const r = btn.getBoundingClientRect(); confetti(r.left + r.width / 2, r.top); }
    setTimeout(() => form.requestSubmit(btn), reduce ? 0 : 320);
  }

  function swipeable(form) {
    const media = form.querySelector('.swipe-media');
    let x0 = null, dx = 0;
    media.addEventListener('pointerdown', (e) => {
      if (e.target.closest('video') && e.offsetY > e.target.clientHeight - 48) return; // let video controls work
      x0 = e.clientX; dx = 0; form.setPointerCapture(e.pointerId); form.classList.add('dragging');
    });
    form.addEventListener('pointermove', (e) => {
      if (x0 === null) return;
      dx = e.clientX - x0;
      form.style.transform = `translateX(${dx}px) rotate(${dx / 25}deg)`;
      form.style.setProperty('--yes', Math.max(0, Math.min(1, dx / 120)));
      form.style.setProperty('--no', Math.max(0, Math.min(1, -dx / 120)));
    });
    const end = () => {
      if (x0 === null) return;
      x0 = null; form.classList.remove('dragging');
      if (Math.abs(dx) > 110) { form.style.transform = ''; fly(form, dx > 0 ? 'right' : 'left'); }
      else { form.style.transform = ''; form.style.setProperty('--yes', 0); form.style.setProperty('--no', 0); }
    };
    form.addEventListener('pointerup', end);
    form.addEventListener('pointercancel', end);
  }

  document.addEventListener('keydown', (e) => {
    if (e.target.closest('input, textarea, select') || e.metaKey || e.ctrlKey) return;
    const top = document.querySelector('form[data-swipe]:not([hidden]):not(.gone)');
    if (!top) return;
    if (e.key === 'ArrowRight') { e.preventDefault(); fly(top, 'right'); }
    if (e.key === 'ArrowLeft') { e.preventDefault(); fly(top, 'left'); }
  });

  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-dir]');
    if (b && b.form && b.form.hasAttribute('data-swipe')) { e.preventDefault(); fly(b.form, b.dataset.dir); return; }
    const c = e.target.closest('[data-confetti]');
    if (c) { const r = c.getBoundingClientRect(); confetti(r.left + r.width / 2, r.top); }
  }, true);

  // Copy caption buttons and download feedback.
  async function copyFrom(sel, btn) {
    const el = document.querySelector(sel);
    if (!el) return;
    const text = el.value !== undefined ? el.value : el.textContent;
    try { await navigator.clipboard.writeText(text); }
    catch (_) { el.select && el.select(); document.execCommand('copy'); }
    if (btn) {
      const was = btn.innerHTML;
      btn.classList.add('copied'); btn.innerHTML = '✓ Copied';
      setTimeout(() => { btn.classList.remove('copied'); btn.innerHTML = was; }, 1600);
    }
    el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash');
    toast('Caption copied. Paste it in your post.');
  }

  document.addEventListener('click', (e) => {
    const c = e.target.closest('[data-copy]');
    if (c) { e.preventDefault(); copyFrom(c.dataset.copy, c); return; }
    const all = e.target.closest('[data-copy-all]');
    if (all) {
      e.preventDefault();
      copyFrom(all.dataset.copyAll, null);
      const dl = all.closest('article, form').querySelector('[data-downloaded]');
      if (dl) dl.click();
      return;
    }
    const d = e.target.closest('[data-downloaded]');
    if (d) {
      toast('⬇ Downloading to your Downloads folder…');
      const card = d.closest('article');
      if (card && !card.querySelector('.dl-badge')) {
        const b = document.createElement('span'); b.className = 'dl-badge'; b.textContent = '✓ downloaded';
        card.querySelector('.dl-media').appendChild(b);
      }
    }
  });

  // ---------- Import page ----------
  function importOpts() {
    const layout = (document.querySelector('input[name="imp-layout"]:checked') || {}).value || 'auto';
    return { streamer: (document.getElementById('imp-streamer') || {}).value || '',
             clips: (document.getElementById('imp-clips') || {}).value || '', layout };
  }

  function uploadOne(file) {
    return new Promise((resolve) => {
      const o = importOpts();
      const row = document.createElement('div');
      row.className = 'up';
      row.innerHTML = `<span class="ellipsis"></span><div class="up-bar"><i></i></div><span class="small muted up-pct">0%</span>`;
      row.querySelector('.ellipsis').textContent = file.name;
      document.getElementById('uploads').prepend(row);
      const q = new URLSearchParams({ name: file.name, streamer: o.streamer, layout: o.layout, clips: o.clips });
      const x = new XMLHttpRequest();
      x.open('POST', '/import/upload?' + q);
      x.upload.onprogress = (e) => {
        if (!e.lengthComputable) return;
        const pct = Math.round(e.loaded / e.total * 100);
        row.querySelector('.up-bar i').style.width = pct + '%';
        row.querySelector('.up-pct').textContent = pct + '%';
      };
      x.onload = () => {
        const ok = x.status < 300;
        row.classList.add(ok ? 'ok' : 'bad');
        row.querySelector('.up-pct').textContent = ok ? '✓ queued' : 'failed';
        if (ok) { const r = row.getBoundingClientRect(); confetti(r.left + 40, r.top); }
        refreshQueue();
        resolve(ok);
      };
      x.onerror = () => { row.classList.add('bad'); row.querySelector('.up-pct').textContent = 'failed'; resolve(false); };
      x.send(file);
    });
  }

  async function uploadAll(files) {
    const vids = [...files].filter((f) => /^video\//.test(f.type) || /\.(mkv|flv|ts|mp4|mov|webm|m4v|avi)$/i.test(f.name));
    if (!vids.length) { toast('Those don\'t look like videos.', 'bad'); return; }
    toast(`Uploading ${vids.length} video${vids.length > 1 ? 's' : ''}…`);
    for (const f of vids) await uploadOne(f);
    toast('All uploaded. Clipping has started.');
  }

  async function refreshQueue() {
    const cur = document.getElementById('queue');
    if (!cur) return;
    try {
      const html = await (await fetch('/import', { headers: { 'X-SXM': '1' } })).text();
      const next = new DOMParser().parseFromString(html, 'text/html').getElementById('queue');
      if (next && next.innerHTML !== cur.innerHTML) cur.innerHTML = next.innerHTML;
    } catch (_) {}
  }

  let queueTimer = null;
  function importPage(root) {
    clearInterval(queueTimer);
    const drop = root.querySelector('#drop');
    if (!drop) return;
    const input = root.querySelector('#files');
    input.addEventListener('change', () => { uploadAll(input.files); input.value = ''; });
    ['dragenter', 'dragover'].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add('over'); }));
    ['dragleave', 'drop'].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
    drop.addEventListener('drop', (e) => uploadAll(e.dataTransfer.files));
    root.querySelectorAll('.layout-pick input').forEach((r) => r.addEventListener('change', () => {
      root.querySelectorAll('.lp').forEach((l) => l.classList.toggle('on', l.contains(r) ? r.checked : false));
    }));
    root.querySelectorAll('[data-step]').forEach((b) => b.addEventListener('click', () => {
      const i = root.querySelector('#imp-clips');
      i.value = Math.max(1, Math.min(15, (parseInt(i.value) || 3) + parseInt(b.dataset.step)));
    }));
    const lf = root.querySelector('#links-form');
    lf.addEventListener('submit', () => {
      const o = importOpts();
      lf.streamer.value = o.streamer; lf.layout.value = o.layout; lf.clips.value = o.clips;
    }, true);
    queueTimer = setInterval(() => { if (!document.hidden && document.getElementById('queue')) refreshQueue(); }, 3000);
  }
  // Dropping a file anywhere else on the Import page shouldn't open it in the browser.
  ['dragover', 'drop'].forEach((t) => window.addEventListener(t, (e) => { if (document.getElementById('drop') || document.getElementById('premade-drop')) e.preventDefault(); }));

  // Home: keep the numbers live without reloading.
  let liveTimer = null;
  function live() {
    clearInterval(liveTimer);
    if (!document.getElementById('stats')) return;
    liveTimer = setInterval(async () => {
      if (document.hidden || !document.getElementById('stats')) return;
      try {
        const h = await (await fetch('/api/home?days=' + (window.SXM_HOME_DAYS || 7))).json();
        h.stats.forEach((s) => {
          const el = document.querySelector(`.stat-value[data-key="${s.key}"]`);
          if (el && parseFloat(el.dataset.shown) !== s.value) countUp(el, s.value);
        });
        document.querySelectorAll('.flow .node b').forEach((el, i) => {
          const n = h.pipeline[i] && h.pipeline[i].n;
          if (n !== undefined && parseFloat(el.dataset.shown) !== n) {
            countUp(el, n); el.closest('.node').classList.toggle('busy', n > 0);
          }
        });
      } catch (_) { /* offline for a moment; try again next tick */ }
    }, 15000);
  }

  // Inventory: tabs on phones, live board, drop in ready-made clips.
  let invTimer = null;
  function premadeOne(file) {
    return new Promise((resolve) => {
      const row = document.createElement('div');
      row.className = 'up';
      row.innerHTML = `<span class="ellipsis"></span><div class="up-bar"><i></i></div><span class="small muted up-pct">0%</span>`;
      row.querySelector('.ellipsis').textContent = file.name;
      document.getElementById('premade-uploads').prepend(row);
      const x = new XMLHttpRequest();
      x.open('POST', '/inventory/premade?' + new URLSearchParams({ name: file.name }));
      x.upload.onprogress = (e) => {
        if (!e.lengthComputable) return;
        const pct = Math.round(e.loaded / e.total * 100);
        row.querySelector('.up-bar i').style.width = pct + '%';
        row.querySelector('.up-pct').textContent = pct + '%';
      };
      x.onload = () => {
        const ok = x.status < 300;
        row.classList.add(ok ? 'ok' : 'bad');
        row.querySelector('.up-pct').textContent = ok ? '✓ in library' : 'failed';
        if (ok) { const r = row.getBoundingClientRect(); confetti(r.left + 40, r.top); }
        resolve(ok);
      };
      x.onerror = () => { row.classList.add('bad'); row.querySelector('.up-pct').textContent = 'failed'; resolve(false); };
      x.send(file);
    });
  }
  async function refreshBoard() {
    try {
      const html = await (await fetch('/inventory', { headers: { 'X-SXM': '1' } })).text();
      const doc = new DOMParser().parseFromString(html, 'text/html');
      for (const id of ['board']) {
        const cur = document.getElementById(id), next = doc.getElementById(id);
        if (!cur || !next) continue;
        cur.querySelectorAll('[data-colpane]').forEach((pane) => {
          const n = next.querySelector(`[data-colpane="${pane.dataset.colpane}"]`);
          if (n && n.innerHTML !== pane.innerHTML) { pane.innerHTML = n.innerHTML; pane.classList.add('flash'); setTimeout(() => pane.classList.remove('flash'), 900); }
        });
      }
      doc.querySelectorAll('.inv-tab').forEach((t) => {
        const cur = document.querySelector(`.inv-tab[data-col="${t.dataset.col}"] .pill`);
        if (cur) cur.textContent = t.querySelector('.pill').textContent;
      });
    } catch (_) {}
  }
  function inventoryPage(root) {
    clearInterval(invTimer);
    if (!root.querySelector('#board')) return;
    const tabs = root.querySelectorAll('.inv-tab');
    const pick = (key) => {
      tabs.forEach((t) => t.classList.toggle('on', t.dataset.col === key));
      root.querySelectorAll('[data-colpane]').forEach((p) => p.classList.toggle('pick', p.dataset.colpane === key));
    };
    tabs.forEach((t) => t.addEventListener('click', () => {
      pick(t.dataset.col);
      const pane = root.querySelector(`[data-colpane="${t.dataset.col}"]`);
      if (pane && window.innerWidth > 900) { pane.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' }); pane.classList.add('flash'); setTimeout(() => pane.classList.remove('flash'), 900); }
    }));
    pick(tabs[0] && tabs[0].dataset.col);
    const drop = root.querySelector('#premade-drop');
    if (drop) {
      const input = root.querySelector('#premade-files');
      const send = async (files) => {
        for (const f of files) await premadeOne(f);
        toast('Ready-made clips added. Auto-post will use them.');
        refreshBoard();
      };
      input.addEventListener('change', () => { send(Array.from(input.files)); input.value = ''; });
      ['dragenter', 'dragover'].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add('over'); }));
      ['dragleave', 'drop'].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
      drop.addEventListener('drop', (e) => send(Array.from(e.dataTransfer.files)));
    }
    invTimer = setInterval(() => { if (!document.hidden && document.getElementById('board')) refreshBoard(); }, 4000);
  }

  // Trending: tabs, arrow scrolling, one-tap "Clip this", and new cards when a refresh lands.
  let trendTimer = null;
  function trendingPage(root) {
    clearInterval(trendTimer);
    root.querySelectorAll('[data-trend]').forEach((sec) => {
      sec.querySelectorAll('[data-tab]').forEach((b) => b.addEventListener('click', () => {
        sec.querySelectorAll('[data-tab]').forEach((x) => x.classList.toggle('on', x === b));
        sec.querySelectorAll('[data-pane]').forEach((p) => {
          const show = p.dataset.pane === b.dataset.tab;
          p.hidden = !show;
          if (show) { p.classList.remove('swap'); void p.offsetWidth; p.classList.add('swap'); p.scrollLeft = 0; }
        });
      }));
      sec.querySelectorAll('[data-scroll]').forEach((b) => b.addEventListener('click', () => {
        const t = sec.querySelector('.track:not([hidden])');
        if (t) t.scrollBy({ left: parseInt(b.dataset.scroll) * t.clientWidth * 0.85, behavior: 'smooth' });
      }));
    });
    root.querySelectorAll('form[data-clip]').forEach((f) => f.addEventListener('submit', async (e) => {
      e.preventDefault();
      const b = f.querySelector('button');
      if (b.disabled) return;
      b.disabled = true; b.textContent = 'Sending…';
      try {
        const r = await fetch(f.getAttribute('action'), { method: 'POST', body: new FormData(f) });
        const j = await r.json();
        if (!r.ok || !j.ok) throw new Error(j.msg || 'Could not add it');
        b.textContent = '✓ In queue'; b.classList.add('done', 'pop');
        const rect = b.getBoundingClientRect();
        confetti(rect.left + rect.width / 2, rect.top);
        toast(`✂ ${j.msg}. Clips land in Review when they're ready.`);
      } catch (err) {
        b.disabled = false; b.textContent = '✂ Clip this';
        toast(err.message, 'bad');
      }
    }));
    const st = window.SXM_TRENDING;
    if (!st || !st.loading || !document.querySelector('[data-trend]')) return;
    let tries = 0;
    trendTimer = setInterval(async () => {
      if (!document.querySelector('[data-trend]') || ++tries > 60) return clearInterval(trendTimer);
      try {
        const j = await (await fetch('/api/trending')).json();
        if (!j.loading) { clearInterval(trendTimer); go('/trending', {}, false); }
      } catch (_) {}
    }, 2500);
  }

  function enhance(root) {
    // Messages passed back after an action pop up as a toast instead of a banner.
    root.querySelectorAll('.toast-src').forEach((n) => { toast(n.textContent.trim()); n.remove(); });
    root.querySelectorAll('[data-count]').forEach((el) => countUp(el, parseFloat(el.dataset.count) || 0));
    root.querySelectorAll('svg[data-spark]').forEach(spark);
    root.querySelectorAll('svg[data-ring]').forEach((r) => {
      requestAnimationFrame(() => r.style.setProperty('--p', r.dataset.ring));
    });
    root.querySelectorAll('form[data-swipe]').forEach(swipeable);
    importPage(root);
    trendingPage(root);
    inventoryPage(root);
    const started = new URLSearchParams(location.search).get('started');
    if (started) toast(started === 'pipeline' ? '⚡ Finding and clipping now. New clips land in Review.' : `Started ${started}.`);
    live();
    root.querySelectorAll('.clip video').forEach((v) => {
      v.addEventListener('mouseenter', () => { v.muted = true; v.play().catch(() => {}); });
      v.addEventListener('mouseleave', () => { if (v.muted) v.pause(); });
    });
  }

  async function swap(html, url, push) {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const next = doc.getElementById('main');
    if (!next) { location.href = url; return; }
    const apply = () => {
      main().replaceWith(next);
      document.querySelector('header nav').replaceWith(doc.querySelector('header nav'));
      document.querySelector('header .status').replaceWith(doc.querySelector('header .status'));
      document.title = doc.title;
      if (push) history.pushState({}, '', url);
      runScripts(next);
      enhance(next);
    };
    if (document.startViewTransition && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
      await document.startViewTransition(apply).finished.catch(() => {});
    } else {
      apply();
    }
  }

  async function go(url, opts = {}, push = true) {
    progress(true);
    try {
      const r = await fetch(url, { ...opts, headers: { 'X-SXM': '1' }, redirect: 'follow' });
      const final = new URL(r.url);
      if (!canBoost(final)) { location.href = r.url; return; }
      await swap(await r.text(), final.pathname + final.search, push);
      if (push && !opts.method) window.scrollTo({ top: 0 });
    } catch (e) {
      toast('Something went wrong. Reloading…', 'bad');
      setTimeout(() => (location.href = url), 800);
    } finally {
      progress(false);
    }
  }

  document.addEventListener('click', (e) => {
    const a = e.target.closest('a');
    if (!a || e.defaultPrevented || e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (a.target || a.hasAttribute('download') || a.hasAttribute('data-no-boost')) return;
    const url = new URL(a.href, location.href);
    if (!canBoost(url) || (url.pathname === location.pathname && url.hash)) return;
    e.preventDefault();
    go(url.href);
  });

  document.addEventListener('submit', (e) => {
    const f = e.target;
    if (f.hasAttribute('data-no-boost')) return;
    // getAttribute: a field named "action" or "method" would shadow f.action / f.method.
    const url = new URL(f.getAttribute('action') || location.href, location.href);
    if (!canBoost(url)) return;
    e.preventDefault();
    const btn = e.submitter;
    const data = new FormData(f, btn);
    f.classList.add('sending');
    if (btn) btn.disabled = true;
    const keep = window.scrollY;
    const method = (f.getAttribute('method') || 'get').toUpperCase();
    const done = () => { f.classList.remove('sending'); if (btn) btn.disabled = false; };
    if (method === 'GET') {
      go(url.pathname + '?' + new URLSearchParams(data)).finally(done);
    } else {
      go(url.href, { method, body: data }).then(() => window.scrollTo({ top: keep })).finally(done);
    }
  });

  // Prefetch pages on hover so they open instantly.
  const warmed = new Set();
  document.addEventListener('pointerover', (e) => {
    const a = e.target.closest && e.target.closest('header nav a');
    if (!a || warmed.has(a.href)) return;
    warmed.add(a.href);
    const l = document.createElement('link');
    l.rel = 'prefetch'; l.href = a.href;
    document.head.appendChild(l);
  });

  window.addEventListener('popstate', () => go(location.href, {}, false));
  enhance(document);
})();
