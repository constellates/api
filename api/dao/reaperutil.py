import bson
import difflib
import pymongo
import datetime
import dateutil.parser

from .. import util
from .. import config
from . import APIStorageException

log = config.log

PROJECTION_FIELDS = ['group', 'name', 'label', 'timestamp', 'permissions', 'public']

class TargetAcquisition(object):

    def __init__(self, acquisition, fileinfo):
        self.acquisition = acquisition
        self.dbc = config.db.acquisitions
        self._id = acquisition['_id']
        self.fileinfo = fileinfo or {}

    def find(self, filename):
        for f in self.acquisition.get('files', []):
            if f['name'] == filename:
                return f
        return None

    def update_file(self, fileinfo):
        update_set = {'files.$.modified': datetime.datetime.utcnow()}
        # in this method, we are overriding an existing file.
        # update_set allows to update all the fileinfo like size, hash, etc.
        fileinfo.update(self.fileinfo)
        for k,v in fileinfo.iteritems():
            update_set['files.$.' + k] = v
        acquisition_obj = self.dbc.update_one(
            {'_id': self.acquisition['_id'], 'files.name': fileinfo['name']},
            {'$set': update_set}
        )

    def add_file(self, fileinfo):
        fileinfo.update(self.fileinfo)
        self.dbc.update_one({'_id': self.acquisition['_id']}, {'$push': {'files': fileinfo}})

def update_fileinfo(cont_name, _id, fileinfo):
    update_set = {'files.$.modified': datetime.datetime.utcnow()}
    # in this method, we are overriding an existing file.
    # update_set allows to update all the fileinfo like size, hash, etc.
    for k,v in fileinfo.iteritems():
        update_set['files.$.' + k] = v
    config.db[cont_name].update_one(
        {'_id': _id, 'files.name': fileinfo['name']},
        {'$set': update_set}
    )

def add_fileinfo(cont_name, _id, fileinfo):
    config.db[cont_name].update_one({'_id': _id}, {'$push': {'files': fileinfo}})

def _find_or_create_destination_project(group_name, project_label, created, modified):
    existing_group_ids = [g['_id'] for g in config.db.groups.find(None, ['_id'])]
    group_id_matches = difflib.get_close_matches(group_name, existing_group_ids, cutoff=0.8)
    if len(group_id_matches) == 1:
        group_name = group_id_matches[0]
    else:
        project_label = group_name + '_' + project_label
        group_name = 'unknown'
    group = config.db.groups.find_one({'_id': group_name})
    project = config.db.projects.find_one_and_update(
        {'group': group['_id'], 'label': project_label},
        {
            '$setOnInsert': {
                'permissions': group['roles'], 'public': False,
                'created': created, 'modified': modified
            }
        },
        PROJECTION_FIELDS,
        upsert=True,
        return_document=pymongo.collection.ReturnDocument.AFTER,
        )
    return project

def create_container_hierarchy(metadata):
    #TODO: possibly try to keep a list of session IDs on the project, instead of having the session point to the project
    #      same for the session and acquisition
    #      queries might be more efficient that way

    group = metadata.get('group', {})
    project = metadata.get('project', {})
    session = metadata.get('session', {})
    acquisition = metadata.get('acquisition', {})
    subject = metadata.get('subject')
    file_ = metadata.get('file')

    # Fail if some fields are missing
    try:
        group_id = group['_id']
        project_label = project['label']
        session_uid = session['uid']
        acquisition_uid = acquisition['uid']
    except Exception as e:
        log.error(metadata)
        raise APIStorageException(str(e))

    subject = metadata.get('subject')
    file_ = metadata.get('file')

    now = datetime.datetime.utcnow()

    session_obj = config.db.sessions.find_one({'uid': session_uid}, ['project'])
    if session_obj: # skip project creation, if session exists
        project_obj = config.db.projects.find_one({'_id': session_obj['project']}, projection=PROJECTION_FIELDS + ['name'])
    else:
        project_obj = _find_or_create_destination_project(group_id, project_label, now, now)
    session['subject'] = subject or {}
    #FIXME session modified date should be updated on updates
    if session.get('timestamp'):
        session['timestamp'] = dateutil.parser.parse(session['timestamp'])
    session['modified'] = now
    session_obj = config.db.sessions.find_one_and_update(
        {'uid': session_uid},
        {
            '$setOnInsert': dict(
                group=project_obj['group'],
                project=project_obj['_id'],
                permissions=project_obj['permissions'],
                public=project_obj['public'],
                created=now
            ),
            '$set': session,
        },
        PROJECTION_FIELDS,
        upsert=True,
        return_document=pymongo.collection.ReturnDocument.AFTER,
    )

    log.info('Storing     %s -> %s -> %s' % (project_obj['group'], project_obj['label'], session_uid))

    if acquisition.get('timestamp'):
        acquisition['timestamp'] = dateutil.parser.parse(acquisition['timestamp'])
        config.db.projects.update_one({'_id': project_obj['_id']}, {'$max': dict(timestamp=acquisition['timestamp']), '$set': dict(timezone=acquisition.get('timezone'))})
        config.db.sessions.update_one({'_id': session_obj['_id']}, {'$min': dict(timestamp=acquisition['timestamp']), '$set': dict(timezone=acquisition.get('timezone'))})

    acquisition['modified'] = now
    acq_operations = {
        '$setOnInsert': dict(
            session=session_obj['_id'],
            permissions=session_obj['permissions'],
            public=session_obj['public'],
            created=now
        ),
        '$set': acquisition
    }
    #FIXME acquisition modified date should be updated on updates
    acquisition_obj = config.db.acquisitions.find_one_and_update(
        {'uid': acquisition_uid},
        acq_operations,
        upsert=True,
        return_document=pymongo.collection.ReturnDocument.AFTER,
    )
    return TargetAcquisition(acquisition_obj, file_)

def update_container_hierarchy(metadata, acquisition_id, level):
    project = metadata.get('project')
    session = metadata.get('session')
    acquisition = metadata.get('acquisition')
    now = datetime.datetime.utcnow()
    if acquisition.get('timestamp'):
        acquisition['timestamp'] = dateutil.parser.parse(acquisition['timestamp'])
    acquisition['modified'] = now
    acquisition_obj = _update_container({'_id': acquisition_id}, acquisition, 'acquisitions')
    if acquisition_obj is None:
        raise APIStorageException('acquisition doesn''t exist')
    if acquisition.get('timestamp'):
        session_obj = config.db.sessions.find_one_and_update(
            {'_id': acquisition_obj['session']},
            {
                '$min': dict(timestamp=acquisition['timestamp']),
                '$set': dict(timezone=acquisition.get('timezone'))
            },
            return_document=pymongo.collection.ReturnDocument.AFTER
        )
        config.db.projects.find_one_and_update(
            {'_id': session_obj['project']},
            {
                '$max': dict(timestamp=acquisition['timestamp']),
                '$set': dict(timezone=acquisition.get('timezone'))
            }
        )
    session_obj = None
    if session:
        session['modified'] = now
        session_obj = _update_container({'_id': acquisition_obj['session']}, session, 'sessions')
    if project:
        project['modified'] = now
        if not session_obj:
            session_obj = config.db.sessions.find_one({'_id': acquisition_obj['session']})
        _update_container({'_id': session_obj['project']}, project, 'projects')
    return acquisition_obj

def _update_container(query, update, cont_name):
    return config.db[cont_name].find_one_and_update(
        query,
        {
            '$set': util.mongo_dict(update)
        },
        return_document=pymongo.collection.ReturnDocument.AFTER
    )
