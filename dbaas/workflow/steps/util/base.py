# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import logging
import requests
from django.core.exceptions import ObjectDoesNotExist
from django.utils.encoding import python_2_unicode_compatible
from collections import namedtuple
from dbaas_credentials.models import CredentialType
from util import get_credentials_for

LOG = logging.getLogger(__name__)


@python_2_unicode_compatible
class BaseStep(object):

    def __str__(self):
        return "I am a step"

    def do(self, workflow_dict):
        raise NotImplementedError

    def undo(self, workflow_dict):
        raise NotImplementedError


@python_2_unicode_compatible
class BaseInstanceStep(object):

    def __str__(self):
        return "I am a step"

    def __init__(self, instance):
        self.instance = instance

    @property
    def infra(self):
        return self.instance.databaseinfra

    @property
    def database(self):
        return self.infra.databases.first()

    @property
    def plan(self):
        return self.infra.plan

    @property
    def engine(self):
        return self.infra.engine

    @property
    def disk_offering(self):
        return self.infra.disk_offering

    @property
    def host(self):
        try:
            return self.instance.hostname
        except ObjectDoesNotExist:
            LOG.info(
                'Instance {} does not have hostname'.format(self.instance))
            return

    @property
    def environment(self):
        return self.infra.environment

    @property
    def restore(self):
        restore = self.database.database_restore.last()
        if restore and restore.is_running:
            return restore

    @property
    def snapshot(self):
        try:
            return self.restore.group.backups.get(instance=self.instance)
        except ObjectDoesNotExist:
            return

    @property
    def latest_disk(self):
        return self.instance.hostname.volumes.last()

    @property
    def resize(self):
        resize = self.database.resizes.last()
        if resize and resize.is_running:
            return resize

    @property
    def is_valid(self):
        return True

    @property
    def can_run(self):
        return True

    @property
    def upgrade(self):
        upgrade = self.database.upgrades.last()
        if upgrade and upgrade.is_running:
            return upgrade

    @property
    def reinstall_vm(self):
        reinstall_vm = self.database.reinstall_vm.last()
        if reinstall_vm and reinstall_vm.is_running:
            return reinstall_vm

    @property
    def create(self):
        create = self.infra.databases_create.last()
        if create and create.is_running:
            return create

    def do(self):
        raise NotImplementedError

    def undo(self):
        raise NotImplementedError


class BaseInstanceStepMigration(BaseInstanceStep):

    @property
    def host(self):
        host = super(BaseInstanceStepMigration, self).host
        return host.future_host if host else None

    @property
    def environment(self):
        environment = super(BaseInstanceStepMigration, self).environment
        return environment.migrate_environment

    @property
    def plan(self):
        plan = super(BaseInstanceStepMigration, self).plan
        return plan.migrate_plan


class HostProviderClient(object):
    credential_type = CredentialType.HOST_PROVIDER

    def __init__(self, env):
        self.env = env
        self._credential = None

    @property
    def credential(self):
        if not self._credential:
            self._credential = get_credentials_for(
                self.env, self.credential_type
            )
        return self._credential

    def get_vm_by_host(self, host):
        api_host_url = '/{}/{}/host/{}'.format(
            self.credential.project,
            self.env.name,
            host.identifier
        )
        resp = requests.get('{}{}'.format(self.credential.endpoint, api_host_url))
        if resp.ok:
            vm = resp.json()
            return namedtuple('VMProperties', vm.keys())(*vm.values())

    def get_offering_id(self, cpus, memory):
        api_host_url = '/{}/{}/credential/{}/{}'.format(
            self.credential.project,
            self.env.name,
            cpus,
            memory
        )

        resp = requests.get('{}{}'.format(self.credential.endpoint, api_host_url))
        if resp.ok:
            data = resp.json()
            return data.get('offering_id')


class ACLFromHellClient(object):

    def __init__(self, env):
        self.environment = env
        self._credential = None

    @property
    def credential(self):
        if not self._credential:
            try:
                self._credential = get_credentials_for(
                    self.environment, CredentialType.ACLFROMHELL
                )
            except IndexError:
                raise Exception(
                    "Credential ACLFROMHELL for env {} not found".format(
                        self.environment.name
                    )
                )

        return self._credential

    def _request(self, action, url, **kw):
        return action(
            url,
            auth=(self.credential.user, self.credential.password),
            **kw
        )

    def get_rule(self, database, app_name=None):
        params = {
            'metadata.owner': 'dbaas',
            'metadata.service-name': self.credential.project,
            'metadata.instance-name': database.name,
        }
        if app_name:
            params.update({'source.tsuruapp.appname': app_name})

        LOG.debug("Tsuru get rule for {} params:{}".format(
            database.name, params))

        return self._request(
            requests.get,
            self.credential.endpoint,
            params=params,
        )

    def add_acl(self, database, app_name, hostname):
        infra = database.infra
        driver = infra.get_driver()

        payload = {
            "source": {
                "tsuruapp": {
                    "appname": app_name
                }
            },
            "destination": {
                "externaldns": {
                    "name": hostname,
                    "ports": map(
                        lambda p: {
                            'protocol': 'tcp',
                            'port': p
                        },
                        driver.ports
                    )
                }
            },
            "target": "accept",
            "metadata": {
                'owner': 'dbaas',
                "service-name": self.credential.project,
                "instance-name": database.name
            }
        }

        LOG.debug("Tsuru Add ACL: payload for host {}:{}".format(
            hostname, payload))
        resp = self._request(
            requests.post,
            self.credential.endpoint,
            json=payload,
        )
        if not resp.ok:
            msg = "Error bind {} database on {} environment: {}".format(
                database.name, self.environment, resp.content
            )
            LOG.debug(msg)
        LOG.debug("Tsuru Add ACL Status for host {}: {}".format(
            hostname, resp.status_code
        ))

        return resp

    def remove_acl(self, database, app_name):

        resp = self.get_rule(database, app_name)
        if not resp.ok:
            msg = "Rule not found for {}.".format(
                database.name)
            LOG.debug(msg)

        rules = resp.json()
        for rule in rules:
            rule_id = rule.get('RuleID')
            host = rule.get('Destination', {}).get('ExternalDNS', {}).get('Name')
            if rule_id:
                LOG.debug('Tsuru Unbind App removing rule for {}'.format(host))
                resp = self._request(
                    requests.delete,
                    '{}/{}'.format(self.credential.endpoint, rule_id)
                )
                if not resp.ok:
                    msg = "Error on delete rule {} for {}.".format(
                        rule_id, host)
                    LOG.debug(msg)
        return None
