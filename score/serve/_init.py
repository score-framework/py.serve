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

from score.init import (
    ConfiguredModule, parse_list, parse_bool, init_from_file,
    InitializationError)
import os
import signal
import threading
import queue
from ._changedetect import ChangeDetector
import enum
import logging
import traceback

log = logging.getLogger('score.serve')


defaults = {
    'autoreload': False,
    'modules': []
}


def init(confdict):
    conf = defaults.copy()
    conf.update(confdict)
    modules = parse_list(conf['modules'])
    if not modules:
        import score.serve
        raise InitializationError(score.serve, 'No modules configured')
    autoreload = parse_bool(conf['autoreload'])
    return ConfiguredServeModule(conf['conf'], modules, autoreload)


class ConfiguredServeModule(ConfiguredModule):

    def __init__(self, conf, modules, autoreload):
        import score.serve
        super().__init__(score.serve)
        self.conf = conf
        self.modules = modules
        self.autoreload = autoreload
        self.running = False

    def _collect_runners(self, score):
        return [score._modules[mod] for mod in self.modules]

    def start(self):
        self.running = True
        while self.running:
            childpid = os.fork()
            if childpid:
                self._run_parent(childpid)
            else:
                self._run_child()

    def _run_parent(self, childpid):
        try:
            (_, status) = os.waitpid(childpid, 0)
            if status >> 8 != 200:
                self.running = False
        except KeyboardInterrupt:
            os.waitpid(childpid, 0)
            self.running = False
        except Exception:
            os.kill(childpid, signal.SIGINT)
            os.waitpid(childpid, 0)
            self.running = False
            raise

    def _run_child(self):
        changedetector = None
        if self.autoreload:
            changedetector = ChangeDetector()
            changedetector.observe(self.conf)
        runners = []
        reload_condition = threading.Condition()
        try:
            score = init_from_file(self.conf)
        except Exception as e:
            if not changedetector:
                raise
            log.exception(e)
            for frame in traceback.extract_tb(e.__traceback__):
                changedetector.observe(frame[0])

            @changedetector.add_callback
            def change(*args, **kwargs):
                # use a threading.Condition here
                nonlocal reload_condition
                with reload_condition:
                    reload_condition.notify()
            with reload_condition:
                reload_condition.wait()
            os._exit(200)
        if changedetector:
            for file in parse_list(score.conf['score.init']['_based_on']):
                changedetector.observe(file)
        for mod in self.modules:
            runners += score._modules[mod].get_serve_runners()
        errors = []
        reloading = False
        result_queue = queue.Queue()
        threads = []
        for runner in runners:
            threads.append(RunnerThread(runner, result_queue))
        stopping = False

        def stop():
            nonlocal stopping
            if stopping:
                return
            stopping = True
            for thread in threads:
                thread.stop()
        try:
            for thread in threads:
                thread.start()
            if changedetector:
                @changedetector.add_callback
                def change1(*args, **kwargs):
                    nonlocal reloading
                    reloading = True
                    stop()
            for thread in threads:
                e = result_queue.get()
                if not e:
                    continue
                if not stopping and changedetector:
                    changedetector.clear_callbacks()

                    @changedetector.add_callback
                    def change2(*args, **kwargs):
                        # use a threading.Condition here
                        nonlocal reloading
                        nonlocal reload_condition
                        with reload_condition:
                            reload_condition.notify()
                        reloading = True
                errors.append(e)
                stop()
        except KeyboardInterrupt:
            stop()
        finally:
            if changedetector:
                changedetector.stop()
            for thread in threads:
                thread.join()
        if errors:
            if reloading:
                with reload_condition:
                    reload_condition.wait()
                os._exit(200)
            raise errors[0]
        if reloading:
            os._exit(200)
        else:
            os._exit(0)


class RunnerThread(threading.Thread):

    class State(enum.Enum):
        PREPARED = 0
        WAITING = 1
        RUNNING = 2
        ABORTED = 3
        SUCCESS = 4
        EXCEPTION = 5

    def __init__(self, runner, result_queue):
        super().__init__()
        self.runner = runner
        self.result_queue = result_queue
        self.state = self.State.PREPARED

    def run(self):
        self.state = self.State.PREPARED
        if self.state != self.State.PREPARED:
            return
        self.state = self.State.RUNNING
        try:
            self.runner.start()
            self.state = self.State.SUCCESS
            self.result_queue.put(None)
        except Exception as e:
            self.state = self.State.EXCEPTION
            self.runner.stop()
            self.result_queue.put(e)

    def stop(self):
        if self.state == self.State.PREPARED:
            self.state = self.State.ABORTED
        elif self.state == self.State.RUNNING:
            self.runner.stop()
