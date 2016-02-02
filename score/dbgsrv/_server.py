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

from ._runner import CallbackRunner
from ._changedetect import ChangeDetector
import os
import logging
import signal

log = logging.getLogger('score.dbgsrv')


class Server:

    def __init__(self, runner):
        if callable(runner):
            runner = CallbackRunner(runner)
        self.runner = runner
        self.running = False

    def start(self):
        self.running = True
        while self.running:
            childpid = os.fork()
            if childpid:
                self._run_parent(childpid)
            else:
                self._run_child()

    def _run_child(self):
        changedetector = ChangeDetector()
        started = False
        reloading = False
        try:
            def stop(*_):
                if started:
                    self.runner.stop()

            @changedetector.onchange
            def change(*args, **kwargs):
                nonlocal reloading
                if not reloading:
                    reloading = True
                    stop()
            signal.signal(signal.SIGINT, stop)
            self.runner.prepare()
            if not reloading:
                started = True
                self.runner.start()
            if reloading:
                os._exit(200)
            else:
                os._exit(0)
        except Exception:
            if started:
                self.runner.stop()
            raise
        finally:
            changedetector.stop()

    def _run_parent(self, childpid):
        try:
            (_, status) = os.waitpid(childpid, 0)
            if status >> 8 == 200:
                log.info('reloading ...')
            else:
                self.running = False
        except KeyboardInterrupt:
            os.waitpid(childpid, 0)
            self.running = False
        except Exception:
            os.kill(childpid, signal.SIGINT)
            os.waitpid(childpid, 0)
            self.running = False
            raise
