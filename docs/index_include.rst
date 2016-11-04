.. module:: score.serve
.. role:: confkey
.. role:: confdefault

***********
score.serve
***********

This module is responsible for starting long-running processes like a
web-server, celery-workers or other services.

Quickstart
==========

Let's write a small service that sends spam to stdout periodically. We will
first need a subclass of :class:`Worker` that will perform the sending. Let's
use the very simple :class:`SimpleWorker` subclass:

.. code-block:: python

    import time
    from score.serve import SimpleWorker

    class Spammer(SimpleWorker):

        def loop(self):
            while self.running:
                print('spam!')
                time.sleep(1)

We now need our module to expose a function called `score_serve_workers` that
returns an instance of this class:

.. code-block:: python

    from score.init import ConfiguredModule


    class ConfiguredSpamModule(ConfiguredModule):

        def __init__(self):
            import demo.spam
            super().__init__(demo.spam)

        def score_serve_workers(self):
            return [Spammer()]

The only thing left to do is to configure the serve module to start this
worker. Add the following to your configuration:

.. code-block:: ini

    [score.serve]
        modules = demo.spam

You can now start your glorious spam server with `score serve`.


Configuration
=============

.. autofunction:: score.serve.init

Details
=======

Although we have tried to render the usage of this module as simple as
possible, it has *very* components inside. The reason for this complexity is
the many constraints, that were imposed upon the architecture:

* It must be possible to *pause* a worker. The worker is expected to cease
  handling new requests, but should also be able to continue operations
  immediately when instructed to do so.
* It should be possible to track the state of every worker at all times, even
  when a worker is transitioning from one state to another.
* Reloading the application when a python file changes should be as fast as
  possible, since this will be done very often during application development.
* The main loop should not rely on the existence of a console. Future versions
  of this module will provide a maintenance port, where workers can be probed
  and controlled by externals applications.

Worker States
-------------

The above constraints have lead us to the following states for workers:

* **STOPPED**: This is the initial as well as the final state of workers.
  Workers are not expected to consume any significant resources in this state.
* **PAUSED**: The worker has all required resources to start perform its duty
  at any point. An HTTP server will have an open socket at port 80, for
  example, but will not accept any connections.
* **RUNNING**: This is the state where the worker actually does whatever it is
  meant to do.

If the worker states are not tweaked manually (via :func:`@transitions
<score.serve.transitions>` annotations), the state machine looks like the
following::

  .       STOPPED
         /      ^
     PAUSING    |
        |     STOPPING
        V     /
        PAUSED
       /     ^
  STARTING   |
      |     PAUSING
      V     /
      RUNNING

So every transition passes through an intermediate state which represents the
state the worker ist transitioning to. This implies that the ``PAUSING`` state
occurs twice in the diagram, as you might have noticed.

It is possible to add furhter transition capabilities to your worker using
:func:`@transitions <score.serve.transitions>`. Have a look at the function
documentation for details.

Worker API
----------

To make these state transitions as transparent as possible, the Worker API
follows a simple design principle: Every state transition is represented by a
function. The transition ``STOPPED -> PAUSED`` is handled by the function
:meth:`prepare <score.serve.Worker.prepare>`, for example. As soon as the
method is entered, the worker is assumed to be in the ``PAUSING`` state. When
the function terminates, the worker is assumed to be in the ``PAUSED`` state.

The layers above the :class:`Worker` will make sure, that these functions are
always called in the correct order. Workers have the guarantee, that they are
in the ``STOPPED`` state, when their :meth:`prepare
<score.serve.Worker.prepare>` method is called, for example.

Due to the working of Service API (the next higher layer), every transition
function is called inside a different thread. This is the only inconvenience
imposed upon this layer. The provided :class:`SimpleWorker
<score.serve.SimpleWorker>` class implements an abstraction around this
limitation, so if you don't want to dirty your code with threading, you can
just go ahead and use that class instead of the more powerful and more complex
:class:`Worker` base class.

Service API
-----------

Every worker is wrapped in a :class:`Service <score.serve.Service>` object that
handles the state transitions of a worker. Service objects expose the same API
as workers, but they have a slightly different meaning: When you call
:meth:`start <score.serve.Service.start>` on a service object, it will make
sure that the worker transitions to the ``RUNNING`` state eventually, no matter
what state it currently is in.

API
===

Configuration
-------------

.. autofunction:: score.serve.init

.. autoclass:: score.serve.ConfiguredServeModule()

    .. attribute:: autoreload

        The configured autoreload value as a `bool`.

    .. automethod:: start

Workers
-------

.. autoclass:: score.serve.Worker
    :members:

.. autofunction:: score.serve.transitions

.. autoclass:: score.serve.SimpleWorker

.. autoclass:: score.serve.SocketServerWorker

.. autoclass:: score.serve.AsyncioWorker
    :members:

Service
-------

.. autoclass:: score.serve.Service
    :members:

