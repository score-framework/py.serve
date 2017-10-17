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

import asyncio
import json
from collections import OrderedDict
import warnings


class ServiceMonitorProtocol(asyncio.Protocol):

    def __init__(self, conf):
        self._conf = conf
        self.server = None
        self.input_buffer = b''

    def connection_made(self, transport):
        self.transport = transport

    def data_received(self, data):
        self.input_buffer = self.input_buffer + data
        self.handle_input()

    def handle_input(self):
        lines = self.input_buffer.split(b'\n')
        self.input_buffer = lines.pop()
        if not self.server:
            return
        for command in lines:
            command = command.strip()
            if command == b'start':
                self._conf.loop.create_task(self.server.controller.start())
            elif command == b'restart':
                self.server.restart()
            elif command == b'pause':
                self._conf.loop.create_task(self.server.controller.pause())
            elif command == b'stop':
                self._conf.loop.create_task(self.server.stop())
            else:
                warnings.warn('Received invalid command: ' + command)

    def set_instance(self, server):
        assert self.server is None
        self.server = server
        self.server.controller.on('state-change', self._state_change)
        self._conf.loop.create_task(self._send_service_states_async())

    def clear_instance(self, reloading):
        assert self.server is not None
        self.server.controller.off('state-change', self._state_change)
        self.server = None
        if not self.transport:
            return
        if reloading:
            self._send(json.dumps('reloading'))
        else:
            self._send(json.dumps('shutting down'))

    def connection_lost(self, exc):
        self.transport = None
        if self.server:
            self.clear_instance(False)
        self._conf._remove_monitor_connection(self)

    def _state_change(self, services):
        if not services:
            return
        if not self.transport:
            return
        services = OrderedDict((k, v.value) for k, v in services.items())
        self._send(json.dumps(services))

    @asyncio.coroutine
    def _send_service_states_async(self):
        if self.server is None:
            return
        services = yield from self.server.controller.service_states()
        self._state_change(services)

    def _send(self, data):
        self.transport.write(data.encode('UTF-8') + b'\n')
