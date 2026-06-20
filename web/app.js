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

/**
 * Giải phóng focus khỏi grid trước khi mở modal.
 * Dừng editing, bỏ chọn ô, reset flag gridHasFocus.
 * Sau đó auto-focus vào phần tử đầu tiên có thể nhập trong modal.
 */
function openModal(modalId, focusSelector) {
  // Dừng editing và giải phóng focus khỏi grid
  try { gridApi?.stopEditing(true); } catch {}
  gridHasFocus = false;
  // Blur element đang focus để trình duyệt không giữ focus trên grid
  if (document.activeElement && document.activeElement !== document.body) {
    document.activeElement.blur();
  }
  // Mở modal
  document.getElementById(modalId).classList.add('open');
  // Auto-focus phần tử chỉ định (hoặc input/textarea đầu tiên trong modal)
  requestAnimationFrame(() => {
    const modal = document.getElementById(modalId);
    const target = focusSelector
      ? modal.querySelector(focusSelector)
      : modal.querySelector('input:not([type=checkbox]), textarea');
    target?.focus();
  });
}


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
    field: 'main_title', headerName: 'Tiêu Đề Chính', width: 240,
    editable: true, sortable: true, filter: true,
  },
  {
    field: 'domain', headerName: 'Domain', width: 160,
    editable: true, sortable: true, filter: true,
  },
  {
    field: 'time_tag', headerName: 'Thời Gian', width: 90,
    editable: true, sortable: true, filter: true,
    cellRenderer: tagCell,
  },
  {
    field: 'title', headerName: 'Tiêu Đề', flex: 1, minWidth: 260,
    editable: true, sortable: true, filter: true,
    cellRenderer: titleCell,
  },
];

