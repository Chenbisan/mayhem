'use strict';

const DD = 'https://ddragon.leagueoflegends.com/cdn';
const OPGG_IMG = 'https://opgg-static.akamaized.net/meta/images/lol';
const app = document.getElementById('app');
const state = { meta: {}, champs: [], descs: {}, cache: {} };

// ---------- per-device prefs (favorites / hidden) ----------
const store = {
  get(k) { try { return JSON.parse(localStorage.getItem(k)) || []; } catch { return []; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};
const favs = () => new Set(store.get('mayhem.favs'));
const hidden = () => new Set(store.get('mayhem.hidden'));
function toggle(key, slug) {
  const s = new Set(store.get(key));
  s.has(slug) ? s.delete(slug) : s.add(slug);
  store.set(key, [...s]);
}

// ---------- helpers ----------
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const tierBadge = t => t ? `<span class="tb t${t}" title="Blitz tier ${t} (1 = best)">T${t}</span>` : '';
const champIcon = id => `${DD}/${state.meta.ddragon}/img/champion/${id}.png`;
function itemImg(it, cls = '') {
  const v = state.meta.ddragon;
  // Data Dragon first; Mayhem-only items (e.g. 22xxxx) fall back to op.gg's CDN.
  return `<span class="it ${cls}" title="${esc(it.name)}">${it.tier ? tierBadge(it.tier) : ''}
    <img loading="lazy" alt="${esc(it.name)}" src="${DD}/${v}/img/item/${it.id}.png"
      onerror="this.onerror=null;this.src='${OPGG_IMG}/${v}/item/${it.id}.png'">
    ${it.count > 1 ? `<span class="cnt">${it.count}</span>` : ''}</span>`;
}
const itemRow = (items, arrows) =>
  `<div class="row">${items.map(itemImg).join(arrows ? '<span class="arrow">›</span>' : '')}</div>`;

async function getJSON(url) {
  const r = await fetch(url, { cache: 'no-cache' });
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

// ---------- home ----------
function renderHome(q = '') {
  const fav = favs(), hid = hidden();
  const ql = q.trim().toLowerCase();
  const match = c => !ql || c.name.toLowerCase().includes(ql) || c.slug.includes(ql);
  const tile = c => `<a class="tile" href="#/${c.slug}">${tierBadge(c.tier)}
      <img loading="lazy" src="${champIcon(c.id)}" alt=""><span class="name">${esc(c.name)}</span></a>`;
  const favList = state.champs.filter(c => fav.has(c.slug) && match(c));
  const rest = state.champs.filter(c => !fav.has(c.slug) && !hid.has(c.slug) && match(c));
  const hidList = state.champs.filter(c => hid.has(c.slug));

  app.innerHTML = `
    <input id="q" class="search" type="search" placeholder="Search champion…" value="${esc(q)}" autocomplete="off">
    ${favList.length ? `<h2>★ Favorites</h2><div class="grid">${favList.map(tile).join('')}</div>` : ''}
    <h2>${ql ? 'Results' : 'All champions'} <span class="muted">(${rest.length})</span></h2>
    <div class="grid">${rest.map(tile).join('') || '<span class="muted">No match.</span>'}</div>
    ${hidList.length ? `
      <h2>Hidden <span class="muted">(${hidList.length})</span></h2>
      <div class="hidden-list">${hidList.map(c => `<button class="chip" data-unhide="${c.slug}">${esc(c.name)} ✕</button>`).join('')}</div>
      <p class="muted" style="font-size:13px;margin:12px 0 0">Hiding only affects this device. To also stop updating them,
        paste this into <code>config.json</code> (or send it to Claude):</p>
      <textarea class="code" rows="3" readonly>${esc(JSON.stringify({ exclude: hidList.map(c => c.slug).sort(), delay: 1.0 }, null, 2))}</textarea>` : ''}
  `;
  const input = document.getElementById('q');
  input.addEventListener('input', () => {
    const pos = input.selectionStart;
    renderHome(input.value);
    const again = document.getElementById('q');
    again.focus(); again.setSelectionRange(pos, pos);
  });
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') {
      const first = [...state.champs].filter(c => match(c) && !hid.has(c.slug))[0];
      if (first) location.hash = `#/${first.slug}`;
    }
  });
  app.querySelectorAll('[data-unhide]').forEach(b => b.onclick = () => { toggle('mayhem.hidden', b.dataset.unhide); renderHome(input.value); });
}

