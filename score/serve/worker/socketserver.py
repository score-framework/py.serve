import abc
import functools
import select
import socket
import socketserver
import threading

from .worker import Worker, transitions
from ..service import Service


class SocketServerWorker(Worker):

    final_states = (
        Service.State.STOPPED, Service.State.STOPPING, Service.State.EXCEPTION)

    def __init__(self):
        self.__server = None
        self.__num_running = 0
        self.__request_lock = threading.Condition()

    def prepare(self):
        self.__intr_pair = socket.socketpair()
        server = self._mkserver()
        assert isinstance(server, socketserver.BaseServer)
        self.__server = server
        original_shutdown = server.shutdown_request

        @functools.wraps(server.shutdown_request)
        def shutdown_request(*args, **kwargs):
            original_shutdown(*args, **kwargs)
            with self.__request_lock:
                self.__num_running -= 1
                self.__request_lock.notify()

        server.shutdown_request = shutdown_request
        threading.Thread(target=self._loop).start()

    def start(self):
        self._interrupt_loop()

    @transitions(Service.State.PREPARING)
    @transitions(Service.State.RUNNING)
    def stop(self):
        self._interrupt_loop()

    def pause(self):
        self._interrupt_loop()

    def cleanup(self, exception):
        if self.__server:
            try:
                self.__server.server_close()
                self.__server = None
            except:
                pass

    def _loop(self):
        if not self.__intr_pair:
            return
        while self.state not in self.final_states:
            try:
                with self.__request_lock:
                    if self.state == Service.State.RUNNING:
                        sockets = (self.__server.socket, self.__intr_pair[0],)
                    else:
                        sockets = (self.__intr_pair[0],)
                r, w, e = select.select(sockets, [], [])
                if self.__intr_pair[0] in r:
                    self.__intr_pair[0].recv(2**10)
                    continue
            except InterruptedError:
                continue
            self._process_request()
        self.__server.server_close()
        self.__server = None

    def _process_request(self):
        server = self.__server
        with self.__request_lock:
            if self.state in self.final_states:
                # we're stopping, abort operation
                return
            request, client_address = server.get_request()
            self.__num_running += 1
        if not server.verify_request(request, client_address):
            return
        try:
            server.process_request(request, client_address)
        except:
            server.handle_error(request, client_address)
            server.shutdown_request(request)

    def _interrupt_loop(self):
        with self.__request_lock:
            if not self.__intr_pair:
                return
            self.__intr_pair[1].send(b'0')
            while self.__num_running:
                self.__request_lock.wait()

    @abc.abstractmethod
    def _mkserver(self):
        pass
