# Copyright (c) 2012 Spotify AB
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations under
# the License.

import worker
import lock
import logging
import rpc
import optparse
import scheduler
import warnings

from ConfigParser import RawConfigParser, NoOptionError, NoSectionError
import task
import parameter


class LuigiConfigParser(RawConfigParser):
    NO_DEFAULT = object()
    _instance = None

    @classmethod
    def instance(cls, *args, **kwargs):
        """ Singleton getter """
        if cls._instance is None:
            config = cls(*args, **kwargs)

            config.read(['/etc/luigi/client.cfg', 'client.cfg'])
            cls._instance = config

        return cls._instance

    def get(self, section, option, default=NO_DEFAULT):
        try:
            return RawConfigParser.get(self, section, option)
        except (NoOptionError, NoSectionError):
            if default is LuigiConfigParser.NO_DEFAULT:
                raise
            return default


def get_config():
    """ Convenience method (for backwards compatibility) for accessing config singleton """
    return LuigiConfigParser.instance()


class EnvironmentParamsContainer(task.Task):
    ''' Keeps track of a bunch of environment params.

    Uses the internal luigi parameter mechanism. The nice thing is that we can instantiate this class
    and get an object with all the environment variables set. This is arguably a bit of a hack.'''
    # TODO(erikbern): would be cleaner if we don't have to read config in global scope
    local_scheduler = parameter.BooleanParameter(is_global=True, default=False,
                                                 description='Use local scheduling')
    scheduler_host = parameter.Parameter(is_global=True, default=get_config().get('core', 'default-scheduler-host', default='localhost'),
                                         description='Hostname of machine running remote scheduler')
    lock = parameter.BooleanParameter(is_global=True, default=False,
                                      description='Do not run if the task is already running')
    lock_pid_dir = parameter.Parameter(is_global=True, default='/var/tmp/luigi',
                                       description='Directory to store the pid file')
    workers = parameter.IntParameter(is_global=True, default=1,
                                     description='Maximum number of parallel tasks to run')


class Register(object):
    def __init__(self):
        self.__reg = {}
        self.__global_params = {}
        self.expose_global_params(EnvironmentParamsContainer)

    def env_params(self, override_defaults):
        # Override any global parameter with whatever is in override_defaults
        for param_name, param_obj in EnvironmentParamsContainer.get_global_params():
            if param_name in override_defaults:
                param_obj.set_default(override_defaults[param_name])

        return EnvironmentParamsContainer()  # instantiate an object with the global params set on it

    def expose(self, cls):
        name = cls.task_family
        assert name not in self.__reg  # TODO: raise better exception
        self.__reg[name] = cls
        self.expose_global_params(cls)
        return cls

    def expose_global_params(self, cls):
        for param_name, param_obj in cls.get_global_params():
            if param_name in self.__global_params and self.__global_params[param_name] != param_obj:
                # Could be registered multiple times in case there's subclasses
                raise Exception('Global parameter %r registered by multiple classes' % param_name)
            self.__global_params[param_name] = param_obj

    def get_reg(self):
        return self.__reg

    def get_global_params(self):
        return self.__global_params.iteritems()

register = Register()


def expose(cls):
    return register.expose(cls)


def expose_main(cls):
    warnings.warn('expose_main is no longer supported, use luigi.run(..., main_task_cls=cls) instead', DeprecationWarning)
    return register.expose(cls)


class Interface(object):
    def parse(self):
        raise NotImplementedError

    @staticmethod
    def run(tasks, override_defaults={}):
        env_params = register.env_params(override_defaults)

        if env_params.lock:
            lock.run_once(env_params.lock_pid_dir)

        if env_params.local_scheduler:
            sch = scheduler.CentralPlannerScheduler()
        else:
            sch = rpc.RemoteScheduler(host=env_params.scheduler_host)

        w = worker.Worker(scheduler=sch, worker_processes=env_params.workers)

        for task in tasks:
            w.add(task)
        w.run()


class ArgParseInterface(Interface):
    ''' Takes the task as the command, with parameters specific to it
    '''
    def parse(self, cmdline_args=None, main_task_cls=None):
        import argparse
        parser = argparse.ArgumentParser()

        def _add_parameter(parser, param_name, param, prefix=None):
            description = []
            if prefix:
                description.append('%s.%s' % (prefix, param_name))
            else:
                description.append(param_name)
            if param.description:
                description.append(param.description)
            if param.has_default:
                description.append(" [default: %s]" % (param.default,))

            if param.is_list:
                action = "append"
            elif param.is_boolean:
                action = "store_true"
            else:
                action = "store"
            parser.add_argument('--' + param_name.replace('_', '-'), help=' '.join(description), default=None, action=action)

        def _add_task_parameters(parser, cls):
            for param_name, param in cls.get_nonglobal_params():
                _add_parameter(parser, param_name, param, cls.task_family)

        def _add_global_parameters(parser):
            for param_name, param in register.get_global_params():
                _add_parameter(parser, param_name, param)

        _add_global_parameters(parser)

        if main_task_cls:
            _add_task_parameters(parser, main_task_cls)

        else:
            subparsers = parser.add_subparsers(dest='command')

            for name, cls in register.get_reg().iteritems():
                subparser = subparsers.add_parser(name)
                _add_task_parameters(subparser, cls)

                # Add global params here as well so that we can support both:
                # test.py --global-param xyz Test --n 42
                # test.py Test --n 42 --global-param xyz
                _add_global_parameters(subparser)

        args = parser.parse_args(args=cmdline_args)
        params = vars(args)  # convert to a str -> str hash

        if main_task_cls:
            task_cls = main_task_cls
        else:
            task_cls = register.get_reg()[args.command]

        # Notice that this is not side effect free because it might set global params
        task = task_cls.from_input(params, register.get_global_params())

        return [task]


