/* SXM: smooth navigation without a framework.
   Links and forms load in the background and swap the page in with a
   cross-fade, so the dashboard never flashes white or jumps. OAuth links,
   new tabs and downloads still behave normally. */
(() => {
  const main = () => document.getElementById('main');
  const bar = document.getElementById('progress');
  const canBoost = (url) => url.origin === location.origin &&
    !/^\/(oauth|media|static|api|login)/.test(url.pathname);
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

  function enhance(root) {
    // Messages passed back after an action pop up as a toast instead of a banner.
    root.querySelectorAll('.toast-src').forEach((n) => { toast(n.textContent.trim()); n.remove(); });
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