// ── Grid init ──────────────────────────────────────────────────
function initGrid() {
  gridApi = agGrid.createGrid(document.getElementById('myGrid'), {
    columnDefs: COL_DEFS,
    rowData: [],
    rowHeight: 20,
    headerHeight: 23,
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
        'cell-selected': p => selectedCells.has(`${p.node.rowIndex}:${p.column.getColId()}`),
        'cell-cut':      p => cutCells.has(`${p.node.rowIndex}:${p.column.getColId()}`)
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
    onCellEditingStopped: params => {
      const allRows = getAllRows();
      const lastRow = allRows[allRows.length - 1];
      if (lastRow && (lastRow.keyword || lastRow.title || lastRow.domain || lastRow.main_title)) {
         gridApi.applyTransaction({ add: [{ stt: allRows.length + 1, keyword: '', title: '', domain: '', time_tag: '', main_title: '' }] });
         updateCount();
      }
      autoSave();
    },
    onCellEditingStarted: params => {
      const colId = params.column.getColId();
      setTimeout(() => {
        const inputEl = document.querySelector('.ag-cell-inline-editing input');
        if (inputEl && inputEl.value) {
          params.node.data[colId] = inputEl.value;
          autoSave();
        }
      }, 50);
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

  // Tự động lưu giá trị mới ngay khi đang gõ (on input) mà không cần nhấn Enter
  document.getElementById('myGrid').addEventListener('input', (event) => {
    const target = event.target;
    if (target && target.tagName === 'INPUT' && target.closest('.ag-cell')) {
      const cellEl = target.closest('.ag-cell');
      const rowEl = target.closest('.ag-row');
      if (cellEl && rowEl) {
        const colId = cellEl.getAttribute('col-id');
        const rowId = rowEl.getAttribute('row-id');
        if (colId && rowId) {
          const rowNode = gridApi.getRowNode(rowId);
          if (rowNode) {
            rowNode.data[colId] = target.value;
            autoSave();
          }
        }
      }
    }
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
const PASTEABLE_COLS = ['keyword', 'main_title', 'domain', 'time_tag', 'title'];

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

  // Giữ nguyên dòng trống để giữ đúng vị trí như Excel gốc
  // Chỉ bỏ các dòng rỗng ở đuôi
  let lines = rawText.split(/\r?\n/);
  while (lines.length > 0 && !lines[lines.length - 1].trim()) lines.pop();
  if (!lines.length) return;

  // Xác định offset cột bắt đầu
  let colOffset = PASTEABLE_COLS.indexOf(anchorField);
  if (colOffset < 0) colOffset = 0;

  // Parse từng dòng clipboard (kể cả dòng trống)
  const parsed = lines.map(line => {
    if (!line.trim()) return null; // dòng trống → giữ ô gốc nguyên vẹn
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
  const isSingleCell = parsed.length === 1 && parsed[0] !== null && Object.keys(parsed[0]).length === 1;
  if (isSingleCell && startAt < allRows.length) {
    Object.assign(allRows[startAt], parsed[0]);
    setGridDataEnsuringEmptyRow(allRows);
    await saveData();
    const fieldName = Object.keys(parsed[0])[0];
    toast(`Paste vào ô [${fieldName}]`, 'info');
    return;
  }

  // Nhiều dòng hoặc nhiều cột
  let filledCount = 0;
  parsed.forEach((updates, i) => {
    const targetIdx = startAt + i;
    if (updates === null) return; // dòng trống trong clipboard → bỏ qua, không ghi đè ô gốc
    if (targetIdx < allRows.length) {
      Object.entries(updates).forEach(([field, val]) => {
        if (val !== '') allRows[targetIdx][field] = val;
      });
    } else {
      // Thêm các hàng mới nếu vượt quá
      // Đảm bảo đủ hàng trống để giữ đúng offset
      while (allRows.length < targetIdx) {
        allRows.push({ stt: allRows.length + 1, keyword: '', title: '', domain: '', time_tag: '', main_title: '' });
      }
      allRows.push({
        stt: allRows.length + 1,
        keyword: '', title: '', domain: '', time_tag: '', main_title: '',
        ...updates,
      });
    }
    filledCount++;
  });

  setGridDataEnsuringEmptyRow(allRows);
  await saveData();
  const startColName = PASTEABLE_COLS[colOffset];
  toast(`✅ Paste ${filledCount} dòng (bắt đầu từ cột "${startColName}", dòng ${startAt + 1})`, 'info');
}

// Track grid focus
const gridEl = document.getElementById('myGrid');
gridEl.addEventListener('mousedown', () => { gridHasFocus = true; });
document.addEventListener('click', e => { gridHasFocus = gridEl.contains(e.target); });

// Global keydown — chặn browser xử lý Delete khi grid đang focus
document.addEventListener('keydown', e => {
  // Debug Ctrl+A
  if (e.key === 'a' && e.ctrlKey) {
    const activeTag = document.activeElement?.tagName;
    const isEditing = !!document.querySelector('.ag-cell-inline-editing');
    console.log('[Ctrl+A Debug]', {
      gridHasFocus,
      activeTag,
      activeElement: document.activeElement,
      isEditing,
      key: e.key,
      ctrlKey: e.ctrlKey,
      shiftKey: e.shiftKey,
    });
  }

  if (!gridHasFocus) return;
  const activeTag = document.activeElement?.tagName;
  if (activeTag === 'INPUT' || activeTag === 'TEXTAREA') return;
  if (document.querySelector('.ag-cell-inline-editing')) return;

  if (e.key === 'Delete' || e.key === 'Backspace') {
    if (selectedCells.size > 0 || (gridApi?.getSelectedNodes().length ?? 0) > 0) {
      e.preventDefault(); clearSelectedCellsContent();
    }
  } else if (e.key === 'Escape') {
    clearCellSelection();
    clearCutBuffer(); // hủy cut khi Esc
  } else if (e.key === 'c' && e.ctrlKey && !e.shiftKey) {
    if (selectedCells.size > 0) { e.preventDefault(); copyCellSelection(); }
  } else if (e.key === 'x' && e.ctrlKey) {
    if (selectedCells.size > 0) { e.preventDefault(); performCut(); }
  } else if (e.key === 'ArrowDown' && e.ctrlKey && e.shiftKey) {
    e.preventDefault(); selectToEndOfColumn('down');
  } else if (e.key === 'ArrowUp' && e.ctrlKey && e.shiftKey) {
    e.preventDefault(); selectToEndOfColumn('up');
  } else if (e.key === 'a' && e.ctrlKey && !e.shiftKey) {
    console.log('[Ctrl+A] → selectAll() called');
    e.preventDefault(); gridApi.selectAll();
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

  // Nếu vừa thực hiện Cut trước đó → xóa ô gốc sau khi paste xong
  if (cutCells.size > 0) clearCutBuffer();
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
  if (msg.type === 'log') {
    appendLog(msg.text, msg.level);
    // Đồng thời hiện log trong mini panel Zhannei (nếu đang chạy)
    if (_zhanneiRunning) appendZhanneiLog(msg.text, msg.level);
  }
  else if (msg.type === 'progress') updateProgress(msg.current, msg.total, msg.keyword, msg.pct);
  else if (msg.type === 'result') applyResult(msg);
  else if (msg.type === 'done') onSearchDone(msg);
  else if (msg.type === 'refresh_data') setGridDataEnsuringEmptyRow(msg.rows);
  // ── Zhannei messages ──
  else if (msg.type === 'zhannei_result') {
    const kwLower = msg.keyword.trim().toLowerCase();
    const isDuplicate = zhanneiResults.some(r => r.keyword.trim().toLowerCase() === kwLower);
    if (isDuplicate) return;

    const item = { keyword: msg.keyword, title: msg.title, domain: msg.domain };
    zhanneiResults.push(item);
    zhanneiAppendRow(item, zhanneiResults.length);
    document.getElementById('zhannei-status').textContent = `Đã tìm: ${zhanneiResults.length} kết quả`;
    document.getElementById('zhannei-add-count').textContent = zhanneiResults.length;
    document.getElementById('btn-zhannei-add').disabled = false;
  }
  else if (msg.type === 'zhannei_sep') zhanneiAppendSep(msg.domain, msg.suffix);
  else if (msg.type === 'zhannei_error') {
    _zhanneiRunning = false;
    document.getElementById('zhannei-status').textContent = '❌ Không kết nối — Hãy bật GOLINK!';
    appendZhanneiLog('❌ Lỗi kết nối — Vui lòng bật GOLINK rồi thử lại!', 'error');
    zhanneiResetUI();
    const tEl = document.getElementById('toast');
    tEl.textContent = '🔒 Vui lòng bật app GOLINK';
    tEl.className = 'show error golink-alert';
    clearTimeout(window._toastTimer);
    window._toastTimer = setTimeout(() => { tEl.className = ''; }, 7000);
  }
  else if (msg.type === 'zhannei_done') {
    _zhanneiRunning = false;
    const stopped = document.getElementById('zhannei-status').textContent.includes('⏹');
    const summary = stopped
      ? `⏹ Dừng — đã thu ${zhanneiResults.length} kết quả`
      : `✅ Xong! ${zhanneiResults.length} kết quả từ ${msg.total_domains} domain`;
    document.getElementById('zhannei-status').textContent = summary;
    appendZhanneiLog(summary, stopped ? 'warning' : 'success');
    zhanneiResetUI();
  }
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

function appendZhanneiLog(text, level = 'info') {
  const el = document.getElementById('zhannei-log-body');
  if (!el) return;
  const now = new Date();
  const ts = now.toTimeString().slice(0, 8); // HH:MM:SS
  const d = document.createElement('div');
  d.className = 'log-' + level;
  d.textContent = `[${ts}] ${text}`;
  el.appendChild(d);
  if (el.children.length > 200) el.removeChild(el.firstChild);
  el.scrollTop = el.scrollHeight;
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
  const updated = { title: msg.title || '', domain: msg.domain || '', time_tag: msg.time_tag || '' };

  // ─ Fast path: dùng row_idx (0-based từ backend) → stt = row_idx+1 → getRowNode O(1)
  if (msg.row_idx >= 0) {
    const node = gridApi.getRowNode(String(msg.row_idx + 1));
    if (node && node.data && node.data.keyword === msg.keyword) {
      gridApi.applyTransaction({ update: [{ ...node.data, ...updated }] });
      return;
    }
  }

  // ─ Fallback: duyệt tất cả node, match theo keyword
  const rowsToUpdate = [];
  gridApi.forEachNode(n => {
    if (n.data && n.data.keyword === msg.keyword) {
      rowsToUpdate.push({ ...n.data, ...updated });
    }
  });
  if (rowsToUpdate.length > 0) {
    gridApi.applyTransaction({ update: rowsToUpdate });
  }
}

async function onSearchDone(msg) {
  setRunning(false);
  document.getElementById('progress-bar-wrap').classList.remove('visible');
  toast(`✅ Xong! Thành công: ${msg.success} | Lỗi: ${msg.error} | Trùng: ${msg.duplicate}`, 'success');
  await loadData();

  // ① Nếu đang ở giai đoạn 1/2 của "Tìm title" (keywords đã hiện trong bảng)
  //    → đặt timer để tự động kick off giai đoạn 2 sau 1.5s
  //    (lưu timer ID vào _phase2Timer để có thể cancel nếu user start job mới)
  if (window._autoRunBaiduAfterKeywords) {
    window._autoRunBaiduAfterKeywords = false;
    const kwCount = getAllRows().filter(r => r.keyword && r.keyword.trim()).length;
    if (kwCount > 0) {
      toast(`🚀 Giai đoạn 2/2 — Bắt đầu tìm title cho ${kwCount} từ khóa trong 1.5s...`, 'info');
      clearTimeout(window._phase2Timer); // huỷ timer cũ nếu có
      window._phase2Timer = setTimeout(() => {
        window._phase2Timer = null;
        runSearch('baidu');
      }, 1500);
    } else {
      toast('⚠️ Không có từ khóa nào trong bảng để tìm title.', 'warning');
    }
  }
}

// ── Search ─────────────────────────────────────────────────────
function setRunning(v) {
  isRunning = v;
  ['btn-baidu','btn-baidu-detail','btn-auto-baidu','btn-hottrend','btn-google','btn-sogou'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.disabled = v;
  });
  document.getElementById('btn-stop').style.display = v ? '' : 'none';
  ['btn-dedup','btn-banned','btn-import','btn-settings','btn-save','btn-clear-results','btn-clear-all','btn-del-row'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.disabled = v;
  });
  // Disable/enable nút trong modal seed khi đang chạy
  const seedRun = document.getElementById('btn-seed-run');
  if (seedRun) seedRun.disabled = v;
  const seedKwOnly = document.getElementById('btn-seed-keywords-only');
  if (seedKwOnly) seedKwOnly.disabled = v;
  // Disable/enable nút trong modal hottrend riêng
  const htKwOnly = document.getElementById('btn-hottrend-keywords-only');
  if (htKwOnly) htKwOnly.disabled = v;
  const htRun = document.getElementById('btn-hottrend-run');
  if (htRun) htRun.disabled = v;
}

async function loadIpInfo() {
  const badge = document.getElementById('ip-badge');
  if (!badge) return;
  try {
    const res = await fetch('/api/ip-info');
    if (res.ok) {
      const data = await res.json();
      badge.textContent = `🌐 IP: ${data.ip} (${data.country})`;
      badge.title = `Địa chỉ IP hiện tại của bạn: ${data.ip}\nQuốc gia: ${data.country}`;
    } else {
      badge.textContent = '🌐 Lỗi lấy IP';
    }
  } catch (e) {
    badge.textContent = '🌐 Lỗi lấy IP';
  }
}

async function runSearch(action) {
  if (isRunning) return;
  // Hủy phase-2 timer nếu có (tránh race condition khi user start job mới)
  if (window._phase2Timer) {
    clearTimeout(window._phase2Timer);
    window._phase2Timer = null;
  }

  // Dừng chỉnh sửa ô và lưu lại trước khi chạy tìm kiếm
  if (gridApi) gridApi.stopEditing();
  try {
    await saveData();
  } catch (e) {
    toast('Lỗi lưu trước khi chạy: ' + e.message, 'error');
    return;
  }

  setRunning(true);
  const headless = document.getElementById('chk-headless').checked;
  try {
    const res = await fetch('/api/search/' + action, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ headless }),
    });
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
document.getElementById('btn-auto-baidu').onclick = async () => {
  // Mở modal seed để nhập từ khóa mới trước khi chạy
  const r = await fetch('/api/seed');
  const d = await r.json();
  document.getElementById('seed-content').value = d.content || '';
  openModal('modal-seed', '#seed-content');
};
// Nút Hottrend trên toolbar → mở modal hottrend riêng
document.getElementById('btn-hottrend').onclick = async () => {
  if (isRunning) { toast('Đang có tác vụ chạy. Nhấn Dừng trước.', 'info'); return; }
  openModal('modal-hottrend', '#hottrend-seed-content');
};
document.getElementById('btn-google').onclick = () => runSearch('google');
document.getElementById('btn-sogou').onclick = () => runSearch('sogou');
document.getElementById('btn-stop').onclick = () => fetch('/api/stop', { method: 'POST' });

// ── Toolbar actions ────────────────────────────────────────────
document.getElementById('btn-dedup').onclick = async () => {
  if (!confirm('Loại bỏ từ khóa trùng lặp?')) return;
  if (gridApi) gridApi.stopEditing();
  try {
    await saveData();
    const res = await fetch('/api/keywords/deduplicate', { method: 'POST' });
    const d = await res.json();
    setGridDataEnsuringEmptyRow(d.rows);
    toast(`Đã xóa ${d.removed} trùng lặp`, 'success');
  } catch (e) {
    toast('Lỗi khi xóa trùng lặp: ' + e.message, 'error');
  }
};

document.getElementById('btn-banned').onclick = () => {
  // Lấy textarea hiện tại (giữ lại nội dung cũ nếu đã nhập)
  openFilterWordsModal();
};

// ── Filter Words Modal ─────────────────────────────────────────
function openFilterWordsModal() {
  updateFilterPreview();
  openModal('modal-filter-words', '#filter-words-content');
}

// Kiểm tra 1 row có khớp điều kiện không (xét tất cả trường)
const FILTER_FIELDS = ['keyword', 'title', 'domain', 'time_tag', 'main_title'];

function rowMatchesTerm(row, term) {
  const lower = term.toLowerCase();
  return FILTER_FIELDS.some(f => String(row[f] || '').toLowerCase().includes(lower));
}

// Tính toán preview: { kept, removed } dựa trên rules hiện tại
function calcFilterResult(rules) {
  const allRows = getAllRows().filter(r => r.keyword || r.title || r.domain || r.main_title);
  if (!rules.length) return { kept: allRows.length, removed: 0, total: allRows.length };

  const mode = document.querySelector('input[name="filter-mode"]:checked')?.value || 'exclude';
  const cleanRules = rules.map(r => r.trim()).filter(Boolean);

  let kept = 0, removed = 0;
  for (const row of allRows) {
    const isMatched = cleanRules.some(term => rowMatchesTerm(row, term));
    if (mode === 'exclude') {
      if (isMatched) {
        removed++;
      } else {
        kept++;
      }
    } else {
      if (isMatched) {
        kept++;
      } else {
        removed++;
      }
    }
  }
  return { kept, removed, total: allRows.length };
}

function parseFilterRules() {
  const raw = document.getElementById('filter-words-content').value;
  return raw.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
}

function updateFilterPreview() {
  const rules = parseFilterRules();
  const preview = document.getElementById('filter-preview');
  if (!rules.length) {
    preview.textContent = '';
    preview.className = 'filter-preview';
    return;
  }
  const { kept, removed, total } = calcFilterResult(rules);
  let html = `<span class="preview-stat">Tổng: <b>${total}</b></span>`;
  html += `<span class="preview-remove">🗑 Xóa: <b>${removed}</b> dòng</span>`;
  html += `<span class="preview-keep">✅ Giữ lại: <b>${kept}</b> dòng</span>`;
  preview.innerHTML = html;
  preview.className = 'filter-preview visible';
}

// Live preview khi gõ hoặc thay đổi chế độ lọc
document.getElementById('filter-words-content').addEventListener('input', updateFilterPreview);
document.querySelectorAll('input[name="filter-mode"]').forEach(radio => {
  radio.addEventListener('change', updateFilterPreview);
});

document.getElementById('btn-filter-words-cancel').onclick = () =>
  document.getElementById('modal-filter-words').classList.remove('open');

document.getElementById('btn-filter-words-apply').onclick = async () => {
  const rules = parseFilterRules();
  if (!rules.length) {
    toast('Chưa nhập điều kiện lọc', 'info');
    return;
  }

  const mode = document.querySelector('input[name="filter-mode"]:checked')?.value || 'exclude';
  const cleanRules = rules.map(r => r.trim()).filter(Boolean);

  const allRows = getAllRows().filter(r => r.keyword || r.title || r.domain || r.main_title);
  const filtered = allRows.filter(row => {
    const isMatched = cleanRules.some(term => rowMatchesTerm(row, term));
    return mode === 'exclude' ? !isMatched : isMatched;
  });

  const removed = allRows.length - filtered.length;
  setGridDataEnsuringEmptyRow(filtered);
  await saveData();
  document.getElementById('modal-filter-words').classList.remove('open');
  toast(`✅ Đã lọc xong — Xóa ${removed} dòng, còn lại ${filtered.length} dòng`, 'success');
};


document.getElementById('btn-save').onclick = async () => {
  try { await saveData(); toast('Đã lưu', 'success'); }
  catch (e) { toast('Lỗi lưu', 'error'); }
};

document.getElementById('btn-clear-all').onclick = () => {
  gridApi.resetColumnState();
  toast('Đã reset cột về mặc định', 'success');
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


// ── Delete rows ──────────────────────────────────────────────────

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
document.getElementById('btn-export').onclick = async () => {
  if (gridApi) gridApi.stopEditing();
  try {
    await saveData();
    window.location.href = '/api/export';
  } catch (e) {
    toast('Lỗi lưu trước khi xuất file: ' + e.message, 'error');
  }
};

// ── Settings ───────────────────────────────────────────────────
document.getElementById('btn-settings').onclick = async () => {
  const r = await fetch('/api/settings');
  const cfg = await r.json();
  document.getElementById('set-profile').value = cfg.profile_path || '';
  document.getElementById('set-chrome').value = cfg.chrome_path || '';
  openModal('modal-settings', '#set-profile');
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


// ── Seed Keywords ──────────────────────────────────────────────
async function saveSeedContent() {
  const content = document.getElementById('seed-content').value;
  await fetch('/api/seed', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  });
  return content;
}

document.getElementById('btn-seed-cancel').onclick = () =>
  document.getElementById('modal-seed').classList.remove('open');

// Chỉ tìm keywords liên quan, KHÔNG tìm title (dùng khi muốn xem/lọc danh sách trước)
document.getElementById('btn-seed-keywords-only').onclick = async () => {
  const content = document.getElementById('seed-content').value.trim();
  if (!content) {
    toast('Vui lòng nhập ít nhất một từ khóa seed', 'info');
    return;
  }
  await saveSeedContent();
  document.getElementById('modal-seed').classList.remove('open');
  toast('Đang tìm keywords liên quan...', 'info');
  window._autoRunBaiduAfterKeywords = false; // chỉ lấy keywords, không tìm title
  runSearch('auto_baidu_keywords_only');
};

// "Tìm title" = 2 giai đoạn
document.getElementById('btn-seed-run').onclick = async () => {
  const content = document.getElementById('seed-content').value.trim();
  if (!content) {
    toast('Vui lòng nhập ít nhất một từ khóa seed', 'info');
    return;
  }
  await saveSeedContent();
  document.getElementById('modal-seed').classList.remove('open');
  toast('🔑 Giai đoạn 1/2 — Đang lấy keywords từ Baidu...', 'info');
  window._autoRunBaiduAfterKeywords = true;
  runSearch('auto_baidu_keywords_only');
};



// ── Modal Hottrend riêng ──────────────────────────────────────────
document.getElementById('btn-hottrend-cancel').onclick = () =>
  document.getElementById('modal-hottrend').classList.remove('open');

// Hàm tiện ích: lấy danh sách seed từ textarea hottrend
function getHottrendSeeds() {
  const content = document.getElementById('hottrend-seed-content').value.trim();
  return content.split(/\r?\n/).map(l => l.trim()).filter(l => l && !l.startsWith('#'));
}

// Nút "🔥 Lấy Keywords" — chỉ lấy hottrend vào bảng, không tìm title
document.getElementById('btn-hottrend-keywords-only').onclick = async () => {
  const seeds = getHottrendSeeds();
  if (!seeds.length) { toast('Vui lòng nhập ít nhất một từ khóa seed', 'info'); return; }
  if (isRunning) { toast('Đang có tác vụ chạy. Nhấn Dừng trước.', 'info'); return; }
  document.getElementById('modal-hottrend').classList.remove('open');
  setRunning(true);
  const headless = document.getElementById('chk-headless').checked;
  try {
    const res = await fetch('/api/search/hottrend_keywords_only', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ seed_keywords: seeds, headless }),
    });
    if (!res.ok) {
      const err = await res.json();
      toast(err.detail || 'Lỗi khởi động Hottrend', 'error');
      setRunning(false);
    } else {
      toast('🔥 Hottrend đang chạy — tìm kiếm ' + seeds.length + ' seed...', 'info');
    }
  } catch (e) {
    toast('Lỗi: ' + e.message, 'error');
    setRunning(false);
  }
};

// Nút "🚀 Lấy + Tìm Title" — lấy hottrend rồi tự động tìm Baidu title
document.getElementById('btn-hottrend-run').onclick = async () => {
  const seeds = getHottrendSeeds();
  if (!seeds.length) { toast('Vui lòng nhập ít nhất một từ khóa seed', 'info'); return; }
  if (isRunning) { toast('Đang có tác vụ chạy. Nhấn Dừng trước.', 'info'); return; }
  document.getElementById('modal-hottrend').classList.remove('open');
  setRunning(true);
  const headless = document.getElementById('chk-headless').checked;
  try {
    const res = await fetch('/api/search/hottrend_and_search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ seed_keywords: seeds, headless }),
    });
    if (!res.ok) {
      const err = await res.json();
      toast(err.detail || 'Lỗi khởi động Hottrend+Search', 'error');
      setRunning(false);
    } else {
      toast('🔥🚀 Hottrend + Tìm Title đang chạy...', 'info');
    }
  } catch (e) {
    toast('Lỗi: ' + e.message, 'error');
    setRunning(false);
  }
};

// ── KW Files (keywords.txt & priority_titles.txt) ──────────
function countKwLines(text) {
  return (text || '').split(/\r?\n/).filter(l => l.trim()).length;
}

function updateKwfCounts() {
  document.getElementById('kwf-count-all').textContent =
    countKwLines(document.getElementById('kwf-all-content').value);
  document.getElementById('kwf-count-priority').textContent =
    countKwLines(document.getElementById('kwf-priority-content').value);
}

// Tab switching
document.querySelectorAll('.kwf-tab').forEach(tab => {
  tab.onclick = () => {
    document.querySelectorAll('.kwf-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.kwf-panel').forEach(p => p.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById(tab.dataset.tab).classList.add('active');
  };
});

// Live count update
document.getElementById('kwf-all-content').addEventListener('input', updateKwfCounts);
document.getElementById('kwf-priority-content').addEventListener('input', updateKwfCounts);

document.getElementById('btn-edit-kwfiles').onclick = async () => {
  try {
    const r = await fetch('/api/getnew-keywords');
    const d = await r.json();
    document.getElementById('kwf-all-content').value = d.keywords || '';
    document.getElementById('kwf-priority-content').value = d.priority_titles || '';
    updateKwfCounts();
    // Reset to first tab
    document.querySelectorAll('.kwf-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.kwf-panel').forEach(p => p.classList.remove('active'));
    document.querySelector('.kwf-tab[data-tab="kwf-all"]').classList.add('active');
    document.getElementById('kwf-all').classList.add('active');
    openModal('modal-kwfiles', '#kwf-all-content');
  } catch (e) {
    toast('Lỗi tải keyword files: ' + e.message, 'error');
  }
};

document.getElementById('btn-kwfiles-cancel').onclick = () =>
  document.getElementById('modal-kwfiles').classList.remove('open');

document.getElementById('btn-kwfiles-save').onclick = async () => {
  try {
    const res = await fetch('/api/getnew-keywords', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        keywords: document.getElementById('kwf-all-content').value,
        priority_titles: document.getElementById('kwf-priority-content').value,
      }),
    });
    if (res.ok) {
      document.getElementById('modal-kwfiles').classList.remove('open');
      toast('Đã lưu keyword files', 'success');
    } else {
      toast('Lỗi lưu keyword files', 'error');
    }
  } catch (e) {
    toast('Lỗi: ' + e.message, 'error');
  }
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
loadIpInfo();

// ── Zhannei ────────────────────────────────────────────────────
let zhanneiResults = [];
let _zhanneiRunning = false;

// ── Domain extractor ──────────────────────────────────────────
// Danh sách 2-level TLD phổ biến (cần lấy 3 phần cuối thay vì 2)
const ZHANNEI_TWO_LEVEL_TLDS = new Set([
  'com.cn','net.cn','org.cn','gov.cn','edu.cn','ac.cn','mil.cn','adm.cn',
  'com.hk','org.hk','net.hk','gov.hk','edu.hk','idv.hk',
  'com.tw','org.tw','net.tw','gov.tw','edu.tw','idv.tw',
  'com.au','org.au','net.au','gov.au','edu.au','id.au',
  'co.uk','org.uk','net.uk','gov.uk','me.uk','ltd.uk','plc.uk',
  'com.br','org.br','net.br','gov.br','edu.br',
  'co.jp','com.jp','or.jp','ne.jp','go.jp','ac.jp','ed.jp',
  'co.in','com.in','net.in','org.in','gov.in','edu.in',
  'co.nz','org.nz','net.nz','govt.nz','edu.nz',
  'co.za','org.za','net.za','gov.za','edu.za',
  'com.sg','org.sg','net.sg','gov.sg','edu.sg','per.sg',
  'com.my','org.my','net.my','gov.my','edu.my',
  'com.vn','org.vn','net.vn','gov.vn','edu.vn','int.vn',
  'com.ph','org.ph','net.ph','gov.ph','edu.ph',
  'com.pk','org.pk','net.pk','gov.pk','edu.pk',
  'com.bd','org.bd','net.bd','gov.bd','edu.bd',
  'com.kh','org.kh','net.kh','gov.kh','edu.kh',
  'com.mm','org.mm','net.mm','gov.mm','edu.mm',
]);

function extractBaseDomain(raw) {
  raw = (raw || '').trim();
  if (!raw) return null;

  // Bỏ protocol (https://, http://, ftp://)
  raw = raw.replace(/^[a-z]+:\/\//i, '');

  // Bỏ path, query, fragment, port
  raw = raw.split('/')[0].split('?')[0].split('#')[0].split(':')[0];

  const hostname = raw.toLowerCase().trim();

  // Phải có ít nhất 1 dấu chấm và ký tự hợp lệ
  if (!hostname || !hostname.includes('.')) return null;
  if (!/^[a-z0-9][a-z0-9.\-]*[a-z0-9]$/i.test(hostname)) return null;

  const parts = hostname.split('.');
  if (parts.length < 2) return null;

  // Kiểm tra 2-level TLD: ví dụ .com.cn → lấy 3 phần cuối
  if (parts.length >= 3) {
    const lastTwo = parts.slice(-2).join('.');
    if (ZHANNEI_TWO_LEVEL_TLDS.has(lastTwo)) {
      return parts.slice(-3).join('.');
    }
  }

  // Mặc định: lấy 2 phần cuối (ví dụ readshare.cn, vipshare.top)
  return parts.slice(-2).join('.');
}

function processZhanneiDomains(text) {
  const lines = text.split(/[\r\n,;\s]+/);
  const seen = new Set();
  const result = [];
  for (const line of lines) {
    const d = extractBaseDomain(line);
    if (d && !seen.has(d)) {
      seen.add(d);
      result.push(d);
    }
  }
  return result;
}

function zhanneiExtractAndUpdate() {
  const ta = document.getElementById('zhannei-domains');
  const raw = ta.value;
  if (!raw.trim()) return;
  const domains = processZhanneiDomains(raw);
  if (!domains.length) return;
  ta.value = domains.join('\n');
  // Hiện badge kết quả
  const badge = document.getElementById('zhannei-extract-badge');
  if (badge) {
    badge.textContent = `✅ ${domains.length} domain`;
    badge.style.display = '';
    clearTimeout(badge._timer);
    badge._timer = setTimeout(() => { badge.style.display = 'none'; }, 4000);
  }
}

document.getElementById('btn-zhannei').onclick = () =>
  openModal('modal-zhannei', '#zhannei-domains');

document.getElementById('btn-zhannei-cancel').onclick = () =>
  document.getElementById('modal-zhannei').classList.remove('open');

// Nút ⚡ Trích xuất
document.getElementById('btn-zhannei-extract').onclick = zhanneiExtractAndUpdate;

// Auto-extract khi paste vào textarea
document.getElementById('zhannei-domains').addEventListener('paste', () => {
  setTimeout(zhanneiExtractAndUpdate, 50); // chờ paste hoàn tất
});



document.getElementById('btn-zhannei-clear').onclick = () => {
  zhanneiResults = [];
  document.getElementById('zhannei-result-body').innerHTML = '';
  document.getElementById('zhannei-status').textContent = 'Chưa chạy';
  document.getElementById('btn-zhannei-add').disabled = true;
  document.getElementById('zhannei-add-count').textContent = '0';
};

document.getElementById('btn-zhannei-clear-log').onclick = () => {
  document.getElementById('zhannei-log-body').innerHTML = '';
};

function getZhanneiSuffix() {
  // Ưu tiên custom input nếu người dùng nhập
  const custom = document.getElementById('zhannei-suffix-custom').value.trim();
  if (custom) return custom;
  // Nếu không có custom → dùng radio đang được chọn (mặc định: app)
  const checked = document.querySelector('input[name="zhannei-suffix"]:checked');
  return checked ? checked.value : 'app';
}

function zhanneiAppendRow(item, index) {
  const tbody = document.getElementById('zhannei-result-body');
  const tr = document.createElement('tr');
  const kw = (item.keyword || '').replace(/</g, '&lt;');
  const tl = (item.title || '').replace(/</g, '&lt;');
  tr.innerHTML = `<td>${index}</td><td title="${kw}">${kw}</td><td title="${tl}">${tl}</td><td>${item.domain || ''}</td>`;
  tbody.appendChild(tr);
  const wrap = document.querySelector('.zhannei-table-wrap');
  if (wrap) wrap.scrollTop = wrap.scrollHeight;
}

function zhanneiAppendSep(domain, suffix) {
  const tbody = document.getElementById('zhannei-result-body');
  const tr = document.createElement('tr');
  tr.className = 'zhannei-sep';
  tr.innerHTML = `<td colspan="4">\uD83C\uDF10 ${domain} + "${suffix}"</td>`;
  tbody.appendChild(tr);
}

// Zhannei: KHONG patch ws.onmessage nua - handler da tich hop vao handleMsg() o tren
// (Tranh bi mat sau khi WS reconnect)


// Helper: reset UI zhannei ve trang thai ban dau
function zhanneiResetUI() {
  clearTimeout(window._zhanneiStopTimer);
  const runBtn = document.getElementById('btn-zhannei-run');
  const stopBtn = document.getElementById('btn-zhannei-stop');
  if (runBtn) { runBtn.disabled = false; runBtn.textContent = '\uD83D\uDD77 B\u1eaft đầu tìm'; }
  if (stopBtn) { stopBtn.style.display = 'none'; stopBtn.disabled = false; stopBtn.textContent = '\u25A0 D\u1eebng'; }
}

// Dung zhannei
document.getElementById('btn-zhannei-stop').onclick = async () => {
  // 1. Reset UI NGAY LAP TUC (optimistic) - khong cho backend
  const kept = zhanneiResults.length;
  zhanneiResetUI();
  document.getElementById('zhannei-status').textContent =
    `\u23F9 D\u1eebng \u2014 gi\u1EEF ${kept} k\u1EBFt qu\u1EA3`;
  // 2. Bao hieu backend dung (background, khong await)
  fetch('/api/stop', { method: 'POST' }).catch(() => {});
};

document.getElementById('btn-zhannei-run').onclick = async () => {
  const domainsRaw = document.getElementById('zhannei-domains').value.trim();
  if (!domainsRaw) { toast('Vui lòng nhập ít nhất một domain', 'info'); return; }
  const domains = domainsRaw.split(/\r?\n/).map(d => d.trim()).filter(Boolean);
  if (!domains.length) { toast('Vui lòng nhập ít nhất một domain', 'info'); return; }
  const suffix = getZhanneiSuffix();
  const maxPages = 99; // Tự động lấy tất cả trang (backend dừng khi hết next page)

  zhanneiResults = [];
  _zhanneiRunning = true;
  document.getElementById('zhannei-result-body').innerHTML = '';
  document.getElementById('zhannei-log-body').innerHTML = '';  // clear log cũ
  document.getElementById('zhannei-add-count').textContent = '0';
  document.getElementById('btn-zhannei-add').disabled = true;
  document.getElementById('zhannei-status').textContent = '\u23F3 Đang tìm...';
  document.getElementById('btn-zhannei-run').disabled = true;
  document.getElementById('btn-zhannei-run').textContent = '\u23F3 \u0110ang ch\u1ea1y...';
  document.getElementById('btn-zhannei-stop').style.display = '';

  try {
    const excludeExisting = document.getElementById('chk-zhannei-exclude-existing').checked;
    const res = await fetch('/api/zhannei', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ domains, suffix, max_pages: maxPages, exclude_existing: excludeExisting }),
    });
    if (!res.ok) {
      const err = await res.json();
      toast(err.detail || 'Lỗi Zhannei', 'error');
      _zhanneiRunning = false;
      document.getElementById('btn-zhannei-run').disabled = false;
      document.getElementById('btn-zhannei-run').textContent = '🕷 Bắt đầu tìm';
      document.getElementById('btn-zhannei-stop').style.display = 'none';
      return;
    }
    // ── Polling fallback (hiện log ngay cả khi WebSocket bị ngắt) ──
    let _pollLogIndex = 0;
    let _pollResultIndex = 0;
    clearInterval(window._zhanneiPollTimer);
    window._zhanneiPollTimer = setInterval(async () => {
      if (!_zhanneiRunning) { clearInterval(window._zhanneiPollTimer); return; }
      try {
        const r = await fetch(`/api/zhannei/status?since=${_pollLogIndex}&results_since=${_pollResultIndex}`);
        if (!r.ok) return;
        const d = await r.json();
        // Cộng dồn log mới vào panel (tránh trùng với WebSocket)
        for (const entry of (d.logs || [])) {
          // Chỉ thêm nếu text chưa có trong log panel (so sánh text)
          const logEl = document.getElementById('zhannei-log-body');
          const already = logEl && [...logEl.children].some(c => c.textContent.includes(entry.text.slice(0, 30)));
          if (!already) appendZhanneiLog(entry.text, entry.level);
        }
        _pollLogIndex = d.log_count;  // cập nhật vị trí
        
        // Nhận kết quả mới từ polling nếu có
        if (d.results && d.results.length > 0) {
          for (const item of d.results) {
            const kwLower = item.keyword.trim().toLowerCase();
            const isDuplicate = zhanneiResults.some(r => r.keyword.trim().toLowerCase() === kwLower);
            if (isDuplicate) continue;

            zhanneiResults.push(item);
            zhanneiAppendRow(item, zhanneiResults.length);
            document.getElementById('zhannei-status').textContent = `Đã tìm: ${zhanneiResults.length} kết quả`;
            document.getElementById('zhannei-add-count').textContent = zhanneiResults.length;
            document.getElementById('btn-zhannei-add').disabled = false;
          }
        }
        if (d.results_count !== undefined) {
          _pollResultIndex = d.results_count;
        }

        // Nếu server báo job done → dừng poll
        if (!d.running && _zhanneiRunning) {
          // Không có zhannei_done qua WS → tự reset
          setTimeout(() => {
            if (_zhanneiRunning) {
              _zhanneiRunning = false;
              const summary = `✅ Xong! ${zhanneiResults.length} kết quả`;
              document.getElementById('zhannei-status').textContent = summary;
              zhanneiResetUI();
            }
          }, 2000);
        }
      } catch (_) { /* bỏ qua lỗi mạng tạm thời */ }
    }, 1500);

  } catch (e) {
    toast('Lỗi: ' + e.message, 'error');
    _zhanneiRunning = false;
    document.getElementById('btn-zhannei-run').disabled = false;
    document.getElementById('btn-zhannei-run').textContent = '🕷 Bắt đầu tìm';
    document.getElementById('btn-zhannei-stop').style.display = 'none';
  }
};

document.getElementById('btn-zhannei-add').onclick = async () => {
  if (!zhanneiResults.length) return;
  const allRows = getAllRows();
  const maxStt = allRows.reduce((m, r) => Math.max(m, r.stt || 0), 0);
  const newRows = zhanneiResults.map((item, i) => ({
    stt: maxStt + i + 1,
    keyword: item.keyword,
    title: item.title,
    domain: item.domain,
    time_tag: '',
    main_title: '',
  }));
  const existing = allRows.filter(r => r.keyword || r.title || r.domain || r.main_title);
  setGridDataEnsuringEmptyRow([...existing, ...newRows]);
  await saveData();
  toast(`\u2705 Đã thêm ${newRows.length} dòng vào bảng`, 'success');
  document.getElementById('modal-zhannei').classList.remove('open');
};

// ── Cell Selection (kiểu Excel) ───────────────────────────────
const selectedCells = new Set();  // Set<"rowIndex:colId">
const cutCells      = new Set();  // cells đang trong trạng thái "cut"
const cutBuffer     = new Map();  // cellKey → value (để clear sau paste)
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

// Xóa nội dung các ô đã chọn (hoặc xóa dòng nếu chọn ô STT hoặc có tick checkbox)
function clearSelectedCellsContent() {
  // Ưu tiên: nếu có dòng được tick checkbox → xóa dòng
  const checkedRows = gridApi?.getSelectedNodes() || [];
  if (checkedRows.length > 0) { deleteSelectedRows(); return; }

  if (selectedCells.size === 0) {
    const fc = gridApi?.getFocusedCell();
    if (!fc) return;
    const col = fc.column.getColId();
    if (col === 'stt') {
      // Focus đang ở cột STT → xóa dòng đó
      const node = gridApi.getDisplayedRowAtIndex(fc.rowIndex);
      if (node?.data?.stt != null) {
        const idToDel = node.data.stt;
        const remaining = getAllRows().filter(r => r.stt !== idToDel);
        setGridDataEnsuringEmptyRow(remaining); autoSave();
        toast('Đã xóa 1 dòng', 'info');
      }
      return;
    }
    const node = gridApi.getDisplayedRowAtIndex(fc.rowIndex);
    if (node?.data) { node.setDataValue(col, ''); autoSave(); }
    return;
  }

  // Tách các ô STT (= xóa dòng) và các ô thường (= xóa nội dung)
  const sttRowIdxSet = new Set();
  let contentCount = 0;
  for (const key of selectedCells) {
    const sep = key.lastIndexOf(':');
    const ri = +key.slice(0, sep), colId = key.slice(sep + 1);
    if (colId === 'stt') {
      sttRowIdxSet.add(ri);
    } else {
      const node = gridApi?.getDisplayedRowAtIndex(ri);
      if (node?.data) { node.setDataValue(colId, ''); contentCount++; }
    }
  }

  if (sttRowIdxSet.size > 0) {
    // Xóa các dòng có ô STT được chọn
    const idsToDelete = new Set(
      [...sttRowIdxSet].map(ri => gridApi.getDisplayedRowAtIndex(ri)?.data?.stt).filter(v => v != null)
    );
    const remaining = getAllRows().filter(r => !idsToDelete.has(r.stt));
    setGridDataEnsuringEmptyRow(remaining);
    clearCellSelection();
    toast(`Đã xóa ${idsToDelete.size} dòng`, 'info');
  } else if (contentCount > 0) {
    toast(`Đã xóa ${contentCount} ô`, 'info');
  }
  autoSave();
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

// Cut: đánh dấu ô bằng viền nét đứt, copy clipboard, xóa gốc SAU KHI paste
function performCut() {
  if (selectedCells.size === 0) return;
  copyCellSelection();        // copy vào clipboard
  cutCells.clear(); cutBuffer.clear();
  for (const key of selectedCells) {
    const sep = key.lastIndexOf(':');
    const ri = +key.slice(0, sep), colId = key.slice(sep + 1);
    const node = gridApi.getDisplayedRowAtIndex(ri);
    cutCells.add(key);
    cutBuffer.set(key, node?.data?.[colId] ?? '');
  }
  // Refresh để hiện class cell-cut
  for (const [ri, cols] of _cellsByRow(cutCells))
    gridApi.refreshCells({ rowNodes: [gridApi.getDisplayedRowAtIndex(+ri)].filter(Boolean), columns: cols, force: true });
  toast(`✂️ Cut ${cutCells.size} ô — Ctrl+V để dán`, 'info');
}

// Xóa ô gốc sau khi paste xong (hoàn tất Cut)
function clearCutBuffer() {
  if (cutCells.size === 0) return;
  // Xóa giá trị ô gốc
  for (const key of cutCells) {
    const sep = key.lastIndexOf(':');
    const ri = +key.slice(0, sep), colId = key.slice(sep + 1);
    const node = gridApi.getDisplayedRowAtIndex(ri);
    if (node?.data) node.setDataValue(colId, '');
  }
  // Lưu danh sách row/col cần refresh TRƯỚC KHI clear
  const rowMap = _cellsByRow(cutCells);
  // Clear TRƯỚC khi refresh → cellClassRules sẽ không thấy cell-cut nữa
  cutCells.clear();
  cutBuffer.clear();
  for (const [ri, cols] of rowMap) {
    const node = gridApi.getDisplayedRowAtIndex(+ri);
    if (node) gridApi.refreshCells({ rowNodes: [node], columns: cols, force: true });
  }
  autoSave();
}

// Helper: gom cells theo row → Map<ri, colId[]>
function _cellsByRow(cellSet) {
  const m = new Map();
  for (const key of cellSet) {
    const sep = key.lastIndexOf(':');
    const ri = key.slice(0, sep), colId = key.slice(sep + 1);
    if (!m.has(ri)) m.set(ri, []);
    m.get(ri).push(colId);
  }
  return m;
}

// Ctrl+Shift+Down / Up: chọn từ ô hiện tại đến cuối / đầu dữ liệu trong cột
function selectToEndOfColumn(direction) {
  const anchor = cellAnchor || (() => {
    const fc = gridApi.getFocusedCell();
    return fc ? { ri: fc.rowIndex, colId: fc.column.getColId() } : null;
  })();
  if (!anchor || anchor.colId === 'stt') return;
  const { ri: startRi, colId } = anchor;
  const total = (gridApi.getDisplayedRowCount() || 1) - 1; // trừ hàng trống cuối
  let endRi = startRi;
  if (direction === 'down') {
    for (let i = startRi + 1; i <= total; i++) {
      const v = gridApi.getDisplayedRowAtIndex(i)?.data?.[colId] ?? '';
      if (v !== '') endRi = i; else break;
    }
    if (endRi === startRi) endRi = total;
  } else {
    for (let i = startRi - 1; i >= 0; i--) {
      const v = gridApi.getDisplayedRowAtIndex(i)?.data?.[colId] ?? '';
      if (v !== '') endRi = i; else break;
    }
    if (endRi === startRi) endRi = 0;
  }
  cellAnchor = anchor;
  refreshCellRange(startRi, colId, endRi, colId);
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

    // Click vào ô dữ liệu (kể cả STT) → cell selection
    rowDragAnchor = null;
    const rowIdx = getRowIdx(e.target);
    const colId  = getColId(e.target);
    if (rowIdx === null || !colId) return;

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
    if (rowIdx === null || !colId) return;

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

  // Click vào HEADER cột → chọn toàn bộ ô trong cột đó
  gridEl.addEventListener('click', e => {
    const headerCell = e.target.closest('.ag-header-cell');
    if (!headerCell) return;
    const colId = headerCell.getAttribute('col-id');
    if (!colId || colId === 'ag-Grid-SelectionColumn') return;

    const total = (gridApi.getDisplayedRowCount() || 1) - 1;
    if (total < 0) return;
    clearCellSelection();
    for (let ri = 0; ri <= total; ri++) {
      const node = gridApi.getDisplayedRowAtIndex(ri);
      if (node?.data?.[colId] !== undefined) selectedCells.add(cellKey(ri, colId));
    }
    // Refresh tất cả ô trong cột
    const nodes = [];
    for (let ri = 0; ri <= total; ri++) {
      const n = gridApi.getDisplayedRowAtIndex(ri);
      if (n) nodes.push(n);
    }
    gridApi.refreshCells({ rowNodes: nodes, columns: [colId], force: true });
    cellAnchor = { ri: 0, colId };
    toast(`Đã chọn cột "${colId}" (${selectedCells.size} ô)`, 'info');
  });
})();


