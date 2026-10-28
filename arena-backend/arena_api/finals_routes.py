from fastapi import APIRouter, Depends, Header, Request
from pydantic import Field, model_validator
from arena_api.schemas import CompetitionCreate, Model
from arena_api.results_routes import publication_admin

router=APIRouter(prefix='/api/v1',tags=['finals'])

class FinalTeam(Model):
    team_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    online_rank: int = Field(ge=1,le=16)

class FinalCreate(CompetitionCreate):
    name: str = Field(min_length=3,max_length=100)
    title: str = 'Chung kết'
    match_ms: int = 300000
    teams: list[FinalTeam] = Field(min_length=16,max_length=16)
    pause_between_rounds: bool = False

    @model_validator(mode='after')
    def final_rules(self):
        if self.format!='double_round_robin' or self.submission_deadline is not None or self.match_ms not in (10000,60000,300000):
            raise ValueError('Chung kết dùng vòng tròn, quỹ giờ mỗi bot và không có hạn GitHub')
        if len({t.team_id for t in self.teams})!=16 or {t.online_rank for t in self.teams}!=set(range(1,17)):
            raise ValueError('Cần 16 đội khác nhau và hạng online 1–16 không trùng')
        self.name=CompetitionCreate.clean_title(self.name)
        return self

@router.post('/admin/finals',status_code=201,summary='Tạo chung kết 16 đội có hạng online duy nhất')
def create(body: FinalCreate,request: Request,idempotency_key: str=Header(),auth=Depends(publication_admin)):
    return request.app.state.competitions.finals.create(auth['user']['id'],body.model_dump(exclude_none=True),idempotency_key)

@router.get('/admin/finals',summary='Danh sách chung kết gồm bản nháp')
def listing(request: Request,auth=Depends(publication_admin)):
    return request.app.state.competitions.finals.listing(True)

@router.get('/admin/finals/{ident}',summary='Trạng thái điều phối chung kết của BTC')
def view(ident: str,request: Request,auth=Depends(publication_admin)):
    return request.app.state.competitions.finals.view(ident,True)

@router.post('/admin/finals/{ident}/start-groups',summary='Ghim một bot mỗi đội và bắt đầu bốn bảng')
def start(ident: str,request: Request,auth=Depends(publication_admin)):
    return request.app.state.competitions.finals.start_groups(auth['user']['id'],ident)

@router.post('/admin/finals/{ident}/start-round',summary='Cho chạy vòng tiếp theo đã chốt khi bật chờ giữa vòng')
def start_round(ident: str,request: Request,auth=Depends(publication_admin)):
    return request.app.state.competitions.finals.start_round(auth['user']['id'],ident)

@router.post('/admin/finals/{ident}/stop',summary='Dừng tự động, giữ nguyên các giải con để BTC điều hành tay')
def stop(ident: str,request: Request,auth=Depends(publication_admin)):
    return request.app.state.competitions.finals.stop(auth['user']['id'],ident)
