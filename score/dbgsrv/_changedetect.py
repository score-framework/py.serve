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

import pyinotify
import os
import sys
import logging
import threading
import time

log = logging.getLogger('score.dbgsrv')
mask = pyinotify.IN_MODIFY | pyinotify.IN_CREATE | pyinotify.IN_DELETE | \
    pyinotify.IN_DELETE_SELF | pyinotify.IN_MOVED_FROM | pyinotify.IN_MOVE_SELF


class ChangeDetector:

    def __init__(self, *, autostart=True):
        self.callbacks = []
        self.wm = pyinotify.WatchManager()
        self.notifier = pyinotify.ThreadedNotifier(self.wm, self)
        self.gatherer = threading.Thread(target=self._gather_modules)
        self.running = False
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
        self.observed_dirs = set()
        self.file2modules = {}
        self.running = True
        self.gatherer.start()
        self.notifier.start()

    def stop(self):
        if not self.running:
            return
        self.running = False
        self.notifier.stop()
        self.notifier.join()
        self.gatherer.join()

    def observe_module(self, module):
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
        if file in self.observed_files:
            return
        if not os.path.exists(file):
            return
        file = os.path.abspath(file)
        self.observed_files.add(file)
        dir = os.path.dirname(file)
        if module:
            try:
                self.file2modules[file].add(module)
            except KeyError:
                self.file2modules[file] = {module}
        if dir in self.observed_dirs:
            return
        self.observed_dirs.add(dir)
        if self.running:
            # safeguarding against startup errors: self.wm.add_watch fails with
            # errno EBADF (bad file descriptor) if the thread is not running.
            # this happens most commonly when the Runner has a startup issue,
            # when lots of new watches are added.
            self.wm.add_watch(dir, mask, proc_fun=self._changed)

    def onchange(self, callback):
        self.callbacks.append(callback)
        return callback

    def _changed(self, event):
        file = event.pathname
        if file not in self.observed_files:
            return
        try:
            modules = self.file2modules[file]
        except KeyError:
            modules = []
        log.debug('file changed: %s' % file)
        for callback in self.callbacks:
            callback(file, modules)
