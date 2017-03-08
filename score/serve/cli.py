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

import click
from score.init import parse_config_file, init as score_init


@click.command('serve')
@click.pass_context
def main(clickctx):
    """
    Starts the application server
    """
    conf = parse_config_file(clickctx.obj['conf'].path)
    overrides = {
        'score.init': {
            'modules': 'score.serve',
        }
    }
    try:
        del conf['score.init']['autoimport']
    except KeyError:
        pass
    if 'serve' not in conf:
        conf['serve'] = {}
    if 'conf' not in conf['serve']:
        conf['serve']['conf'] = clickctx.obj['conf'].path
    score = score_init(conf, overrides=overrides)
    try:
        # delete logging configuration, we have already initialized logging
        # during score_init, above.
        del conf['formatters']
    except KeyError:
        pass
    score.serve.start()
