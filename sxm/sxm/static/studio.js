/* SXM Studio: Streams (live + hot clips + scan), scan progress, and the clip editor. */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const toast = (t, k) => (window.SXM_toast ? window.SXM_toast(t, k) : console.log(t));
  const nav = (url) => { const a = document.createElement('a'); a.href = url; document.body.appendChild(a); a.click(); a.remove(); };
  const fmtT = (s) => {
    s = Math.max(0, Math.round(s || 0));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(x).padStart(2, '0');
  };
  const fmtN = (n) => (n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1e3 ? (n / 1e3).toFixed(n >= 1e4 ? 0 : 1) + 'K' : String(n || 0));
  const ago = (d) => {
    if (!d) return '';
    const t = typeof d === 'number' ? d * 1000 : Date.parse(d);
    if (!t) return String(d);
    const s = (Date.now() - t) / 1000;
    if (s < 3600) return Math.max(1, Math.round(s / 60)) + 'm ago';
    if (s < 86400) return Math.round(s / 3600) + 'h ago';
    return Math.round(s / 86400) + 'd ago';
  };
  const timers = {};
  const every = (key, ms, fn, alive) => {
    clearInterval(timers[key]);
    timers[key] = setInterval(() => { if (!alive()) return clearInterval(timers[key]); if (!document.hidden) fn(); }, ms);
  };

  /* ------------------------------ Streams ------------------------------ */
  function streams(root) {
    const st = $('#st', root);
    if (!st) return;
    const form = $('#st-search', st), q = $('#st-q', st), pIn = form.querySelector('[name=p]');
    const names = { twitch: 'Twitch', kick: 'Kick', youtube: 'YouTube' };
    $$('.st-pbtn', st).forEach((b) => b.addEventListener('click', () => {
      $$('.st-pbtn', st).forEach((x) => x.classList.toggle('on', x === b));
      pIn.value = b.dataset.p;
      q.placeholder = `Search ${names[b.dataset.p]} creators or paste a stream link`;
      q.focus();
    }));

    // Scan sheet
    const sheet = $('#st-sheet', st), sf = $('#st-scan', st);
    const pills = (box) => box && box.addEventListener('change', () =>
      $$('label', box).forEach((l) => l.classList.toggle('on', !!l.querySelector('input:checked'))));
    $$('.sc-modes, .sc-pills, .sc-window, .sc-layouts', sf).forEach(pills);
    const sync = () => {
      const quick = sf.mode.value === 'quick';
      $('.sc-quick', sf).hidden = !quick;
      $('.sc-count', sf).hidden = quick;
      $('.sc-range', sf).hidden = sf.window.value !== 'custom';
      $('#sc-go', sf).textContent = quick ? 'Find that moment' : 'Start scan';
      if (quick) setTimeout(() => $('#sc-prompt', sf).focus(), 50);
    };
    sf.addEventListener('change', sync);
    sync();
    const open = (o) => {
      sf.url.value = o.url; sf.title.value = o.title || ''; sf.streamer.value = o.streamer || '';
      sf.platform.value = o.platform || 'manual'; sf.duration.value = o.duration || 0;
      $('#sc-title', sf).textContent = o.title || o.url;
      const short = o.duration && o.duration < 240;
      if (short) { // a clip or short video: one clip, whole thing
        sf.querySelector('[name=clips][value="3"]').checked = true;
        sf.querySelector('[name=window][value=any]').checked = true;
      }
      sf.dispatchEvent(new Event('change'));
      sheet.hidden = false;
      requestAnimationFrame(() => sheet.classList.add('open'));
    };
    const close = () => { sheet.classList.remove('open'); setTimeout(() => (sheet.hidden = true), 220); };
    $$('[data-close]', sheet).forEach((b) => b.addEventListener('click', close));
    document.addEventListener('keydown', function esc(e) {
      if (!document.body.contains(sheet)) return document.removeEventListener('keydown', esc);
      if (e.key === 'Escape' && !sheet.hidden) close();
    });
    sf.addEventListener('submit', (e) => {
      if (!sf.url.value) { e.preventDefault(); e.stopImmediatePropagation(); toast('Pick a stream first', 'bad'); return; }
      if (sf.mode.value === 'quick') {
        if (!sf.prompt.value.trim()) { e.preventDefault(); e.stopImmediatePropagation(); toast('Describe the moment you want', 'bad'); return; }
        sf.querySelector('[name=clips][value="3"]').checked = true;
      } else {
        sf.prompt.value = '';
      }
    }, true);
    if (sheet && !sheet.hidden) { sheet.classList.add('open'); }

    const cr = $('#st-creator', st);
    if (cr) creator(cr, open);
    else { board(st); viralBoard(st, open); }
  }

  /* Most viral right now: biggest live streams and most watched clips across platforms */
  function viralBoard(st, openScan) {
    const box = $('#viral', st);
    if (!box) return;
    const list = $('#sv-list', box), cats = $('#sv-cats', box), msgs = $('#sv-msgs', box), note = $('#sv-note', box);
    const PN = { twitch: 'Twitch', kick: 'Kick', youtube: 'YouTube' };
    let plat = 'all', kind = 'live', cat = '', data = null;
    const ckey = (r) => r.game || '';
    const since = (d) => { const t = Date.parse(d); if (!t) return ''; const m = Math.round((Date.now() - t) / 60000); return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h ${m % 60}m`; };
    const page = (r) => `/streams?p=${r.platform}&q=${encodeURIComponent(r.handle)}`;
    const liveCard = (r, i) => `
      <div class="sv-card ax-in" style="--i:${Math.min(i, 12)}">
        <a class="sv-th" href="${r.platform === 'youtube' ? esc(r.url) : page(r)}"${r.platform === 'youtube' ? ' target="_blank" rel="noopener"' : ''}>
          ${r.thumb ? `<img src="${esc(r.thumb)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : '<i></i>'}
          <span class="sv-rank">#${i + 1}</span><span class="sv-live">LIVE</span>
          <em>👁 ${fmtN(r.viewers)}</em>${r.started && since(r.started) ? `<em class="l">${since(r.started)}</em>` : ''}
        </a>
        <div class="sv-body">
          <span class="sv-who"><span class="st-av sm">${r.avatar ? `<img src="${esc(r.avatar)}" alt="" referrerpolicy="no-referrer">` : esc((r.name || '?')[0].toUpperCase())}</span>
            <b>${esc(r.name)}</b><span class="st-pl ${r.platform}">${PN[r.platform]}</span></span>
          <span class="sv-title" title="${esc(r.title)}">${esc(r.title)}</span>
          ${r.game ? `<span class="sv-game">${esc(r.game)}</span>` : ''}
          <span class="st-acts">
            ${r.platform !== 'youtube' ? `<button type="button" class="st-clipit" data-hot="${i}">⚡ Clip hottest</button>` : ''}
            ${r.platform === 'youtube' ? `<a class="btn ghost" href="${esc(r.url)}" target="_blank" rel="noopener">Watch</a>${r.handle && !/^UC/.test(r.handle) ? `<a class="btn ghost" href="${page(r)}">Channel</a>` : ''}` : `<a class="btn ghost" href="${page(r)}">Open</a>`}
          </span>
        </div>
      </div>`;
    const clipCard = (c, i) => `
      <div class="sv-card ax-in" style="--i:${Math.min(i, 12)}">
        <a class="sv-th" href="${esc(c.url)}" target="_blank" rel="noopener">
          ${c.thumb ? `<img src="${esc(c.thumb)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : '<i></i>'}
          <span class="sv-rank">#${i + 1}</span>
          <em>👁 ${fmtN(c.views)}</em>${c.duration ? `<em class="l">${fmtT(c.duration)}</em>` : ''}
        </a>
        <div class="sv-body">
          <span class="sv-who"><b>${esc(c.name)}</b><span class="st-pl ${c.platform}">${PN[c.platform]}</span><small class="muted">${esc(ago(c.date))}</small></span>
          <span class="sv-title" title="${esc(c.title)}">${esc(c.title || 'Untitled clip')}</span>
          ${c.game ? `<span class="sv-game">${esc(c.game)}</span>` : ''}
          <span class="st-acts">
            <button type="button" class="st-clipit" data-clip="${i}">⚡ Clip it</button>
            <button type="button" class="ghost" data-opts="${i}" title="Choose layout, length and more">⚙</button>
          </span>
        </div>
      </div>`;
    function paint() {
      if (!data) return;
      const rows = kind === 'live' ? data.streams : data.clips;
      const counts = {};
      rows.forEach((r) => { const k = ckey(r); if (k) counts[k] = (counts[k] || 0) + 1; });
      const top = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 10).map((x) => x[0]);
      if (cat && !top.includes(cat)) cat = '';
      cats.innerHTML = top.length > 1 ? [''].concat(top).map((k) =>
        `<button type="button" class="ghost ${k === cat ? 'on' : ''}" data-cat="${esc(k)}">${k ? esc(k) : 'Everything'}</button>`).join('') : '';
      const shown = rows.filter((r) => !cat || ckey(r) === cat).slice(0, 24);
      const m = [];
      if (data.notes.twitch === 'keys' && (plat === 'all' || plat === 'twitch'))
        m.push('<div class="sv-msg">Twitch needs your free Twitch app keys to list its top streams. <a href="/setup">Add them in Connect</a> (2 minutes at dev.twitch.tv).</div>');
      ['kick', 'youtube', 'twitch'].forEach((p) => {
        if ((plat === 'all' || plat === p) && data.notes[p] === 'error') m.push(`<div class="sv-msg">Couldn't reach ${PN[p]} right now.</div>`);
      });
      if (kind === 'clips' && plat === 'youtube') m.push('<div class="sv-msg">YouTube has no live clips list. Switch to Live now.</div>');
      msgs.innerHTML = m.join('');
      list.innerHTML = shown.length ? shown.map(kind === 'live' ? liveCard : clipCard).join('') : '<p class="muted">Nothing to show here right now.</p>';
      const total = shown.reduce((a, r) => a + (r.viewers || 0), 0);
      note.textContent = kind === 'live'
        ? `${shown.length} streams · ${fmtN(total)} watching · checked ${new Date(data.checked * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`
        : 'Most watched clips from the last 24 hours';
      $$('[data-hot]', list).forEach((b) => b.addEventListener('click', async () => {
        const r = shown[+b.dataset.hot];
        b.disabled = true; b.textContent = 'Finding it…';
        try {
          const d = await (await fetch(`/api/streams/creator?p=${r.platform}&h=${encodeURIComponent(r.handle)}`)).json();
          const c = (d.clips || [])[0];
          if (!c) { toast(`${r.name} has no clips from today yet. Opening their page.`); nav(page(r)); return; }
          quickClip({ url: c.url, title: c.title, streamer: r.handle, platform: r.platform, duration: c.duration }, b);
        } catch (_) { b.disabled = false; b.textContent = '⚡ Clip hottest'; toast('Could not reach ' + PN[r.platform], 'bad'); }
      }));
      $$('[data-clip]', list).forEach((b) => b.addEventListener('click', () => {
        const c = shown[+b.dataset.clip];
        quickClip({ url: c.url, title: c.title, streamer: c.handle, platform: c.platform, duration: c.duration }, b);
      }));
      $$('[data-opts]', list).forEach((b) => b.addEventListener('click', () => {
        const c = shown[+b.dataset.opts];
        openScan({ url: c.url, title: c.title, streamer: c.handle, platform: c.platform, duration: c.duration });
      }));
    }
    cats.addEventListener('click', (e) => { const b = e.target.closest('[data-cat]'); if (b) { cat = b.dataset.cat; paint(); } });
    async function load(fresh) {
      list.classList.add('loading');
      try {
        data = await (await fetch(`/api/streams/viral?p=${plat}${fresh ? '&fresh=1' : ''}`)).json();
        paint();
      } catch (_) { list.innerHTML = '<p class="muted">Couldn\'t load the viral board. Try ↻.</p>'; }
      finally { list.classList.remove('loading'); }
    }
    $$('#sv-plat button', box).forEach((b) => b.addEventListener('click', () => {
      $$('#sv-plat button', box).forEach((x) => x.classList.toggle('on', x === b));
      plat = b.dataset.vp; cat = ''; load(false);
    }));
    $$('#sv-kind button', box).forEach((b) => b.addEventListener('click', () => {
      $$('#sv-kind button', box).forEach((x) => x.classList.toggle('on', x === b));
      kind = b.dataset.vk; cat = ''; paint();
    }));
    $('#sv-refresh', box).addEventListener('click', () => load(true));
    load(false);
    every('viral', 180000, () => load(false), () => document.body.contains(box));
  }

  function board(st) {
    const favs = $('#st-favs', st), note = $('#st-board-note', st);
    if (!favs) { if (note) note.textContent = ''; return; }
    const load = async () => {
      try {
        const rows = await (await fetch('/api/streams/live')).json();
        let live = 0;
        rows.forEach((r) => {
          const el = favs.querySelector(`[data-key="${CSS.escape(r.platform + ':' + r.handle)}"]`);
          if (!el) return;
          const s = el.querySelector('.st-state');
          el.classList.toggle('live', !!r.live);
          if (r.live) {
            live++;
            s.innerHTML = `<span class="st-live-dot"></span>${fmtN(r.live.viewers)} watching`;
            el.title = r.live.title || '';
          } else s.textContent = 'Offline';
          if (r.avatar && !el.querySelector('img')) el.querySelector('.st-av').innerHTML = `<img src="${esc(r.avatar)}" alt="" referrerpolicy="no-referrer">`;
          if (r.live) favs.prepend(el);
        });
        note.textContent = live ? `${live} live now` : 'Nobody live right now';
      } catch (_) { note.textContent = "Couldn't check right now"; }
    };
    load();
    every('board', 60000, load, () => document.body.contains(favs));
  }

  async function creator(cr, openScan) {
    const p = cr.dataset.p, h = cr.dataset.h;
    let d;
    const card = (it, i) => `
      <div class="st-item ax-in" style="--i:${Math.min(i, 10)}">
        <span class="st-th">${it.thumb ? `<img src="${esc(it.thumb)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : '<i></i>'}
          ${it.duration ? `<em>${fmtT(it.duration)}</em>` : ''}${it.views ? `<em class="v">👁 ${fmtN(it.views)}</em>` : ''}</span>
        <b title="${esc(it.title)}">${esc(it.title || 'Untitled')}</b>
        <small class="muted">${esc(ago(it.date))}</small>
        <span class="st-acts">
          <button type="button" class="st-clipit" data-i="${i}" data-kind="${it.kind}">${it.kind === 'clip' ? '⚡ Clip it' : '🔎 Scan'}</button>
          <a class="btn ghost" href="${esc(it.url)}" target="_blank" rel="noopener">Open</a>
        </span>
      </div>`;
    const render = () => {
      const live = d.live;
      const lists = { clip: d.clips || [], vod: d.vods || [] };
      cr.innerHTML = `
        <div class="st-c-head ax-in" style="--i:0">
          <span class="st-av big">${d.avatar ? `<img src="${esc(d.avatar)}" alt="" referrerpolicy="no-referrer">` : esc((d.name || h)[0].toUpperCase())}</span>
          <div class="st-c-name"><b>${esc(d.name || h)}</b>
            <small><span class="st-pl ${p}">${p === 'youtube' ? 'YouTube' : p[0].toUpperCase() + p.slice(1)}</span>
            ${live ? `<span class="st-badge">LIVE NOW</span> ${fmtN(live.viewers)} watching${live.game ? ' · ' + esc(live.game) : ''}` : '<span class="muted">Offline</span>'}</small></div>
          <div class="st-c-tools">
            <button type="button" class="ghost st-star ${cr.dataset.fav === '1' ? 'on' : ''}" id="st-star" title="Keep on your board">${cr.dataset.fav === '1' ? '★ Saved' : '☆ Save'}</button>
            <button type="button" class="ghost" id="st-refresh" title="Check again">↻</button>
            <a class="btn ghost" href="${esc(d.channel_url)}" target="_blank" rel="noopener">Open ${p === 'youtube' ? 'YouTube' : p[0].toUpperCase() + p.slice(1)}</a>
          </div>
        </div>
        ${d.found === false ? `<div class="ax-note">Couldn't find <b>${esc(h)}</b> on this platform${d.error ? ': ' + esc(d.error) : ''}. Check the spelling or paste their channel link.</div>` : ''}
        ${live ? `
        <div class="st-live ax-in" style="--i:1">
          <div class="st-player">${d.embed ? `<iframe src="${esc(d.embed)}" allowfullscreen allow="autoplay; fullscreen" title="Live stream"></iframe>` : (live.thumb ? `<img src="${esc(live.thumb)}" alt="">` : '')}</div>
          <div class="st-live-side">
            <span class="ax-k">Streaming now</span>
            <b>${esc(live.title)}</b>
            <p class="muted small">Live streams can't be scanned until they end, so the quickest clips come from what chat is clipping right now.</p>
            ${lists.clip.length ? `<button type="button" class="big" id="st-hottest">⚡ Clip the hottest moment</button>` : ''}
          </div>
        </div>` : ''}
        <section class="st-sec ax-in" style="--i:2">
          <div class="ax-sec-head"><h2 class="ax-h">🔥 Hot right now</h2><span class="ax-k">${lists.clip.length ? 'Clips chat made, most watched first' : ''}</span></div>
          ${lists.clip.length ? `<div class="st-grid" data-list="clip">${lists.clip.slice(0, 12).map(card).join('')}</div>`
            : `<p class="muted">No clips from the last day${p === 'youtube' ? ' (YouTube has no stream clips list)' : ''}.</p>`}
        </section>
        <section class="st-sec ax-in" style="--i:3">
          <div class="ax-sec-head"><h2 class="ax-h">Past streams</h2><span class="ax-k">Scan one for its best moments</span></div>
          ${lists.vod.length ? `<div class="st-grid" data-list="vod">${lists.vod.slice(0, 12).map(card).join('')}</div>` : '<p class="muted">No past streams found.</p>'}
        </section>
        <p class="muted small center">Checked ${new Date((d.checked || Date.now() / 1000) * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}</p>`;
      $$('.st-clipit', cr).forEach((b) => b.addEventListener('click', () => {
        const it = lists[b.dataset.kind][+b.dataset.i];
        openScan({ url: it.url, title: it.title, streamer: h, platform: p, duration: it.duration });
      }));
      const hot = $('#st-hottest', cr);
      if (hot) hot.addEventListener('click', () => {
        const it = lists.clip[0];
        quickClip({ url: it.url, title: it.title, streamer: h, platform: p, duration: it.duration }, hot);
      });
      $('#st-refresh', cr).addEventListener('click', () => load(true));
      $('#st-star', cr).addEventListener('click', async (e) => {
        const fd = new FormData();
        fd.append('p', p); fd.append('h', h); fd.append('name', d.name || h); fd.append('avatar', d.avatar || '');
        const r = await (await fetch('/streams/fav', { method: 'POST', body: fd })).json();
        cr.dataset.fav = r.fav ? '1' : '0';
        e.target.classList.toggle('on', r.fav);
        e.target.textContent = r.fav ? '★ Saved' : '☆ Save';
        toast(r.fav ? `${d.name || h} is on your board` : 'Removed from your board');
      });
    };
    const load = async (fresh) => {
      if (fresh) cr.classList.add('loading');
      try {
        d = await (await fetch(`/api/streams/creator?p=${p}&h=${encodeURIComponent(h)}${fresh ? '&fresh=1' : ''}`)).json();
        render();
      } catch (e) {
        cr.innerHTML = `<div class="ax-note">Couldn't reach ${esc(p)} right now. <button type="button" class="ghost" id="st-retry">Try again</button></div>`;
        $('#st-retry', cr).addEventListener('click', () => load(true));
      } finally { cr.classList.remove('loading'); }
    };
    await load(false);
  }

  // One tap: clip a short clip with the default settings and open its progress.
  function quickClip(o, btn) {
    const fd = new FormData();
    Object.entries({ url: o.url, title: o.title || '', streamer: o.streamer || '', platform: o.platform || 'manual',
      duration: o.duration || 0, clips: 1, window: 'any' }).forEach(([k, v]) => fd.append(k, v));
    if (btn) btn.disabled = true;
    const f = document.createElement('form');
    f.method = 'post'; f.action = '/streams/scan'; f.hidden = true;
    for (const [k, v] of fd.entries()) { const i = document.createElement('input'); i.name = k; i.value = v; f.appendChild(i); }
    document.body.appendChild(f);
    f.requestSubmit();
    setTimeout(() => f.remove(), 2000);
  }

  /* ------------------------------ Scan progress ------------------------------ */
  function scan(root) {
    const sp = $('#sp', root);
    if (!sp) return;
    const sid = sp.dataset.sid;
    let last = window.SXM_SCAN || {};
    let shownClips = new Set((last.clips || []).map((c) => c.id));
    const paint = (s) => {
      $('#sp-arc', sp).style.setProperty('--p', s.overall);
      const pe = $('#sp-pct', sp), from = +pe.textContent || 0, to = s.overall;
      const t0 = performance.now();
      const tick = (t) => { const k = Math.min(1, (t - t0) / 600); pe.textContent = Math.round(from + (to - from) * k); if (k < 1) requestAnimationFrame(tick); };
      requestAnimationFrame(tick);
      $('#sp-now', sp).textContent = s.waiting ? (s.ahead ? `Waiting for ${s.ahead} job${s.ahead > 1 ? 's' : ''} ahead of it…` : 'Starting…')
        : s.status === 'clipped' ? (s.clips.length ? `Done. ${s.clips.length} clip${s.clips.length > 1 ? 's' : ''} ready.` : 'Done, but no strong moments stood out.')
        : s.status === 'failed' ? 'Stopped' : (s.progress || 'Working…');
      $$('#sp-steps li', sp).forEach((li, i) => {
        li.classList.toggle('done', i < s.step || s.status === 'clipped');
        li.classList.toggle('on', i === s.step && s.status !== 'clipped' && s.status !== 'failed');
      });
      const err = $('#sp-err', sp);
      err.hidden = !(s.status === 'failed' && s.error);
      err.textContent = s.error || '';
      const finished = ['clipped', 'failed', 'skipped'].includes(s.status);
      $('#sp-cancel', sp).hidden = finished;
      sp.classList.toggle('finished', finished);
      const bg = $('#sp-bg', sp);
      if (finished && s.clips.length) { bg.textContent = 'Open the first clip'; bg.href = `/clip/${s.clips[0].id}`; bg.classList.remove('ghost'); }
      const box = $('#sp-clips', sp);
      $('#sp-count', sp).textContent = `${s.clips.length} ${finished ? 'found' : 'so far'}`;
      if (s.clips.length) {
        const empty = $('.sp-empty', box);
        if (empty) empty.remove();
        s.clips.forEach((c, i) => {
          if (shownClips.has(c.id)) return;
          shownClips.add(c.id);
          const a = document.createElement('a');
          a.className = 'sp-clip new'; a.href = `/clip/${c.id}`;
          a.innerHTML = `<span class="sp-thumb">${c.thumb_path ? `<img src="/media/clips/clip_${c.id}.jpg" alt="">` : ''}<i>${Math.round(c.secs || 0)}s</i></span><b>${esc(c.hook || 'Clip')}</b>`;
          box.appendChild(a);
        });
      }
      return finished;
    };
    paint(last);
    const poll = async () => {
      try {
        const s = await (await fetch(`/api/scan/${sid}`)).json();
        if (paint(s)) { clearInterval(timers.scan); if (s.status === 'clipped' && s.clips.length) toast(`✂️ ${s.clips.length} clip${s.clips.length > 1 ? 's' : ''} ready`); }
        last = s;
      } catch (_) {}
    };
    if (!['clipped', 'failed', 'skipped'].includes(last.status)) every('scan', 1500, poll, () => document.body.contains(sp));
  }

  /* ------------------------------ Clip editor ------------------------------ */
  function editor(root) {
    const ce = $('#ce', root);
    if (!ce) return;
    const id = ce.dataset.id;
    const S = JSON.parse(JSON.stringify(window.SXM_CLIP || {}));
    const keys = ['start', 'end', 'layout', 'aspect', 'facecam', 'game', 'captions', 'hook', 'caption', 'fps', 'height'];
    const snap = () => JSON.stringify(keys.reduce((o, k) => ((o[k] = S[k]), o), {}));
    let saved = snap();
    const undo = [], redo = [];
    const v = $('#ce-video', ce), cv = $('#ce-canvas', ce), cx = cv.getContext('2d');
    const fin = $('#ce-final', ce), stage = $('#ce-stage', ce), tl = $('#ce-tl', ce), range = $('#ce-range', ce);
    const view = S.view && S.view[1] > S.view[0] ? S.view : [0, S.media_duration || Math.max(S.end + 5, 60)];
    const span = () => view[1] - view[0];
    const DEF = { facecam: [0.02, 0.04, 0.26, 0.3], game: [0.2, 0, 0.6, 1] };

    // ---------- history ----------
    const commit = (fn, label) => {
      const before = snap();
      fn();
      if (snap() === before) return;
      undo.push(before); redo.length = 0;
      if (undo.length > 80) undo.shift();
      refresh(label);
    };
    const restore = (json) => { Object.assign(S, JSON.parse(json)); refresh(); };
    $('#ce-undo', ce).addEventListener('click', () => { if (!undo.length) return; redo.push(snap()); restore(undo.pop()); });
    $('#ce-redo', ce).addEventListener('click', () => { if (!redo.length) return; undo.push(snap()); restore(redo.pop()); });

    // ---------- UI sync ----------
    const dirty = () => snap() !== saved;
    function refresh() {
      $('#ce-undo', ce).disabled = !undo.length;
      $('#ce-redo', ce).disabled = !redo.length;
      $('#ce-save', ce).classList.toggle('dirty', dirty());
      $('#ce-save', ce).textContent = dirty() ? 'Save & render' : 'Saved';
      $$('#ce-aspects button', ce).forEach((b) => b.classList.toggle('on', b.dataset.aspect === S.aspect));
      $$('#ce-layouts button', ce).forEach((b) => b.classList.toggle('on', b.dataset.layout === S.layout));
      const [aw, ah] = S.aspect.split(':').map(Number);
      const H = 640, W = Math.round((H * aw) / ah);
      if (cv.width !== W) { cv.width = W; cv.height = H; }
      $('#ce-phone', ce).style.setProperty('--ar', `${aw}/${ah}`);
      $('#ce-hook', ce).value !== S.hook && ($('#ce-hook', ce).value = S.hook || '');
      $('#ce-caption', ce).value !== S.caption && ($('#ce-caption', ce).value = S.caption || '');
      $('#ce-captions', ce).checked = !!S.captions;
      $('#ce-fps', ce).value = String(S.fps); $('#ce-height', ce).value = String(S.height);
      $('#ce-hook-title', ce).textContent = S.hook || `Clip #${id}`;
      placeRange(); placeBoxes(); words(); draw();
    }

    // ---------- timeline ----------
    const strip = $('#ce-strip', ce);
    if (S.has_media) {
      const img = new Image();
      img.onload = () => { strip.style.backgroundImage = `url(${img.src})`; strip.classList.add('in'); };
      img.src = `/clip/${id}/strip.jpg?t=${Date.now()}`;
    }
    $('#ce-va', ce).textContent = fmtT(view[0]);
    $('#ce-vb', ce).textContent = fmtT(view[1]);
    const pct = (t) => ((t - view[0]) / span()) * 100;
    function placeRange() {
      const a = pct(S.start), b = pct(S.end);
      range.style.left = a + '%'; range.style.width = (b - a) + '%';
      $('.ce-dim.l', tl).style.width = a + '%';
      $('.ce-dim.r', tl).style.width = (100 - b) + '%';
      $('#ce-sel', ce).textContent = `${fmtT(S.start)} – ${fmtT(S.end)} · ${(S.end - S.start).toFixed(1)}s`;
      $('#ce-len', ce).textContent = fmtT(S.end - S.start);
    }
    const tAt = (x) => { const r = tl.getBoundingClientRect(); return view[0] + Math.max(0, Math.min(1, (x - r.left) / r.width)) * span(); };
    tl.addEventListener('pointerdown', (e) => {
      if (!S.has_media) return;
      const h = e.target.closest('[data-h]');
      const inRange = e.target === range;
      const t0 = tAt(e.clientX), s0 = S.start, e0 = S.end;
      let moved = false;
      const before = snap();
      tl.setPointerCapture(e.pointerId);
      const mv = (ev) => {
        const t = tAt(ev.clientX), d = t - t0;
        if (Math.abs(ev.clientX - e.clientX) > 3) moved = true;
        if (!moved) return;
        if (h && h.dataset.h === 'start') S.start = Math.min(S.end - 1, Math.max(view[0], t));
        else if (h) S.end = Math.max(S.start + 1, Math.min(view[1], t));
        else if (inRange) {
          const len = e0 - s0;
          S.start = Math.max(view[0], Math.min(view[1] - len, s0 + d)); S.end = S.start + len;
        } else return;
        if (S.end - S.start > 180) { if (h && h.dataset.h === 'start') S.start = S.end - 180; else S.end = S.start + 180; }
        placeRange();
        if (v) v.currentTime = h && h.dataset.h === 'end' ? S.end : S.start;
      };
      const up = () => {
        tl.removeEventListener('pointermove', mv); tl.removeEventListener('pointerup', up); tl.removeEventListener('pointercancel', up);
        if (moved) {
          if (snap() !== before) { undo.push(before); redo.length = 0; }
          refresh();
        } else if (v) { v.currentTime = Math.max(view[0], Math.min(view[1], t0)); draw(); }
      };
      tl.addEventListener('pointermove', mv); tl.addEventListener('pointerup', up); tl.addEventListener('pointercancel', up);
    });

    // ---------- playback ----------
    const playBtn = $('#ce-play', ce), head = $('#ce-headline', ce), cur = $('#ce-cur', ce);
    const sound = $('#ce-sound', ce);
    if (v) {
      v.addEventListener('loadedmetadata', () => {
        stage.style.aspectRatio = `${v.videoWidth} / ${v.videoHeight}`;
        v.currentTime = S.start;
        placeBoxes();
      });
      v.addEventListener('seeked', draw);
      sound.addEventListener('change', () => (v.muted = !sound.checked));
      const toggle = () => {
        if (v.paused) { if (v.currentTime < S.start || v.currentTime >= S.end - 0.05) v.currentTime = S.start; v.play().catch(() => {}); }
        else v.pause();
      };
      playBtn.addEventListener('click', toggle);
      stage.addEventListener('click', (e) => { if (!e.target.closest('.ce-box')) toggle(); });
      v.addEventListener('play', () => (playBtn.textContent = '❚❚'));
      v.addEventListener('pause', () => (playBtn.textContent = '▶'));
      const loop = () => {
        if (!document.body.contains(ce)) return;
        if (!v.paused && v.currentTime >= S.end) { v.pause(); v.currentTime = S.start; }
        head.style.left = pct(v.currentTime) + '%';
        cur.textContent = fmtT(v.currentTime - S.start);
        if (!v.paused) draw();
        requestAnimationFrame(loop);
      };
      requestAnimationFrame(loop);
      document.addEventListener('keydown', function keys(e) {
        if (!document.body.contains(ce)) return document.removeEventListener('keydown', keys);
        if (/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) return;
        if (e.code === 'Space') { e.preventDefault(); toggle(); }
        else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); $(e.shiftKey ? '#ce-redo' : '#ce-undo', ce).click(); }
        else if (e.key === 'ArrowLeft') v.currentTime = Math.max(view[0], v.currentTime - (e.shiftKey ? 1 : 0.2));
        else if (e.key === 'ArrowRight') v.currentTime = Math.min(view[1], v.currentTime + (e.shiftKey ? 1 : 0.2));
        else if (e.key === 'i') commit(() => (S.start = Math.min(S.end - 1, v.currentTime)));
        else if (e.key === 'o') commit(() => (S.end = Math.max(S.start + 1, v.currentTime)));
      });
    }

    // ---------- crop boxes ----------
    const boxes = { facecam: $('[data-box=facecam]', ce), game: $('[data-box=game]', ce) };
    function placeBoxes() {
      const show = { facecam: S.layout === 'stack', game: S.layout === 'stack' || S.layout === 'crop' };
      for (const k of ['facecam', 'game']) {
        const el = boxes[k];
        el.hidden = !show[k] || !S.has_media;
        const b = S[k] || autoBox(k);
        el.classList.toggle('auto', !S[k]);
        Object.assign(el.style, { left: b[0] * 100 + '%', top: b[1] * 100 + '%', width: b[2] * 100 + '%', height: b[3] * 100 + '%' });
      }
    }
    // Where the render will crop when you haven't placed a box yourself.
    function autoBox(k) {
      if (k === 'facecam') return DEF.facecam;
      const vw = (v && v.videoWidth) || 16, vh = (v && v.videoHeight) || 9;
      const [aw, ah] = S.aspect.split(':').map(Number);
      const target = S.layout === 'stack' ? (aw / (ah * (1 - 0.36))) : aw / ah;
      const w = Math.min(1, (target * vh) / vw);
      return [(1 - w) / 2, 0, w, 1];
    }
    Object.entries(boxes).forEach(([k, el]) => el.addEventListener('pointerdown', (e) => {
      e.preventDefault(); e.stopPropagation();
      const r = stage.getBoundingClientRect();
      const b0 = (S[k] || autoBox(k)).slice();
      const grip = e.target.classList.contains('ce-grip');
      const before = snap();
      el.setPointerCapture(e.pointerId);
      el.classList.add('drag');
      const mv = (ev) => {
        const dx = (ev.clientX - e.clientX) / r.width, dy = (ev.clientY - e.clientY) / r.height;
        let [x, y, w, h] = b0;
        if (grip) { w = Math.max(0.05, Math.min(1 - x, w + dx)); h = Math.max(0.05, Math.min(1 - y, h + dy)); }
        else { x = Math.max(0, Math.min(1 - w, x + dx)); y = Math.max(0, Math.min(1 - h, y + dy)); }
        S[k] = [x, y, w, h].map((n) => Math.round(n * 1e4) / 1e4);
        placeBoxes(); draw();
      };
      const up = () => {
        el.classList.remove('drag');
        el.removeEventListener('pointermove', mv); el.removeEventListener('pointerup', up);
        if (snap() !== before) { undo.push(before); redo.length = 0; }
        refresh();
      };
      el.addEventListener('pointermove', mv); el.addEventListener('pointerup', up);
    }));
    $('#ce-reset', ce).addEventListener('click', () => commit(() => { S.facecam = null; S.game = null; }));

    // ---------- live preview ----------
    const cover = (sx, sy, sw, sh, dx, dy, dw, dh) => { // crop (sx..) to fill (dx..) without stretching
      const sr = sw / sh, dr = dw / dh;
      if (sr > dr) { const nw = sh * dr; sx += (sw - nw) / 2; sw = nw; } else { const nh = sw / dr; sy += (sh - nh) / 2; sh = nh; }
      cx.drawImage(v, sx, sy, sw, sh, dx, dy, dw, dh);
    };
    function draw() {
      const W = cv.width, H = cv.height;
      cx.fillStyle = '#05080d'; cx.fillRect(0, 0, W, H);
      if (!v || !v.videoWidth) return;
      const vw = v.videoWidth, vh = v.videoHeight;
      const px = (b) => [b[0] * vw, b[1] * vh, b[2] * vw, b[3] * vh];
      cx.save();
      if (S.layout === 'stack' && H > W) {
        const camH = Math.round(H * 0.36);
        cover(...px(S.facecam || autoBox('facecam')), 0, 0, W, camH);
        cover(...px(S.game || autoBox('game')), 0, camH, W, H - camH);
        cx.fillStyle = 'rgba(255,255,255,.85)'; cx.fillRect(0, camH - 1, W, 2);
      } else if (S.layout === 'crop') {
        cover(...px(S.game || autoBox('game')), 0, 0, W, H);
      } else {
        cx.filter = 'blur(14px) brightness(.55)';
        cover(0, 0, vw, vh, -20, -20, W + 40, H + 40);
        cx.filter = 'none';
        const s = Math.min(W / vw, H / vh), dw = vw * s, dh = vh * s;
        cx.drawImage(v, 0, 0, vw, vh, (W - dw) / 2, (H - dh) / 2, dw, dh);
      }
      cx.restore();
      // Hook at the top, captions near the bottom: like the final render.
      const t = v.currentTime;
      if (S.hook && t - S.start < 4) {
        cx.font = `800 ${Math.round(W * 0.07)}px Inter, system-ui, sans-serif`;
        wrapText(S.hook.toUpperCase(), W / 2, H * 0.12, W * 0.86, Math.round(W * 0.085), '#fff', true);
      }
      if (S.captions && S.words && S.words.length) {
        const i = S.words.findIndex((w) => w.s <= t && t < w.e + 0.15);
        if (i >= 0) {
          const g = Math.floor(i / 3) * 3, grp = S.words.slice(g, g + 3);
          cx.font = `900 ${Math.round(W * 0.085)}px Inter, system-ui, sans-serif`;
          const parts = grp.map((w) => w.t.trim().toUpperCase());
          const full = parts.join(' '), tw = cx.measureText(full).width;
          let x = W / 2 - tw / 2;
          const y = H * (H > W ? 0.72 : 0.84);
          cx.textAlign = 'left'; cx.lineJoin = 'round';
          parts.forEach((p, j) => {
            cx.lineWidth = W * 0.018; cx.strokeStyle = '#000'; cx.strokeText(p, x, y);
            cx.fillStyle = grp[j] === S.words[i] ? '#facc15' : '#fff'; cx.fillText(p, x, y);
            x += cx.measureText(p + ' ').width;
          });
        }
      }
    }
    function wrapText(text, x, y, max, lh, color, box) {
      const words = text.split(/\s+/), lines = [];
      let line = '';
      words.forEach((w) => { const t = line ? line + ' ' + w : w; if (cx.measureText(t).width > max && line) { lines.push(line); line = w; } else line = t; });
      if (line) lines.push(line);
      cx.textAlign = 'center'; cx.textBaseline = 'middle';
      lines.forEach((l, i) => {
        const ly = y + i * lh;
        if (box) { const w = cx.measureText(l).width + lh * 0.6; cx.fillStyle = 'rgba(0,0,0,.72)'; roundRect(x - w / 2, ly - lh / 2, w, lh, lh * 0.25); }
        cx.fillStyle = color; cx.fillText(l, x, ly);
      });
      cx.textBaseline = 'alphabetic';
    }
    function roundRect(x, y, w, h, r) { cx.beginPath(); cx.roundRect ? cx.roundRect(x, y, w, h, r) : cx.rect(x, y, w, h); cx.fill(); }

    // ---------- transcript ----------
    function words() {
      const box = $('#ce-words', ce);
      const ws = (S.words || []).filter((w) => w.s >= S.start - 8 && w.s < S.end + 8);
      if (!ws.length) { box.innerHTML = `<p class="muted small">${esc(S.transcript || 'No transcript yet.')}</p>`; return; }
      box.innerHTML = ws.map((w) => `<span data-s="${w.s}" class="${w.s >= S.start && w.s < S.end ? 'in' : ''}">${esc(w.t)}</span>`).join('');
    }
    $('#ce-words', ce).addEventListener('click', (e) => {
      const s = e.target.closest('[data-s]');
      if (!s || !v) return;
      const t = +s.dataset.s;
      if (e.shiftKey) commit(() => (t < S.start ? (S.start = t) : (S.end = t + 0.4)));
      else { v.currentTime = t; }
    });

    // ---------- controls ----------
    $$('#ce-aspects button', ce).forEach((b) => b.addEventListener('click', () => commit(() => (S.aspect = b.dataset.aspect))));
    $$('#ce-layouts button', ce).forEach((b) => b.addEventListener('click', () => commit(() => (S.layout = b.dataset.layout))));
    const bindIn = (sel, key, conv = (x) => x) => {
      const el = $(sel, ce);
      let before = null;
      el.addEventListener('focus', () => (before = snap()));
      el.addEventListener('input', () => { S[key] = conv(el.type === 'checkbox' ? el.checked : el.value); if (key === 'hook') draw(); refreshSave(); });
      el.addEventListener('change', () => { if (before && before !== snap()) { undo.push(before); redo.length = 0; } before = snap(); refresh(); });
    };
    const refreshSave = () => { $('#ce-save', ce).classList.toggle('dirty', dirty()); $('#ce-save', ce).textContent = dirty() ? 'Save & render' : 'Saved'; };
    bindIn('#ce-hook', 'hook'); bindIn('#ce-caption', 'caption'); bindIn('#ce-captions', 'captions');
    bindIn('#ce-fps', 'fps', Number); bindIn('#ce-height', 'height', Number);
    $$('#ce-tabs button', ce).forEach((b) => b.addEventListener('click', () => {
      $$('#ce-tabs button', ce).forEach((x) => x.classList.toggle('on', x === b));
      $$('.ce-pane', ce).forEach((p) => (p.hidden = p.dataset.pane !== b.dataset.tab));
    }));
    $$('#ce-pv-mode button', ce).forEach((b) => b.addEventListener('click', () => {
      $$('#ce-pv-mode button', ce).forEach((x) => x.classList.toggle('on', x === b));
      const fin_ = b.dataset.pv === 'final';
      cv.hidden = fin_; fin.hidden = !fin_;
      if (fin_) fin.play().catch(() => {}); else fin.pause();
    }));
    $('#ce-fav', ce).addEventListener('click', async (e) => {
      const r = await (await fetch(`/api/clip/${id}/favorite`, { method: 'POST' })).json();
      e.target.classList.toggle('on', r.favorite);
      toast(r.favorite ? '★ Added to favourites' : 'Removed from favourites');
    });

    // ---------- save + render ----------
    const busy = $('#ce-busy', ce);
    async function watchRender() {
      busy.hidden = false;
      clearInterval(timers.render);
      timers.render = setInterval(async () => {
        if (!document.body.contains(ce)) return clearInterval(timers.render);
        try {
          const s = await (await fetch(`/api/clip/${id}`)).json();
          if (s.busy) return;
          clearInterval(timers.render);
          busy.hidden = true;
          if (s.error) { toast(s.error, 'bad'); return; }
          fin.src = `/media/clips/clip_${id}.mp4?t=${Date.now()}`;
          $('[data-pv=final]', ce).click();
          toast('✅ Clip updated');
        } catch (_) {}
      }, 1500);
    }
    async function save(note) {
      const btn = $('#ce-save', ce);
      btn.disabled = true;
      try {
        const body = keys.reduce((o, k) => ((o[k] = S[k]), o), {});
        body.note = note || 'Manual edit';
        const r = await fetch(`/api/clip/${id}/save`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        const j = await r.json();
        if (!r.ok || j.ok === false) throw new Error(j.msg || 'Save failed');
        Object.assign(S, j.state || {});
        saved = snap(); refresh();
        watchRender();
      } catch (e) { toast(e.message, 'bad'); } finally { btn.disabled = false; }
    }
    $('#ce-save', ce).addEventListener('click', () => (dirty() ? save() : toast('No changes to save')));
    if (S.busy) watchRender();
    window.addEventListener('beforeunload', function warn(e) {
      if (!document.body.contains(ce)) return window.removeEventListener('beforeunload', warn);
      if (dirty()) { e.preventDefault(); e.returnValue = ''; }
    });

    // ---------- Edit with AI ----------
    const chat = $('#ce-chat', ce), ask = $('#ce-ask', ce), inp = $('#ce-ask-in', ce);
    const say = (who, text) => {
      const m = document.createElement('div');
      m.className = 'ce-msg ' + who; m.textContent = text;
      chat.appendChild(m); chat.scrollTop = chat.scrollHeight;
      return m;
    };
    async function aiAsk(text) {
      text = text.trim();
      if (!text) return;
      if (dirty()) await save('Manual edit');
      say('me', text);
      const typing = say('bot typing', '');
      typing.innerHTML = '<i></i><i></i><i></i>';
      inp.value = ''; inp.disabled = true;
      try {
        const r = await fetch(`/api/clip/${id}/ai`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ask: text }) });
        const j = await r.json();
        typing.remove();
        if (!r.ok || j.ok === false) { say('bot err', j.msg || 'That didn\'t work.'); return; }
        say('bot', j.reply || 'Done.');
        if (j.changes && Object.keys(j.changes).length) {
          undo.push(snap()); redo.length = 0;
          const s = await (await fetch(`/api/clip/${id}`)).json();
          keys.forEach((k) => (S[k] = s[k]));
          S.words = s.words;
          saved = snap(); refresh();
          if (v) v.currentTime = S.start;
          watchRender();
        }
      } catch (e) { typing.remove(); say('bot err', 'Lost connection to SXM. Try again.'); } finally { inp.disabled = false; inp.focus(); }
    }
    ask.addEventListener('submit', (e) => { e.preventDefault(); e.stopImmediatePropagation(); aiAsk(inp.value); }, true);
    $$('#ce-sugg button', ce).forEach((b) => b.addEventListener('click', () => aiAsk(b.textContent)));

    refresh();
  }

  window.SXMStudio = (root) => { streams(root); scan(root); editor(root); };
})();
