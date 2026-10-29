import test from 'node:test';
import assert from 'node:assert/strict';
import {emailStatusText,emailVerificationPage} from '../dist/live/email-ui.js';
import {publicRoute} from '../dist/live/access.js';

test('email status explains queued, dropped, delivered and verified states',()=>{
  assert.match(emailStatusText({status:'queued',estimated_sent_ms:1}),/lúc khoảng/);
  assert.match(emailStatusText({status:'dropped'}),/30 phút.*gửi mã mới/);
  assert.match(emailStatusText({status:'sent'}),/đã được gửi/);
  assert.match(emailStatusText({verified_ms:1}),/đã được xác minh/);
  assert.equal(publicRoute('verify-email'),true);
  assert.equal(publicRoute('email'),false);
});

test('verification page has the OTP input, Spam hint and escaped address',()=>{
  const html=emailVerificationPage({emailAddress:'<img src=x>',emailRequest:{id:'a',status:'sent',resend_after_seconds:10}});
  assert.match(html,/one-time-code/);
  assert.match(html,/Không thấy thư\? Xem mục Spam/);
  assert.match(html,/disabled/);
  assert.doesNotMatch(html,/<img/);
  assert.doesNotMatch(emailVerificationPage({emailRequest:{verified_ms:1}}),/email-code-form/);
});
