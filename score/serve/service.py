import enum
import time
import threading
import logging


log = logging.getLogger(__name__)


class State(enum.Enum):
    STOPPED = 'stopped'
    STARTING = 'starting'
    RUNNING = 'running'
    PAUSING = 'pausing'
    PREPARING = 'preparing'
    PAUSED = 'paused'
    STOPPING = 'stopping'
    EXCEPTION = 'exception'


STOPPED = State.STOPPED
STARTING = State.STARTING
RUNNING = State.RUNNING
PAUSING = State.PAUSING
PREPARING = State.PREPARING
PAUSED = State.PAUSED
STOPPING = State.STOPPING
EXCEPTION = State.EXCEPTION


intermediate_states = {
    RUNNING: PAUSED,
    STOPPED: PAUSED,
}


class Service:
    """
    A wrapper around workers, that you can use to control your workers without
    worrying about threading.
    """

    State = globals()['State']

    def __init__(self, name, worker):
        self.name = name
        self.worker = worker
        self.exception = None
        self.state_listeners = set()
        self.next_transition = None
        self.state_lock = threading.RLock()
        self._target_state = None
        self._next_state = None
        self._state = STOPPED
        self._transition = None
        self.state_timestamp = time.time()
        worker.service = self

    def start(self):
        """
        Makes sure the worker ends up in the ``RUNNING`` state eventually.
        """
        self._transition_to(RUNNING)

    def pause(self):
        """
        Makes sure the worker ends up in the ``PAUSED`` state eventually.
        """
        self._transition_to(PAUSED)

    def prepare(self):
        """
        An alias for :meth:`pause` for ensuring compaitibility with the
        :class:`Worker` API.
        """
        self._transition_to(PAUSED)

    def stop(self):
        """
        Makes sure the worker ends up in the ``STOPPED`` state eventually.
        """
        self._transition_to(STOPPED)

    def register_state_change_listener(self, callback):
        """
        Registers a `callable` that will be invoked whenever the state of the
        worker changes. The *callback* will receive three arguments:

        * this service,
        * the old state and
        * the new (current) state.

        Note, that due to the nature of threading, it is possible that the
        Service is already in another state than the one provided as the third
        argument.
        """
        self.state_listeners.add(callback)

    def unregister_state_change_listener(self, callback):
        """
        Removes a previously registered listener.
        """
        self.state_listeners.discard(callback)

    def _transition_to(self, target_state):
        with self.state_lock:
            if self._state == EXCEPTION:
                log.debug('_transition_to(%s) -> EXCEPTION' % target_state)
                return
            if self._state == target_state:
                self._target_state = None
                self._next_state = None
                log.debug('_transition_to(%s) -> NOP' % target_state)
                return
            if (self._target_state == target_state and self._transition and
                    self._transition[1] == target_state):
                # already transitioning to give state
                self._target_state = None
                self._next_state = None
                log.debug('_transition_to(%s) -> in progress' % target_state)
                return
            transition = (self._state, target_state)
            if transition in self.worker._state_transitions:
                log.debug('_transition_to(%s) -> transition initiated' %
                          target_state)
                self._target_state = None
                self._next_state = None
                if target_state == RUNNING:
                    self.state = STARTING
                elif target_state == STOPPED:
                    self.state = STOPPING
                elif target_state == PAUSED and self.state == STOPPED:
                    self.state = PREPARING
                elif target_state == PAUSED:
                    self.state = PAUSING
                self._target_state = target_state
                funcname = self.worker._state_transitions[transition]
                callback = getattr(self.worker, funcname)
                self._transition = transition
                threading.Thread(target=self._execute_transition,
                                 args=(transition, callback)).start()
                return
            if target_state in intermediate_states:
                log.debug('_transition_to(%s) -> intermediate(%s)' % (
                    target_state, intermediate_states[target_state]))
                self._transition_to(intermediate_states[target_state])
                self._next_state = target_state
            else:
                log.debug('_transition_to(%s) -> queued' % target_state)
                self._next_state = target_state

    def _execute_transition(self, transition, callback):
        log.debug('_execute_transition(%s, %s)' % (str(transition),
                                                   callback.__name__))
        state_timestamp = self.state_timestamp
        try:
            callback()
            with self.state_lock:
                if self._transition == transition:
                    self._transition = None
                if state_timestamp >= self.state_timestamp:
                    self.state = transition[1]
        except Exception as exception:
            self.set_exception(exception)

    def set_exception(self, exception):
        with self.state_lock:
            if self._state == EXCEPTION:
                return
            old_state = self._state
            self._state = EXCEPTION
            self.exception = exception
            self.state_timestamp = time.time()
        self.worker.cleanup(exception)
        self._state_changed(old_state, EXCEPTION)

    def _state_changed(self, old, new):
        log.debug('changed state: %s -> %s' % (old, new))
        if new == EXCEPTION:
            log.exception(self.exception)
        for callback in self.state_listeners:
            callback(self, old, new)
        with self.state_lock:
            if self._next_state:
                log.debug('  next state: %s' % (self._next_state))
                next_state = self._next_state
                self._next_state = None
                self._transition_to(next_state)
            elif self._target_state and self._state != self._target_state:
                log.debug('  target state: %s' % (self._target_state))
                self._transition_to(self._target_state)

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, new_state):
        with self.state_lock:
            if new_state == self._state or self._state == EXCEPTION:
                return
            old_state = self._state
            self._state = new_state
            self.state_timestamp = time.time()
        self._state_changed(old_state, new_state)
