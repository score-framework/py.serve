from .worker import Worker
import watchdog.observers
import watchdog.events
import watchdog.utils
import os
import warnings
import threading


Observer = watchdog.observers.Observer

if Observer.__name__ == 'InotifyObserver':
    import watchdog.observers.inotify

    # The inotify original observer has a small delay for pairing IN_MOVED_FROM
    # and IN_MOVE_TO events. Since we do not care whether something was moved
    # *into* or *out of* our watches, we will override this behaviour to achieve
    # the fastest possible reload times.

    class InotifyBuffer(watchdog.observers.inotify.InotifyBuffer):

        def __init__(self, *args, **kwargs):
            self.delay = 0
            super().__init__(*args, **kwargs)

        def close(self):
            self.stop()

    class InotifyEmitter(watchdog.observers.inotify.InotifyEmitter):

        def on_thread_start(self):
            path = watchdog.utils.unicode_paths.encode(self.watch.path)
            self._inotify = InotifyBuffer(path, self.watch.is_recursive)

    class InotifyObserver(watchdog.observers.inotify.InotifyObserver):

        def __init__(self, *args, **kwargs):
            watchdog.observers.api.BaseObserver.__init__(
                self, *args, emitter_class=InotifyEmitter, **kwargs)

    Observer = InotifyObserver


class FileWatcherWorker(Worker, watchdog.events.FileSystemEventHandler):
    """
    The implementation of a single service.

    The worker will be wrapped in :class:`Service` objects before being started.
    """

    def watch(self, path):
        if not os.path.exists(path):
            warnings.warn('Cannot watch "%s": path does not exist' % path)
            return
        if os.path.isfile(path):
            dir = os.path.dirname(path)
            self.target_files.append(path)
        else:
            dir = path
            self.target_dirs.append(path)
        if dir in self.observed_dirs:
            return
        with self._observer_lock:
            for other in self.observed_dirs.copy():
                if dir.startswith(other):
                    return
                if other.startswith(dir):
                    if self.observer:
                        self.observer.unschedule(self.observed_dirs[other])
                    del self.observed_dirs[other]
            if self.observer:
                self.observed_dirs[dir] = \
                    self.observer.schedule(self, dir, recursive=True)
            else:
                self.observed_dirs[dir] = None

    def prepare(self):
        self._observer_lock = threading.Lock()
        self.observer = None
        self.observed_dirs = {}
        self.target_files = []
        self.target_dirs = []

    def start(self):
        self.observer = Observer()
        for dir in self.observed_dirs:
            self.observed_dirs[dir] = \
                self.observer.schedule(self, dir, recursive=True)
        self.observer.start()

    def stop(self):
        pass

    def pause(self):
        with self._observer_lock:
            self.observer.stop()
        self.observer.join()
        self.observer = None

    def cleanup(self, exception):
        if hasattr(self, '_observer_lock'):
            with self._observer_lock:
                if hasattr(self, 'observer'):
                    self.observer.stop()
                    self.observer.join()

    def on_any_event(self, event):
        relevant = hasattr(event, 'src_path') and (
            event.src_path in self.target_files or
            any(event.src_path.startswith(dir) for dir in self.target_dirs))
        if relevant:
            self.changed(event.src_path)
