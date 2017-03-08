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

import watchdog.events
import watchdog.observers
import watchdog.utils
import os
import sys
import logging
import threading
import time

log = logging.getLogger('score.serve.changedetector')


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


class ChangeDetector(watchdog.events.FileSystemEventHandler):

    def __init__(self, *, autostart=True):
        self.callbacks = []
        self.observer = Observer()
        # set thread name
        self.observer.name = 'ChangeDetector'
        self.gatherer = threading.Thread(target=self._gather_modules)
        self.running = False
        self._observer_lock = threading.Lock()
        if autostart:
            self.start()

    def _gather_modules(self):
        while self.running:
            for module in list(sys.modules.values()):
                self.observe_module(module)
                if not self.running:
                    return
            time.sleep(0.5)

    def start(self):
        self.observed_files = set()
        self.observed_dirs = {}
        self.observed_modules = set()
        self.file2modules = {}
        self.running = True
        self.gatherer.start()
        with self._observer_lock:
            self.observer.start()

    def stop(self, wait=True):
        if not self.running:
            return
        self.running = False
        self.observer.stop()
        if wait:
            if threading.current_thread() != self.observer:
                self.observer.join()
            self.gatherer.join()

    def observe_module(self, module):
        if module in self.observed_modules:
            return
        self.observed_modules.add(module)
        try:
            file = module.__file__
        except AttributeError:
            return
        if not file:
            return
        old = None
        while not os.path.isfile(file):
            old = file
            file = os.path.dirname(file)
            if file == old:
                return
        if file[-4:] in ('.pyc', '.pyo'):
            file = file[:-1]
        self.observe(file, module)

    def observe(self, file, module=None):
        if not os.path.exists(file):
            return
        file = os.path.abspath(file)
        if file in self.observed_files:
            return
        self.observed_files.add(file)
        dir = os.path.dirname(file)
        if module:
            try:
                self.file2modules[file].add(module)
            except KeyError:
                self.file2modules[file] = {module}
        if dir in self.observed_dirs:
            return
        if self.running:
            # safeguarding against startup errors: self.observer.schedule fails
            # with errno EBADF (bad file descriptor) if the thread is not
            # running.  this happens most commonly when the Worker has a startup
            # issue, when lots of new watches are added.
            with self._observer_lock:
                for other in self.observed_dirs.copy():
                    if dir.startswith(other):
                        return
                    if other.startswith(dir):
                        log.debug('unscheduling %s in favor of %s' %
                                  (other, dir))
                        self.observer.unschedule(self.observed_dirs[other])
                        del self.observed_dirs[other]
                log.debug('scheduling %s' % (dir))
                self.observed_dirs[dir] = \
                    self.observer.schedule(self, dir, recursive=True)

    def add_callback(self, callback):
        self.callbacks.append(callback)
        return callback

    def remove_callback(self, callback):
        self.callbacks.remove(callback)
        return callback

    def clear_callbacks(self):
        self.callbacks = []

    def on_any_event(self, event):
        if isinstance(event, watchdog.events.DirCreatedEvent):
            return
        if isinstance(event, watchdog.events.DirModifiedEvent):
            return
        FileCreatedEvent = watchdog.events.FileCreatedEvent
        file = event.src_path
        if file in self.observed_files:
            try:
                modules = self.file2modules[file]
            except KeyError:
                modules = []
            log.debug('file changed: %s' % file)
        elif isinstance(event, FileCreatedEvent) and file.endswith('.py'):
            log.debug('new file: %s' % file)
            modules = []
        else:
            return
        for callback in self.callbacks:
            callback(file, modules)
