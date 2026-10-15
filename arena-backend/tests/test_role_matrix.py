"""Exercise real auth/CSRF dependencies on EVERY OpenAPI operation.

Only the final handler is replaced by a 200 probe so matrix traversal cannot
delete fixtures or revoke its own sessions. Real actions/lifecycles are covered
by test_web_api, test_admin_operations, test_competitions and Docker integration.
"""
import json
import inspect
from pathlib import Path
import re
from fastapi.routing import APIRoute
from starlette.responses import Response
from arena_api.dependencies import COOKIE, SPECTATOR_PATHS
from test_web_api import APIHarness, registration


class RoleMatrix(APIHarness):
    def test_every_openapi_operation_all_roles_with_and_without_csrf(self):
        team,issued=self.create_team()
        temporary,_=self.create_team("Temporary Team","temp-team",change=False)
        locked,lock_issued=self.create_team("Locked Team","lock-team")
        archived,archive_issued=self.create_team("Archived Team","archive-team")
        self.app.state.accounts.lock_account(lock_issued['account']['id'],True,'matrix')
        self.app.state.operations.archive_team(archive_issued['account']['team_id'],1,True,'matrix')
        clients={'guest':self.other_client(),'temporary':temporary,'team':team,'locked':locked,'archived':archived,'admin':self.client}
        refs=self.app.openapi()['components']['schemas']
        def data(schema):
            if '$ref' in schema:return data(refs[schema['$ref'].split('/')[-1]])
            if 'const' in schema:return schema['const']
            if 'enum' in schema:return schema['enum'][0]
            if 'anyOf' in schema:return data(next(s for s in schema['anyOf'] if s.get('type')!='null'))
            kind=schema.get('type')
            if kind=='object':return {k:data(v) for k,v in schema.get('properties',{}).items() if k in schema.get('required',[])}
            if kind=='array':return [data(schema.get('items',{})) for _ in range(schema.get('minItems',0))]
            if kind=='integer':return int(schema.get('minimum',1))
            if kind=='boolean':return True
            if kind=='string':
                pattern=schema.get('pattern','')
                if '0-9a-f' in pattern:return 'a'*32
                if 'A-Za-z0-9' in pattern:return 'matrixuser'
                if schema.get('format')=='binary':return 'zip'
                return 'matrix-value'[:schema.get('maxLength',1000)]
            return {}
        public={'/api/v1/registrations','/api/v1/auth/login','/api/v1/auth/recovery','/api/v1/time','/api/v1/health/live','/api/v1/health/ready','/api/v1/platform','/api/v1/rules','/api/v1/experimental/guide'}
        principal_only={'/api/v1/auth/me','/api/v1/auth/logout','/api/v1/auth/password'}
        shared={'/api/v1/rulesets','/api/v1/rulesets/schema','/api/v1/rulesets/{ident}','/api/v1/rulesets/{ident}/preview','/api/v1/rulesets/{ident}/package.json','/api/v1/rulesets/{ident}/kit.zip','/api/v1/rulesets/{ident}/starter.zip','/api/v1/experimental/starter.zip'}
        operations={(path,method.upper()):op for path,methods in self.app.openapi()['paths'].items() for method,op in methods.items()}
        rows=[]
        candidates=[]
        for branch in self.app.routes:
            if hasattr(branch,'effective_route_contexts'):
                candidates.extend(r for r in branch.effective_route_contexts() if isinstance(r.original_route,APIRoute))
            elif isinstance(branch,APIRoute):candidates.append(branch)
        for route in candidates:
            original=route.dependant.call
            async def async_probe(**kwargs):return Response(status_code=200)
            route.dependant.call=async_probe if inspect.iscoroutinefunction(original) else lambda **kwargs:Response(status_code=200)
            try:
                for method in route.methods:
                    if (route.path,method) not in operations:continue
                    op=operations[(route.path,method)]
                    path=re.sub(r'\{number\}','1',route.path)
                    path=re.sub(r'\{[^}]+\}','a'*32,path)
                    headers={'Idempotency-Key':'matrix-idempotency','X-Submission-Version':'v1.0.0','X-Bot-Name':'matrix'}
                    for p in op.get('parameters',[]):
                        if p['in']=='header' and p.get('required'):headers[p['name']]=str(data(p['schema']))
                    body=None
                    if op.get('requestBody'):
                        content=op['requestBody']['content']
                        if 'application/json' in content:
                            body=data(content['application/json']['schema'])
                            if route.path.endswith('/registrations'):body=registration()
                            if '/teams'==route.path.removeprefix('/api/v1/admin'):body={'profile':registration(),'username':'matrixuser'}
                            if route.path.endswith('/teams/{ident}/edit'):body={**registration(),'expected_revision':1}
                            if route.path.endswith('/matches'):body={'red_submission_id':'a'*32,'blue_submission_id':'b'*32,'delegate_timeout_claims':True}
                        else:headers['Content-Type']='application/zip'
                    for role,client in clients.items():
                        for csrf in (False,True):
                            h=dict(headers)
                            h['X-CSRF-Token']=client.headers.get('X-CSRF-Token','') if csrf else 'invalid'
                            if route.path in public or method=='GET' and route.path in SPECTATOR_PATHS:expected=200
                            elif role in ('guest','locked','archived'):expected=401
                            elif method!='GET' and not csrf:expected=403
                            elif role=='temporary' and route.path not in principal_only:expected=403
                            elif '/admin/' in route.path and role!='admin':expected=403
                            elif role=='admin' and route.path not in principal_only and route.path not in shared and '/admin/' not in route.path:expected=403
                            else:expected=200
                            response=client.request(method,path,headers=h,**({'json':body} if body is not None else {'content':b''} if method=='POST' else {}))
                            rows.append({'method':method,'path':route.path,'role':role,'csrf':csrf,'expected':expected,'actual':response.status_code})
                            self.assertEqual(response.status_code,expected,(rows[-1],response.text))
            finally:route.dependant.call=original
        self.assertEqual({(r['path'],r['method']) for r in rows},set(operations))
        output=Path(__file__).resolve().parents[1]/'test-results'
        output.mkdir(exist_ok=True)
        (output/'role-matrix.json').write_text(json.dumps({'scope':'real authentication dependencies; final handlers are 200 probes','rows':rows},indent=2))
