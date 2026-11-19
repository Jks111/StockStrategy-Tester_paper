'use strict';
const $ = s => document.querySelector(s),
  $$ = s => [...document.querySelectorAll(s)];
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;'
} [c]));
const num = (v, d = 2) => v == null || !Number.isFinite(v) ? '—' : Number(v).toLocaleString('en-US', {
  maximumFractionDigits: d,
  minimumFractionDigits: d
});
const pct = v => v == null ? '—' : `${num(v*100)}%`;
const names = {
  SPY: 'SPDR S&P 500 ETF · market reference',
  QQQ: 'Invesco Nasdaq 100 ETF',
  AAPL: 'Apple',
  MSFT: 'Microsoft',
  NVDA: 'NVIDIA',
  AMZN: 'Amazon'
};
const defaults = {
  capital: 1000,
  max_positions: 4,
  position_fraction: .2,
  risk_fraction: .01,
  spread_bps: 4,
  slippage_bps: 2,
  commission: 0,
  borrow_apr: .03,
  max_drawdown: .2,
  risk_free: 0,
  share_mode: 'fractional'
};
const riskLabels = {
  capital: 'Starting capital (USD)',
  max_positions: 'Maximum simultaneous positions',
  position_fraction: 'Position allocation (fraction)',
  risk_fraction: 'Risk per entry (fraction)',
  spread_bps: 'Full spread (basis points)',
  slippage_bps: 'Slippage per side (basis points)',
  commission: 'Commission per order (USD)',
  borrow_apr: 'Annual short borrow rate (fraction)',
  max_drawdown: 'Drawdown entry halt (fraction)',
  risk_free: 'Annual risk-free rate (fraction)',
  share_mode: 'Share sizing'
};
const paramLabels = {
  ema_length: 'EMA length',
  atr_multiple: 'ATR multiple',
  allow_short: 'Allow simulated short positions',
  lookback: 'Lookback bars',
  min_r2: 'Minimum regression R²',
  hold_bars: 'Maximum bars held',
  entry_z: 'Entry z-score',
  exit_z: 'Exit z-score',
  weekday: 'Entry day',
  event_days: 'Public event window (days)',
  min_value: 'Minimum purchase value (USD)',
  min_insiders: 'Distinct buyers',
  target_multiple: 'Range target multiple',
  target_cash: 'Cash target (USD)'
};
let boot, runs = [],
  events = [],
  chosen, range = 252,
  chartRows = {},
  toastTimer, activeTab = 'charts',
  charts = new Map(),
  pollBusy = false;
let savedRisk;
try {
  savedRisk = {
    ...defaults,
    ...JSON.parse(localStorage.getItem('quantlab-risk') || '{}')
  };
  if (localStorage.getItem('quantlab-capital-version') !== '1000-v1') {
    savedRisk.capital = defaults.capital;
    localStorage.setItem('quantlab-risk', JSON.stringify(savedRisk));
    localStorage.setItem('quantlab-capital-version', '1000-v1');
  }
} catch {
  savedRisk = {
    ...defaults
  }
}
async function api(path, data) {
  const options = data === undefined ? {} : {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Quantlab-Token': boot.token
    },
    body: JSON.stringify(data)
  };
  const response = await fetch('/api/' + path, options);
  const body = await response.json();
  if (!response.ok) {
    if (response.status === 404 && path === 'recent-batch') {
      throw Error('This QuantLab server is missing stock backtests. Restart QuantLab and reload this tab.');
    }
    throw Error(body.error || 'Request failed');
  }
  return body;
}

function toast(message, error = false) {
  clearTimeout(toastTimer);
  const node = $('#toast');
  node.textContent = message;
  node.classList.remove('hidden');
  node.classList.toggle('error', error);
  toastTimer = setTimeout(() => node.classList.add('hidden'), error ? 10000 : 5500);
}
async function safely(action) {
  try {
    await action()
  } catch (error) {
    toast(error.message, true)
  }
}

function tab(name) {
  if (!$('#' + name)) name = 'charts';
  activeTab = name;
  $$('.page').forEach(p => p.classList.toggle('active', p.id === name));
  $$('nav button').forEach(b => {
    b.classList.toggle('active', b.dataset.tab === name);
    b.setAttribute('aria-current', b.dataset.tab === name ? 'page' : 'false')
  });
  history.replaceState(null, '', '#' + name);
  requestAnimationFrame(() => charts.forEach(c => c.draw()));
  if (name === 'experiments') safely(refreshRuns);
  if (name === 'insiders') safely(refreshInsiders);
  if (name === 'data') safely(refreshTasks);
  if (name === 'replay') safely(refreshReplay);
  if (name === 'database') safely(refreshDatabase);
}
$$('nav button').forEach(button => button.addEventListener('click', () => tab(button.dataset.tab)));
$('.brand').addEventListener('click', e => {
  e.preventDefault();
  tab('charts')
});

