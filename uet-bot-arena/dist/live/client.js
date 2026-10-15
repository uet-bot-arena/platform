// Same-origin session only. No password, session or CSRF in browser storage.
const responseBody=response=>(response.headers.get('Content-Type')||'').includes('json')?response.json():response.text();
export class APIError extends Error {
  constructor(response, result) {
    const data=result&&typeof result==='object'&&!Array.isArray(result)?result:{};
    const error = data.error || {};
    const code=error.code||(response.status===503?'server_busy':response.status>=500?'server_error':response.status===429?'rate_limited':undefined);
    const labels = {unauthorized:'Phiên hết hạn hoặc tài khoản/mật khẩu chưa đúng.', forbidden:'Bạn không có quyền thực hiện thao tác này.',
      server_busy:'Máy chủ đang bận vì quá nhiều kết nối. Hãy chờ một chút rồi thử lại.',
      server_error:'Máy chủ đang gặp sự cố. Hãy giữ nguyên biểu mẫu và thử lại sau.',
      password_change_required:'Hãy đổi mật khẩu tạm trước khi tiếp tục.', worker_unavailable:'Worker Docker chưa sẵn sàng. Hãy kiểm tra Docker Desktop.',
      qualification_required:'Cần benchmark đạt đủ 72 mẫu với đúng thời gian và cấu hình của giải.',
      competition_conflict:error.detail, registration_conflict:'Tên đội đã đăng ký hoặc hồ sơ đã được xử lý.',
      account_conflict:'Tên đăng nhập đã tồn tại. Hãy chọn tên khác.', quota_exceeded:'Đã đạt giới hạn hoặc hàng đợi đang đầy. Hãy thử lại sau.',
      rate_limited:'Bạn đã gửi quá nhiều yêu cầu. Hãy thử lại sau.', version_conflict:'Phiên bản đã tồn tại. Hãy dùng một phiên bản mới.'};
    super(labels[code] || (typeof error.detail === 'string' ? error.detail : 'Thông tin chưa hợp lệ. Hãy kiểm tra các trường đã nhập.'));
    this.status = response.status; this.code = code; this.requestId = data.request_id||response.headers.get('X-Request-ID');
    this.retryAfter = response.headers.get('Retry-After');
  }
}

export class ArenaAPI {
  csrf = null;
  async request(path, {method='GET', body, headers={}, signal}={}) {
    if (method !== 'GET' && this.csrf) headers = {...headers, 'X-CSRF-Token':this.csrf};
    if (body !== undefined && !(body instanceof Blob)) {
      headers = {...headers, 'Content-Type':'application/json'}; body = JSON.stringify(body);
    }
    let response;
    try { response = await fetch('/api/v1'+path, {method, body, headers, signal:signal||AbortSignal.timeout(30000), credentials:'same-origin', cache:'no-store'}); }
    catch (error) { if(error.name === 'AbortError') throw error; throw new Error('Không kết nối được máy chủ. Giữ nguyên biểu mẫu và thử lại.'); }
    const result = await responseBody(response);
    if (!response.ok) { if(response.status === 401) this.csrf=null; throw new APIError(response, result); }
    if (result.csrf_token) this.csrf=result.csrf_token;
    return result;
  }
  post(path, body={}, key) { return this.request(path, {method:'POST', body, headers:key?{'Idempotency-Key':key}:{}}); }
  async all(path) {
    const items=[]; let offset=0;
    while(true) {
      const result=await this.request(path+(path.includes('?')?'&':'?')+`limit=100&offset=${offset}`);
      items.push(...result.items); if(result.items.length<100 || (result.total !== undefined && items.length>=result.total)) return items;
      offset+=100;
    }
  }
  async blob(path) {
    const response=await fetch('/api/v1'+path, {credentials:'same-origin', cache:'no-store'});
    if(!response.ok) throw new APIError(response, await responseBody(response));
    return response.blob();
  }
  stream(path) { return new EventSource('/api/v1'+path); }
}

export function saveBlob(name, blob) {
  const url=URL.createObjectURL(blob), a=document.createElement('a');
  a.href=url; a.download=name; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
}
