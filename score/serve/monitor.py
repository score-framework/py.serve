import asyncio
import json
from collections import OrderedDict


class ServiceMonitorProtocol(asyncio.Protocol):

    def __init__(self, conf):
        self._conf = conf
        self.server = None

    def connection_made(self, transport):
        self.transport = transport
        self._conf.loop.create_task(self._send_service_states_async())
        self.server.controller.on('state-change', self._state_change)

    def set_instance(self, server):
        assert self.server is None
        self.server = server
        self.server.controller.on('state-change', self._state_change)

    def clear_instance(self, reloading):
        assert self.server is not None
        self.server.controller.off('state-change', self._state_change)
        self.server = None

    def connection_lost(self, exc):
        self._conf._remove_monitor_connection(self)

    def _state_change(self, services):
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
