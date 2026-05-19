"use strict";

// ── Theme ──────────────────────────────────────────────────────
let currentTheme = localStorage.getItem('kw-theme') || 'light';

function applyTheme(theme) {
  currentTheme = theme;
  document.documentElement.setAttribute('data-theme', theme);
  const grid = document.getElementById('myGrid');
  if (theme === 'dark') {
    grid.className = 'ag-theme-alpine-dark';
    document.getElementById('btn-theme').textContent = '🌙 Tối';
  } else {
    grid.className = 'ag-theme-alpine';
    document.getElementById('btn-theme').textContent = '☀️ Sáng';
  }
  localStorage.setItem('kw-theme', theme);
}

document.getElementById('btn-theme').onclick = () => {
  applyTheme(currentTheme === 'light' ? 'dark' : 'light');
};

// ── State ──────────────────────────────────────────────────────
let gridApi = null;
let ws = null;
let isRunning = false;
let gridHasFocus = false;


// ── Cell renderers ─────────────────────────────────────────────
function kwCell(p) {
  const s = document.createElement('span');
  s.textContent = p.value || '';
  return s;
}

function titleCell(p) {
  const d = document.createElement('div');
  d.className = 'title-cell';
  const title = p.value || '';
  const kw = (p.data?.keyword || '').toLowerCase();
  if (kw && title.toLowerCase().includes(kw)) {
    const idx = title.toLowerCase().indexOf(kw);
    d.innerHTML = esc(title.slice(0, idx)) + '<mark>' + esc(title.slice(idx, idx + kw.length)) + '</mark>' + esc(title.slice(idx + kw.length));
  } else {
    d.textContent = title;
  }
  return d;
}

function tagCell(p) {
  const v = p.value || '';
  if (!v) return document.createElement('span');
  const s = document.createElement('span');
  s.className = 'tag-pill ' + (v === '刚刚发布' ? 'tag-fresh' : v === '今日发布' ? 'tag-today' : v === '近期发布' ? 'tag-recent' : '');
  s.textContent = v;
  return s;
}

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Column definitions ─────────────────────────────────────────
const COL_DEFS = [
  {
    field: 'stt', headerName: '#', width: 44, minWidth: 44, pinned: 'left',
    editable: false, sortable: true, suppressSizeToFit: true,
    suppressNavigable: true,
    cellStyle: { textAlign: 'center', color: 'var(--text-muted)', fontSize: '11px', padding: '0 2px' },
  },
  {
    field: 'keyword', headerName: 'Từ Khóa', width: 180, pinned: 'left',
    editable: true, sortable: true, filter: true,
    cellRenderer: kwCell,
  },
  {
    field: 'title', headerName: 'Tiêu Đề', flex: 1, minWidth: 260,
    editable: true, sortable: true, filter: true,
    cellRenderer: titleCell,
  },
  {
    field: 'domain', headerName: 'Domain', width: 160,
    editable: true, sortable: true, filter: true,
  },
  {
    field: 'time_tag', headerName: 'Thời Gian', width: 115,
    editable: true, sortable: true, filter: true,
    cellRenderer: tagCell,
  },
  {
    field: 'main_title', headerName: 'Tiêu Đề Chính', width: 240,
    editable: true, sortable: true, filter: true,
  },
];

