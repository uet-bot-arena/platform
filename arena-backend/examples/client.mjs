/** Same-origin browser adapter for the main UI. No credentials in localStorage. */
export class ArenaAPI {
  constructor(base = '/api/v1') {
    this.base = base;
    this.csrf = null;
  }

  async request(path, {method = 'GET', body, headers = {}} = {}) {
    if (method !== 'GET' && this.csrf) headers = {...headers, 'X-CSRF-Token': this.csrf};
    if (body && !(body instanceof Blob)) {
      headers = {...headers, 'Content-Type': 'application/json'};
      body = JSON.stringify(body);
    }
    const response = await fetch(this.base + path, {method, headers, body, credentials: 'same-origin'});
    const result = await response.json();
    if (!response.ok) {
      const error = new Error(typeof result.error?.detail === 'string' ? result.error.detail : 'Request validation failed');
      error.code = result.error?.code;
      error.status = response.status;
      error.requestId = result.request_id;
      if (response.status === 401) this.csrf = null;
      throw error;
    }
    if (result.csrf_token) this.csrf = result.csrf_token;
    return result;
  }

  me() { return this.request('/auth/me'); }
  login(username, password) { return this.request('/auth/login', {method: 'POST', body: {username, password}}); }
  async logout() { await this.request('/auth/logout', {method: 'POST'}); this.csrf = null; }
  register(profile) { return this.request('/registrations', {method: 'POST', body: profile}); }
  changePassword(current_password, new_password) { return this.request('/auth/password', {method: 'POST', body: {current_password, new_password}}); }
  dashboard() { return this.request('/dashboard'); }
  upload(file, version, key = crypto.randomUUID()) {
    return this.request('/submissions', {method: 'POST', body: file, headers: {
      'Content-Type': 'application/zip', 'X-Submission-Version': version, 'Idempotency-Key': key,
    }});
  }
  benchmark(submission_id, key = crypto.randomUUID(), turn_ms = 15000) {
    return this.request('/benchmarks', {method: 'POST', body: {submission_id, turn_ms}, headers: {'Idempotency-Key': key}});
  }
  match(red_submission_id, blue_submission_id, turn_ms = 15000, key = crypto.randomUUID()) {
    return this.request('/matches', {method: 'POST', body: {red_submission_id, blue_submission_id, turn_ms, delegate_timeout_claims: true}, headers: {'Idempotency-Key': key}});
  }
  live(jobId, after = 0) {
    // Browser reconnect sends Last-Event-ID. Sequence IDs persist in SQLite.
    return new EventSource(`${this.base}/jobs/${encodeURIComponent(jobId)}/events?after=${after}`);
  }
  competitions() { return this.request('/competitions'); }
  competition(id) { return this.request(`/competitions/${encodeURIComponent(id)}`); }
  fixtures(id, limit = 50, offset = 0) {
    return this.request(`/competitions/${encodeURIComponent(id)}/fixtures?limit=${limit}&offset=${offset}`);
  }
  standings(id) { return this.request(`/competitions/${encodeURIComponent(id)}/standings`); }
  enterCompetition(id, submission_id, benchmark_id, key = crypto.randomUUID()) {
    return this.request(`/competitions/${encodeURIComponent(id)}/entry`, {method: 'POST',
      body: {submission_id, benchmark_id, delegate_timeout_claims: true}, headers: {'Idempotency-Key': key}});
  }
  withdrawCompetition(id, key = crypto.randomUUID()) {
    return this.request(`/competitions/${encodeURIComponent(id)}/withdraw`, {method: 'POST', headers: {'Idempotency-Key': key}});
  }
  createCompetition(title, turn_ms = 15000, max_teams = 16, key = crypto.randomUUID()) {
    return this.request('/admin/competitions', {method: 'POST', body: {title, turn_ms, max_teams}, headers: {'Idempotency-Key': key}});
  }
  competitionAction(id, action, key = crypto.randomUUID()) {
    if (!['open', 'lock', 'start', 'pause'].includes(action)) throw new Error('Unknown competition action');
    return this.request(`/admin/competitions/${encodeURIComponent(id)}/${action}`, {method: 'POST', headers: {'Idempotency-Key': key}});
  }
  retryFixture(id, fixtureId, key = crypto.randomUUID()) {
    return this.request(`/admin/competitions/${encodeURIComponent(id)}/fixtures/${encodeURIComponent(fixtureId)}/retry`,
      {method: 'POST', headers: {'Idempotency-Key': key}});
  }
  liveFixture(id, fixtureId) {
    // SSE IDs are attempt_id:seq. Reconnect resets the cursor after an admin
    // retries an unscored attempt. Patch the board; never reload the page.
    return new EventSource(`${this.base}/competitions/${encodeURIComponent(id)}/fixtures/${encodeURIComponent(fixtureId)}/events`);
  }
}