// ---------- champion ----------
function augRow(a) {
  return `<div class="aug ${a.rarity || ''}" data-rarity="${a.rarity || ''}">
    <span class="rank">${a.rank}</span>
    <img loading="lazy" src="${esc(a.icon)}" alt=""
      ${a.icon2 ? `onerror="this.onerror=null;this.src='${esc(a.icon2)}'"` : ''}>
    <div class="body">
      <div class="nm">${esc(a.name)} ${tierBadge(a.tier)}
        ${a.rarity ? `<span class="rar ${a.rarity}">${a.rarity[0].toUpperCase() + a.rarity.slice(1)}</span>` : ''}</div>
      ${state.descs[a.name] ? `<div class="desc">${esc(state.descs[a.name])}</div>` : ''}
    </div></div>`;
}

const SORTS = {
  rank: { label: 'Rank', fn: (x, y) => x.rank - y.rank },
  tier: { label: 'Tier', fn: (x, y) => (x.tier || 9) - (y.tier || 9) || x.rank - y.rank },
  rarity: { label: 'Rarity', fn: (x, y) => (RAR[y.rarity] || 0) - (RAR[x.rarity] || 0) || x.rank - y.rank },
  name: { label: 'A–Z', fn: (x, y) => x.name.localeCompare(y.name) },
};
const RAR = { prismatic: 3, gold: 2, silver: 1 };
const AUG_SHOW = 20;