// ── Grid init ──────────────────────────────────────────────────
function initGrid() {
  gridApi = agGrid.createGrid(document.getElementById('myGrid'), {
    columnDefs: COL_DEFS,
    rowData: [],
    rowHeight: 30,
    headerHeight: 33,
    rowClassRules: {
      'row-error':     p => String(p.data?.title || '').startsWith('Lỗi'),
      'row-duplicate': p => p.data?.title === 'Trùng lặp từ khóa',
    },

    // ─ Selection ─
    rowSelection: {
      mode: 'multiRow',
      checkboxes: true,
      headerCheckbox: true,
      enableClickSelection: false,
      copySelectedRows: true
    },
    // Ghim cột checkbox vào trái, trước cột STT
    selectionColumnDef: {
      pinned: 'left',
      width: 38,
      minWidth: 38,
      maxWidth: 38,
      suppressHeaderMenuButton: true,
    },

    // ─ Excel-like Editing ─
    singleClickEdit: false,                  // double-click để edit, single-click chỉ chọn ô
    stopEditingWhenCellsLoseFocus: true,     // click ra ngoài → dừng edit
    enterNavigatesVerticallyAfterEdit: true, // Enter → xuống dòng kế
    enterNavigatesVertically: true,

    // ─ Clipboard ─
    // (moved to rowSelection.copySelectedRows)

    // ─ Display ─
    animateRows: true,
    enableCellTextSelection: false,
    defaultColDef: {
      resizable: true, suppressMovable: false, enableCellChangeFlash: true,
      cellClassRules: {
        'cell-selected': p => selectedCells.has(`${p.node.rowIndex}:${p.column.getColId()}`)
      }
    },
    getRowId: p => String(p.data.stt),

    // ─ Events ─
    onCellValueChanged: params => {
      const allRows = getAllRows();
      const lastRow = allRows[allRows.length - 1];
      if (lastRow && (lastRow.keyword || lastRow.title || lastRow.domain || lastRow.main_title)) {
         gridApi.applyTransaction({ add: [{ stt: allRows.length + 1, keyword: '', title: '', domain: '', time_tag: '', main_title: '' }] });
      }
      autoSave();
    },
    onGridReady: () => loadData(),
    onCellFocused: () => { gridHasFocus = true; },

    // Phím tắt kiểu Excel
    onCellKeyDown: params => {
      const key   = params.event.key;
      const isEditing = !!document.querySelector('.ag-cell-inline-editing');
      const col   = params.column.getColId();
      const node  = params.node;
      const rowIdx = params.rowIndex;

      // Delete/Backspace khi không đang edit → xóa nội dung các ô đã chọn
      if ((key === 'Delete' || key === 'Backspace') && !isEditing) {
        if (col === 'stt') { params.event.preventDefault(); return; }
        clearSelectedCellsContent();
        params.event.preventDefault();
        return;
      }

      // Ctrl+D → fill down (sao chép giá trị từ dòng trên xuống)
      if (key === 'd' && params.event.ctrlKey && !isEditing) {
        params.event.preventDefault();
        if (rowIdx > 0 && col !== 'stt') {
          const prev = gridApi.getDisplayedRowAtIndex(rowIdx - 1);
          const val  = prev?.data?.[col] ?? '';
          node.setDataValue(col, val);
          autoSave();
          toast(`Fill down: "${val}"`, 'info');
        }
      }
    },
  });
}


// ── Data helpers ───────────────────────────────────────────────
function setGridDataEnsuringEmptyRow(rows) {
  const valid = rows.filter(r => r.keyword || r.title || r.domain || r.main_title);
  valid.push({ keyword: '', title: '', domain: '', time_tag: '', main_title: '' });
  valid.forEach((r, i) => r.stt = i + 1);
  gridApi.setGridOption('rowData', valid);
  updateCount();
}

async function loadData() {
  try {
    const res = await fetch('/api/keywords');
    const data = await res.json();
    setGridDataEnsuringEmptyRow(data.rows || []);
  } catch (e) { toast('Lỗi tải: ' + e.message, 'error'); }
}

function getAllRows() {
  const r = [];
  gridApi.forEachNode(n => r.push({ ...n.data }));
  return r;
}

async function saveData(rowsToSave) {
  let rows = rowsToSave || getAllRows();
  const valid = rows.filter(r => r.keyword || r.title || r.domain || r.main_title);
  valid.forEach((r, i) => r.stt = i + 1);
  const res = await fetch('/api/keywords', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rows: valid }),
  });
  if (!res.ok) throw new Error('Lưu thất bại');
  updateCount();
  return valid;
}

function updateCount() {
  const allRows = getAllRows();
  const count = allRows.filter(r => r.keyword || r.title || r.domain || r.main_title).length;
  const totalRows = allRows.length; // gồm cả dòng trống cuối
  document.getElementById('kw-count').textContent = count + ' từ khóa';
  document.getElementById('row-count').textContent = totalRows + ' dòng';
}

