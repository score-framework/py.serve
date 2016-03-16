# Copyright © 2015 STRG.AT GmbH, Vienna, Austria
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

import abc
import select
import socket
import socketserver


class Runner:

    def start(self):
        pass

    def stop(self):
        pass


class SocketServerRunner(Runner, abc.ABC):

    def __init__(self):
        self.__running = False
        server = self._mkserver()
        assert isinstance(server, socketserver.BaseServer)
        self.__server = server

    def start(self):
        server = self.__server
        self.__running = True
        self.__intr_pair = socket.socketpair()
        while self.__running:
            try:
                sockets = (server.socket, self.__intr_pair[0])
                r, w, e = select.select(sockets, [], [])
                if self.__intr_pair[0] in r:
                    continue
            except InterruptedError:
                continue
            request, client_address = server.get_request()
            if not server.verify_request(request, client_address):
                continue
            try:
                server.process_request(request, client_address)
            except:
                server.handle_error(request, client_address)
                server.shutdown_request(request)
        self.__server.server_close()

    def stop(self):
        if not self.__running:
            return
        self.__running = False
        self.__intr_pair[1].send(b'0')

    @abc.abstractmethod
    def _mkserver(self):
        pass


class CallbackRunner(Runner):

    def __init__(self, callback):
        self.callback = callback

    def start(self):
        self.callback()
