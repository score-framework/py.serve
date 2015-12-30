import pyinotify
import os
import builtins
import sys


mask = pyinotify.IN_MODIFY | pyinotify.IN_CREATE | pyinotify.IN_DELETE | \
    pyinotify.IN_DELETE_SELF | pyinotify.IN_MOVED_FROM | pyinotify.IN_MOVE_SELF


class ChangeDetector:

    def __init__(self):
        self.observed_files = set()
        self.observed_dirs = set()
        self.file2modules = {}
        self.callbacks = []
        self.wm = pyinotify.WatchManager()
        self.notifier = pyinotify.ThreadedNotifier(self.wm, self)
        self._original_import = builtins.__import__
        builtins.__import__ = self._import
        for module in sys.modules.values():
            self.observe_module(module)
        self.start()

    def start(self):
        self.notifier.start()

    def stop(self):
        self.notifier.stop()
        self.notifier.join()

    def _import(self, *args, **kwargs):
        module = self._original_import(*args, **kwargs)
        self.observe_module(module)
        return module

    def observe_module(self, module):
        try:
            file = module.__file__
        except AttributeError:
            return
        else:
            if file:
                self.observe(file, module)

    def observe(self, file, module=None):
        if file in self.observed_files:
            return
        if not os.path.exists(file):
            return
        file = os.path.abspath(file)
        self.observed_files.add(file)
        dir = os.path.dirname(file)
        if dir in self.observed_dirs:
            return
        self.observed_dirs.add(dir)
        self.wm.add_watch(dir, mask, proc_fun=self._changed)
        if module:
            try:
                self.file2modules[file].add(module)
            except KeyError:
                self.file2modules[file] = {module}

    def onchange(self, callback):
        self.callbacks.append(callback)

    def _changed(self, event):
        file = event.pathname
        if file not in self.observed_files:
            return
        try:
            modules = self.file2modules[file]
        except KeyError:
            modules = []
        for callback in self.callbacks:
            callback(file, modules)