let _saveTimer = null;
function autoSave() {
  clearTimeout(_saveTimer);
  _saveTimer = setTimeout(async () => { try { await saveData(); } catch {} }, 800);
}

// ── Smart Paste ────────────────────────────────────────────────
// Thứ tự cột có thể paste (không tính STT)
const PASTEABLE_COLS = ['keyword', 'title', 'domain', 'time_tag', 'main_title'];

/**
 * Paste thông minh:
 * - anchorField: cột đang focus (paste BẮT ĐẦU từ cột này)
 * - anchorRowIdx: dòng đang focus (paste BẮT ĐẦU từ dòng này)
 * Ví dụ focus ở cột "title" dòng 5:
 *   col0 của clipboard → title, col1 → domain, col2 → time_tag
 *   bắt đầu điền từ dòng 5 xuống
 */
async function handleGridPaste(rawText, anchorField, anchorRowIdx) {
  if (!rawText?.trim()) return;

  const lines = rawText.split(/\r?\n/).filter(l => l.trim());
  if (!lines.length) return;

  // Xác định offset cột bắt đầu
  let colOffset = PASTEABLE_COLS.indexOf(anchorField);
  if (colOffset < 0) colOffset = 0; // mặc định bắt đầu từ keyword

  // Parse từng dòng clipboard
  const parsed = lines.map(line => {
    const cols = line.split('\t');
    const updates = {};
    cols.forEach((val, i) => {
      const fieldIdx = colOffset + i;
      if (fieldIdx < PASTEABLE_COLS.length) {
        updates[PASTEABLE_COLS[fieldIdx]] = val.trim();
      }
    });
    return updates;
  });

  const allRows = getAllRows();
  const startAt = (anchorRowIdx !== null && anchorRowIdx !== undefined && anchorRowIdx < allRows.length)
    ? anchorRowIdx
    : allRows.length;

  // Trường hợp đặc biệt: 1 dòng, 1 cột → điền đúng 1 ô
  const isSingleCell = parsed.length === 1 && Object.keys(parsed[0]).length === 1;
  if (isSingleCell && startAt < allRows.length) {
    Object.assign(allRows[startAt], parsed[0]);
    setGridDataEnsuringEmptyRow(allRows);
    await saveData();
    const fieldName = Object.keys(parsed[0])[0];
    toast(`Paste vào ô [${fieldName}]`, 'info');
    return;
  }

  // Nhiều dòng hoặc nhiều cột
  parsed.forEach((updates, i) => {
    const targetIdx = startAt + i;
    if (targetIdx < allRows.length) {
      Object.entries(updates).forEach(([field, val]) => {
        if (val !== '') allRows[targetIdx][field] = val;
      });
    } else {
      allRows.push({
        stt: allRows.length + 1,
        keyword: '', title: '', domain: '', time_tag: '', main_title: '',
        ...updates,
      });
    }
  });

  setGridDataEnsuringEmptyRow(allRows);
  await saveData();
  const startColName = PASTEABLE_COLS[colOffset];
  toast(`✅ Paste ${parsed.length} dòng (bắt đầu từ cột "${startColName}", dòng ${startAt + 1})`, 'info');
}

// Track grid focus
const gridEl = document.getElementById('myGrid');
gridEl.addEventListener('mousedown', () => { gridHasFocus = true; });
document.addEventListener('click', e => { gridHasFocus = gridEl.contains(e.target); });

// Global keydown — chặn browser xử lý Delete khi grid đang focus
document.addEventListener('keydown', e => {
  if (!gridHasFocus) return;
  const activeTag = document.activeElement?.tagName;
  if (activeTag === 'INPUT' || activeTag === 'TEXTAREA') return;
  if (document.querySelector('.ag-cell-inline-editing')) return;

  if (e.key === 'Delete' || e.key === 'Backspace') {
    if (selectedCells.size > 0) { e.preventDefault(); clearSelectedCellsContent(); }
  } else if (e.key === 'Escape') {
    clearCellSelection();
  } else if (e.key === 'c' && e.ctrlKey) {
    if (selectedCells.size > 0) { e.preventDefault(); copyCellSelection(); }
  }
}, true);

