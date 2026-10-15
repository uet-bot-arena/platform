import {field,select,note} from './view.js';

export function tournamentOptions(){
  return `<label class="field"><span>Thể thức</span>${select('format',[['double_round_robin','Vòng tròn 2 lượt (tối đa 32 đội)'],['swiss','Swiss (nhiều đội) · mỗi cặp 2 ván đổi màu']])}</label>${field('Số vòng Swiss (để trống: hệ thống tự tính theo số đội)','rounds','min="1" max="64"','number')}<label class="check-field"><input type="checkbox" name="custom-basic">Tùy chỉnh luật bàn cờ (kích thước, căn cứ, điều kiện thắng)</label><div class="form-grid">${field('Số hàng (7–15)','board-rows','min="7" max="15" required','number','9')}${field('Số cột (7–15)','board-cols','min="7" max="15" required','number','9')}${field('Căn cứ Đỏ','base-red','pattern="[a-o]([1-9]|1[0-5])" required','text','a1')}${field('Căn cứ Xanh (để trống: góc đối diện)','base-blue','','text','')}<label class="field"><span>Thắng khi diệt sạch</span>${select('eliminate-types',[['1','1 loại quân'],['2','2 loại quân']])}</label></div><label class="check-field"><input name="base-capture" type="checkbox" checked>Thắng khi chiếm căn cứ đối phương</label><label class="field"><span>Xếp quân thủ công (không bắt buộc; để trống = xếp tự động đối xứng như OTTv2)</span><textarea class="input" name="placement" rows="3" placeholder='[{"square":"a2","side":"red","kind":"rock"}]'></textarea></label>${note('Luật, thể thức và số vòng được giữ cố định sau khi tạo giải. Quỹ giờ của bot không đổi theo kích thước bàn; chỉ giới hạn số nước tăng theo số ô. Hệ thống từ chối luật cho phép một bên thắng ngay ở nước đầu. Swiss: mỗi vòng ghép các đội cùng điểm và tránh gặp lại đối thủ cũ; nếu số đội lẻ, một đội được nghỉ và nhận 6 điểm (như thắng cả vòng), mỗi đội nghỉ tối đa 1 lần; bằng điểm thì xét Buchholz (tổng điểm các đối thủ đã gặp).')}`;
}

export function readTournamentOptions(data){
  const format=data.get('format')||'double_round_robin',result={format};
  if(format==='double_round_robin'&&Number(data.get('max_teams'))>32)throw new Error('Vòng tròn tối đa 32 đội; chọn Swiss cho giải lớn.');
  if(data.get('rounds'))result.rounds=Number(data.get('rounds'));
  if(data.get('custom-basic')==='on'){
    if(data.get('use-experimental')==='on')throw new Error('Luật cơ bản SDK v2 dùng riêng với add-on v3.');
    const rows=Number(data.get('board-rows')),cols=Number(data.get('board-cols'));
    const bases={red:String(data.get('base-red')),blue:String(data.get('base-blue')||String.fromCharCode(96+cols)+rows)};
    const valid=cell=>/^[a-o]([1-9]|1[0-5])$/.test(cell)&&cell.charCodeAt(0)-97<cols&&Number(cell.slice(1))<=rows;
    if(!Number.isInteger(rows)||!Number.isInteger(cols)||rows<7||rows>15||cols<7||cols>15||!Object.values(bases).every(valid)||bases.red===bases.blue)throw new Error('Kích thước/căn cứ không hợp lệ.');
    const placement=data.get('placement')?JSON.parse(data.get('placement')):'auto';
    if(placement!=='auto'){
      const seen=new Set(),kinds={red:new Set(),blue:new Set()};
      if(!Array.isArray(placement))throw new Error('Xếp quân phải là mảng JSON.');
      for(const p of placement){if(!valid(p.square)||seen.has(p.square)||Object.values(bases).includes(p.square)||!kinds[p.side]||!['rock','paper','scissors'].includes(p.kind))throw new Error('Quân trùng ô, sai tọa độ/loại hoặc đứng trên căn cứ.');seen.add(p.square);kinds[p.side].add(p.kind);}
      if(Object.values(kinds).some(k=>k.size!==3))throw new Error('Mỗi bên cần đủ 3 loại quân ban đầu.');
    }
    result.basic_rules={rows,cols,bases,eliminate_types:Number(data.get('eliminate-types')),base_capture:data.get('base-capture')==='on',placement};
  }
  return result;
}
