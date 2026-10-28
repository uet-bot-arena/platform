import {tr,html as l10n,language} from './i18n.js';
// Same-origin session only. No password, session or CSRF in browser storage.
import {snapshotPath,publicRequest,PublicFeed} from './public-client.js';
import {VisibleSource} from './polling.js';
// getRandomValues also works on the isolated HTTP LAN, unlike randomUUID.
export const uuid=()=>Array.from(crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,'0')).join('');
export async function copyText(text){
  try{if(navigator.clipboard?.writeText){await navigator.clipboard.writeText(text);return true;}}catch{}
  // HTTP LAN has no Clipboard API; selection must stay focusable for execCommand.
  const field=document.createElement('textarea'),previous=document.activeElement;
  field.value=text;field.readOnly=true;field.tabIndex=-1;field.setAttribute('aria-hidden','true');
  field.style.cssText='position:fixed;top:0;left:0;opacity:0;pointer-events:none';
  try{document.body.append(field);field.focus({preventScroll:true});field.select();return document.execCommand('copy');}
  catch{return false;}
  finally{field.remove();previous?.focus({preventScroll:true});}
}
const responseBody=response=>(response.headers.get('Content-Type')||'').includes('json')?response.json():response.text();
export class APIError extends Error {
  constructor(response, result) {
    const data=result&&typeof result==='object'&&!Array.isArray(result)?result:{};
    const error = data.error || {};
    const code=error.code||(response.status===503?'server_busy':response.status>=500?'server_error':response.status===429?'rate_limited':undefined);
    const labels = {unauthorized:tr('Phiên hết hạn hoặc tài khoản/mật khẩu chưa đúng.'), forbidden:tr('Bạn không có quyền thực hiện thao tác này.'),
      server_busy:tr('Máy chủ đang bận vì quá nhiều kết nối. Hãy chờ một chút rồi thử lại.'),
      server_error:tr('Máy chủ đang gặp sự cố. Hãy giữ nguyên biểu mẫu và thử lại sau.'),
      password_change_required:tr('Hãy đổi mật khẩu tạm trước khi tiếp tục.'), worker_unavailable:tr('Máy chấm chưa nhận được yêu cầu. Hãy thử lại sau.'),
      qualification_required:error.detail==='Bản thử nhanh chỉ dùng để kiểm và luyện tập; giải nhận bot từ GitHub'?tr(error.detail):tr('Cần bản kiểm tra Đạt đủ 72 mẫu với đúng bộ luật và cấu hình máy chấm của giải.'),
      competition_conflict:error.detail, registration_conflict:tr('Tên đội đã đăng ký hoặc hồ sơ đã được xử lý.'),
      account_conflict:tr('Tên đăng nhập đã tồn tại. Hãy chọn tên khác.'), quota_exceeded:tr('Đã đạt giới hạn hoặc hàng đợi đang đầy. Hãy thử lại sau.'),
      rate_limited:error.detail==='Tối đa 10 bản thử nhanh mỗi giờ'?tr(error.detail):tr('Bạn đã gửi quá nhiều yêu cầu. Hãy thử lại sau.'), version_conflict:error.detail==='Bạn đã nạp đúng mã này'?tr(error.detail):tr('Phiên bản đã tồn tại. Hãy dùng một phiên bản mới.')};
    super(labels[code] || (typeof error.detail === 'string' && language()==='vi' ? error.detail : tr('Thông tin chưa hợp lệ. Hãy kiểm tra các trường đã nhập.')));
    this.status = response.status; this.code = code; this.requestId = data.request_id||response.headers.get('X-Request-ID');
    this.retryAfter = response.headers.get('Retry-After');
  }
}

export class ArenaAPI {
  csrf = null;
  admin = false;
  async request(path, {method='GET', body, headers={}, signal, poll=false}={}) {
    if(method==='GET'&&!this.admin&&(snapshotPath(path)||path.split('?')[0].endsWith('/watch')))return publicRequest(this,path);
    if (poll) headers = {...headers, 'X-Arena-Poll':'1'};
    if (method !== 'GET' && this.csrf) headers = {...headers, 'X-CSRF-Token':this.csrf};
    if (body !== undefined && !(body instanceof Blob)) {
      headers = {...headers, 'Content-Type':'application/json'}; body = JSON.stringify(body);
    }
    let response;
    try { response = await fetch('/api/v1'+path, {method, body, headers, signal:signal||AbortSignal.timeout(30000), credentials:'same-origin', cache:'no-store'}); }
    catch (error) { if(error.name === 'AbortError') throw error; throw new Error(tr('Không kết nối được máy chủ. Giữ nguyên biểu mẫu và thử lại.')); }
    const result = await responseBody(response);
    if (!response.ok) { if(response.status === 401) this.csrf=null; throw new APIError(response, result); }
    if (result.csrf_token) this.csrf=result.csrf_token;
    return result;
  }
  post(path, body={}, key) { return this.request(path, {method:'POST', body, headers:key?{'Idempotency-Key':key}:{}}); }
  async all(path, options={}) {
    if(!this.admin&&snapshotPath(path))return (await publicRequest(this,path)).items;
    const items=[]; let offset=0;
    while(true) {
      const result=await this.request(path+(path.includes('?')?'&':'?')+`limit=100&offset=${offset}`,options);
      items.push(...result.items); if(result.items.length<100 || (result.total !== undefined && items.length>=result.total)) return items;
      offset+=100;
    }
  }
  async blob(path) {
    const response=await fetch('/api/v1'+path, {credentials:'same-origin', cache:'no-store'});
    if(!response.ok) throw new APIError(response, await responseBody(response));
    return response.blob();
  }
  async snapshot(name){
    // Revalidate every time (ETag/304): a CDN may stretch browser max-age, snapshots must stay live.
    const response=await fetch('/snapshots/'+name,{credentials:'omit',cache:'no-cache',signal:AbortSignal.timeout(30000)});
    const result=await responseBody(response);
    if(!response.ok)throw new APIError(response,result);
    return result;
  }
  async featured(){
    if(!this.featuredValue||Date.now()-this.featuredValue.at>=1500){
      this.featuredValue={at:Date.now(),value:this.snapshot('featured.json')};
      this.featuredValue.value.catch(()=>{this.featuredValue=null;});
    }
    return this.featuredValue.value;
  }
  stream(path) { return !this.admin&&path.startsWith('/competitions/')?new PublicFeed(this,path):new VisibleSource('/api/v1'+path); }
}

export function saveBlob(name, blob) {
  const url=URL.createObjectURL(blob), a=document.createElement('a');
  a.href=url; a.download=name; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
}