// Global paste intercept — lấy đúng vị trí ô đang focus
document.addEventListener('paste', async e => {
  const activeTag = document.activeElement?.tagName;
  if (activeTag === 'INPUT' || activeTag === 'TEXTAREA') return;
  if (document.querySelector('.ag-cell-inline-editing')) return;
  if (!gridHasFocus) return;

  e.preventDefault();
  const text = (e.clipboardData || window.clipboardData).getData('text');

  // Lấy vị trí ô đang focus
  const focused = gridApi.getFocusedCell();
  const anchorField = focused?.column?.getColId?.() || 'keyword';
  const anchorRowIdx = focused?.rowIndex ?? null;

  await handleGridPaste(text, anchorField, anchorRowIdx);
});

// ── WebSocket ──────────────────────────────────────────────────
function connectWS() {
  ws = new WebSocket('ws://' + location.host + '/ws');
  ws.onopen = () => setDot(true);
  ws.onclose = () => { setDot(false); setTimeout(connectWS, 2000); };
  ws.onmessage = e => handleMsg(JSON.parse(e.data));
}

function setDot(ok) {
  document.getElementById('ws-dot').classList.toggle('connected', ok);
}

function handleMsg(msg) {
  if (msg.type === 'log') appendLog(msg.text, msg.level);
  else if (msg.type === 'progress') updateProgress(msg.current, msg.total, msg.keyword, msg.pct);
  else if (msg.type === 'result') applyResult(msg);
  else if (msg.type === 'done') onSearchDone(msg);
}

function appendLog(text, level = 'info') {
  const el = document.getElementById('log-body');
  const d = document.createElement('div');
  d.className = 'log-' + level;
  d.textContent = text;
  el.appendChild(d);
  if (el.children.length > 500) el.removeChild(el.firstChild);
  if (document.getElementById('auto-scroll').checked) el.scrollTop = el.scrollHeight;
}

function updateProgress(cur, tot, kw, pct) {
  document.getElementById('progress-bar-wrap').classList.add('visible');
  document.getElementById('progress-cur').textContent = cur;
  document.getElementById('progress-tot').textContent = tot;
  document.getElementById('progress-kw').textContent = kw;
  document.getElementById('progress-pct').textContent = pct + '%';
  document.getElementById('progress-fill').style.width = pct + '%';
}

function applyResult(msg) {
  gridApi.forEachNode(n => {
    if (n.data.keyword === msg.keyword) {
      n.setData({ ...n.data, title: msg.title, domain: msg.domain, time_tag: msg.time_tag });
    }
  });
}

async function onSearchDone(msg) {
  setRunning(false);
  document.getElementById('progress-bar-wrap').classList.remove('visible');
  toast(`✅ Xong! Thành công: ${msg.success} | Lỗi: ${msg.error} | Trùng: ${msg.duplicate}`, 'success');
  await loadData();
}

// ── Search ─────────────────────────────────────────────────────
function setRunning(v) {
  isRunning = v;
  ['btn-baidu','btn-baidu-detail','btn-google','btn-sogou'].forEach(id =>
    document.getElementById(id).disabled = v);
  document.getElementById('btn-stop').style.display = v ? '' : 'none';
  ['btn-dedup','btn-banned','btn-import'].forEach(id =>
    document.getElementById(id).disabled = v);
}

async function runSearch(action) {
  if (isRunning) return;
  setRunning(true);
  try {
    const res = await fetch('/api/search/' + action, { method: 'POST' });
    if (!res.ok) {
      const err = await res.json();
      toast(err.detail || 'Lỗi', 'error');
      setRunning(false);
    }
  } catch (e) {
    toast('Lỗi: ' + e.message, 'error');
    setRunning(false);
  }
}

