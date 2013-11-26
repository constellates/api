# @author:  Gunnar Schaefer
# @author:  Kevin S. Hahn

import base64
import datetime
import json
import logging
import requests
import webapp2
import socket       # socket.gethostname()
from Crypto.Hash import HMAC
from Crypto.Random import random
logging.basicConfig(level=logging.INFO)


class NIMSRequestHandler(webapp2.RequestHandler):

    """fetches pubkey from own self.db.remotes. needs to be aware of OWN site uid"""

    def __init__(self, request=None, response=None):
        webapp2.RequestHandler.__init__(self, request, response)
        # call initialize, isntead of __init__
        self.request.remote_user = self.request.get('user', None) # FIXME: auth system should set REMOTE_USER
        self.userid = self.request.remote_user or '@public'
        self.user = self.app.db.users.find_one({'_id': self.userid})
        self.user_is_superuser = self.user.get('superuser')
        self.response.headers['Content-Type'] = 'application/json'
        try:
            self.target_id = request.route_kwargs['iid']            # for what site is the request meant
        except KeyError:
            self.target_id = 'local'                                # change to site_id?
        self.useragent = self.request.headers['user-agent']         # browser or internimsP2P request
        self.site_id = self.app.config['site_id']                   # what is THIS site
        self.pubkey = open(self.app.config['pubkey']).read()

        # requests coming from another NIMS instance are dealt with differently
        if self.useragent.startswith('NIMS Instance'):
            logging.info("request from '{0}', interNIMS p2p initiated".format(self.useragent))
            try:
                authinfo = self.request.headers['authorization']
                challenge_id, digest = base64.b64decode(authinfo).split()
                user, remote_site = challenge_id.split(':')
                # logging.info('{0} {1} {2}'.format(user, remote_site, digest))
                projection = {'_id': False, 'pubkey': True}
                remote_pubkey = self.app.db.remotes.find_one({'_id': remote_site}, projection)['pubkey']
                # get the challenge from db.challenges
                projection = {'_id': False, 'challenge': True}
                challenge = self.app.db.challenges.find_one({'_id': challenge_id}, projection)['challenge']
                # purge challenge (challenges are single use)
                self.app.db.challenges.remove({'_id': challenge_id})
                # verify
                h = HMAC.new(remote_pubkey, challenge)
                self.expected = base64.b64encode('%s %s' % (challenge_id, h.hexdigest()))
                if self.expected == authinfo:
                    logging.info('CRAM successful')
                else:
                    self.abort(403, 'Not Authorized: cram failed')
            except KeyError, e:
                # send a 401 with a fresh challenge
                cid = self.request.get('cid')
                if not cid: self.abort(403, 'challenge_id not in payload')
                challenge = {'_id': cid,
                             'challenge': str(random.getrandbits(128)),
                             'timestamp': datetime.datetime.now()}
                # upsert challenge with time of creation
                spam = self.app.db.challenges.find_and_modify(query={'_id': cid}, update=challenge, upsert=True, new=True)
                # send 401 + challenge in 'www-authenticate' header
                self.response.headers['www-authenticate'] = base64.b64encode(challenge['challenge'])
                self.response.set_status(401)

    def dispatch(self):
        """dispatching and request forwarding"""
        if self.target_id in ['local', self.site_id]:
            logging.info('{0} delegating to local {1}'.format(socket.gethostname(), self.request.url))
            super(NIMSRequestHandler, self).dispatch()
        else:
            logging.info('{0} delegating to remote {1}'.format(socket.gethostname(), self.target_id))
            # is target registered?
            target = self.app.db.remotes.find_one({'_id': self.target_id}, {'_id':False, 'hostname':True})
            if not target:
                logging.info('remote host {0} not in auth list. DENIED'.format(self.target_id))
                self.abort(403, 'forbidden: site is not registered with interNIMS')
            self.cid = self.userid + ':' + self.site_id
            reqheaders = dict(self.request.headers)

            # adjust the request, pass as much of orig request as possible
            reqheaders['User-Agent'] = 'NIMS Instance {0}'.format(self.site_id)
            del reqheaders['Host']
            target_api = 'http://{0}{1}?{2}'.format(target['hostname'], self.request.path, self.request.query_string)
            reqparams = {'cid': self.cid}

            # first attempt, expect 401, send as little as possible...
            r = requests.request(method=self.request.method, url=target_api, params=reqparams, headers=reqheaders, cookies=self.request.cookies)

            if r.status_code == 401:
                challenge = base64.b64decode(r.headers['www-authenticate'])
                # logging.info('challenge {0} recieved'.format(challenge))
                h = HMAC.new(self.pubkey, challenge)
                response = base64.b64encode('%s %s' % (self.cid, h.hexdigest()))
                # logging.info('sending: {0} {1}'.format(self.cid, h.hexdigest()))
                #adjust the request and try again
                reqheaders['authorization'] = response
                r = requests.request(method=self.request.method, url=target_api, params=reqparams, data=self.request.body, headers=reqheaders, cookies=self.request.cookies)

            self.response.write(r.content)