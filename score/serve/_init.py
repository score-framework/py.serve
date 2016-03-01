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


defaults = {
    'auto_reload': False,
    'modules': []
}


def init(confdict):
    conf = defaults.copy()
    conf.update(confdict)
    modules = parse_list(conf['modules'])
    if not modules:
        import score.serve
        raise InitializationError(score.serve, 'No modules configured')
    auto_reload = parse_bool(conf['auto_reload'])
    return ConfiguredServeModule(conf['conf'], modules, auto_reload)


class ConfiguredServeModule(ConfiguredModule):

    def __init__(self, conf, modules, auto_reload):
        import score.serve
        super().__init__(score.serve)
        self.conf = conf
        self.modules = modules
        self.auto_reload = auto_reload
        self.running = False

    def _collect_runners(self, score):
        return [score._modules[mod] for mod in self.modules]

    def start(self):
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
        if self.auto_reload:
            changedetector = ChangeDetector()
            changedetector.observe(self.conf)
        runners = []
        score = init_from_file(self.conf)
        if changedetector:
            for file in parse_list(score.conf['score.init']['_based_on']):
                changedetector.observe(file)
        for mod in self.modules:
            runners += score._modules[mod].get_serve_runners()
        errors = []
        reloading = False
        start_barrier = threading.Barrier(len(runners) + 1)
        result_queue = queue.Queue()
        threads = []
        for runner in runners:
            threads.append(RunnerThread(runner, start_barrier, result_queue))

        def stop():
            for thread in threads:
                thread.stop()
        try:
            for thread in threads:
                thread.start()
            if changedetector:
                @changedetector.onchange
                def change(*args, **kwargs):
                    global reloading
                    reloading = True
                    stop()
            # wait for all threads to start
            start_barrier.wait()
            for thread in threads:
                e = result_queue.get()
                if e:
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

    def __init__(self, runner, start_barrier, result_queue):
        super().__init__()
        self.runner = runner
        self.start_barrier = start_barrier
        self.result_queue = result_queue
        self.state = self.State.PREPARED

    def run(self):
        self.state = self.State.PREPARED
        self.start_barrier.wait()
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