class PassThroughOptionParser(optparse.OptionParser):
    '''
    An unknown option pass-through implementation of OptionParser.

    When unknown arguments are encountered, bundle with largs and try again,
    until rargs is depleted.

    sys.exit(status) will still be called if a known argument is passed
    incorrectly (e.g. missing arguments or bad argument types, etc.)
    '''
    def _process_args(self, largs, rargs, values):
        while rargs:
            try:
                optparse.OptionParser._process_args(self, largs, rargs, values)
            except (optparse.BadOptionError, optparse.AmbiguousOptionError), e:
                largs.append(e.opt_str)


class OptParseInterface(Interface):
    ''' Supported for legacy reasons where it's necessary to interact with an existing parser.

    Takes the task using --task. All parameters to all possible tasks will be defined globally
    in a big unordered soup.
    '''
    def __init__(self, existing_optparse):
        self.__existing_optparse = existing_optparse

    def parse(self, cmdline_args=None, main_task_cls=None):
        global_params = list(register.get_global_params())

        parser = PassThroughOptionParser()
        tasks_str = '/'.join(sorted([name for name in register.get_reg()]))

        def add_task_option(p):
            if main_task_cls:
                p.add_option('--task', help='Task to run (' + tasks_str + ') [default: %default]', default=main_task_cls.task_family)
            else:
                p.add_option('--task', help='Task to run (%s)' % tasks_str)

        def _add_parameter(parser, param_name, param):
            description = [param_name]
            if param.description:
                description.append(param.description)
            if param.has_default:
                description.append(" [default: %s]" % (param.default,))

            if param.is_list:
                action = "append"
            elif param.is_boolean:
                action = "store_true"
            else:
                action = "store"
            parser.add_option('--' + param_name.replace('_', '-'),
                              help=' '.join(description),
                              default=None,
                              action=action)

        for param_name, param in global_params:
            _add_parameter(parser, param_name, param)

        add_task_option(parser)
        options, args = parser.parse_args(args=cmdline_args)

        task_cls_name = options.task
        if self.__existing_optparse:
            parser = self.__existing_optparse
        else:
            parser = optparse.OptionParser()
        add_task_option(parser)

        if task_cls_name not in register.get_reg():
            raise Exception('Error: %s is not a valid tasks (must be %s)' % (task_cls_name, tasks_str))

        # Register all parameters as a big mess
        task_cls = register.get_reg()[task_cls_name]
        params = task_cls.get_nonglobal_params()

        for param_name, param in global_params:
            _add_parameter(parser, param_name, param)

        for param_name, param in params:
            _add_parameter(parser, param_name, param)

        # Parse and run
        options, args = parser.parse_args(args=cmdline_args)

        params = {}
        for k, v in vars(options).iteritems():
            if k != 'task':
                params[k] = v

        task = task_cls.from_input(params, global_params)

        return [task]


def run(cmdline_args=None, existing_optparse=None, use_optparse=False, main_task_cls=None):
    ''' Run from cmdline.

    The default parser uses argparse.
    However for legacy reasons we support optparse that optinally allows for
    overriding an existing option parser with new args.
    '''
    setup_interface_logging()
    if use_optparse:
        interface = OptParseInterface(existing_optparse)
    else:
        interface = ArgParseInterface()
    tasks = interface.parse(cmdline_args, main_task_cls=main_task_cls)
    interface.run(tasks)


def build(tasks, **env_params):
    ''' Run internally, bypassing the cmdline parsing.

    Useful if you have some luigi code that you want to run internally.
    Example
    luigi.build([MyTask1(), MyTask2()], local_scheduler=True)
    '''
    setup_interface_logging()
    Interface.run(tasks, env_params)


def setup_interface_logging():
    logger = logging.getLogger('luigi-interface')
    logger.setLevel(logging.DEBUG)

    streamHandler = logging.StreamHandler()
    streamHandler.setLevel(logging.DEBUG)

    formatter = logging.Formatter('%(levelname)s: %(message)s')
    streamHandler.setFormatter(formatter)

    logger.addHandler(streamHandler)