class Chart {
  constructor(canvas, series, options = {}) {
    this.canvas = canvas;
    this.series = series;
    this.options = options;
    this.pointer = null;
    canvas.addEventListener('pointermove', event => {
      const r = canvas.getBoundingClientRect();
      this.pointer = event.clientX - r.left;
      this.draw()
    });
    canvas.addEventListener('pointerleave', () => {
      this.pointer = null;
      this.draw()
    });
    this.observer = new ResizeObserver(() => this.draw());
    this.observer.observe(canvas);
    charts.set(canvas, this);
    this.draw();
  }
  destroy() {
    this.observer.disconnect();
    charts.delete(this.canvas)
  }
  draw() {
    const canvas = this.canvas,
      w = canvas.clientWidth,
      h = canvas.clientHeight;
    if (!w || !h) return;
    const ratio = devicePixelRatio || 1;
    canvas.width = Math.round(w * ratio);
    canvas.height = Math.round(h * ratio);
    const ctx = canvas.getContext('2d');
    ctx.scale(ratio, ratio);
    ctx.clearRect(0, 0, w, h);
    const flat = this.series.flatMap(s => s.points.map(p => p.value)).filter(Number.isFinite);
    if (!flat.length) {
      ctx.fillStyle = '#95959c';
      ctx.font = '12px Segoe UI';
      ctx.fillText('No observations', 10, 30);
      return
    }
    let lo = Math.min(...flat),
      hi = Math.max(...flat);
    if (hi === lo) {
      lo -= Math.abs(lo) * .01 || 1;
      hi += Math.abs(hi) * .01 || 1
    }
    const pad = (hi - lo) * .08;
    lo -= pad;
    hi += pad;
    const left = 3,
      right = w - 64,
      top = 16,
      bottom = h - 28,
      base = this.series[0].points;
    const x = i => left + (right - left) * (base.length > 1 ? i / (base.length - 1) : .5),
      y = value => bottom - (bottom - top) * (value - lo) / (hi - lo);
    ctx.font = '10px Consolas,monospace';
    ctx.textAlign = 'left';
    for (let i = 0; i < 4; i++) {
      const v = lo + (hi - lo) * i / 3,
        Y = y(v);
      ctx.strokeStyle = '#1c1c20';
      ctx.beginPath();
      ctx.moveTo(left, Y);
      ctx.lineTo(right, Y);
      ctx.stroke();
      ctx.fillStyle = '#85858f';
      ctx.fillText(this.options.percent ? num(v * 100, 1) + '%' : num(v, Math.abs(v) > 100 ? 0 : 2), right + 9, Y + 3)
    }
    for (const series of this.series) {
      if (!series.points.length) continue;
      ctx.beginPath();
      series.points.forEach((point, i) => {
        const index = point.index ?? i;
        const X = x(index),
          Y = y(point.value);
        i ? ctx.lineTo(X, Y) : ctx.moveTo(X, Y)
      });
      ctx.strokeStyle = series.color || '#a6c8db';
      ctx.lineWidth = 1.5;
      ctx.stroke();
      if (series === this.series[0]) {
        const last = series.points.at(-1);
        ctx.fillStyle = series.color || '#a6c8db';
        ctx.beginPath();
        ctx.arc(x(last.index ?? series.points.length - 1), y(last.value), 2.5, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.fillStyle = '#777781';
    ctx.font = '10px Consolas,monospace';
    [0, Math.floor((base.length - 1) / 2), base.length - 1].forEach((i, k) => {
      ctx.textAlign = k === 0 ? 'left' : k === 2 ? 'right' : 'center';
      ctx.fillText(String(base[i]?.date || '').slice(0, 10), x(i), h - 7)
    });
    if (this.pointer != null && base.length) {
      const index = Math.max(0, Math.min(base.length - 1, Math.round((this.pointer - left) / (right - left) * (base.length - 1))));
      const p = base[index];
      ctx.strokeStyle = '#53535b';
      ctx.setLineDash([3, 4]);
      ctx.beginPath();
      ctx.moveTo(x(index), top);
      ctx.lineTo(x(index), bottom);
      ctx.stroke();
      ctx.setLineDash([]);
      const text = String(p.date) + '  ' + (this.options.percent ? pct(p.value) : num(p.value));
      ctx.font = '11px Consolas,monospace';
      const tw = ctx.measureText(text).width + 14,
        tx = Math.min(Math.max(x(index) - tw / 2, 0), w - tw);
      ctx.fillStyle = '#202025';
      ctx.fillRect(tx, 0, tw, 20);
      ctx.fillStyle = '#ececee';
      ctx.textAlign = 'left';
      ctx.fillText(text, tx + 7, 14);
    }
  }
}

function plot(id, series, options = {}) {
  const node = $(id);
  if (charts.has(node)) charts.get(node).destroy();
  return new Chart(node, series, options)
}

function datasetOptions(filter = () => true) {
  return boot.datasets.filter(filter).map(d => `<option value="${esc(d.id)}">${esc(d.name)}${d.source==='synthetic'?' [SYNTHETIC]':''}</option>`).join('')
}
const allocations = [];

function renderAllocations() {
  $('#allocation-list').innerHTML = '<table><thead><tr><th>Strategy</th><th>Symbols / dates</th><th>Weight %</th><th></th></tr></thead><tbody>' + allocations.map((c, i) => `<tr><td>${esc(c.spec.strategy)}<small>${esc(c.spec.risk.share_mode)}</small></td><td>${esc(c.spec.symbols.join(', '))}<small>${esc(c.spec.start)} — ${esc(c.spec.end)}</small></td><td><input aria-label="Allocation ${i+1} weight" data-weight="${i}" type="number" min="1" max="100" value="${c.weight*100}"></td><td><button data-remove="${i}">Remove</button></td></tr>`).join('') + '</tbody></table>';
  $$('[data-weight]').forEach(el => el.addEventListener('change', () => allocations[+el.dataset.weight].weight = Number(el.value) / 100));
  $$('[data-remove]').forEach(el => el.addEventListener('click', () => {
    allocations.splice(+el.dataset.remove, 1);
    renderAllocations();
  }));
}
$('#add-allocation').addEventListener('click', () => safely(async () => {
  if (allocations.length >= 5) throw Error('Maximum five allocations');
  allocations.push({
    spec: spec(),
    weight: .5
  });
  renderAllocations();
  toast('Strategy setup added. Add another, then open Portfolio.');
}));
$('#run-portfolio').addEventListener('click', () => safely(async () => {
  const result = await api('portfolio', {
    capital: Number($('#portfolio-capital').value),
    label: $('#portfolio-label').value,
    components: allocations
  });
  $('#portfolio-note').textContent = `Saved experiment ${result.run_id}. View its result in Experiments.`;
  await refreshRuns();
  tab('experiments');
}));
let stockHistory = [], stockHistoryLimit = 25;
const strategyName = id => boot?.strategies.find(s => s.id === id)?.name || id;

function renderStockHistory() {
  const search = $('#stock-history-search').value.trim().toLowerCase();
  const key = $('#stock-history-sort').value;
  const matches = stockHistory.filter(row => `${row.symbol} ${row.name}`.toLowerCase().includes(search));
  matches.sort((a, b) => {
    if ((a[key] == null) !== (b[key] == null)) return a[key] == null ? 1 : -1;
    const strict = key === 'created' ? b.created.localeCompare(a.created)
      : key === 'drawdown' ? a.drawdown - b.drawdown : b[key] - a[key];
    return strict || a.symbol.localeCompare(b.symbol) || a.name.localeCompare(b.name)
      || b.created.localeCompare(a.created);
  });
  const visible = matches.slice(0, stockHistoryLimit);
  $('#stock-history-status').textContent = !stockHistory.length ? 'Completed stock backtests will appear here.'
    : !matches.length ? 'No saved backtests match your search.'
    : `Showing ${visible.length} of ${matches.length} matching backtests · ${matches.filter(row => row.net_pnl > 0).length} profitable after costs`;
  $('#stock-history-table').hidden = !visible.length;
  $('#stock-history-more').hidden = visible.length >= matches.length;
  $('#stock-history-rows').innerHTML = visible.map(row => `
    <tr><td><button type="button" class="link" data-stock-history-result="${esc(row.run_id)}">${esc(row.symbol)} · ${esc(row.name)}</button>
    <small>Saved ${esc(row.created.slice(0, 10))}${row.feed ? ' · ' + esc(row.feed.toUpperCase()) : ''}</small></td>
    <td>${esc(row.start)} to ${esc(row.end)}</td><td class="number ${row.net_pnl < 0 ? 'down' : row.net_pnl > 0 ? 'up' : ''}">${usd.format(row.net_pnl)}</td>
    <td class="number">${row.trades}</td><td class="number">${row.wins}</td><td class="number">${pct(row.win_rate)}</td>
    <td class="number">${pct(row.drawdown)}</td></tr>`).join('');
}

async function refreshDatabase() {
  const [history, data, batches] = await Promise.all([
    api('stock-backtest-history'), api('database'), api('stock-backtest-batches')
  ]);
  const select = $('#stock-batch-list');
  const previous = select.value;
  select.innerHTML = batches.map(batch => `<option value="${esc(batch.id)}">${esc(batch.created.slice(0, 16).replace('T', ' '))} UTC · ${batch.mode === 'deep' ? 'Deep test' : 'Standard test'} · ${batch.symbols} tickers · ${batch.strategies} strategies · ${esc(batch.feed.toUpperCase())}</option>`).join('')
    || '<option value="">No saved batch reports yet</option>';
  if (batches.some(batch => batch.id === previous)) select.value = previous;
  $('#stock-batch-open').disabled = !batches.length;
  stockHistory = history.map(row => ({...row, name: strategyName(row.strategy)}));
  renderStockHistory();
  $('#database-path').textContent = data.path;
  $('#database-stats').innerHTML = '<div class="metric-strip">' + metric('Experiments', data.counts.runs || 0) + metric('Insider records', data.counts.insider_events || 0) + metric('Price datasets', data.counts.datasets || 0) + metric('Price bars', num(data.bars, 0)) + metric('Result files', data.result_files) + metric('SQLite size · MB', num(data.database_bytes / 1048576)) + '</div><p class="muted">' + Object.entries(data.statuses).map(([k, v]) => `${esc(k)}: ${v}`).join(' · ') + '</p>';
}
$('#refresh-database').addEventListener('click', () => safely(refreshDatabase));
$('#recent-history').addEventListener('click', () => tab('database'));
$('#stock-batch-open').addEventListener('click', () => safely(async () => {
  const report = await api(`stock-backtest-batches/${encodeURIComponent($('#stock-batch-list').value)}`);
  tab('recent');
  $('#recent-progress-panel').hidden = !recentBusy;
  renderRecentBatch(report);
  $('#recent-results-title').scrollIntoView({block: 'start'});
}));
for (const id of ['stock-history-search', 'stock-history-sort']) {
  $(`#${id}`).addEventListener('input', () => {
    stockHistoryLimit = 25;
    renderStockHistory();
  });
}
$('#stock-history-more').addEventListener('click', () => {
  stockHistoryLimit += 25;
  renderStockHistory();
});
$('#stock-history-rows').addEventListener('click', event => {
  const button = event.target.closest('[data-stock-history-result]');
  if (button) safely(() => showResult(button.dataset.stockHistoryResult));
});
$('#audit-data').addEventListener('click', () => safely(async () => {
  const button = $('#audit-data');
  button.disabled = true;
  try {
    const report = await api('audit', {
      dataset: $('#audit-dataset').value
    });
    $('#audit-output').innerHTML = `<h3>${esc(report.status)}</h3><p class="muted">Saved ${esc(report.created)} · ${esc(report.calendar)} calendar ${esc(report.calendar_version)}</p><div class="table-wrap"><table><thead><tr><th>Symbol</th><th>Bars</th><th>Missing sessions</th><th>Unexpected sessions</th><th>Timestamp errors</th><th>Incomplete sessions</th></tr></thead><tbody>${report.symbols.map(r=>`<tr><td>${esc(r.symbol)}</td><td>${r.bars}</td><td>${r.missing_sessions}</td><td>${r.unexpected_sessions}</td><td>${r.boundary_errors}</td><td>${r.incomplete_sessions}</td></tr>`).join('')}</tbody></table></div><ul>${report.notes.map(n=>`<li>${esc(n)}</li>`).join('')}</ul>`;
  } finally {
    button.disabled = false;
  }
}));

let recentBusy = false;
const recentChecked = kind => $$(`#recent-${kind} input:checked`).map(input => input.value);
const recentTicker = /^[A-Z][A-Z0-9.\-]{0,14}$/;

function recentSelection() {
  const extras = $('#recent-extra').value.toUpperCase().split(',').map(s => s.trim()).filter(Boolean);
  return {
    symbols: [...new Set([...recentChecked('symbols'), ...extras])],
    strategies: recentChecked('strategies'),
    period: $('#recent-period').value,
    feed: $('#recent-feed').value
  };
}

function recentWarning() {
  const {symbols, strategies, period} = recentSelection();
  const messages = [];
  const invalid = symbols.some(s => !recentTicker.test(s));
  if (invalid) messages.push('Enter valid US tickers separated by commas.');
  if (period === 'today' && boot?.strategies.some(s => strategies.includes(s.id) && s.timeframe === '1Day')) {
    messages.push('Daily strategies need completed daily bars and will be skipped for today’s window.');
  }
  if (symbols.includes('SPY') && strategies.includes('residual_reversion') && period !== 'today') {
    messages.push('Residual reversion uses SPY as its benchmark and will be skipped for SPY.');
  }
  $('#recent-warning').textContent = messages.join(' ');
  $('#recent-run').disabled = recentBusy || invalid || !symbols.length || !strategies.length;
  $('#recent-deep').disabled = $('#recent-run').disabled;
  $$('[data-recent-remove]').forEach(button => {
    button.setAttribute('aria-disabled', String(!strategies.includes(button.dataset.recentRemove)));
  });
}

function recentOptions(kind, options, initial) {
  const list = $(`#recent-${kind}`);
  if (JSON.stringify(options.map(([value]) => value)) === JSON.stringify([...list.querySelectorAll('input')].map(el => el.value))) return;
  const selected = list.children.length ? recentChecked(kind) : [initial];
  list.innerHTML = options.map(([value, label]) => `<label><input type="checkbox" value="${esc(value)}" ${selected.includes(value) ? 'checked' : ''}>${esc(label)}</label>`).join('');
}

$('#recent-form').addEventListener('input', recentWarning);
$$('[data-recent-all], [data-recent-clear]').forEach(button => {
  button.addEventListener('click', () => {
    const kind = button.dataset.recentAll || button.dataset.recentClear;
    $$(`#recent-${kind} input`).forEach(input => { input.checked = Boolean(button.dataset.recentAll); });
    recentWarning();
  });
});

const usd = new Intl.NumberFormat('en-US', {style: 'currency', currency: 'USD'});
let recentTradeHighlights = new Map();

function recentTradeList(trades, title) {
  if (!trades.length) return '';
  return `<div><strong>${title}</strong><ul>${trades.map(({trade, run_id}) =>
    `<li><button type="button" class="link" data-recent-result="${esc(run_id)}">${esc(trade.symbol)} · ${esc(trade.exit_time.slice(0, 10))} · ${usd.format(trade.net_pnl)}</button></li>`
  ).join('')}</ul></div>`;
}

function sortRecentCards() {
  const list = $('#recent-cards');
  const key = $('#recent-sort').value;
  const cards = [...list.querySelectorAll('.recent-result')].sort((a, b) => {
    const x = a.dataset[key], y = b.dataset[key];
    if ((x === '') !== (y === '')) return x === '' ? 1 : -1;
    return Number(y) - Number(x) || a.dataset.symbol.localeCompare(b.dataset.symbol)
      || Number(a.dataset.window) - Number(b.dataset.window);
  });
  const groups = new Map();
  for (const card of cards) {
    const name = card.dataset.strategy;
    if (!groups.has(name)) groups.set(name, {name, cards: [], wins: 0, trades: 0, net: 0});
    const group = groups.get(name);
    group.cards.push(card);
    group.wins += Number(card.dataset.wins);
    group.trades += Number(card.dataset.trades);
    group.net += Number(card.dataset.net_pnl);
  }
  for (const group of groups.values()) {
    group.net_pnl = group.net / group.cards.length;
    group.win_rate = group.trades ? group.wins / group.trades : -1;
  }
  const ranked = [...groups.values()].sort((a, b) => b[key] - a[key] || a.name.localeCompare(b.name));
  list.replaceChildren(...ranked.map(group => {
    const section = document.createElement('section');
    section.className = 'recent-group';
    const heading = document.createElement('h3');
    heading.textContent = group.name;
    const summary = document.createElement('p');
    summary.className = 'muted';
    const color = value => value > 0 ? 'up' : value < 0 ? 'down' : 'recent-neutral';
    const metrics = [
      [usd.format(group.net), 'total net P/L', color(group.net)],
      [usd.format(group.net_pnl), 'average net P/L', color(group.net_pnl)],
      [group.wins, 'winning trades', color(group.wins)],
      [pct(group.trades ? group.win_rate : null), 'win rate', color(group.trades ? group.win_rate - .5 : 0)],
      [group.cards.length, 'completed runs', 'recent-neutral']
    ];
    summary.innerHTML = metrics.map(([value, label, tone]) =>
      `<strong class="${tone}">${esc(value)}</strong> ${label}`
    ).join(' · ');
    section.append(heading, summary);
    const highlights = recentTradeHighlights.get(group.name);
    if (highlights) {
      const details = document.createElement('details');
      details.className = 'recent-highlights';
      details.innerHTML = `<summary>Best and worst trades</summary><div class="recent-pickers">
        ${recentTradeList(highlights.wins, 'Top winning trades')}${recentTradeList(highlights.losses, 'Largest losses')}</div>`;
      section.append(details);
    }
    section.append(...group.cards);
    return section;
  }));
}
$('#recent-sort').addEventListener('change', sortRecentCards);

function removeStrategyButton(row) {
  const label = `Deselect ${row.name} for the next batch`;
  return `<button type="button" class="recent-remove" data-recent-remove="${esc(row.strategy)}" aria-label="${esc(label)}" title="${esc(label)}">×</button>`;
}

function comparisonText(reference, label, link = false) {
  const title = esc(label);
  if (reference?.net_pnl == null) {
    return `${title} unavailable${link && reference?.message ? ' · ' + esc(reference.message) : ''}`;
  }
  const text = `${title} ${usd.format(reference.net_pnl)}`;
  return link ? `<button type="button" class="link" data-recent-result="${esc(reference.run_id)}">${text}</button>` : text;
}

function renderRecentBatch({outcomes, feed, mode, batch_id}) {
  const completed = outcomes.filter(row => row.status === 'complete');
  const skipped = outcomes.filter(row => row.status === 'skipped');
  const failed = outcomes.filter(row => row.status === 'failed');
  recentTradeHighlights = new Map();
  for (const row of completed) {
    if (!row.top_trades?.length && !row.bottom_trades?.length) continue;
    if (!recentTradeHighlights.has(row.name)) recentTradeHighlights.set(row.name, {wins: [], losses: []});
    const group = recentTradeHighlights.get(row.name);
    group.wins.push(...(row.top_trades ?? []).map(trade => ({trade, run_id: row.run_id})));
    group.losses.push(...(row.bottom_trades ?? []).map(trade => ({trade, run_id: row.run_id})));
  }
  for (const group of recentTradeHighlights.values()) {
    group.wins = group.wins.sort((a, b) => b.trade.net_pnl - a.trade.net_pnl).slice(0, 5);
    group.losses = group.losses.sort((a, b) => a.trade.net_pnl - b.trade.net_pnl).slice(0, 5);
  }
  $('#recent-status').textContent = `${completed.length} completed · ${skipped.length} skipped · ${failed.length} failed · ${feed.toUpperCase()} feed`;
  $('#recent-selection-status').textContent = '';
  $('#recent-results-title').textContent = mode === 'deep' ? 'Deep test results' : 'Batch results';
  const report = $('#recent-report-download');
  report.hidden = !batch_id;
  if (batch_id) report.href = `/api/stock-backtest-batches/${encodeURIComponent(batch_id)}?format=json`;
  $('#recent-cards').innerHTML = completed.map(row => `
    <div class="recent-result" data-net_pnl="${row.net_pnl}" data-wins="${row.wins}" data-win_rate="${row.win_rate ?? ''}"
      data-symbol="${esc(row.symbol)}" data-strategy="${esc(row.name)}" data-trades="${row.trades}" data-window="${row.window}">
    <details class="recent-card">
      <summary><span><strong>${esc(row.symbol)}</strong>${mode === 'deep' ? ` · ${esc(row.start)} to ${esc(row.end)}` : ''}
        <small>${comparisonText(row.comparisons?.[0], 'Stock buy & hold')} · ${comparisonText(row.comparisons?.[1], 'SPY')}</small></span>
        <span class="${row.net_pnl < 0 ? 'down' : row.net_pnl > 0 ? 'up' : ''}">${usd.format(row.net_pnl)} net P/L</span>
        <span>${row.trades} ${row.trades === 1 ? 'trade' : 'trades'}</span>
        <span>${row.win_rate == null ? 'Win rate unavailable' : pct(row.win_rate) + ' win rate'}</span></summary>
      <div class="recent-detail">
        <p>${esc(row.start)} to ${esc(row.end)} · ${usd.format(row.risk.capital)} starting capital</p>
        <p>${usd.format(row.costs)} trading costs · ${pct(row.drawdown)} maximum drawdown</p>
        <p>${row.wins} wins · ${row.losses} losses · ${row.trades - row.wins - row.losses} break-even${row.trades ? '' : ' · No closed trades'}</p>
        <p>${comparisonText(row.comparisons?.[0], `${row.symbol} buy & hold`, true)} · ${comparisonText(row.comparisons?.[1], 'SPY buy & hold', true)}</p>
        <p>Cost model uses ${row.risk.spread_bps} bps full spread, ${row.risk.slippage_bps} bps slippage per side, ${usd.format(row.risk.commission)} per order, and ${pct(row.risk.borrow_apr)} annual short borrow.</p>
        <ul>${row.assumptions.map(note => `<li>${esc(note)}</li>`).join('')}</ul>
        <button type="button" data-recent-result="${esc(row.run_id)}">Open full saved result</button>
      </div>
    </details>${removeStrategyButton(row)}</div>`).join('');
  sortRecentCards();
  $('#recent-issues').innerHTML = [...skipped, ...failed].map(row => `
    <div class="recent-result"><p class="recent-issue down"><strong>${row.status === 'skipped' ? 'Skipped' : 'Failed'} · ${esc(row.symbol)} · ${esc(row.name)}${mode === 'deep' ? ` · Window ${row.window}` : ''}</strong><br>${esc(row.message)}
    ${row.comparisons ? `<br>${comparisonText(row.comparisons[0], 'Stock buy & hold', true)} · ${comparisonText(row.comparisons[1], 'SPY', true)}` : ''}</p>
    ${removeStrategyButton(row)}</div>`).join('');
  recentWarning();
  $('#recent-results').hidden = false;
}

$('#recent-results').addEventListener('click', event => {
  const button = event.target.closest('[data-recent-result]');
  if (button) safely(() => showResult(button.dataset.recentResult));
  const remove = event.target.closest('[data-recent-remove]');
  if (!remove || remove.getAttribute('aria-disabled') === 'true') return;
  const strategy = remove.dataset.recentRemove;
  const checkbox = $$('#recent-strategies input').find(input => input.value === strategy);
  if (!checkbox) return;
  checkbox.checked = false;
  $('#recent-selection-status').textContent = `${strategyName(strategy)} deselected for the next batch.`;
  recentWarning();
});

function renderRecentProgress(task = {}) {
  const bar = $('#recent-progress');
  const {processed, total} = task.progress || {};
  const done = task.status === 'complete';
  const processedAll = done || (total > 0 && processed === total);
  if (total > 0 || done) {
    bar.max = total || 1;
    bar.value = done ? bar.max : processed;
  } else {
    bar.removeAttribute('value');
  }
  $('#recent-progress-count').textContent = total > 0 ? `${processed} of ${total}` : '';
  const stages = [Boolean(task.status), processedAll, done];
  $$('.recent-progress-steps li').forEach((step, i) => {
    step.classList.toggle('done', stages[i]);
    step.firstElementChild.textContent = stages[i] ? '✓' : '○';
    const label = step.lastChild.textContent;
    step.setAttribute('aria-label', `${label}, ${stages[i] ? 'complete' : 'pending'}`);
  });
}

$('#recent-form').onsubmit = async event => {
  event.preventDefault();
  recentWarning();
  const button = event.submitter || $('#recent-run');
  if (button.disabled) return;
  const label = button.textContent;
  const mode = button.id === 'recent-deep' ? 'deep' : 'standard';
  recentBusy = true;
  recentWarning();
  button.textContent = 'Downloading and testing…';
  $('#recent-results').hidden = true;
  $('#recent-progress-panel').hidden = false;
  renderRecentProgress();
  $('#recent-status').textContent = 'Submitting batch…';
  let errorMessage;
  try {
    const {task_id} = await api('recent-batch', {...recentSelection(), mode});
    for (;;) {
      const task = await api(`tasks/${task_id}`);
      renderRecentProgress(task);
      if (task.status === 'complete') {
        renderRecentBatch(task.result);
        break;
      }
      if (task.status === 'failed') throw Error(task.error);
      const status = task.status === 'queued' ? 'Waiting in the backtest queue…'
        : task.progress && task.progress.processed === task.progress.total ? 'Saving the batch report…'
        : 'Downloading prices and running stock backtests…';
      if ($('#recent-status').textContent !== status) $('#recent-status').textContent = status;
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  } catch (error) {
    $('#recent-progress-panel').hidden = true;
    $('#recent-status').textContent = '';
    errorMessage = error.message;
  } finally {
    button.textContent = label;
    recentBusy = false;
    recentWarning();
    if (errorMessage) $('#recent-warning').textContent = errorMessage;
  }
};
async function refreshBoot() {
  const previous = boot;
  boot = await api('bootstrap');
  const symbols = [...new Set(['AAPL', ...boot.datasets.flatMap(d => d.symbols)])].filter(s => recentTicker.test(s)).sort();
  recentOptions('symbols', symbols.map(s => [s, s]), 'AAPL');
  recentOptions('strategies', boot.strategies.map(s => [s.id, s.name]), boot.strategies[0]?.id);
  recentWarning();
  const hours = boot.market_hours;
  $('#market-hours').innerHTML = hours.markets.map(m => `<div class="market-hour"><a href="${esc(m.source)}" target="_blank" rel="noopener noreferrer">${esc(m.name)}</a><strong>${esc(m.today)}</strong><span>${esc(m.status)}${m.early_close?' · early close':''}</span><small>${esc(m.exchange)} · regular ${esc(m.local)} local</small><small>Next open ${esc(m.next_open||'unavailable')}</small></div>`).join('');
  $('#market-date').textContent = `Helsinki time · DST conversion for ${hours.date}`;
  if (!previous || previous.datasets.length !== boot.datasets.length || !$('#audit-dataset').options.length) {
    const auditId = $('#audit-dataset').value;
    $('#audit-dataset').innerHTML = datasetOptions();
    if (boot.datasets.some(d => d.id === auditId)) $('#audit-dataset').value = auditId;
    const old = $('#chart-dataset').value;
    $('#chart-dataset').innerHTML = datasetOptions();
    $('#chart-dataset').value = boot.datasets.some(d => d.id === old) ? old : (boot.datasets.find(d => d.timeframe === '1Day')?.id || boot.datasets[0]?.id || '');
    if (chosen) setRunDatasets();
    await renderCharts()
  }
  renderData();
  $('#connection-status').innerHTML = `<p>Alpaca credentials: <strong>${boot.connections.alpaca?'configured':'not configured'}</strong></p><p>Trading 212 demo credentials: <strong>${boot.connections.trading212?'configured':'not configured'}</strong></p><p>SEC contact: <strong>${boot.connections.sec?'configured':'not configured'}</strong></p>`;
}
async function renderCharts() {
  const id = $('#chart-dataset').value,
    d = boot.datasets.find(d => d.id === id);
  $$('#chart-grid canvas').forEach(c => charts.get(c)?.destroy());
  if (!d) {
    $('#chart-grid').innerHTML = '<div class="empty">Import a dataset or create synthetic examples in Data.</div>';
    return
  }
  $('#source-label').textContent = d.source === 'synthetic' ? 'SYNTHETIC · NOT MARKET DATA' : `${d.source.toUpperCase()} · ${d.feed||'IMPORT'}`;
  $('#source-label').classList.toggle('real', d.source !== 'synthetic');
  $('#chart-caption').textContent = `${d.timeframe==='1Day'?'Daily':'Intraday '+d.timeframe} · ${d.currency||'USD'} · ${d.start} — ${d.end} · ${d.adjustment} adjustment · ${d.symbols.length} symbols`;
  $('#chart-grid').innerHTML = d.symbols.map(s => `<article class="chart-panel"><div class="chart-head"><button class="enlarge" data-symbol="${esc(s)}">${esc(s)}</button><span id="price-${esc(s)}" class="chart-value"></span></div><div class="chart-name">${esc(names[s]||s)}</div><canvas data-symbol="${esc(s)}" aria-label="${esc(s)} ${esc(d.timeframe)} historical price chart"></canvas></article>`).join('');
  const limit = d.timeframe === '1Day' ? range : range === 20000 ? 20000 : range === 252 ? 1200 : range * 78;
  const loaded = await Promise.allSettled(d.symbols.map(async s => [s, await api(`bars?dataset=${encodeURIComponent(id)}&symbol=${encodeURIComponent(s)}&limit=${limit}`)]));
  chartRows = {};
  for (const r of loaded) {
    if (r.status !== 'fulfilled') {
      toast(r.reason.message, true);
      continue
    }
    const [s, rows] = r.value;
    chartRows[s] = rows;
    const first = rows[0],
      last = rows.at(-1),
      change = first && last ? last.close / first.close - 1 : 0;
    const head = document.getElementById('price-' + s);
    head.innerHTML = `${num(last?.close)} <span class="chart-change ${change>=0?'up':'down'}">${change>=0?'+':''}${pct(change)}</span>`;
    const canvas = $$('#chart-grid canvas').find(c => c.dataset.symbol === s);
    new Chart(canvas, [{
      points: rows.map(b => ({
        date: d.timeframe === '1Day' ? b.session : b.time.replace('T', ' ').slice(0, 16) + ' UTC',
        value: b.close
      })),
      color: change >= 0 ? '#a6c8b0' : '#d19b96'
    }]);
  }
  $$('.enlarge').forEach(b => b.addEventListener('click', () => {
    const s = b.dataset.symbol;
    $('#dialog-title').textContent = s + ' / ' + (names[s] || 'Price');
    $('#dialog-caption').textContent = `${d.name} · ${d.currency||'USD'} · ${d.source} · closing prices`;
    $('#chart-dialog').showModal();
    plot('#dialog-chart', [{
      points: chartRows[s].map(b => ({
        date: b.time,
        value: b.close
      }))
    }])
  }));
}
$('#chart-dataset').addEventListener('change', () => safely(renderCharts));
$$('[data-range]').forEach(b => b.addEventListener('click', () => {
  range = +b.dataset.range;
  $$('[data-range]').forEach(other => other.classList.toggle('selected', other === b));
  safely(renderCharts)
}));
$('#close-dialog').addEventListener('click', () => $('#chart-dialog').close());

function renderStrategyList() {
  const query = $('#strategy-filter').value.toLowerCase();
  $('#strategy-count').textContent = `${boot.strategies.length} strategy modules`;
  $('#strategy-list').innerHTML = boot.strategies.filter(s => `${s.name} ${s.family}`.toLowerCase().includes(query)).map(s => `<button class="strategy-item ${chosen?.id===s.id?'active':''}" data-id="${s.id}"><span>${esc(s.name)}</span><small>${s.timeframe==='any'?'ANY':s.timeframe.toUpperCase()}</small></button>`).join('');
  $$('.strategy-item').forEach(b => b.addEventListener('click', () => selectStrategy(b.dataset.id)));
}

function field(key, value, prefix, labels) {
  if (key === 'share_mode') return `<label>Share sizing<select id="${prefix}-${key}"><option value="fractional" ${value==='fractional'?'selected':''}>Fractional shares (6 decimals)</option><option value="whole" ${value==='whole'?'selected':''}>Whole shares only</option></select></label>`;
  if (typeof value === 'boolean') return `<label class="checkbox"><input id="${prefix}-${key}" type="checkbox" ${value?'checked':''}>${labels[key]||key}</label>`;
  if (key === 'weekday') return `<label>${labels[key]}<select id="${prefix}-${key}">${['all','weekdays','friday'].map(v=>`<option ${v===value?'selected':''}>${v}</option>`).join('')}</select></label>`;
  return `<label>${labels[key]||key}<input id="${prefix}-${key}" type="number" step="any" value="${value}" required></label>`;
}

function riskForm(id, prefix) {
  $(id).innerHTML = Object.entries(savedRisk).filter(([k]) => k in defaults).map(([k, v]) => field(k, v, prefix, riskLabels)).join('')
}

function setRunDatasets() {
  const previous = $('#run-dataset').value;
  $('#run-dataset').innerHTML = datasetOptions(d => chosen.timeframe === 'any' || d.timeframe === chosen.timeframe);
  if (boot.datasets.some(d => d.id === previous && (chosen.timeframe === 'any' || d.timeframe === chosen.timeframe))) $('#run-dataset').value = previous;
  runDatasetChanged();
}

function runDatasetChanged() {
  const d = boot.datasets.find(d => d.id === $('#run-dataset').value);
  if (!d) {
    $('#holdout-note').textContent = `Import ${chosen.timeframe} bars to run this strategy.`;
    return
  }
  $('#run-symbols').value = d.symbols.join(', ');
  $('#run-start').value = d.start;
  const cutoff = new Date(d.holdout.start + 'T12:00:00Z');
  cutoff.setUTCDate(cutoff.getUTCDate() - 1);
  $('#run-end').value = cutoff.toISOString().slice(0, 10);
  $('#holdout-note').textContent = `Reserved holdout starts ${d.holdout.start}. ${d.holdout.final_run?'Final evaluation already consumed.':'Exploratory runs stay before this date.'} ${d.source==='synthetic'?'These are synthetic examples.':''}`;
}

function selectStrategy(id) {
  chosen = boot.strategies.find(s => s.id === id);
  $('#strategy-family').textContent = chosen.family;
  $('#strategy-name').textContent = chosen.name;
  $('#strategy-description').textContent = chosen.description;
  $('#parameter-fields').innerHTML = Object.entries(chosen.defaults).map(([k, v]) => field(k, v, 'param', paramLabels)).join('') || '<p class="muted">No strategy parameters.</p>';
  riskForm('#run-risk-fields', 'risk');
  setRunDatasets();
  renderStrategyList();
  let grid = {};
  if (chosen.id.includes('regression') || chosen.id === 'insider_trend') grid = {
    lookback: [20, 30, 40, 60, 80],
    min_r2: [.3, .4, .5, .6, .7],
    atr_multiple: [1.5, 2, 2.5, 3, 4]
  };
  else {
    const candidate = Object.entries(chosen.defaults).find(([k, v]) => typeof v === 'number' && k !== 'min_value');
    if (candidate) {
      const [k, v] = candidate;
      grid[k] = Number.isInteger(v) && !['atr_multiple', 'target_multiple'].includes(k) ? [Math.max(2, Math.round(v * .8)), v, Math.round(v * 1.2)] : [Number((v * .8).toFixed(2)), v, Number((v * 1.2).toFixed(2))]
    }
  }
  $('#grid-json').value = JSON.stringify(grid, null, 2);
  gridCount();
}
$('#strategy-filter').addEventListener('input', renderStrategyList);
$('#run-dataset').addEventListener('change', runDatasetChanged);

function spec() {
  const params = {};
  for (const [k, v] of Object.entries(chosen.defaults)) {
    const el = $('#param-' + k);
    params[k] = typeof v === 'boolean' ? el.checked : k === 'weekday' ? el.value : Number(el.value)
  }
  const risk = {};
  for (const k of Object.keys(defaults)) risk[k] = k === 'share_mode' ? $('#risk-' + k).value : Number($('#risk-' + k).value);
  return {
    strategy: chosen.id,
    dataset: $('#run-dataset').value,
    symbols: $('#run-symbols').value.split(',').map(v => v.trim().toUpperCase()).filter(Boolean),
    start: $('#run-start').value,
    end: $('#run-end').value,
    params,
    risk,
    label: $('#run-label').value
  };
}

function gridCount() {
  try {
    const grid = JSON.parse($('#grid-json').value),
      count = Object.values(grid).reduce((a, v) => a * (Array.isArray(v) ? v.length : NaN), 1);
    $('#grid-count').textContent = Number.isFinite(count) ? `${count} variants` : 'Invalid grid'
  } catch {
    $('#grid-count').textContent = 'Invalid JSON'
  }
}
$('#grid-json').addEventListener('input', gridCount);
$('#run-form').addEventListener('submit', e => {
  e.preventDefault();
  safely(async () => {
    await api('run', spec());
    toast('Backtest queued');
    tab('experiments');
    await refreshRuns()
  })
});
$('#run-batch').addEventListener('click', () => safely(async () => {
  const r = await api('batch', {
    spec: spec(),
    grid: JSON.parse($('#grid-json').value)
  });
  toast(`${r.count} variants queued · ${r.batch_id}`);
  tab('experiments');
  await refreshRuns()
}));
$('#walk-forward').addEventListener('click', () => safely(async () => {
  await api('walk-forward', spec());
  toast('Three nonoverlapping evaluation folds queued, with fixed parameters');
  tab('experiments');
  await refreshRuns()
}));
$('#final-holdout').addEventListener('click', () => safely(async () => {
  await api('run', {
    ...spec(),
    split: 'final_holdout'
  });
  toast('One final holdout evaluation queued');
  await refreshBoot();
  tab('experiments')
}));
$('#to-replay').addEventListener('click', () => safely(async () => {
  const r = await api('replay', {
    spec: spec()
  });
  renderReplay(r);
  tab('replay');
  toast('Replay starts with one revealed session')
}));
async function refreshRuns() {
  runs = await api('runs');
  renderRuns();
}

function renderRuns() {
  const status = $('#run-status').value,
    sort = $('#run-sort').value;
  let list = runs.filter(r => !status || r.status === status);
  if (sort !== 'recent') {
    const key = sort === 'return' ? 'net_return' : sort === 'sharpe' ? 'sharpe' : 'max_drawdown';
    list.sort((a, b) => {
      const x = a.metrics?.[key],
        y = b.metrics?.[key];
      if (x == null) return 1;
      if (y == null) return -1;
      return sort === 'drawdown' ? x - y : y - x
    })
  }
  const pending = runs.filter(r => ['queued', 'running'].includes(r.status)).length;
  $('#run-count').textContent = pending || '';
  $('#trials-note').textContent = `${runs.length} recorded trials in the recent history (up to 500 shown). All variants persist in SQLite. Review costs, exposure, drawdowns and separate evaluation windows before ranking candidates.`;
  $('#runs-table').innerHTML = list.map(r => {
    const s = boot.strategies.find(s => s.id === r.spec.strategy),
      d = boot.datasets.find(d => d.id === r.spec.dataset),
      m = r.metrics;
    return `<tr><td>${r.status==='complete'?`<button class="link result-link" data-id="${r.id}">${esc(r.spec.label||s?.name||r.spec.strategy)}</button>`:esc(r.spec.label||s?.name||r.spec.strategy)}<small>${esc(r.batch_id||r.spec.split)} · ${esc(JSON.stringify(r.spec.params))}</small></td><td>${esc(d?.source)} · ${esc(d?.timeframe)}<small>${esc(r.spec.start)} — ${esc(r.spec.end)}</small></td><td class="status-${esc(r.status)}">${esc(r.status)}${r.status==='running'?' '+num(r.progress*100,0)+'%':''}<small>${esc(r.message)}</small></td><td class="number ${m?.net_return>=0?'up':'down'}">${pct(m?.net_return)}</td><td class="number">${pct(m?.max_drawdown)}</td><td class="number">${m?.trades??'—'}</td><td class="number">${num(m?.sharpe)}</td><td>${esc(r.created.slice(0,16).replace('T',' '))}</td></tr>`
  }).join('') || '<tr><td colspan="8" class="muted">No experiments yet.</td></tr>';
  $$('.result-link').forEach(b => b.addEventListener('click', () => safely(() => showResult(b.dataset.id))));
  $('#footer-status').textContent = pending ? `${pending} queued or running · local research` : 'Local workspace · no broker orders';
}
$('#refresh-runs').addEventListener('click', () => safely(refreshRuns));
$('#run-status').addEventListener('change', renderRuns);
$('#run-sort').addEventListener('change', renderRuns);

function metric(label, value) {
  return `<div class="metric"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`;
}
async function showResult(id) {
  const r = await api('results/' + id),
    m = r.metrics,
    d = r.manifest.dataset;
  tab('results');
  $('#result-empty').classList.add('hidden');
  $('#result-content').classList.remove('hidden');
  $('#result-title').textContent = r.spec.label || boot.strategies.find(s => s.id === r.spec.strategy)?.name || r.spec.strategy;
  $('#result-meta').textContent = `${d.source==='synthetic'?'SYNTHETIC EXAMPLE · ':''}${d.name} · ${r.spec.start} — ${r.spec.end} · ${r.spec.symbols.join(', ')} · ${r.spec.split} · Starting balance $${num(r.spec.risk.capital)}`;
  if (d.recent?.latest_bar_end) {
    const ended = new Date(d.recent.latest_bar_end).toLocaleString('en-GB', {timeZone:'America/New_York'});
    $('#result-meta').textContent += ` · Latest ${d.feed.toUpperCase()} bar ended ${ended} New York time`;
  }
  $('#metric-strip').innerHTML = metric('Net return', pct(m.net_return)) + metric('Maximum drawdown', pct(m.max_drawdown)) + metric('Trades', m.trades) + metric('Win rate', pct(m.win_rate)) + metric('Profit factor', num(m.profit_factor)) + metric('Sharpe · daily', num(m.sharpe));
  $('#secondary-metrics').innerHTML = metric('Ending equity · USD', num(m.end_equity)) + metric('Trading costs · USD', num(m.costs)) + metric('Avg holding hours', num(m.average_hold_hours, 1)) + metric('Market beta', num(m.beta)) + metric('Realized alpha / year', pct(m.annualized_alpha)) + metric('Recovered losing trades', pct(m.recovery_fraction));
  if (r.components) $('#secondary-metrics').innerHTML += metric('Peak stock exposure · daily', pct(m.peak_symbol_exposure)) + r.components.map(c => metric(`${c.spec.strategy} · ${num(c.weight*100,0)}% allocation`, `$${num(c.metrics.end_equity)}`)).join('');
  const benchmark = new Map(r.benchmark.map(b => [b.session, b.close])),
    base = r.benchmark[0]?.close,
    equity = r.equity.map(p => ({
      date: p.session,
      value: p.equity
    }));
  const reference = r.equity.flatMap((p, i) => benchmark.has(p.session) && base ? [{
    date: p.session,
    value: r.spec.risk.capital * benchmark.get(p.session) / base,
    index: i
  }] : []);
  plot('#equity-chart', [{
    points: equity,
    color: '#a6c8db'
  }, {
    points: reference,
    color: '#5f636d'
  }]);
  let peak = r.spec.risk.capital;
  plot('#drawdown-chart', [{
    points: r.equity.map(p => {
      peak = Math.max(peak, p.equity);
      return {
        date: p.session,
        value: p.equity / peak - 1
      }
    }),
    color: '#d19b96'
  }], {
    percent: true
  });
  $('#result-warnings').innerHTML = r.warnings.map(w => `<li>${esc(w)}</li>`).join('');
  $('#result-manifest').textContent = `Run ${id} · dataset hash ${d.hash.slice(0,16)} · code ${r.manifest.code_hash.slice(0,16)} · ${m.capm_observations} aligned CAPM observations · rejected entries ${m.rejected_entries} · entry halt ${m.drawdown_halt?'triggered':'not triggered'}. SPY chart is a normalized price reference, not an executable benchmark.`;
  const trades = r.spec.split === 'recent'
    ? r.trades.slice().sort((a, b) => b.net_pnl - a.net_pnl || b.exit_time.localeCompare(a.exit_time))
    : r.trades.slice().reverse();
  $('#trades-table').innerHTML = trades.slice(0, 1000).map(t => `<tr><td>${esc(t.symbol)}</td><td>${t.side}</td><td>${esc(t.entry_time.slice(0,16).replace('T',' '))}<small>${num(t.entry_price)} USD</small></td><td>${esc(t.exit_time.slice(0,16).replace('T',' '))}<small>${num(t.exit_price)} USD</small></td><td class="number ${t.net_pnl>=0?'up':'down'}">${num(t.net_pnl)}</td><td class="number">${num(t.total_cost)}</td><td class="number">${num(t.hold_hours,1)}</td><td class="number">${num(t.recovery_hours,1)}</td><td>${esc(t.exit_reason)}</td></tr>`).join('') || '<tr><td colspan="9">No completed trades.</td></tr>';
  $('#annual-table').innerHTML = (r.annual || []).map(a => `<tr><td>${a.year}</td><td>${pct(a.net_return)}</td><td>${pct(a.max_drawdown)}</td><td>${a.trades}</td><td>${pct(a.win_rate)}</td><td>${num(a.costs)}</td></tr>`).join('') || '<tr><td colspan="6">Re-run this experiment to generate yearly metrics.</td></tr>';
  $('#export-json').href = '/api/results/' + id;
  $('#export-csv').href = '/api/results/' + id + '?format=csv';
  $('#export-json').classList.remove('hidden');
  $('#export-csv').classList.remove('hidden');
}
async function refreshInsiders() {
  events = await api('insiders');
  renderInsiders()
}

function renderInsiders() {
  const query = $('#insider-filter').value.toLowerCase(),
    source = $('#insider-source').value;
  let list = events.filter(e => (source === 'all' || (source === 'synthetic' ? e.source === 'synthetic' : e.source !== 'synthetic')) && `${e.symbol} ${e.insider}`.toLowerCase().includes(query));
  list.sort((a, b) => (b.available_at || b.observed_at).localeCompare(a.available_at || a.observed_at));
  $('#insider-note').textContent = `${list.length} transactions · unknown availability and amended filings are excluded from strategies. Public acceptance time is the earliest assumed availability; production ingestion delay may be longer.`;
  $('#insiders-table').innerHTML = list.slice(0, 1000).map(e => `<tr><td>${esc(e.symbol)}</td><td>${esc(e.insider)}<small>${esc(e.role)}</small></td><td>${esc(e.transaction_date)}${e.snapshot_date?`<small>Snapshot ${esc(e.snapshot_date)}</small>`:""}</td><td>${e.available_at?esc(e.available_at.slice(0,16).replace('T',' '))+' UTC':'Unknown'}<small>Observed ${esc(e.observed_at.slice(0,10))}</small></td><td>${esc(e.code)}${e.amended?' / amended':''}</td><td class="number">${num(e.price*e.shares)}</td><td>${esc(e.source)}</td><td>${e.available_at&&e.code==='P'&&!e.amended?'Yes*':'No'}<small>*Strategy filters still apply</small></td></tr>`).join('') || '<tr><td colspan="8" class="muted">No imported transactions. Fetch SEC filings or import a CSV below.</td></tr>'
}
$('#refresh-insiders').addEventListener('click', () => safely(refreshInsiders));
$('#insider-filter').addEventListener('input', renderInsiders);
$('#insider-source').addEventListener('change', renderInsiders);

function renderData() {
  $('#datasets-table').innerHTML = `<table><thead><tr><th>Dataset</th><th>Source / feed</th><th>Bars</th><th>Symbols</th><th>Dates</th><th>Holdout</th></tr></thead><tbody>${boot.datasets.map(d=>`<tr><td>${esc(d.name)}<small>${esc(d.id)} · ${esc(d.adjustment)}</small></td><td>${esc(d.source)} / ${esc(d.feed||'—')}<small>${esc(d.currency||'USD')}</small></td><td>${esc(d.timeframe)}<small>${d.bars.toLocaleString()} observations</small></td><td>${esc(d.symbols.join(', '))}</td><td>${d.start} — ${d.end}</td><td>From ${d.holdout.start}<small>${d.holdout.final_run?'Final consumed':'Reserved'}</small></td></tr>`).join('')}</tbody></table>`;
}
async function refreshTasks() {
  const tasks = await api('tasks');
  $('#task-list').innerHTML = tasks.slice().reverse().map(t => `<div class="task"><span>${esc(t.kind)}</span><small class="status-${t.status}">${esc(t.status)}</small><small>${esc(t.error||'')}</small>${t.result?`<details><summary>Task output</summary><pre>${esc(JSON.stringify(t.result,null,2))}</pre></details>`:''}</div>`).join('') || '<p class="muted">No background tasks.</p>';
  return tasks;
}

function formValues(form) {
  return Object.fromEntries(new FormData(form).entries())
}

function taskForm(id, endpoint, transform = v => v) {
  $(id).addEventListener('submit', e => {
    e.preventDefault();
    const button = e.currentTarget.querySelector('button');
    safely(async () => {
      button.disabled = true;
      try {
        const data = transform(formValues(e.currentTarget));
        await api(endpoint, data);
        toast('Import queued; progress is shown in Data');
        tab('data');
        await refreshTasks()
      } finally {
        button.disabled = false
      }
    })
  })
}
taskForm('#alpaca-form', 'import/alpaca');
taskForm('#sec-form', 'import/sec', v => ({
  ...v,
  limit: Number(v.limit)
}));
$('#legacy-form').addEventListener('submit', e => {
  e.preventDefault();
  safely(async () => {
    const r = await api('import/legacy', formValues(e.currentTarget));
    toast(`${r.added} legacy snapshots imported; original database unchanged`);
    await refreshInsiders()
  })
});

function csvForm(id, endpoint) {
  $(id).addEventListener('submit', e => {
    e.preventDefault();
    const form = e.currentTarget;
    safely(async () => {
      const values = formValues(form),
        file = values.file;
      if (file.size > 19000000) throw Error('File must be under 19 MB');
      delete values.file;
      const r = await api(endpoint, {
        ...values,
        text: await file.text()
      });
      toast(r.added != null ? `${r.added} insider rows imported` : 'Dataset imported');
      await refreshBoot();
      await refreshInsiders()
    })
  })
}
csvForm('#bar-csv-form', 'import/bars');
csvForm('#insider-csv-form', 'import/insiders');
$('#generate-sample').addEventListener('click', () => safely(async () => {
  await api('sample', {});
  toast('Sample generation queued');
  await refreshTasks()
}));

function download(text, name, mime) {
  const url = URL.createObjectURL(new Blob([text], {
      type: mime
    })),
    link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
$('#sample-csv').addEventListener('click', () => download('symbol,time,open,high,low,close,volume,session,session_end\nSPY,2026-10-05T13:30:00+00:00,570,575,568,574,12345678,2026-10-05,2026-10-05T20:00:00+00:00\n', 'bar-format-example.csv', 'text/csv'));
async function refreshReplay() {
  renderReplay(await api('replay'))
}

function renderReplay(state) {
  $('#replay-empty').classList.toggle('hidden', !!state);
  $('#replay-content').classList.toggle('hidden', !state);
  $('#advance-replay').disabled = !state || state.index >= state.dates.length - 1;
  if (!state) return;
  const r = state.result;
  $('#replay-caption').textContent = `${boot.strategies.find(s=>s.id===state.spec.strategy)?.name} · ${state.dates[state.index]} · ${state.index+1} / ${state.dates.length} sessions revealed · equity ${num(r.metrics.end_equity)} USD`;
  plot('#replay-chart', [{
    points: r.equity.map(p => ({
      date: p.session,
      value: p.equity
    }))
  }]);
  $('#replay-positions').innerHTML = `<h3>Open positions</h3><div class="table-wrap"><table><thead><tr><th>Symbol</th><th>Side</th><th>Quantity</th><th>Entry price</th><th>Entry time</th><th>Stop</th></tr></thead><tbody>${r.open_positions.map(p=>`<tr><td>${esc(p.symbol)}</td><td>${p.side===1?'long':'short'}</td><td>${num(p.quantity,4)}</td><td>${num(p.entry)}</td><td>${esc(p.entry_time)}</td><td>${num(p.stop)}</td></tr>`).join('')||'<tr><td colspan="6">No open positions.</td></tr>'}</tbody></table></div><p class="footnote">${r.trades.length} closed trades. Each step recomputes the revealed prefix deterministically; hidden future sessions cannot supply signals.</p>`
}
$('#advance-replay').addEventListener('click', () => safely(async () => {
  const button = $('#advance-replay');
  button.disabled = true;
  try {
    renderReplay(await api('replay', {
      advance: true
    }))
  } finally {}
}));
async function readConnection(endpoint, data = {}) {
  const r = await api(endpoint, data);
  $('#connection-result').textContent = 'Request queued…';
  let found;
  for (let i = 0; i < 40; i++) {
    await new Promise(resolve => setTimeout(resolve, 1000));
    const tasks = await api('tasks');
    found = tasks.find(t => t.id === r.task_id);
    if (found && ['complete', 'failed'].includes(found.status)) break
  }
  $('#connection-result').textContent = found?.status === 'complete' ? JSON.stringify(found.result, null, 2) : found?.error || 'Still queued; see Data tasks.'
}
$('#paper-account').addEventListener('click', () => safely(() => readConnection('paper-account')));
$('#latest-quotes').addEventListener('click', () => safely(() => readConnection('quotes', {
  symbols: spec().symbols
})));
$('#settings-form').addEventListener('submit', e => {
  e.preventDefault();
  safely(async () => {
    const values = {};
    for (const k of Object.keys(defaults)) {
      values[k] = k === 'share_mode' ? $('#setting-' + k).value : Number($('#setting-' + k).value);
      if (k !== 'share_mode' && !Number.isFinite(values[k])) throw Error('Enter finite values')
    }
    savedRisk = values;
    localStorage.setItem('quantlab-risk', JSON.stringify(values));
    riskForm('#run-risk-fields', 'risk');
    toast('Default execution settings saved')
  })
});
$('#reset-settings').addEventListener('click', () => {
  savedRisk = {
    ...defaults
  };
  localStorage.removeItem('quantlab-risk');
  riskForm('#settings-fields', 'setting');
  riskForm('#run-risk-fields', 'risk');
  toast('Defaults reset')
});
async function poll() {
  if (pollBusy) return;
  pollBusy = true;
  try {
    const tasks = await api('tasks');
    const before = boot.datasets.length;
    await refreshBoot();
    if (activeTab === 'experiments' || runs.some(r => ['running', 'queued'].includes(r.status))) await refreshRuns();
    if (activeTab === 'data') await refreshTasks();
    if (tasks.some(t => t.status === 'complete') && events.length === 0) await refreshInsiders();
    if (boot.datasets.length !== before) toast('Dataset ready')
  } catch {
    /* Explicit actions report errors; background refresh remains quiet. */
  } finally {
    pollBusy = false
  }
}
safely(async () => {
  boot = await api('bootstrap');
  $('#chart-dataset').innerHTML = datasetOptions();
  $('#chart-dataset').value = boot.datasets.find(d => d.timeframe === '1Day')?.id || boot.datasets[0]?.id || '';
  selectStrategy('regression_trend');
  riskForm('#settings-fields', 'setting');
  $('#alpaca-form input[name=end]').value = new Date().toISOString().slice(0, 10);
  await refreshBoot();
  await renderCharts();
  await refreshRuns();
  await refreshInsiders();
  tab(location.hash.slice(1) || 'charts');
  setInterval(poll, 4000)
});

$('#trading212-demo').addEventListener('click', () => safely(() => readConnection('trading212-demo')));
