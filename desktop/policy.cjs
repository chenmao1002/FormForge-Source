const path = require('node:path');
const fs = require('node:fs');
const crypto = require('node:crypto');

const PROVIDERS = Object.freeze({
  'jlc-pcb': 'https://www.jlc.com/newOrder/#/pcb/pcbPlaceOrder',
  'jlc-smt': 'https://www.jlc-smt.com/calc-online',
  'jlc-3dp': 'https://www.jlc-3dp.cn/placeOrder',
});
const DOMAINS = ['jlc.com', 'jlc-smt.com', 'jlc-3dp.cn'];
const COMMIT = /提交.{0,8}订单|确认.{0,8}下单|立即下单|确认.{0,8}订单|确认.{0,8}支付|立即支付|去支付|确认付款|立即付款|自动扣款|放行生产|确认生产|submit\s*order|place\s*order|pay\s*now|checkout|release\s*production/i;
const PRIVATE = /密码|验证码|口令|收货|地址|电话|手机|邮箱|联系人|姓名|发票|税号|银行|password|passwd|otp|captcha|verification|phone|mobile|email|address|recipient|invoice|tax|secret|token|authorization/i;

function fail(error, message, status = 400) { const e = new Error(message); Object.assign(e, { error, status }); throw e; }
function vendorUrl(value, testOrigin) {
  try {
    const u = new URL(value);
    if (testOrigin && u.origin === testOrigin) return true;
    return u.protocol === 'https:' && !u.username && !u.password && DOMAINS.some(d => u.hostname === d || u.hostname.endsWith('.' + d));
  } catch { return false; }
}
function safeUrl(value) {
  if(value==='about:blank')return 'about:blank';
  try { const u = new URL(value); return u.origin + u.pathname + (u.hash && !/[?&=]/.test(u.hash) ? u.hash : ''); } catch { return ''; }
}
function sensitive(value) { return PRIVATE.test(value || ''); }
function commit(value) { return COMMIT.test(value || ''); }
function blockedRequest(url, method) {
  if (!['POST','PUT','PATCH','DELETE'].includes(method)) return false;
  let pathname = ''; try { pathname = new URL(url).pathname; } catch { return true; }
  return /\/(?:order|orders|payment|pay|trade)\/(?:create|submit|confirm|pay|checkout|place|release)(?:[A-Z_/-]|$)/i.test(pathname)
    || /\/(?:createOrder|submitOrder|payOrder|confirmPayment|autoPay|releaseProduction)(?:[/?]|$)/i.test(pathname);
}
function checkedFiles(root, files) {
  if (!Array.isArray(files) || files.length < 1 || files.length > 12) fail('invalid_input','请选择 1–12 个制造文件。');
  const realRoot = fs.realpathSync(root);
  return files.map(f => {
    if (!f || typeof f.path !== 'string' || !/^[a-f0-9]{64}$/.test(f.sha256 || '')) fail('invalid_input','上传文件需要路径和 SHA-256。');
    const absolute = path.resolve(f.path), real = fs.realpathSync(absolute);
    const relative = path.relative(realRoot, real);
    if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) fail('invalid_input','仅允许上传 FormForge 工作区内的制造文件。');
    const parts=relative.split(path.sep);
    if (!(parts[0]==='deliverables'||parts[0]==='validation'||(parts[0]==='data'&&parts[1]==='projects'))) fail('invalid_input','只允许上传交付、验证或工程目录内的制造文件。');
    if (parts.some(s => ['.local','.cache','.git','node_modules'].includes(s))) fail('invalid_input','不能上传本机会话、缓存或依赖文件。');
    if (!/\.(zip|csv|xlsx|step|stp|stl)$/i.test(real)) fail('invalid_input','只允许制造资料 ZIP、表格和三维制造文件。');
    const stat = fs.statSync(real);
    if (!stat.isFile() || stat.size > 128 * 1024 * 1024) fail('invalid_input','文件不存在或超过 128 MB。');
    if (crypto.createHash('sha256').update(fs.readFileSync(real)).digest('hex') !== f.sha256) fail('file_mismatch','上传文件已变化，请重新核对摘要。',409);
    return real;
  });
}
module.exports = { PROVIDERS, DOMAINS, COMMIT, PRIVATE, fail, vendorUrl, safeUrl, sensitive, commit, blockedRequest, checkedFiles };