function renderChamp(d) {
  const fav = favs().has(d.slug), hid = hidden().has(d.slug);
  let sort = 'rank';
  try { sort = SORTS[localStorage.getItem('mayhem.sort')] ? localStorage.getItem('mayhem.sort') : 'rank'; } catch { /* ignore */ }

  app.innerHTML = `
    <a class="back" href="#/">‹ All champions</a>
    <div class="hero">
      <img src="${champIcon(d.id)}" alt="">
      <div>
        <h1>${esc(d.name)}</h1>
        <div class="sub">${d.tier ? `Mayhem ${tierBadge(d.tier)}` : ''}<span>Patch ${esc(d.patch || '?')}</span><span>· ${esc(d.updated)}</span></div>
      </div>
      <div class="actions">
        <button class="iconbtn ${fav ? 'on' : ''}" id="fav" title="Favorite">${fav ? '★' : '☆'}</button>
        <button class="iconbtn ${hid ? 'on' : ''}" id="hide" title="Hide from list">${hid ? 'Unhide' : 'Hide'}</button>
      </div>
    </div>

    <div class="card">
      <h3>Augments <span class="muted">${d.augments.length} · # = op.gg rank · T = Blitz tier</span></h3>
      <input id="aq" class="search sm" type="search" placeholder="Search augments…" autocomplete="off">
      <div class="filters"><span class="flbl">Sort</span>${Object.entries(SORTS).map(([k, v]) =>
        `<button class="chip ${k === sort ? 'on' : ''}" data-s="${k}">${v.label}</button>`).join('')}</div>
      <div class="filters"><span class="flbl">Show</span>${['all', 'prismatic', 'gold', 'silver'].map((r, i) =>
        `<button class="chip ${i ? '' : 'on'}" data-f="${r}">${r[0].toUpperCase() + r.slice(1)}</button>`).join('')}</div>
      <div id="augs"></div>
      <button class="more" id="augmore"></button>
    </div>

    <div class="card">
      <h3>Core build <span class="muted">op.gg #1</span></h3>
      ${d.core[0] ? itemRow(d.core[0], true) : '<span class="muted">No data</span>'}
      <div class="lbl" style="margin-top:12px">Boots</div>${itemRow(d.boots)}
    </div>

    <p class="legend">${[1, 2, 3, 4, 5].map(tierBadge).join('')} Blitz Mayhem tier, 1 = best ·
      <a href="https://op.gg/lol/modes/aram-mayhem/${d.slug}/augments" target="_blank" rel="noopener">op.gg</a> ·
      <a href="https://blitz.gg/lol/champions/${d.id}/aram-mayhem" target="_blank" rel="noopener">Blitz</a></p>
  `;

  let filter = 'all', expanded = false, q = '';
  const box = document.getElementById('augs'), more = document.getElementById('augmore');
  function drawAugs() {
    const ql = q.trim().toLowerCase();
    const list = d.augments
      .filter(a => filter === 'all' || a.rarity === filter)
      .filter(a => !ql || a.name.toLowerCase().includes(ql) || (state.descs[a.name] || '').toLowerCase().includes(ql))
      .sort(SORTS[sort].fn);
    const shown = expanded || ql ? list : list.slice(0, AUG_SHOW);
    box.innerHTML = shown.map(augRow).join('') || '<span class="muted">None.</span>';
    more.hidden = shown.length === list.length && !expanded;
    more.textContent = expanded ? 'Show fewer' : `Show all ${list.length}`;
  }
  drawAugs();
  more.onclick = () => { expanded = !expanded; drawAugs(); };
  box.onclick = e => { const a = e.target.closest('.aug'); if (a) a.classList.toggle('open'); };
  document.getElementById('aq').addEventListener('input', e => { q = e.target.value; drawAugs(); });
  app.querySelectorAll('[data-f]').forEach(b => b.onclick = () => {
    filter = b.dataset.f; expanded = false;
    app.querySelectorAll('[data-f]').forEach(x => x.classList.toggle('on', x === b));
    drawAugs();
  });
  app.querySelectorAll('[data-s]').forEach(b => b.onclick = () => {
    sort = b.dataset.s; expanded = false;
    try { localStorage.setItem('mayhem.sort', sort); } catch { /* ignore */ }
    app.querySelectorAll('[data-s]').forEach(x => x.classList.toggle('on', x === b));
    drawAugs();
  });
  document.getElementById('fav').onclick = () => { toggle('mayhem.favs', d.slug); renderChamp(d); };
  document.getElementById('hide').onclick = () => { toggle('mayhem.hidden', d.slug); renderChamp(d); };
}

// ---------- routing ----------
async function route() {
  const slug = decodeURIComponent(location.hash.replace(/^#\/?/, '')).toLowerCase();
  window.scrollTo(0, 0);
  if (!slug) return renderHome();
  try {
    const d = state.cache[slug] || (state.cache[slug] = await getJSON(`data/champ/${slug}.json`));
    d.slug = slug;
    document.title = `${d.name} · Mayhem Builds`;
    renderChamp(d);
  } catch (e) {
    app.innerHTML = `<p class="pad">No data for “${esc(slug)}”. <a href="#/">Back</a></p>`;
  }
}

(async function init() {
  try {
    [state.meta, state.champs, state.descs] = await Promise.all(
      [getJSON('data/meta.json'), getJSON('data/champions.json'), getJSON('data/augments.json').catch(() => ({}))]);
  } catch (e) {
    app.innerHTML = `<p class="pad">Couldn't load data: ${esc(e.message)}</p>`;
    return;
  }
  const m = state.meta;
  document.getElementById('meta').innerHTML = `Patch ${esc(m.patch || '?')}<br>updated ${esc((m.lastRun || '').split(' ')[0])}`;
  window.addEventListener('hashchange', () => { document.title = 'Mayhem Builds'; route(); });
  route();
})();
