import {ladderMinutes,ladderPreview} from './ladder-clock.js';
import {tr,html as l10n} from './i18n.js';
import {vnDeadlineField,readVnDeadline,updateVnDeadlinePreviews} from './vn-deadline.js';
import {field,select,note} from './view.js';

export function readLadderOptions(data){
  const hours=Number(data.get('ladder-hours')??2),minutes=Number(data.get('ladder-minutes')??0),total=hours*60+minutes;
  if(!Number.isInteger(hours)||hours<0||hours>168||!Number.isInteger(minutes)||minutes<0||minutes>59||total<5||total>10080)throw new Error(tr('Khoảng mở đợt phải từ 5 phút tới 7 ngày; giờ 0–168, phút 0–59.'));
  const first=readVnDeadline(data,'first_round_ms');
  return {round_minutes:total,...(first!=null?{first_round_ms:first}:{})};
}
export function ladderEdit(data,original){
  const ladder=readLadderOptions(data);
  return ladderMinutes(ladder)===ladderMinutes(original)&&(ladder.first_round_ms??null)===(original.first_round_ms??null)?{}:{ladder};
}
export function ladderFields(ladder={round_minutes:120}){
  const total=ladderMinutes(ladder);
  return `<div class="field"><span>${tr('Mở đợt mỗi')}</span><div class="form-grid">${field(tr('giờ'),'ladder-hours','min="0" max="168" step="1"','number',Math.floor(total/60))}${field(tr('phút'),'ladder-minutes','min="0" max="59" step="1"','number',total%60)}</div><p class="form-error" data-ladder-error role="alert"></p></div>`+vnDeadlineField('first_round_ms',ladder.first_round_ms)+`<p class="muted" data-ladder-calendar></p>`;
}
export function updateLadderPreview(form){
  if(!form)return;
  updateVnDeadlinePreviews(form);
  const inputs=[form.elements['ladder-hours'],form.elements['ladder-minutes']].filter(Boolean),calendar=form.querySelector('[data-ladder-calendar]'),error=form.querySelector('[data-ladder-error]');
  if(!calendar)return;
  if(form.elements.format&&form.elements.format.value!=='elo'){if(error){error.textContent='';error.classList.remove('visible');}inputs.forEach(i=>i.setCustomValidity(''));calendar.textContent='';return;}
  try{
    const data=new FormData(form),value=readLadderOptions(data),original=form.dataset.ladder?JSON.parse(form.dataset.ladder):null;
    const ladder=original&&!Object.hasOwn(ladderEdit(data,original),'ladder')?original:value;
    inputs.forEach(i=>i.setCustomValidity(''));if(error){error.textContent='';error.classList.remove('visible');}
    calendar.textContent=ladderPreview(ladder,readVnDeadline(data,'submission_deadline')??NaN);
  }catch(e){inputs.forEach(i=>i.setCustomValidity(e.message));if(error){error.textContent=e.message;error.classList.add('visible');}calendar.textContent='';}
}

