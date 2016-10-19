# Copyright © 2015,2016 STRG.AT GmbH, Vienna, Austria
#
# This file is part of the The SCORE Framework.
#
# The SCORE Framework and all its parts are free software: you can redistribute
# them and/or modify them under the terms of the GNU Lesser General Public
# License version 3 as published by the Free Software Foundation which is in the
# file named COPYING.LESSER.txt.
#
# The SCORE Framework and all its parts are distributed without any WARRANTY;
# without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE. For more details see the GNU Lesser General Public
# License.
#
# If you have not received a copy of the GNU Lesser General Public License see
# http://www.gnu.org/licenses/.
#
# The License-Agreement realised between you as Licensee and STRG.AT GmbH as
# Licenser including the issue of its valid conclusion and its pre- and
# post-contractual effects is governed by the laws of Austria. Any disputes
# concerning this License-Agreement including the issue of its valid conclusion
# and its pre- and post-contractual effects are exclusively decided by the
# competent court, in whose district STRG.AT GmbH has its registered seat, at
# the discretion of STRG.AT GmbH also the competent court, in whose district the
# Licensee has his registered seat, an establishment or assets.

import asyncio
from score.init import (
    ConfiguredModule, parse_list, parse_bool, init_from_file,
    InitializationError)
from .service import Service
from ._forked import fork, Backgrounded
from ._changedetect import ChangeDetector
from collections import OrderedDict
from contextlib import contextmanager
import traceback
import signal
import logging

log = logging.getLogger('score.serve')


defaults = {
    'autoreload': False,
    'modules': []
}


def init(confdict):
    conf = defaults.copy()
    conf.update(confdict)
    modules = parse_list(conf['modules'])
    if not modules:
        import score.serve
        raise InitializationError(score.serve, 'No modules configured')
    autoreload = parse_bool(conf['autoreload'])
    return ConfiguredServeModule(conf['conf'], modules, autoreload)


class ConfiguredServeModule(ConfiguredModule):

    def __init__(self, conf, modules, autoreload):
        import score.serve
        ConfiguredModule.__init__(self, score.serve)
        self.conf = conf
        self.modules = modules
        self.autoreload = autoreload

    def start(self):
        reload = ServerInstance(self).reload
        while reload:
            log.info('reloading')
            reload = ServerInstance(self).reload


class ServerInstance:

    def __init__(self, conf):
        self.conf = conf
        self.loop = asyncio.new_event_loop()
        self.controller = fork(self.loop, ServiceController, self.conf)
        if self.conf.autoreload:
            self.controller.on('restart', self.restart)
        self.reload = False
        self.controller.on('state-change', self.quit_if_stopped)
        # self.loop.set_debug(True)
        self.loop.add_signal_handler(signal.SIGINT, self.signal_handler_stop)
        self.loop.create_task(self.controller.start()).add_done_callback(
            lambda *_: log.info('started'))
        self.loop.run_forever()
        self.loop.remove_signal_handler(signal.SIGINT)

    def signal_handler_stop(self):
        log.info('Ctrl+C detected, stopping')
        self.loop.remove_signal_handler(signal.SIGINT)
        self.loop.create_task(self.controller.stop())
        self.reload = False

    def quit_if_stopped(self, states):
        if not all(state in (Service.State.STOPPED, Service.State.EXCEPTION)
                   for state in states.values()):
            return
        self.controller.off('state-change', self.quit_if_stopped)
        self.stop_loop()

    @asyncio.coroutine
    def restart(self):
        self.reload = True
        yield from self.controller.stop()
        self.stop_loop()

    def stop_loop(self, *_):
        pending_tasks = [t for t in asyncio.Task.all_tasks(self.loop)
                         if not t.done()]
        if not pending_tasks:
            self.loop.stop()
        else:
            task = pending_tasks.pop()
            task.add_done_callback(self.stop_loop)


class ServiceController(Backgrounded):

    def __init__(self, conf):
        self.conf = conf
        self._services = None
        self._changedetector = None

    def start(self):
        if not self._services:
            self._init_services()
        self._call_on_subservices('start')

    def pause(self):
        if not self._services:
            self._init_services()
        self._call_on_subservices('pause')

    def stop(self):
        if not self._services:
            return
        if self._changedetector:
            self._changedetector.stop(wait=False)
            self._changedetector = None
        self._call_on_subservices('stop')

    def service_states(self):
        if not self._services:
            self._init_services()
        result = OrderedDict()
        for name, service in self._services.items():
            result[name] = service.state
        return result

    @property
    @contextmanager
    def _acquire_service_locks(self):
        acquired = []
        try:
            for service in self._services.values():
                service.state_lock.acquire()
                acquired.append(service)
            yield
        finally:
            for service in acquired:
                service.state_lock.release()

    def _init_services(self):
        if self.conf.autoreload:
            self._changedetector = ChangeDetector()
            self._changedetector.observe(self.conf.conf)
            self._changedetector.add_callback(self.restart)
        try:
            self._collect_services()
            for service in self._services.values():
                service.register_state_change_listener(
                    self._service_state_changed)
        except Exception as e:
            if not self._changedetector:
                raise

            self._services.clear()
            self.conf.log.exception(e)
            if isinstance(e, SyntaxError):
                self._changedetector.observe(e.filename)
            else:
                for frame in traceback.extract_tb(e.__traceback__):
                    self._changedetector.observe(frame[0])

    def _service_state_changed(self, service, old, new):
        states = {service.name: service.state
                  for service in self._services.values()}
        self.trigger('state-change', states)
        if new == Service.State.EXCEPTION:
            self.conf.log.exception(service.exception)

    def _collect_services(self):
        self._services = OrderedDict()
        score = init_from_file(self.conf.conf)
        if self._changedetector:
            for file in parse_list(score.conf['score.init']['_files']):
                self._changedetector.observe(file)
        for mod in self.conf.modules:
            workers = score._modules[mod].score_serve_workers()
            if isinstance(workers, list):
                for i, worker in enumerate(workers):
                    name = '%s/%d' % (mod, i)
                    self._services[name] = Service(name, worker)
            elif isinstance(workers, dict):
                for name, worker in workers.items():
                    name = '%s/%s' % (mod, name)
                    self._services[name] = Service(name, worker)

    def restart(self, *_):
        self.trigger('restart')
        changedetector = self._changedetector
        self._changedetector = None
        if changedetector:
            changedetector.stop(wait=False)
        self.stop()

    def _call_on_subservices(self, func, *args):
        for service in self._services.values():
            getattr(service, func)(*args)
