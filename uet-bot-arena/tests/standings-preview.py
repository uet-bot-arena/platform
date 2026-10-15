"""Display-only chart fixture, without live DB or accounts."""
import importlib.util
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

source=Path(__file__).with_name('refresh-preview.py')
spec=importlib.util.spec_from_file_location('refresh_fixture',source)
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)

def team(i,name,wins,draws,losses,rank):
    return {'team_id':'chart-'+str(i),'name':name,'version':'v1.0.'+str(i),'wins':wins,'draws':draws,'losses':losses,
      'played':wins+draws+losses,'points':3*wins+draws,'rank':rank}
items=[team(1,'Binary Garden',10,2,0,1),team(2,'UET Alpha',9,1,2,2),team(3,'Null Pointer',8,2,2,3),
       team(4,'Greedy Geese',7,2,3,4),team(5,'Đội có tên dài để kiểm tra hiển thị trên thiết bị di động',6,3,3,5),
       team(6,'Minimax Club',5,3,4,6),team(7,'Stack Overflow',4,2,6,7),team(8,'Three Draws',0,12,0,8),
       team(9,'Four Wins',4,0,8,8),team(10,'Last Top Ten',3,0,9,10),team(11,'Shared Tenth',2,3,7,10),
       team(12,'One More Try',1,2,9,12),team(13,'Chưa thi đấu',0,0,0,13)]
comp={'id':'chart-fixture','title':'Fixture · Bảng điểm 3–1–0','status':'running','entries':items,
      'config':{'profile':{'turn_ms':15000},'max_teams':16}}
class Handler(base.Handler):
    def do_GET(self):
        path=urlsplit(self.path).path
        if path=='/':return self.send((base.DIST/'live/index.html').read_text(),'text/html')
        if path=='/api/v1/competitions':return self.send({'items':[comp]})
        if path=='/api/v1/competitions/chart-fixture':return self.send(comp)
        if path.endswith('/fixtures'):return self.send({'items':[]})
        if path.endswith('/standings'):return self.send({'competition_id':comp['id'],'scoring':{'win':3,'draw':1,'loss':0},'ranking':'points_shared_rank','provisional':True,'items':items})
        return super().do_GET()
if __name__=='__main__':
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    print(f'http://127.0.0.1:{server.server_port}/#/standings',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
