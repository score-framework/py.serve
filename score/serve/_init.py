# Copyright © 2015-2017 STRG.AT GmbH, Vienna, Austria
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

import socket
import asyncio
from score.init import (
    ConfiguredModule, parse_list, parse_bool, parse_host_port, init_from_file,
    InitializationError)
from .service import Service
from ._forked import fork, Backgrounded
from ._changedetect import ChangeDetector
from collections import OrderedDict
from contextlib import contextmanager
import traceback
import signal
import logging
from .worker import Worker
try:
    import uvloop
except ImportError:
    pass
else:
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())


log = logging.getLogger('score.serve')

defaults = {
    'autoreload': False,
    'modules': [],
    'monitor': None,
}


def init(confdict):
    """
    Initializes this module acoording to :ref:`our module initialization
    guidelines <module_initialization>` with the following configuration keys:

    :confkey:`autoreload` :confdefault:`False`
        When set to :func:`true <score.init.parse_bool>`, the server will
        automatically reload whenever it detects a change in one of the python
        files, that are in use.

    :confkey:`modules`
        The :func:`list <score.init.parse_list>` of modules to serve. This need
        to be a list of module aliases, i.e. the same name, with which you
        configured the module with ("score.http" becomes "http" if not specified
        otherwise.)

    """
    conf = defaults.copy()
    conf.update(confdict)
    modules = parse_list(conf['modules'])
    if not modules:
        import score.serve
        raise InitializationError(score.serve, 'No modules configured')
    autoreload = parse_bool(conf['autoreload'])
    monitor_host_port = None
    if conf['monitor']:
        monitor_host_port = parse_host_port(conf['monitor'])
    return ConfiguredServeModule(conf['conf'], modules, autoreload,
                                 monitor_host_port)


class ConfiguredServeModule(ConfiguredModule):
    """
    This module's :class:`configuration class`
    """

    def __init__(self, conf, modules, autoreload, monitor_host_port):
        import score.serve
        ConfiguredModule.__init__(self, score.serve)
        self.conf = conf
        self.modules = modules
        self.autoreload = autoreload
        self.monitor_connections = []
        self.monitor_host_port = monitor_host_port
        self.loop = asyncio.new_event_loop()
        self.loop.getaddrinfo = self._getaddrinfo

    def _getaddrinfo(self, host, port, *, family=0, type=0, proto=0, flags=0):
        """
        The default loop implementation uses loop.run_in_executor to resolve
        addresses, which in turn uses a ThreadPoolExecutor or a
        ProcessPoolExecutor. Neither of these executors is feasable, since
        forking would break both of them. This is the reason we are resolving
        hostnames synchronously.
        """
        @asyncio.coroutine
        def getaddrinfo():
            return socket.getaddrinfo(host, port, family, type, proto, flags)
        return getaddrinfo()

    def start(self):
        """
        Starts all configured workers and runs until the workers stop or
        <CTRL-C> ist pressed. Will optionally reload the server, if it was
        configured to do so via ``autoreload``.
        """
        if self.monitor_host_port:
            coroutine = self.loop.create_server(self._create_monitor_connection,
                                                host=self.monitor_host_port[0],
                                                port=self.monitor_host_port[1])
            self.loop.create_task(coroutine)
        restart_server = True
        while restart_server:
            self.instance = _ServerInstance(self)
            for connection in self.monitor_connections:
                connection.set_instance(self.instance)
            self.instance.run_until_stopped()
            reload = self.instance.reload
            self.instance = None
            for connection in self.monitor_connections:
                connection.clear_instance(reload)
            if reload:
                log.info('reloading')
            else:
                restart_server = False

    def _create_monitor_connection(self):
        from .monitor import ServiceMonitorProtocol
        connection = ServiceMonitorProtocol(self)
        if self.instance:
            connection.set_instance(self.instance)
        self.monitor_connections.append(connection)
        return connection

    def _remove_monitor_connection(self, connection):
        self.monitor_connections.remove(connection)


