import abc
from ..service import Service


STOPPED = Service.State.STOPPED
STARTING = Service.State.STARTING
RUNNING = Service.State.RUNNING
PAUSING = Service.State.PAUSING
PREPARING = Service.State.PREPARING
PAUSED = Service.State.PAUSED
STOPPING = Service.State.STOPPING
EXCEPTION = Service.State.EXCEPTION


final_states = {
    'start': RUNNING,
    'stop': STOPPED,
    'pause': PAUSED,
    'prepare': PAUSED,
}

invalid_transitions = {
    (STARTING, RUNNING),
    (STOPPING, STOPPED),
    (PAUSING, PAUSED),
    (PREPARING, PAUSED),
}


def transitions(state1, state2=None):
    def wrapper(func):
        nonlocal state1, state2
        if not hasattr(func, 'transitions'):
            func.transitions = set()
        name = func.__name__
        if not state2:
            try:
                state2 = final_states[name]
            except KeyError:
                raise Exception(
                    'Function %s() has no end state for transition from %s' %
                    (name, state1))
        elif name in final_states and final_states[name] != state2:
            raise Exception('Function %s() must transition to %s' %
                            (name, final_states[name]))
        for transition in func.transitions:
            if state2 != transition[1]:
                raise Exception(
                    'Function %s() cannot traansition to both %s and %s' %
                    (name, state2, transition[1]))
        func.transitions.add((state1, state2))
        return func
    if state1 == state2 or (state1, state2) in invalid_transitions:
        raise ValueError('Invalid transition: %s' % str((state1, state2)))
    return wrapper


class WorkerMeta(abc.ABCMeta):

    def __init__(cls, name, parents, members):
        transitions = {}
        for name in members:
            func = members[name]
            if not callable(func):
                continue
            if not hasattr(func, 'transitions'):
                continue
            for transition in func.transitions:
                if transition in transitions:
                    raise Exception(
                        'Transition %s already registered as function %s' %
                        (str(transition), transitions[transition]))
                transitions[transition] = name
        if not parents:
            tmp = {
                (STOPPED, PAUSED): 'prepare',
                (PAUSED, RUNNING): 'start',
                (RUNNING, PAUSED): 'pause',
                (PAUSED, STOPPED): 'stop',
            }
            tmp.update(transitions)
            transitions = tmp
        if transitions:
            for parent in parents:
                if hasattr(parent, '_state_transitions'):
                    tmp = parent._state_transitions.copy()
                    tmp.update(transitions)
                    transitions = tmp
                    break
            members['_state_transitions'] = transitions
            cls._state_transitions = transitions
        abc.ABCMeta.__init__(cls, name, parents, members)


class Worker(metaclass=WorkerMeta):

    @property
    def state(self):
        return self.service.state

    @abc.abstractmethod
    def prepare(self):
        # only called when in stopped state
        # assumed to be paused when finished
        pass

    @abc.abstractmethod
    def start(self):
        # only called when in paused state
        # assumed to be running when finished
        pass

    @abc.abstractmethod
    def stop(self):
        # can be called at any time, even during transitions
        # assumed to be stopped when finished
        pass

    @abc.abstractmethod
    def pause(self):
        # only called when in running state
        # assumed to be paused when finished
        pass

    @abc.abstractmethod
    def cleanup(self, exception):
        # only called when an exception occured
        pass