document.getElementById('btn-baidu').onclick = () => runSearch('baidu');
document.getElementById('btn-baidu-detail').onclick = () => runSearch('baidu_detailed');
document.getElementById('btn-google').onclick = () => runSearch('google');
document.getElementById('btn-sogou').onclick = () => runSearch('sogou');
document.getElementById('btn-stop').onclick = () => fetch('/api/stop', { method: 'POST' });

// ── Toolbar actions ────────────────────────────────────────────
document.getElementById('btn-dedup').onclick = async () => {
  if (!confirm('Loại bỏ từ khóa trùng lặp?')) return;
  const res = await fetch('/api/keywords/deduplicate', { method: 'POST' });
  const d = await res.json();
  setGridDataEnsuringEmptyRow(d.rows);
  toast(`Đã xóa ${d.removed} trùng lặp`, 'success');
};

document.getElementById('btn-banned').onclick = async () => {
  if (!confirm('Xóa tất cả từ khóa chứa từ cấm?')) return;
  const res = await fetch('/api/keywords/filter-banned', { method: 'POST' });
  if (!res.ok) { toast('Lỗi', 'error'); return; }
  const d = await res.json();
  setGridDataEnsuringEmptyRow(d.rows);
  toast(`Đã xóa ${d.removed} từ khóa`, 'success');
};

document.getElementById('btn-save').onclick = async () => {
  try { await saveData(); toast('Đã lưu', 'success'); }
  catch (e) { toast('Lỗi lưu', 'error'); }
};

document.getElementById('btn-clear-all').onclick = async () => {
  if (!confirm('Xóa TẤT CẢ dữ liệu? Hành động này không thể hoàn tác!')) return;
  setGridDataEnsuringEmptyRow([]);
  await saveData([]);
  toast('Đã xóa tất cả', 'success');
};

document.getElementById('btn-clear-results').onclick = async () => {
  if (!confirm('Xóa kết quả tìm kiếm? (Giữ nguyên từ khóa)')) return;
  const rows = getAllRows().map(r => ({ ...r, title: '', domain: '', time_tag: '' }));
  setGridDataEnsuringEmptyRow(rows);
  await saveData();
  toast('Đã xóa kết quả', 'success');
};

document.getElementById('btn-clear-log').onclick = () => {
  document.getElementById('log-body').innerHTML = '';
};

// ── Add / Delete rows ──────────────────────────────────────────
document.getElementById('btn-add-row').onclick = addRowFromInput;
document.getElementById('quick-input').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); addRowFromInput(); }
});

// Smart paste trong quick-input: nếu có tab hoặc nhiều dòng → vào bảng
document.getElementById('quick-input').addEventListener('paste', e => {
  const text = (e.clipboardData || window.clipboardData).getData('text');
  const lines = text.split(/\r?\n/).filter(l => l.trim());
  if (lines.length > 1 || lines[0]?.includes('\t')) {
    e.preventDefault();
    document.getElementById('quick-input').value = '';
    // Paste vào cuối bảng, bắt đầu từ cột keyword
    handleGridPaste(text, 'keyword', null);
  }
});

function addRowFromInput() {
  const inp = document.getElementById('quick-input');
  const kw = inp.value.trim();
  if (!kw) return;
  const rows = getAllRows();
  rows.push({ stt: rows.length + 1, keyword: kw, title: '', domain: '', time_tag: '', main_title: '' });
  setGridDataEnsuringEmptyRow(rows);
  inp.value = '';
  inp.focus();
  autoSave();
}

// Hàm xóa dòng đang chọn (dùng chung cho button & phím Delete)
async function deleteSelectedRows() {
  const sel = gridApi.getSelectedNodes();
  if (!sel.length) { toast('Chưa chọn dòng nào — dùng checkbox hoặc Ctrl+Click', 'info'); return; }
  if (sel.length > 1 && !confirm(`Xóa ${sel.length} dòng đã chọn?`)) return;
  const ids = new Set(sel.map(n => n.data.stt));
  const remaining = getAllRows().filter(r => !ids.has(r.stt));
  setGridDataEnsuringEmptyRow(remaining);
  await saveData();
  gridApi.deselectAll();
  toast(`Đã xóa ${sel.length} dòng`, 'success');
}

