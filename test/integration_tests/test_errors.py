import requests
import json
import logging
log = logging.getLogger(__name__)
sh = logging.StreamHandler()
log.addHandler(sh)
import warnings
warnings.filterwarnings('ignore')
base_url = 'https://localhost:8443/api'

import pymongo
db = pymongo.MongoClient('mongodb://localhost/scitran').get_default_database()
projects = db.projects

def test_extra_param():
    payload = {
        'group': 'unknown',
        'label': 'SciTran/Testing',
        'public': False,
        'extra_param': 'some_value'
    }
    payload = json.dumps(payload)
    r = requests.post(base_url + '/projects?user=admin@user.com&root=true', data=payload, verify=False)
    assert r.status_code == 400
    r = projects.delete_many({'label': 'SciTran/Testing'})
    assert r.deleted_count == 0
