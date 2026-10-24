import {tr,html as l10n} from './i18n.js';
export const resultReasons={control_target:tr('Đạt mục tiêu kiểm soát'),base_capture:tr('Chiếm căn cứ'),resignation:tr('Đầu hàng'),all_pieces_eliminated:tr('Hết quân'),type_eliminated:tr('Diệt sạch loại quân của đối thủ'),no_legal_moves:tr('Đối thủ bí nước'),stalemate:tr('Bí nước'),timeout:tr('Hết giờ'),illegal_action:tr('Nước đi không hợp lệ'),protocol_error:tr('Sai giao thức'),stale_action:tr('Trả lời sai lượt'),output_limit:tr('In ra quá nhiều dữ liệu'),crash:tr('Bot bị dừng'),threefold_repetition:tr('Lặp thế 3 lần'),ply_limit:tr('Đạt giới hạn số nước'),wall_limit:tr('Quá thời lượng tối đa của trận'),startup_failure:tr('Đối thủ lỗi khi khởi động'),double_startup_failure:tr('Cả hai bot lỗi khi khởi động')};
export const sideName=side=>side==='red'?tr('Đỏ'):side==='blue'?tr('Xanh'):'—';
export function jobErrorExplanation(job){
 if(!job?.error)return '';
 if(job.error.code==='timeout'&&job.profile?.match_ms&&job.error.stage!=='startup')return tr('Bot đã dùng hết quỹ thời gian. ')+(job.kind==='benchmark'?tr('Cả 72 mẫu dùng chung một quỹ, không cấp lại ở mỗi mẫu. '):tr('Quỹ được chia cho cả trận, không cấp lại khi đổi lượt. '))+tr('Giảm thời gian tìm kiếm và đọc timing.budget_ms mới ở mỗi observation để phân bổ cho các nước tiếp theo.');
 if(job.error.code==='superseded')return tr('Đội đã gửi một yêu cầu mới hơn cùng loại trước khi yêu cầu này được chạy, nên hệ thống bỏ yêu cầu cũ để không chiếm hàng đợi.');
 const logs=(job.report?.sandbox_metrics||[]).map(x=>x.stderr||'').join('\n');
 if(!job.ruleset&&job.error.code==='crash'&&job.error.stage==='startup'&&logs.includes('This bot requires SDK v3 / Experimental.'))return tr('Bot yêu cầu SDK v3 / Experimental, nhưng tác vụ này chạy luật classic (protocol v2). Kiểm tra lại và chọn ruleset experimental đã công bố. Nếu chưa có lựa chọn, BTC cần tạo → kiểm tra → công bố ruleset; bật Experimental thôi chưa đủ. Không cần nộp lại ZIP.');
 return '';
}
export function resultLabel(result,name=sideName){
  if(!result)return '';
  const reason=resultReasons[result.reason]||result.reason;
  if(result.winner==='red'||result.winner==='blue')return l10n`${name(result.winner)} thắng · ${reason}`;
  return result.reason==='double_startup_failure'?tr('Cả hai bot lỗi khi khởi động · Không có đội thắng'):l10n`Hòa · ${reason}`;
}
export function rulesLabel(config){
  const d=config?.basic_rules?.definition;
  if(!d)return tr('Luật OTTv2 chuẩn: bàn 9×9, căn cứ Đỏ a1 / Xanh i9. Thắng khi chiếm căn cứ đối phương, diệt sạch một loại quân của đối phương, hoặc đối phương bí nước.');
  const wins=[d.base_capture?tr('chiếm căn cứ đối phương'):'',l10n`diệt sạch ${d.eliminate_types} loại quân của đối phương`,tr('đối phương bí nước')].filter(Boolean).join(', ');
  return l10n`Luật của giải: bàn ${d.rows}×${d.cols}, căn cứ Đỏ ${d.bases.red} / Xanh ${d.bases.blue}, ${d.placement==='auto'?tr('xếp quân tự động đối xứng'):tr('xếp quân do BTC nhập')}. Thắng khi ${wins}.`;
}
export function policyLabel(config){
  const plies=config?.profile?.max_plies||512;
  return config?.adjudication?.id==='ottv2-match-v2'
    ?l10n`Thể lệ: mỗi bot có quỹ giờ riêng cho cả trận, không cộng giây; hết quỹ là thua. Hòa khi cùng thế cờ và bên tới lượt lặp lại 3 lần, khi đạt ${plies} nước hoặc trận kéo dài quá ${Math.round((config?.profile?.job_wall_ms||1200000)/60000)} phút. Bot lỗi khi khởi động bị xử thua kỹ thuật; nếu cả hai bot lỗi, mỗi đội nhận một trận thua. Lỗi hạ tầng không tính kết quả, BTC chạy lại.`
    :tr('Thể lệ v1 đã ghim: giới hạn 512 nước/20 phút và mọi lỗi khởi động cần BTC xử lý, không tự tính hòa hoặc thua. Tạo giải mới để áp dụng thể lệ v2.');
}
