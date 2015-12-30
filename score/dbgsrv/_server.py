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

from ._runner import Runner
from ._changedetect import ChangeDetector
import os
import importlib
import logging

log = logging.getLogger('score.dbgsrv')


class CallbackRunner(Runner):

    def __init__(self, callback):
        self.callback = callback

    def start(self):
        self.callback()


class Server:

    def __init__(self, runner):
        if callable(runner):
            runner = CallbackRunner(runner)
        self.runner = runner

    def start(self):
        changedetector = ChangeDetector()

        @changedetector.onchange
        def reload_modules(file, modules):
            for module in modules:
                importlib.reload(module)
        while True:
            childpid = os.fork()
            if childpid:  # parent
                try:
                    (_, status) = os.waitpid(childpid, 0)
                    if status == 200:
                        log.info('reloading ...')
                        continue
                    else:
                        break
                except KeyboardInterrupt:
                    break

            # this is the child
            @changedetector.onchange
            def exit(file, modules):
                self.runner.stop()
                os._exit(200)
            self.runner.prepare()
            self.runner.start()
        changedetector.stop()