document.getElementById('btn-del-row').onclick = deleteSelectedRows;

// ── Import / Export ────────────────────────────────────────────
document.getElementById('btn-import').onclick = () => document.getElementById('file-input').click();
document.getElementById('file-input').onchange = async e => {
  const file = e.target.files[0];
  if (!file) return;
  const form = new FormData();
  form.append('file', file);
  toast('Đang import...', 'info');
  const res = await fetch('/api/import', { method: 'POST', body: form });
  if (!res.ok) { toast('Lỗi import', 'error'); return; }
  const d = await res.json();
  setGridDataEnsuringEmptyRow(d.rows);
  toast(`Import ${d.rows.length} từ khóa`, 'success');
  e.target.value = '';
};
document.getElementById('btn-export').onclick = () => { window.location.href = '/api/export'; };

// ── Settings ───────────────────────────────────────────────────
document.getElementById('btn-settings').onclick = async () => {
  const r = await fetch('/api/settings');
  const cfg = await r.json();
  document.getElementById('set-profile').value = cfg.profile_path || '';
  document.getElementById('set-chrome').value = cfg.chrome_path || '';
  document.getElementById('modal-settings').classList.add('open');
};
document.getElementById('btn-settings-cancel').onclick = () =>
  document.getElementById('modal-settings').classList.remove('open');
document.getElementById('btn-settings-save').onclick = async () => {
  const res = await fetch('/api/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      profile_path: document.getElementById('set-profile').value.trim(),
      chrome_path: document.getElementById('set-chrome').value.trim(),
    }),
  });
  if (res.ok) {
    document.getElementById('modal-settings').classList.remove('open');
    toast('Đã lưu cài đặt', 'success');
  }
};

// ── Banned ─────────────────────────────────────────────────────
document.getElementById('btn-edit-banned').onclick = async () => {
  const r = await fetch('/api/banned');
  const d = await r.json();
  document.getElementById('banned-content').value = d.content || '';
  document.getElementById('modal-banned').classList.add('open');
};
document.getElementById('btn-banned-cancel').onclick = () =>
  document.getElementById('modal-banned').classList.remove('open');
document.getElementById('btn-banned-save').onclick = async () => {
  await fetch('/api/banned', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content: document.getElementById('banned-content').value }),
  });
  document.getElementById('modal-banned').classList.remove('open');
  toast('Đã lưu từ cấm', 'success');
};

// Close modals on overlay click
document.querySelectorAll('.modal-overlay').forEach(el =>
  el.addEventListener('click', e => { if (e.target === el) el.classList.remove('open'); }));

// ── Toast ──────────────────────────────────────────────────────
let _toastTimer = null;
function toast(msg, type = 'info') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'show ' + type;
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { el.className = ''; }, 3500);
}

// ── Init ───────────────────────────────────────────────────────
applyTheme(currentTheme);
initGrid();
connectWS();

// ── Cell Selection (kiểu Excel) ───────────────────────────────
const selectedCells = new Set();  // Set<"rowIndex:colId">
let cellAnchor   = null;          // { ri, colId } điểm bắt đầu
let cellLastEnd  = null;          // { ri, colId } điểm cuối hiện tại
let isCellDrag   = false;

function cellKey(ri, colId) { return `${ri}:${colId}`; }

function getRowIdx(el) {
  const row = el?.closest?.('[row-index]');
  if (!row) return null;
  const i = parseInt(row.getAttribute('row-index'));
  return isNaN(i) ? null : i;
}

function getColId(el) {
  const cell = el?.closest?.('[col-id]');
  return cell ? cell.getAttribute('col-id') : null;
}

function getDisplayColIds() {
  return (gridApi?.getColumns() || [])
    .map(c => c.getColId())
    .filter(id => id !== 'ag-Grid-SelectionColumn');
}