export function tournamentOptions(){
  return l10n`${vnDeadlineField('submission_deadline')}<label class="field"><span>Máy chấm cho trận tính điểm</span>${select('grader_platform',[['windows','Windows (AppContainer)'],['linux','Linux (bubblewrap)']])}</label><label class="field"><span>Thể thức</span>${select('format',[['elo',tr('Ladder Elo → vòng chung → vòng tròn tính điểm')],['double_round_robin',tr('Vòng tròn 2 lượt (tối đa 32 đội)')],['swiss',tr('Swiss (nhiều đội) · mỗi cặp 2 ván đổi màu')]])}</label><details><summary>Luật ladder và xếp hạng chính thức</summary><div class="form-grid">${ladderFields()}${field(tr('Số đợt vòng chung'),'general-rounds','min="1" max="64"','number','20')}${field(tr('Số đội vòng tròn'),'group-size','min="2" max="128"','number','64')}${field(tr('Số đội vào chung kết'),'finalists','min="1" max="128"','number','16')}${field(tr('Số ván chính thức đầu dùng K lớn'),'k-games','min="1"','number','20')}${field(tr('K ban đầu'),'first-k','min="1" max="64"','number','32')}${field(tr('K sau đó'),'later-k','min="1" max="64"','number','16')}</div>${note(tr('Vòng tròn: thắng 1, hòa ½, thua 0; không cập nhật Elo. Số đội không quá kích thước nhóm đầu thì bỏ vòng chung.'))}</details>${field(tr('Số vòng Swiss (để trống: hệ thống tự tính theo số đội)'),'rounds','min="1" max="64"','number')}<label class="check-field"><input type="checkbox" name="custom-basic">Tùy chỉnh luật bàn cờ (kích thước, căn cứ, điều kiện thắng)</label><div class="form-grid">${field(tr('Số hàng (7–15)'),'board-rows','min="7" max="15" required','number','9')}${field(tr('Số cột (7–15)'),'board-cols','min="7" max="15" required','number','9')}${field(tr('Căn cứ Đỏ'),'base-red','pattern="[a-o]([1-9]|1[0-5])" required','text','a1')}${field(tr('Căn cứ Xanh (để trống: góc đối diện)'),'base-blue','','text','')}<label class="field"><span>Thắng khi diệt sạch</span>${select('eliminate-types',[['1',tr('1 loại quân')],['2',tr('2 loại quân')]])}</label></div><label class="check-field"><input name="base-capture" type="checkbox" checked>Thắng khi chiếm căn cứ đối phương</label><label class="field"><span>Xếp quân thủ công (không bắt buộc; để trống = xếp tự động đối xứng như OTTv2)</span><textarea class="input" name="placement" rows="3" placeholder='[{"square":"a2","side":"red","kind":"rock"}]'></textarea></label>${note(tr('Luật, thể thức và số vòng được giữ cố định sau khi tạo giải. Quỹ giờ của bot không đổi theo kích thước bàn; chỉ giới hạn số nước tăng theo số ô. Hệ thống từ chối luật cho phép một bên thắng ngay ở nước đầu. Swiss: mỗi vòng ghép các đội cùng điểm và tránh gặp lại đối thủ cũ; nếu số đội lẻ, một đội được nghỉ và nhận 6 điểm (như thắng cả vòng), mỗi đội nghỉ tối đa 1 lần; bằng điểm thì xét Buchholz (tổng điểm các đối thủ đã gặp).'))}`;
}

export function readTournamentOptions(data){
  const format=data.get('format')||'double_round_robin',result={format,grader_platform:data.get('grader_platform')||'windows'};
  const deadline=readVnDeadline(data,'submission_deadline');if(deadline!=null)result.submission_deadline=deadline;
  if(format==='double_round_robin'&&Number(data.get('max_teams'))>32)throw new Error(tr('Vòng tròn tối đa 32 đội; chọn Swiss cho giải lớn.'));
  if(format==='elo'){
    if(!result.submission_deadline)throw new Error(tr('Giải Elo cần hạn nộp'));
    const n=(key,fallback)=>Number(data.get(key)||fallback);
    result.ladder=readLadderOptions(data);
    result.official={general_rounds:n('general-rounds',20),group_size:n('group-size',64),finalists:n('finalists',16),k:[[n('k-games',20),n('first-k',32)],[null,n('later-k',16)]]};
  }
  if(data.get('rounds'))result.rounds=Number(data.get('rounds'));
  if(data.get('custom-basic')==='on'){
    if(data.get('use-experimental')==='on')throw new Error(tr('Luật cơ bản SDK v2 dùng riêng với add-on v3.'));
    const rows=Number(data.get('board-rows')),cols=Number(data.get('board-cols'));
    const bases={red:String(data.get('base-red')),blue:String(data.get('base-blue')||String.fromCharCode(96+cols)+rows)};
    const valid=cell=>/^[a-o]([1-9]|1[0-5])$/.test(cell)&&cell.charCodeAt(0)-97<cols&&Number(cell.slice(1))<=rows;
    if(!Number.isInteger(rows)||!Number.isInteger(cols)||rows<7||rows>15||cols<7||cols>15||!Object.values(bases).every(valid)||bases.red===bases.blue)throw new Error(tr('Kích thước/căn cứ không hợp lệ.'));
    const placement=data.get('placement')?JSON.parse(data.get('placement')):'auto';
    if(placement!=='auto'){
      const seen=new Set(),kinds={red:new Set(),blue:new Set()};
      if(!Array.isArray(placement))throw new Error(tr('Xếp quân phải là mảng JSON.'));
      for(const p of placement){if(!valid(p.square)||seen.has(p.square)||Object.values(bases).includes(p.square)||!kinds[p.side]||!['rock','paper','scissors'].includes(p.kind))throw new Error(tr('Quân trùng ô, sai tọa độ/loại hoặc đứng trên căn cứ.'));seen.add(p.square);kinds[p.side].add(p.kind);}
      if(Object.values(kinds).some(k=>k.size!==3))throw new Error(tr('Mỗi bên cần đủ 3 loại quân ban đầu.'));
    }
    result.basic_rules={rows,cols,bases,eliminate_types:Number(data.get('eliminate-types')),base_capture:data.get('base-capture')==='on',placement};
  }
  return result;
}
