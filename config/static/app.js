/* ============================================================
   引擎与密钥配置面板的前端逻辑
   —— 密钥只写不回显：这里永远不接收密钥原文，只提交用户新输入的
      值（提交后立即清空输入框），显示状态一律来自服务端的 has_key。
   ============================================================ */
let CONFIG = null;

const $ = (id) => document.getElementById(id);

async function api(url, body) {
  const res = await fetch(url, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  let data = {};
  try { data = await res.json(); } catch (e) { /* 非 JSON */ }
  if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
  return data;
}

function esc(s) {
  return String(s === undefined || s === null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function flash(msg, kind) {
  const n = $('notice');
  n.className = 'notice ' + (kind || 'ok');
  n.textContent = msg;
  n.classList.remove('hidden');
  window.clearTimeout(flash._t);
  flash._t = window.setTimeout(() => n.classList.add('hidden'), 6000);
}

/* ---------------- 渲染 ---------------- */
function renderStatus() {
  const a = CONFIG.active || {};
  const chips = (CONFIG.providers || []).map(p =>
    '<span class="chip ' + (p.has_key ? 'ok' : 'warn') + '">' + esc(p.label) + ' ' +
    (p.has_key ? '✅ 已配置' : '⚠️ 未配置') + '</span>').join(' ');
  $('statusBar').innerHTML =
    '当前默认：<b>' + esc(a.tag) + '</b> ' + chips +
    ' <span class="dim">· 监听 ' + esc(CONFIG.bind_addr) + ' · 密钥写入：' + (CONFIG.can_write_keys ? '可写' : '已禁止') + '</span>';
}

function renderDefault() {
  const ps = $('defaultProvider'), ms = $('defaultModel');
  ps.innerHTML = (CONFIG.providers || []).map(p =>
    '<option value="' + p.id + '"' + (CONFIG.active.provider === p.id ? ' selected' : '') + '>' + esc(p.label) + '</option>').join('');
  fillModels();
  ps.onchange = fillModels;
}

function fillModels() {
  const pid = $('defaultProvider').value;
  const p = (CONFIG.providers || []).find(x => x.id === pid) || { models: [] };
  $('defaultModel').innerHTML = p.models.map(m =>
    '<option value="' + esc(m) + '"' + (p.model === m ? ' selected' : '') + '>' + esc(m) + '</option>').join('');
}

function renderProviderCard(p) {
  const can = CONFIG.can_write_keys;
  const sec = document.createElement('section');
  sec.className = 'panel provider';
  sec.innerHTML =
    '<h2>' + esc(p.label) +
      ' <span class="chip ' + (p.has_key ? 'ok' : 'warn') + '">' +
      (p.has_key ? '✅ 已配置密钥' : '⚠️ 未配置密钥') + '</span>' +
      (p.key_source ? ' <span class="dim">来源：' + esc(p.key_source) + '</span>' : '') +
    '</h2>' +
    '<p class="hint">' + esc(p.hint || '') + '</p>' +
    '<div class="grid2">' +
      '<label>接口地址<input data-f="endpoint" value="' + esc(p.endpoint) + '"></label>' +
      '<label>认证方式<select data-f="auth_style">' +
        '<option value="bearer"' + (p.auth_style === 'bearer' ? ' selected' : '') + '>Authorization: Bearer &lt;key&gt;（主流）</option>' +
        '<option value="raw"' + (p.auth_style === 'raw' ? ' selected' : '') + '>Authorization: &lt;key&gt;（原始密钥）</option>' +
      '</select></label>' +
      '<label>默认模型<input data-f="model" value="' + esc(p.model) + '" list="mk-' + p.id + '">' +
        '<datalist id="mk-' + p.id + '">' + p.models.map(m => '<option value="' + esc(m) + '">').join('') + '</datalist></label>' +
      '<label class="chk"><input type="checkbox" data-f="json_mode"' + (p.json_mode ? ' checked' : '') + '> 要求 JSON 模式（response_format）</label>' +
    '</div>' +
    '<div class="row">' +
      '<input type="password" data-f="key" autocomplete="new-password" ' + (can ? '' : 'disabled ') +
        'placeholder="' + (p.has_key ? '已配置（留空 = 不修改；输入新值 = 覆盖）' : '请输入 ' + esc(p.key_env)) + '">' +
      '<button class="btn primary" data-act="save"' + (can ? '' : ' disabled') + '>保存</button>' +
      '<button class="btn" data-act="clear"' + (can ? '' : ' disabled') + '>清空密钥</button>' +
      '<button class="btn" data-act="test">测试连通性</button>' +
    '</div>' +
    '<div class="result" data-f="result"></div>';

  const q = (f) => sec.querySelector('[data-f="' + f + '"]');
  const out = () => q('result');

  async function save() {
    out().className = 'result';
    out().textContent = '保存中…';
    const patch = {
      endpoints: {}, models: {}, json_mode: {}, auth_style: {}
    };
    patch.endpoints[p.id] = q('endpoint').value.trim();
    patch.models[p.id] = q('model').value.trim();
    patch.json_mode[p.id] = q('json_mode').checked;
    patch.auth_style[p.id] = q('auth_style').value;
    try {
      await api('/api/settings', patch);
      const newKey = q('key').value;
      let msg = esc(p.label) + ' 参数已保存';
      if (newKey) {
        await api('/api/keys', { keys: { [p.id]: newKey } });
        q('key').value = '';
        msg += '，密钥已更新';
      }
      flash(msg + '（即刻生效）', 'ok');
      await load();
    } catch (e) {
      out().className = 'result bad';
      out().textContent = '失败：' + e.message;
    }
  }

  async function clearKey() {
    if (!window.confirm('确定清空 ' + p.label + ' 的密钥吗？（会写入 .env，可用备份恢复）')) return;
    try {
      await api('/api/keys', { keys: { [p.id]: '' } });
      flash(p.label + ' 密钥已清空', 'ok');
      await load();
    } catch (e) {
      out().className = 'result bad';
      out().textContent = '失败：' + e.message;
    }
  }

  async function test() {
    out().className = 'result';
    out().textContent = '正在测试（最多等 30 秒）…';
    try {
      const r = await api('/api/test', { provider: p.id, model: q('model').value.trim() });
      if (r.ok) {
        out().className = 'result ok';
        out().textContent = '✅ 连通正常：' + r.elapsed_ms + ' ms，模型回复「' + (r.reply || '') + '」';
      } else {
        out().className = 'result bad';
        out().textContent = '❌ ' + (r.error || '失败') + '（端点 ' + r.endpoint + '）';
      }
    } catch (e) {
      out().className = 'result bad';
      out().textContent = '失败：' + e.message;
    }
  }

  sec.querySelector('[data-act="save"]').onclick = save;
  sec.querySelector('[data-act="clear"]').onclick = clearKey;
  sec.querySelector('[data-act="test"]').onclick = test;
  return sec;
}

function renderProviders() {
  const host = $('providers');
  host.innerHTML = '';
  (CONFIG.providers || []).forEach(p => host.appendChild(renderProviderCard(p)));
}

function renderNotice() {
  const n = $('notice');
  $('pSettings').textContent = CONFIG.settings_file || 'settings.json';
  $('pEnv').textContent = CONFIG.env_file || '.env';
  if (CONFIG.can_write_keys) { n.classList.add('hidden'); return; }
  n.className = 'notice warn';
  n.textContent = CONFIG.write_hint || '当前部署不允许在页面上修改密钥。';
  n.classList.remove('hidden');
}

/* ---------------- 加载与保存 ---------------- */
async function load() {
  CONFIG = await api('/api/config');
  renderStatus();
  renderDefault();
  renderProviders();
  renderNotice();
}

$('saveDefault').onclick = async () => {
  const custom = $('defaultModelCustom').value.trim();
  try {
    await api('/api/settings', {
      provider: $('defaultProvider').value,
      model: custom || $('defaultModel').value || ''
    });
    $('defaultModelCustom').value = '';
    flash('默认引擎已保存（即刻生效）', 'ok');
    await load();
  } catch (e) {
    flash('保存失败：' + e.message, 'warn');
  }
};

load().catch(e => {
  $('statusBar').textContent = '加载失败：' + e.message;
});