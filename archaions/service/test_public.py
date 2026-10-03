import os, tempfile, unittest
os.environ['BASECALL_DATA']=tempfile.mkdtemp()
os.environ['BASECALL_TOKEN']='test-public-isolated-token-123456'
import server
from fastapi.testclient import TestClient
from fastapi import HTTPException
async def identity(request):
    user=request.cookies.get('archaions_session')
    if not user: raise HTTPException(401,'Sign in')
    return server.hashlib.sha256(user.encode()).hexdigest()+':'
server.resolve_account=identity
(server.ROOT/'gpu-status.json').write_text('{"B300":{"available":true,"models":["fast","hac","sup"]}}')
class PublicTests(unittest.TestCase):
 def test_account_isolation_and_models(self):
    anonymous=TestClient(server.app)
    self.assertEqual(anonymous.get('/jobs').status_code,401)
    a=TestClient(server.app,cookies={'archaions_session':'a'},headers={'Origin':'https://archaions.com'})
    b=TestClient(server.app,cookies={'archaions_session':'b'},headers={'Origin':'https://archaions.com'})
    self.assertEqual(a.get('/health').json()['gpus'][0]['models'],['hac','sup'])
    body=dict(run='same-run',filename='test.pod5',sha256='a'*64,size=4,model='hac',gpu='B300')
    for patch in [{'model':'fast'},{'gpu':'H100'}]:
      self.assertEqual(a.post('/uploads',json=body|patch).status_code,422)
    self.assertEqual(a.post('/uploads',json=body,headers={'Origin':'https://evil.test'}).status_code,403)
    job=a.post('/uploads',json=body).json(); other=b.post('/uploads',json=body).json()
    self.assertNotEqual(job['id'],other['id']); self.assertEqual(job['run'],'same-run')
    self.assertEqual(a.post('/uploads',json=body).json()['id'],job['id'])
    for query in ['/jobs','/jobs?run=same-run']:
      self.assertEqual([x['id'] for x in a.get(query).json()],[job['id']])
      self.assertEqual([x['id'] for x in b.get(query).json()],[other['id']])
    for route in ['/jobs/'+job['id'],'/jobs/'+job['id']+'/download/bam']:
      self.assertEqual(b.get(route).status_code,404)
    self.assertEqual(b.put('/uploads/'+job['id']+'?offset=0',content=b'test').status_code,404)
    self.assertEqual(b.post('/uploads/'+job['id']+'/complete').status_code,404)
    self.assertEqual(a.put('/uploads/'+job['id']+'?offset=0',content=b'test').status_code,200)
    self.assertEqual(a.get('/jobs/'+job['id']).json()['offset'],4)
    self.assertEqual(a.post('/session').status_code,404)
    self.assertEqual(a.get('/jobs').headers['cache-control'],'no-store')
if __name__=='__main__': unittest.main()