// Bỏ chọn tất cả và refresh DOM
function clearCellSelection() {
  if (selectedCells.size === 0) return;
  const byRow = new Map();
  for (const key of selectedCells) {
    const sep = key.lastIndexOf(':');
    const ri = key.slice(0, sep);
    const colId = key.slice(sep + 1);
    if (!byRow.has(ri)) byRow.set(ri, []);
    byRow.get(ri).push(colId);
  }
  selectedCells.clear();
  for (const [ri, cols] of byRow) {
    const node = gridApi?.getDisplayedRowAtIndex(+ri);
    if (node) gridApi.refreshCells({ rowNodes: [node], columns: cols, force: true });
  }
}

// Chọn vùng hình chữ nhật từ (r1,c1) đến (r2,c2)
function refreshCellRange(r1, c1Id, r2, c2Id) {
  const colIds = getDisplayColIds();
  const ci1 = colIds.indexOf(c1Id), ci2 = colIds.indexOf(c2Id);
  if (ci1 < 0 || ci2 < 0) return;
  const rMin = Math.min(r1, r2), rMax = Math.max(r1, r2);
  const cMin = Math.min(ci1, ci2), cMax = Math.max(ci1, ci2);

  selectedCells.clear();
  for (let ri = rMin; ri <= rMax; ri++)
    for (let ci = cMin; ci <= cMax; ci++) {
      const colId = colIds[ci];
      if (colId !== 'stt') selectedCells.add(cellKey(ri, colId));
    }

  const rangeCols = colIds.slice(cMin, cMax + 1).filter(id => id !== 'stt');
  for (let ri = rMin; ri <= rMax; ri++) {
    const node = gridApi?.getDisplayedRowAtIndex(ri);
    if (node) gridApi.refreshCells({ rowNodes: [node], columns: rangeCols, force: true });
  }
}

// Xóa nội dung các ô đã chọn (hoặc xóa dòng nếu có tick checkbox)
function clearSelectedCellsContent() {
  // Ưu tiên: nếu có dòng được tick checkbox → xóa dòng
  const checkedRows = gridApi?.getSelectedNodes() || [];
  if (checkedRows.length > 0) {
    deleteSelectedRows();
    return;
  }
  // Không có checkbox tick → xóa nội dung các ô đã chọn
  if (selectedCells.size === 0) {
    const fc = gridApi?.getFocusedCell();
    if (!fc) return;
    const col = fc.column.getColId();
    if (col === 'stt') return;
    const node = gridApi.getDisplayedRowAtIndex(fc.rowIndex);
    if (node?.data) { node.setDataValue(col, ''); autoSave(); }
    return;
  }
  let count = 0;
  for (const key of selectedCells) {
    const sep = key.lastIndexOf(':');
    const ri = +key.slice(0, sep), colId = key.slice(sep + 1);
    if (colId === 'stt') continue;
    const node = gridApi?.getDisplayedRowAtIndex(ri);
    if (node?.data) { node.setDataValue(colId, ''); count++; }
  }
  if (count > 0) { autoSave(); toast(`Đã xóa ${count} ô`, 'info'); }
}

// Copy các ô đã chọn (tab-separated, Excel-compatible)
function copyCellSelection() {
  const colIds = getDisplayColIds();
  const keys = [...selectedCells];
  const rowSet = new Set(keys.map(k => +k.slice(0, k.lastIndexOf(':'))));
  const colSet = [...new Set(keys.map(k => k.slice(k.lastIndexOf(':') + 1)))]
    .sort((a, b) => colIds.indexOf(a) - colIds.indexOf(b));
  const sortedRows = [...rowSet].sort((a, b) => a - b);

  const lines = sortedRows.map(ri => {
    const node = gridApi.getDisplayedRowAtIndex(ri);
    return colSet.map(colId => String(node?.data?.[colId] ?? '')).join('\t');
  });
  navigator.clipboard.writeText(lines.join('\n'));
  toast(`Đã copy ${selectedCells.size} ô`, 'success');
}

