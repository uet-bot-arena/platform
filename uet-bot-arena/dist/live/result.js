export const resultReasons={control_target:'Đạt mục tiêu kiểm soát',base_capture:'Chiếm căn cứ',resignation:'Đầu hàng',all_pieces_eliminated:'Hết quân',type_eliminated:'Hết một loại quân',no_legal_moves:'Bí nước',stalemate:'Bí nước',timeout:'Hết giờ',illegal_action:'Nước đi không hợp lệ',protocol_error:'Lỗi protocol',stale_action:'Action sai lượt / trạng thái',output_limit:'Vượt giới hạn output',crash:'Bot dừng',threefold_repetition:'Lặp thế 3 lần',ply_limit:'Đạt giới hạn 512 ply',wall_limit:'Đạt giới hạn 20 phút',startup_failure:'Đối thủ lỗi startup',double_startup_failure:'Cả hai bot lỗi startup'};
export const sideName=side=>side==='red'?'Đỏ':side==='blue'?'Xanh':'—';
export function jobErrorExplanation(job){
 if(!job?.error)return '';
 if(job.error.code==='timeout'&&job.profile?.match_ms&&job.error.stage!=='startup')return 'Bot đã dùng hết quỹ thời gian. '+(job.kind==='benchmark'?'Cả 72 mẫu dùng chung một quỹ, không cấp lại ở mỗi mẫu. ':'Quỹ được chia cho cả trận, không cấp lại khi đổi lượt. ')+'Giảm thời gian tìm kiếm và đọc timing.budget_ms mới ở mỗi observation để phân bổ cho các nước tiếp theo.';
 const logs=(job.report?.sandbox_metrics||[]).map(x=>x.stderr||'').join('\n');
 if(!job.ruleset&&job.error.code==='crash'&&job.error.stage==='startup'&&logs.includes('This bot requires SDK v3 / Experimental.'))return 'Bot yêu cầu SDK v3 / Experimental, nhưng tác vụ này chạy luật classic (protocol v2). Chạy benchmark mới và chọn ruleset experimental đã công bố. Nếu chưa có lựa chọn, BTC cần tạo → kiểm tra → công bố ruleset; bật Experimental thôi chưa đủ. Không cần nộp lại ZIP.';
 return '';
}
export function resultLabel(result,name=sideName){
  if(!result)return '';
  const reason=resultReasons[result.reason]||result.reason;
  if(result.winner==='red'||result.winner==='blue')return `${name(result.winner)} thắng · ${reason}`;
  return result.reason==='double_startup_failure'?'Hai bot lỗi startup · Không có đội thắng':`Hòa · ${reason}`;
}
export function policyLabel(config){
  return config?.adjudication?.id==='ottv2-match-v2'
    ?'Thể lệ v2: hòa khi lặp cùng thế cờ và bên tới lượt 3 lần, đạt 512 ply hoặc 20 phút. Hòa không cộng trận thắng. Bot lỗi startup bị xử thua kỹ thuật; cả hai bot lỗi thì mỗi đội nhận một trận thua. Lỗi hạ tầng cần BTC xử lý.'
    :'Thể lệ v1 đã ghim: giới hạn 512 ply/20 phút và mọi lỗi startup cần BTC xử lý, không tự tính hòa hoặc thua. Tạo giải mới để áp dụng thể lệ v2.';
}