class _ServerInstance:

    def __init__(self, conf):
        self.conf = conf
        self.loop = conf.loop
        self.controller = fork(self.loop, ServiceController, self.conf)

    def run_until_stopped(self):
        if self.conf.autoreload:
            self.controller.on('restart', self.restart)
        self.reload = None
        self.controller.on('state-change', self.quit_if_stopped)
        # self.loop.set_debug(True)
        self.loop.add_signal_handler(signal.SIGINT, self.signal_handler_stop)
        self.__start_1()
        self.__stopping = False
        self.loop.run_forever()
        self.loop.remove_signal_handler(signal.SIGINT)

    def __start_1(self):
        task = self.loop.create_task(self.controller.pause())
        task.add_done_callback(self.__start_2)

    def __start_2(self, future):
        exc = future.exception()
        if not exc:
            task = self.loop.create_task(self.controller.start())
            task.add_done_callback(lambda *_: log.info('started'))

    def signal_handler_stop(self):
        log.info('Ctrl+C detected, stopping')
        self.loop.remove_signal_handler(signal.SIGINT)
        self.loop.create_task(self.stop())
        self.reload = False

    @asyncio.coroutine
    def stop(self):
        if self.__stopping:
            return
        self.__stopping = True
        event = asyncio.Event(loop=self.loop)

        def signal_if_all_stopped(states):
            if not self.all_services_stopped(states):
                return
            self.controller.off('state-change', signal_if_all_stopped)
            task = self.loop.create_task(self.controller.kill())
            task.add_done_callback(wait_on_pending_tasks)

        def wait_on_pending_tasks(future):
            coro = self.wait_on_pending_tasks([current_task])
            task = self.loop.create_task(coro)
            task.add_done_callback(signal_done)

        def signal_done(future):
            event.set()

        current_task = asyncio.Task.current_task(loop=self.loop)
        states = yield from self.controller.service_states()
        if not self.all_services_stopped(states):
            self.controller.off('state-change', self.quit_if_stopped)
            self.controller.on('state-change', signal_if_all_stopped)
            yield from self.controller.stop()
            yield from event.wait()
        self.loop.stop()

    def all_services_stopped(self, states):
        if isinstance(states, dict):
            states = states.values()
        return all(state in (Service.State.STOPPED, Service.State.EXCEPTION)
                   for state in states)

    def quit_if_stopped(self, states):
        if not self.all_services_stopped(states):
            return
        self.controller.off('state-change', self.quit_if_stopped)
        self.loop.create_task(self.stop())

    def restart(self):
        if self.reload is None:
            self.reload = True
        self.loop.create_task(self.stop())

    @asyncio.coroutine
    def wait_on_pending_tasks(self, ignored_tasks=None):
        event = asyncio.Event(loop=self.loop)
        if ignored_tasks is None:
            ignored_tasks = []
        ignored_tasks.append(asyncio.Task.current_task(loop=self.loop))

        def check_pending(future=None):
            if future:
                # collect the task exception, otherwise the asyncio library will
                # complain
                future.exception()
            pending_tasks = [t for t in asyncio.Task.all_tasks(self.loop)
                             if not t.done()]
            if len(pending_tasks) <= len(ignored_tasks):
                event.set()
                return
            task = pending_tasks.pop()
            while task in ignored_tasks:
                task = pending_tasks.pop()
            task.add_done_callback(check_pending)

        check_pending()
        yield from event.wait()


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
            return []
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
            self.conf.log.exception(e)
            if self._changedetector:
                self._services.clear()
                if isinstance(e, SyntaxError):
                    self._changedetector.observe(e.filename)
                else:
                    for frame in traceback.extract_tb(e.__traceback__):
                        self._changedetector.observe(frame[0])
            raise

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
        for desc in self.conf.modules:
            for name, worker in self._iter_workers(score, desc):
                self._services[name] = Service(name, worker)

    def _iter_workers(self, score, descriptor):
        if ':' in descriptor:
            module, names = tuple(map(str.strip, descriptor.split(':', 1)))
            names = tuple(map(str.strip, names.split(',')))
        else:
            module = descriptor
            names = None
        response = score._modules[module].score_serve_workers()
        if isinstance(response, Worker):
            yield module, response
        elif isinstance(response, list):
            if len(response) == 1:
                yield module, response[0]
                return
            for i, worker in enumerate(response):
                name = '%s:%d' % (module, i)
                yield name, worker
        elif isinstance(response, dict):
            for name, worker in response.items():
                if names is not None and name not in names:
                    continue
                name = '%s:%s' % (module, name)
                yield name, worker
        else:
            raise RuntimeError(
                'Invalid return value of %s.score_serve_workers(): %s' %
                (module, repr(response)))

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