// Mouse handlers
(function setupCellSelection() {
  const gridEl = document.getElementById('myGrid');

  // ─ Row drag-select (chỉ hoạt động khi kéo trên cột checkbox) ─
  let rowDragAnchor = null;
  let isRowDragging = false;

  function isCheckboxArea(el) {
    return !!el?.closest('[col-id="ag-Grid-SelectionColumn"], .ag-checkbox-input-wrapper');
  }

  function selectRowRange(from, to) {
    gridApi.deselectAll();
    const min = Math.min(from, to), max = Math.max(from, to);
    for (let i = min; i <= max; i++)
      gridApi.getDisplayedRowAtIndex(i)?.setSelected(true);
  }

  // MOUSEDOWN ─ phân biệt checkbox vs. ô dữ liệu
  document.addEventListener('mousedown', e => {
    if (e.button !== 0) return;
    if (!gridEl.contains(e.target)) { clearCellSelection(); return; }
    if (e.target.closest('.ag-header')) return;

    if (isCheckboxArea(e.target)) {
      // Bắt đầu row-drag trên cột checkbox
      const rowIdx = getRowIdx(e.target);
      if (rowIdx !== null) {
        rowDragAnchor = rowIdx;
        isRowDragging = false;
        clearCellSelection(); // bỏ chọn ô khi chọn dòng
      }
      return; // để AG Grid xử lý click checkbox
    }

    // Click vào ô dữ liệu → cell selection
    rowDragAnchor = null;
    const rowIdx = getRowIdx(e.target);
    const colId  = getColId(e.target);
    if (rowIdx === null || !colId || colId === 'stt') return;

    gridHasFocus = true;
    gridApi?.stopEditing(true);
    gridApi?.deselectAll(); // bỏ tick checkbox khi chọn ô

    if (e.shiftKey && cellAnchor) {
      refreshCellRange(cellAnchor.ri, cellAnchor.colId, rowIdx, colId);
    } else {
      clearCellSelection();
      cellAnchor  = { ri: rowIdx, colId };
      cellLastEnd = { ri: rowIdx, colId };
      isCellDrag  = false;
    }
  }, { capture: true });

  // MOUSEMOVE
  document.addEventListener('mousemove', e => {
    if (e.buttons !== 1) return;

    const elUnder = document.elementFromPoint(e.clientX, e.clientY);
    if (!elUnder || !gridEl.contains(elUnder)) return;
    if (elUnder.closest('.ag-header')) return;

    const rowIdx = getRowIdx(elUnder);

    // ─ Row drag (khi đang kéo từ cột checkbox) ─
    if (rowDragAnchor !== null) {
      if (rowIdx === null) return;
      if (!isRowDragging && rowIdx === rowDragAnchor) return;
      isRowDragging = true;
      gridEl.classList.add('drag-selecting');
      selectRowRange(rowDragAnchor, rowIdx);
      return;
    }

    // ─ Cell drag ─
    if (!cellAnchor) return;
    if (isCheckboxArea(elUnder)) return;
    const colId = getColId(elUnder);
    if (rowIdx === null || !colId || colId === 'stt') return;

    if (!isCellDrag && rowIdx === cellAnchor.ri && colId === cellAnchor.colId) return;
    isCellDrag = true;
    gridEl.classList.add('drag-selecting');

    if (rowIdx !== cellLastEnd?.ri || colId !== cellLastEnd?.colId) {
      cellLastEnd = { ri: rowIdx, colId };
      refreshCellRange(cellAnchor.ri, cellAnchor.colId, rowIdx, colId);
    }
  });

  // MOUSEUP
  document.addEventListener('mouseup', () => {
    gridEl.classList.remove('drag-selecting');
    if (isRowDragging) {
      isRowDragging = false;
      rowDragAnchor = null;
    } else if (isCellDrag) {
      isCellDrag = false;
    } else if (cellAnchor && selectedCells.size === 0) {
      // Click đơn → chọn 1 ô
      selectedCells.add(cellKey(cellAnchor.ri, cellAnchor.colId));
      const node = gridApi?.getDisplayedRowAtIndex(cellAnchor.ri);
      if (node) gridApi.refreshCells({ rowNodes: [node], columns: [cellAnchor.colId], force: true });
    }
  });
})();


