import abc
import threading
import asyncio
from .worker import Worker
import concurrent.futures

try:
    from asyncio import run_coroutine_threadsafe
except ImportError:

    def run_coroutine_threadsafe(coro, loop):
        future = concurrent.futures.Future()

        def done(task_future):
            exception = task_future.exception()
            if exception:
                future.set_exception(exception)
            else:
                future.set_result(task_future.result())

        def queue_task():
            try:
                task_future = asyncio.async(coro, loop=loop)
                task_future.add_done_callback(done)
            except Exception as exc:
                if future.set_running_or_notify_cancel():
                    future.set_exception(exc)
                raise

        loop.call_soon_threadsafe(queue_task)
        return future


class AsyncioWorker(Worker):
    """
    A specialized worker for :mod:`asyncio` servers.

    This base class will add a layer of abstraction to eliminate threading.
    Subclasses can override the functions :meth:`_prepare`, :meth:`_start`,
    :meth:`_pause`, :meth:`_stop` and :meth:`_cleanup`. These functions will be
    called inside a running event loop (which can be accessed as ``self.loop``)
    and can be regular functions or :term:`coroutines <coroutine>`.

    Example implementation:

    .. code-block:: python

        class EchoServer(AsyncioWorker):

            @asyncio.coroutine
            def _start(self):
                self.server = yield from self.loop.create_server(myserver)

            def _pause(self):
                self.server.close()
    """

    def prepare(self):
        event = threading.Event()
        threading.Thread(target=self.__start_loop, args=(event,)).start()
        event.wait()
        event.clear()
        future = run_coroutine_threadsafe(self.__prepare(), self.loop)
        future.add_done_callback(lambda future: event.set())
        exception = future.exception()
        if exception:
            raise exception
        event.wait()

    def start(self):
        event = threading.Event()
        future = run_coroutine_threadsafe(self.__start(), self.loop)
        future.add_done_callback(lambda future: event.set())
        exception = future.exception()
        if exception:
            raise exception
        event.wait()

    def pause(self):
        event = threading.Event()
        future = run_coroutine_threadsafe(self.__pause(), self.loop)
        future.add_done_callback(lambda future: event.set())
        exception = future.exception()
        if exception:
            raise exception
        event.wait()

    def stop(self):

        def stop_loop(future):
            self.loop.call_soon_threadsafe(self.__stop_loop, event)

        event = threading.Event()
        future = run_coroutine_threadsafe(self.__stop(), self.loop)
        future.add_done_callback(stop_loop)
        exception = future.exception()
        if exception:
            raise exception
        event.wait()

    def cleanup(self, exception):
        if not self.loop.is_running():
            return

        def stop_loop(future):
            self.loop.call_soon_threadsafe(self.__stop_loop, event)

        event = threading.Event()
        future = run_coroutine_threadsafe(
            self.__cleanup(exception), self.loop)
        future.add_done_callback(stop_loop)
        event.wait()

    def _prepare(self):
        """
        Equivalent of :meth:`Worker.prepare`.

        This function will be called inside a running event loop.
        """
        pass

    @abc.abstractmethod
    def _start(self):
        """
        Equivalent of :meth:`Worker.start`.

        This function will be called inside a running event loop.
        """
        pass

    @abc.abstractmethod
    def _pause(self):
        """
        Equivalent of :meth:`Worker.pause`.

        This function will be called inside a running event loop.
        """
        pass

    def _stop(self):
        """
        Equivalent of :meth:`Worker.stop`.

        This function will be called inside a running event loop.
        """
        pass

    @abc.abstractmethod
    def _cleanup(self, exception):
        """
        Equivalent of :meth:`Worker.cleanup`.

        This function will be called inside a running event loop.
        """
        pass

    def __start_loop(self, event):
        self.loop = asyncio.new_event_loop()
        event.set()
        self.loop.run_forever()

    @asyncio.coroutine
    def __prepare(self):
        result = self._prepare()
        if asyncio.iscoroutine(result):
            result = yield from result

    @asyncio.coroutine
    def __start(self):
        result = self._start()
        if asyncio.iscoroutine(result):
            result = yield from result

    @asyncio.coroutine
    def __pause(self):
        result = self._pause()
        if asyncio.iscoroutine(result):
            result = yield from result

    @asyncio.coroutine
    def __stop(self):
        result = self._stop()
        if asyncio.iscoroutine(result):
            result = yield from result

    @asyncio.coroutine
    def __cleanup(self, exception):
        result = self._cleanup(exception)
        if asyncio.iscoroutine(result):
            result = yield from result

    def __stop_loop(self, event):
        if not self.loop.is_running():
            event.set()
            return

        def stop(future=None):
            pending_tasks = [t for t in asyncio.Task.all_tasks(self.loop)
                             if not t.done()]
            if pending_tasks:
                task = pending_tasks.pop()
                task.add_done_callback(stop)
            else:
                self.loop.stop()
                event.set()

        stop()
