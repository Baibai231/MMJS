const get = (id) => document.getElementById(id);
let currentChallenge, expiresAt = 0, timer, consumed = false, busy = false, parsedProof;
function message(text) { get('status').textContent = text; get('status').classList.toggle('visible', Boolean(text)); }
function updateButtons() {
  get('verify').disabled = busy || consumed || !currentChallenge || Date.now() >= expiresAt || !parsedProof;
  get('copy').disabled = busy || consumed || !currentChallenge || Date.now() >= expiresAt;
  get('challenge').disabled = busy;
  get('proof').readOnly = busy || consumed;
}
function steps(values) {
  ['step1', 'step2', 'step3'].forEach((id, i) => { get(id).className = 'flow-step ' + values[i]; });
}
function tick() {
  if (consumed) { get('countdown').textContent = '已使用'; clearInterval(timer); return; }
  const remaining = Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000));
  get('countdown').textContent = remaining ? Math.floor(remaining / 60) + ':' + String(remaining % 60).padStart(2, '0') + ' 内有效' : '已过期';
  if (!remaining) { get('challenge-state').textContent = '请重新获取'; clearInterval(timer); }
  updateButtons();
}
get('challenge').onclick = async () => {
  busy = true; updateButtons(); message('');
  try {
    const response = await fetch('/challenge', { method: 'POST' });
    if (!response.ok) throw Error('暂时无法获取挑战，请稍后重试。');
    const value = await response.json();
    if (typeof value.nonce !== 'string' || typeof value.site !== 'string') throw Error('未取得有效挑战，请重试。');
    currentChallenge = value; expiresAt = Date.now() + 300000; consumed = false; parsedProof = undefined;
    get('nonce').textContent = value.nonce; get('challenge-state').textContent = '等待使用';
    get('challenge-state').className = 'badge'; get('challenge').textContent = '重新获取挑战';
    get('proof').value = ''; get('proof-state').className = 'badge'; get('proof-state').textContent = '等待证明'; get('proof-summary').textContent = '粘贴认证证明';
    get('proof-account').textContent = '尚未读取账户'; get('receipt-count').textContent = '等待计算方回执';
    get('proof-details').open = true; get('session-result').hidden = true; get('copy-status').textContent = '';
    steps(['done', 'active', '']); clearInterval(timer); timer = setInterval(tick, 1000); tick();
  } catch (error) { message(error.message || '无法获取挑战，请检查连接。'); }
  finally { busy = false; updateButtons(); }
};
get('copy').onclick = async () => {
  if (!currentChallenge || consumed || Date.now() >= expiresAt) return;
  try { await navigator.clipboard.writeText(currentChallenge.nonce); get('copy-status').textContent = '已复制'; }
  catch { get('copy-status').textContent = '请手动复制挑战码'; }
};
get('proof').addEventListener('input', () => {
  parsedProof = undefined; message('');
  try {
    const value = JSON.parse(get('proof').value);
    if (!value.context || !Array.isArray(value.events) || typeof value.context.account !== 'string') throw Error();
    parsedProof = { events: value.events, context: value.context };
    get('proof-state').textContent = '待网站核验'; get('proof-summary').textContent = '已读取认证证明';
    get('proof-account').textContent = '账户 ' + value.context.account; get('receipt-count').textContent = value.events.length + ' 份计算方回执';
    steps(['done', 'done', 'active']);
  } catch { get('proof-state').textContent = '等待有效证明'; get('proof-summary').textContent = '粘贴认证证明'; get('proof-account').textContent = '尚未读取账户'; get('receipt-count').textContent = '等待计算方回执'; }
  updateButtons();
});
get('verify').onclick = async () => {
  if (!currentChallenge || consumed || !parsedProof || Date.now() >= expiresAt) return;
  busy = true; updateButtons(); message(''); get('verify').textContent = '正在核验…';
  try {
    const response = await fetch('/authenticate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...parsedProof, challenge: currentChallenge.nonce }) });
    const value = await response.json();
    if (!response.ok) throw Error(typeof value.detail === 'string' ? value.detail : '证明未通过核验，请核对挑战与账户。');
    consumed = true; tick(); get('challenge-state').textContent = '已使用'; get('challenge-state').className = 'badge used';
    get('proof-state').textContent = '核验通过'; get('proof-state').className = 'badge used'; get('proof-summary').textContent = '认证证明已核验 · 查看已提交内容';
    get('proof-details').open = false; get('session-account').textContent = value.account; get('session-result').hidden = false;
    steps(['done', 'done', 'done']); get('verify').textContent = '认证已完成';
  } catch (error) { message(error.message || '连接中断，请检查网站连接后重试。'); get('verify').textContent = '验证证明并建立会话'; }
  finally { busy = false; updateButtons(); }
};
