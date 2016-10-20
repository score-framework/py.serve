import abc
import threading

from .worker import Worker


class SimpleWorker(Worker):
    """
    A simplified worker base class, that hides all the ugly threading logic.

    You can subclass this and implement a ``loop`` function that periodically
    checks this.running:

    .. code-block:: python

        class Spammer(SimpleWorker):

            def loop():
                while self.running:
                    print('spam!')
                    time.sleep(1)
    """

    def __init__(self):
        self.__lock = threading.Lock()
        self.__thread = None

    def prepare(self):
        pass

    def start(self):
        self.__thread = threading.Thread(target=self.__loop).start()

    def pause(self):
        with self.__lock:
            self.__running = False
        if self.__thread:
            self.__thread.join()
            self.__thread = None

    def stop(self):
        self.pause()

    def __loop(self):
        with self.__lock:
            self.__running = True
        try:
            self.loop()
        except Exception as e:
            self.service.set_exception(e)

    def cleanup(self, exception):
        pass

    @property
    def running(self):
        with self.__lock:
            return self.__running

    @abc.abstractmethod
    def loop(self):
        pass
