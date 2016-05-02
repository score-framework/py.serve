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
        try:
            runners = self._collect_runners(changedetector)
            errors = []
            reloading = False
            stopping = False
            result_queue = queue.Queue()
            reload_condition = threading.Condition()
            threads = list(
                RunnerThread(runner, result_queue) for runner in runners)
        except KeyboardInterrupt:
            os._exit(0)
        try:
            for thread in threads:
                thread.start()

            def stop():
                nonlocal stopping
                if stopping:
                    return
                stopping = True
                for thread in threads:
                    thread.stop()
            log.info('ready to serve!')

            if changedetector:
                @changedetector.add_callback
                def reload1(*args, **kwargs):
                    nonlocal reloading
                    if reloading:
                        return
                    log.info('Detected file change, reloading ...')
                    reloading = True
                    stop()
            for thread in threads:
                e = result_queue.get()
                if not e:
                    continue
                if not stopping and changedetector:
                    log.exception(e)
                    reloading = True
                    for frame in traceback.extract_tb(e.__traceback__):
                        changedetector.observe(frame[0])
                    changedetector.clear_callbacks()

                    @changedetector.add_callback
                    def reload2(*args, **kwargs):
                        nonlocal reloading
                        nonlocal reload_condition
                        log.info('Detected file change, reloading ...')
                        with reload_condition:
                            reload_condition.notify()
                errors.append(e)
                stop()
        except KeyboardInterrupt:
            stop()
            reloading = False
        finally:
            if changedetector:
                changedetector.stop()
            for thread in threads:
                thread.join()
        if errors:
            if changedetector and reloading:
                with reload_condition:
                    reload_condition.wait()
                os._exit(200)
            raise errors[0]
        if reloading:
            os._exit(200)
        else:
            os._exit(0)

    def _collect_runners(self, changedetector):
        try:
            runners = []
            score = init_from_file(self.conf)
            if changedetector:
                for file in parse_list(score.conf['score.init']['_files']):
                    changedetector.observe(file)
            for mod in self.modules:
                runners += score._modules[mod].get_serve_runners()
            return runners
        except Exception as e:
            if not changedetector:
                raise
            log.exception(e)
            if isinstance(e, SyntaxError):
                changedetector.observe(e.filename)
            else:
                for frame in traceback.extract_tb(e.__traceback__):
                    changedetector.observe(frame[0])
            reload_condition = threading.Condition()

            @changedetector.add_callback
            def change(*args, **kwargs):
                nonlocal reload_condition
                with reload_condition:
                    log.info('Detected file change, reloading ...')
                    reload_condition.notify()
            try:
                with reload_condition:
                    reload_condition.wait()
            except KeyboardInterrupt:
                os._exit(0)
            os._exit(200)


class RunnerThread(threading.Thread):

    class State(enum.Enum):
        PREPARED = 0
        RUNNING = 1
        ABORTED = 2
        SUCCESS = 3
        EXCEPTION = 4

    def __init__(self, runner, result_queue):
        super().__init__()
        self.runner = runner
        self.result_queue = result_queue
        self.state = self.State.PREPARED

    def run(self):
        try:
            self.state = self.State.RUNNING
            self.runner.start()
            self.state = self.State.SUCCESS
            self.result_queue.put(None)
        except Exception as e:
            self.state = self.State.EXCEPTION
            self.runner.stop()
            self.result_queue.put(e)

    def stop(self):
        if self.state == self.State.RUNNING:
            self.runner.stop()
