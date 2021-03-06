import requests
import json
import warnings
from nose.tools import with_setup
import logging

log = logging.getLogger(__name__)
sh = logging.StreamHandler()
log.addHandler(sh)
log.setLevel(logging.INFO)
warnings.filterwarnings('ignore')

adm_user = 'admin@user.com'
user = 'test@user.com'
test_data = type('',(object,),{})()
base_url = 'https://localhost:8443/api'

def _build_url(_id=None, requestor=adm_user, site='local'):
    if _id is None:
        url = test_data.proj_url + '?user=' + requestor
    else:
        url = test_data.proj_url + '/' + site + '/' + _id + '?user=' + requestor
    return url


def setup_db():
    payload = {
        'group': 'unknown',
        'label': 'SciTran/Testing',
        'public': False
    }
    payload = json.dumps(payload)
    r = requests.post(base_url + '/projects?user=admin@user.com&root=true', data=payload, verify=False)
    test_data.pid = json.loads(r.content)['_id']
    assert r.ok
    log.debug('pid = \'{}\''.format(test_data.pid))
    test_data.proj_url = base_url + '/projects/{}/permissions'.format(test_data.pid)

def teardown_db():
    r = requests.delete(base_url + '/projects/' + test_data.pid + '?user=admin@user.com&root=true', verify=False)
    assert r.ok

@with_setup(setup_db, teardown_db)
def test_permissions():
    url_post = _build_url()
    url_get = _build_url(user)
    r = requests.get(url_get, verify=False)
    assert r.status_code == 404
    data = {
        '_id': user,
        'site': 'local',
        'access': 'ro'
    }
    r = requests.post(url_post, data = json.dumps(data), verify=False)
    assert r.ok
    r = requests.get(url_get, verify=False)
    assert r.ok
    content = json.loads(r.content)
    assert content['_id'] == user
    assert content['site'] == 'local'
    assert content['access'] == 'ro'
    data = {
        'access': 'admin'
    }
    r = requests.put(url_get, data = json.dumps(data), verify=False)
    assert r.ok
    r = requests.get(url_get, verify=False)
    assert r.ok
    content = json.loads(r.content)
    assert content['_id'] == user
    assert content['site'] == 'local'
    assert content['access'] == 'admin'
    r = requests.delete(url_get, verify=False)
    assert r.ok
    r = requests.get(url_get, verify=False)
    assert r.status_code == 404
