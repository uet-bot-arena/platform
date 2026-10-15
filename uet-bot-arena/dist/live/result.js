export const resultReasons={control_target:'Đạt mục tiêu kiểm soát',base_capture:'Chiếm căn cứ',resignation:'Đầu hàng',all_pieces_eliminated:'Hết quân',type_eliminated:'Diệt sạch loại quân của đối thủ',no_legal_moves:'Đối thủ bí nước',stalemate:'Bí nước',timeout:'Hết giờ',illegal_action:'Nước đi không hợp lệ',protocol_error:'Sai giao thức',stale_action:'Trả lời sai lượt',output_limit:'In ra quá nhiều dữ liệu',crash:'Bot bị dừng',threefold_repetition:'Lặp thế 3 lần',ply_limit:'Đạt giới hạn số nước',wall_limit:'Quá thời lượng tối đa của trận',startup_failure:'Đối thủ lỗi khi khởi động',double_startup_failure:'Cả hai bot lỗi khi khởi động'};
export const sideName=side=>side==='red'?'Đỏ':side==='blue'?'Xanh':'—';
export function jobErrorExplanation(job){
 if(!job?.error)return '';
 if(job.error.code==='timeout'&&job.profile?.match_ms&&job.error.stage!=='startup')return 'Bot đã dùng hết quỹ thời gian. '+(job.kind==='benchmark'?'Cả 72 mẫu dùng chung một quỹ, không cấp lại ở mỗi mẫu. ':'Quỹ được chia cho cả trận, không cấp lại khi đổi lượt. ')+'Giảm thời gian tìm kiếm và đọc timing.budget_ms mới ở mỗi observation để phân bổ cho các nước tiếp theo.';
 if(job.error.code==='superseded')return 'Đội đã gửi một yêu cầu mới hơn cùng loại trước khi yêu cầu này được chạy, nên hệ thống bỏ yêu cầu cũ để không chiếm hàng đợi.';
 const logs=(job.report?.sandbox_metrics||[]).map(x=>x.stderr||'').join('\n');
 if(!job.ruleset&&job.error.code==='crash'&&job.error.stage==='startup'&&logs.includes('This bot requires SDK v3 / Experimental.'))return 'Bot yêu cầu SDK v3 / Experimental, nhưng tác vụ này chạy luật classic (protocol v2). Chạy benchmark mới và chọn ruleset experimental đã công bố. Nếu chưa có lựa chọn, BTC cần tạo → kiểm tra → công bố ruleset; bật Experimental thôi chưa đủ. Không cần nộp lại ZIP.';
 return '';
}
export function resultLabel(result,name=sideName){
  if(!result)return '';
  const reason=resultReasons[result.reason]||result.reason;
  if(result.winner==='red'||result.winner==='blue')return `${name(result.winner)} thắng · ${reason}`;
  return result.reason==='double_startup_failure'?'Cả hai bot lỗi khi khởi động · Không có đội thắng':`Hòa · ${reason}`;
}
export function rulesLabel(config){
  const d=config?.basic_rules?.definition;
  if(!d)return 'Luật OTTv2 chuẩn: bàn 9×9, căn cứ Đỏ a1 / Xanh i9. Thắng khi chiếm căn cứ đối phương, diệt sạch một loại quân của đối phương, hoặc đối phương bí nước.';
  const wins=[d.base_capture?'chiếm căn cứ đối phương':'',`diệt sạch ${d.eliminate_types} loại quân của đối phương`,'đối phương bí nước'].filter(Boolean).join(', ');
  return `Luật của giải: bàn ${d.rows}×${d.cols}, căn cứ Đỏ ${d.bases.red} / Xanh ${d.bases.blue}, ${d.placement==='auto'?'xếp quân tự động đối xứng':'xếp quân do BTC nhập'}. Thắng khi ${wins}.`;
}
export function policyLabel(config){
  const plies=config?.profile?.max_plies||512;
  return config?.adjudication?.id==='ottv2-match-v2'
    ?`Thể lệ: mỗi bot có quỹ giờ riêng cho cả trận, không cộng giây; hết quỹ là thua. Hòa khi cùng thế cờ và bên tới lượt lặp lại 3 lần, khi đạt ${plies} nước hoặc trận kéo dài quá ${Math.round((config?.profile?.job_wall_ms||1200000)/60000)} phút. Bot lỗi khi khởi động bị xử thua kỹ thuật; nếu cả hai bot lỗi, mỗi đội nhận một trận thua. Lỗi hạ tầng không tính kết quả, BTC chạy lại.`
    :'Thể lệ v1 đã ghim: giới hạn 512 nước/20 phút và mọi lỗi khởi động cần BTC xử lý, không tự tính hòa hoặc thua. Tạo giải mới để áp dụng thể lệ v2.';
}
