# Copyright 2011 Justin Santa Barbara
# Copyright 2015 Canonical Ltd
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.from oslo_config import cfg

from nova.compute import task_states
from nova import exception
from nova import i18n
from nova import image
from oslo_config import cfg
from oslo_log import log as logging

from nclxd.nova.virt.lxd import container_client

_ = i18n._

CONF = cfg.CONF
LOG = logging.getLogger(__name__)

IMAGE_API = image.API()

class LXDSnapshot(object):

    def __init__(self):
        self.container_client = container_client.LXDContainerClient()

    def snapshot(self, context, instance, image_id, update_task_state):
        LOG.debug('in snapshot')
        update_task_state(task_state=task_states.IMAGE_PENDING_UPLOAD)

        snapshot = IMAGE_API.get(context, image_id)

        ''' Create a snapshot of the running contianer'''
        self.create_container_snapshot(snapshot, instance)

        ''' Publish the image to LXD '''
        (state, data) = self.container_client.client('stop', instance=instance.uuid,
                                                     host=instance.host)
        self.container_client.client('wait',
            oid=data.get('operation').split('/')[3],
            host=instance.host)
        fingerprint = self.create_lxd_image(snapshot, instance)
        self.create_glance_image(context, image_id, snapshot, fingerprint, instance)

        update_task_state(task_state=task_states.IMAGE_UPLOADING,
                          expected_state=task_states.IMAGE_PENDING_UPLOAD)

        (state, data) = self.container_client.client('start', instance=instance.uuid,
                                                     host=instance.host)


    def create_container_snapshot(self, snapshot, instance):
        LOG.debug('Creating container snapshot')
        container_snapshot = {'name': snapshot['name'],
                              'stateful': False}
        (state, data) = self.container_client.client('snapshot_create',
                                instance=instance.uuid,
                                container_snapshot=container_snapshot,
                                host=instance.host)
        self.container_client.client('wait',
                                     oid=data.get('operation').split('/')[3],
                                     host=instance.host)

    def create_lxd_image(self, snapshot, instance):
        LOG.debug('Uploading image to LXD image store.')
        container_image = {
            'source': {
                'name': '%s/%s' % (instance.uuid,
                                   snapshot['name']),
                'type': 'snapshot'
            }
        }
        LOG.debug(container_image)
        (state, data) = self.container_client.client('publish',
                            container_image=container_image,
                            host=instance.host)

        LOG.debug('Creating LXD alias')
        fingerprint = str(data['metadata']['fingerprint'])
        snapshot_alias = {'name': snapshot['name'],
                          'target': fingerprint}
        LOG.debug(snapshot_alias)
        self.container_client.client('alias_create',
                            alias=snapshot_alias,
                            host=instance.host)
        return fingerprint

    def create_glance_image(self, context, image_id, snapshot, fingerprint, instance):
        LOG.debug('Uploading image to glance')
        image_metadata = {'name': snapshot['name'],
                          "disk_format": "raw",
                          "container_format": "bare",
                          "properties": {
                            'lxd-image-alias': fingerprint
                          }}
        try:
            data = self.container_client.client('image_export',
                    fingerprint=fingerprint,
                    host=instance.host)
            IMAGE_API.update(context, image_id, image_metadata, data)
        except Exception as ex:
            msg = _("Failed: %s") % ex
            raise exception.NovaException(msg)
