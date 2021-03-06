#!/usr/bin/env python3

'''
Builds the Benchmarks
'''

from argparse import Action
from argparse import ArgumentError
from argparse import ArgumentParser
from argparse import ArgumentTypeError
from argparse import SUPPRESS
from io import StringIO
from logging import getLogger
from os import path
from subprocess import CalledProcessError
from traceback import format_exc
from typing import Tuple

import csv
import sys

from performance.common import get_repo_root_path
from performance.common import remove_directory
from performance.common import validate_supported_runtime
from performance.logger import setup_loggers

import dotnet


class TargetFrameworkAction(Action):
    '''
    Used by the ArgumentParser to represent the information needed to parse the
    supported .NET Core target frameworks argument from the command line.
    '''

    def __call__(self, parser, namespace, values, option_string=None):
        if values:
            wrong_choices = []
            supported_target_frameworks = TargetFrameworkAction\
                .get_supported_target_frameworks()

            for value in values:
                if value not in supported_target_frameworks:
                    wrong_choices.append(value)
            if wrong_choices:
                message = ', '.join(wrong_choices)
                message = 'Invalid choice(s): {}'.format(message)
                raise ArgumentError(self, message)
            setattr(namespace, self.dest, list(set(values)))

    @staticmethod
    def get_supported_target_frameworks() -> list:
        '''List of supported .NET Core target frameworks.'''
        frameworks = list(
            TargetFrameworkAction.__get_framework_channel_map().keys()
        )
        if sys.platform == 'win32':
            frameworks.append('net461')
        return frameworks

    @staticmethod
    def __get_framework_channel_map() -> dict:
        # TODO: Can we do better? For example, read it from csproj?
        return {
            'netcoreapp3.0': 'master',
            'netcoreapp2.2': '2.2',
            'netcoreapp2.1': '2.1',
            'netcoreapp2.0': '2.0',
        }

    @staticmethod
    def get_channel(framework: str) -> str:
        '''
        Attemps to retrieve the channel that can be used to download the
        DotNet Cli tools.
        '''
        dct = TargetFrameworkAction.__get_framework_channel_map()
        return dct[framework] if framework in dct else None


def get_supported_configurations() -> list:
    '''
    The configuration to use for building the project. The default for most
    projects is 'Release'
    '''
    return ['Release', 'Debug']


def add_arguments(parser: ArgumentParser) -> ArgumentParser:
    '''
    Adds new arguments to the specified ArgumentParser object.
    '''
    def dotnet_configuration(configuration: str) -> str:
        for config in get_supported_configurations():
            is_valid = config.casefold() == configuration.casefold()
            if is_valid:
                return config
        raise ArgumentTypeError(
            'Unknown configuration: {}.'.format(configuration))

    supported_configurations = get_supported_configurations()
    parser.add_argument(
        '-c', '--configuration',
        required=False,
        default=supported_configurations[0],
        choices=supported_configurations,
        type=dotnet_configuration,
        help=SUPPRESS,
    )

    supported_target_frameworks = TargetFrameworkAction\
        .get_supported_target_frameworks()
    parser.add_argument(
        '-f', '--frameworks',
        required=True,
        nargs='+',
        action=TargetFrameworkAction,
        choices=supported_target_frameworks,
        help='''The target framework to build/run for. '''
             '''The target framework must also be specified in the project '''
             '''file.''',
    )

    parser.add_argument(
        '--incremental',
        required=False,
        default='yes',
        choices=['yes', 'no'],
        type=str,
        help='''Controls whether previous packages/bin/obj folders should '''
             '''be kept or removed before the dotnet restore/build/run are '''
             '''executed (Default yes).''',
    )

    # BenchmarkDotNet
    parser.add_argument(
        '--enable-hardware-counters',
        dest='enable_pmc',
        required=False,
        default=False,
        action='store_true',
        help='''Enables the following performance metric counters: '''
             '''BranchMispredictions+CacheMisses+InstructionRetired''',
    )

    parser.add_argument(
        '--category',
        required=False,
        choices=['coreclr', 'corefx'],
        type=str.lower
    )
    parser.add_argument(
        '--filter',
        required=False,
        nargs='+',
        help='Glob patterns to execute benchmarks that match.',
    )

    def __valid_file_path(file_path: str) -> str:
        '''Verifies that specified file path exists.'''
        file_path = path.abspath(file_path)
        if not path.isfile(file_path):
            raise ArgumentTypeError('{} does not exist.'.format(file_path))
        return file_path

    parser.add_argument(
        '--corerun',
        dest='corerun',
        required=False,
        type=__valid_file_path,
        help='Full path to CoreRun.exe (corerun on Unix)',
    )
    parser.add_argument(
        '--cli',
        dest='cli',
        required=False,
        type=__valid_file_path,
        help='Full path to dotnet.exe',
    )

    def __get_bdn_arguments(user_input: str) -> list:
        file = StringIO(user_input)
        reader = csv.reader(file, delimiter=' ')
        for args in reader:
            return args
        return []

    parser.add_argument(
        '--bdn-arguments',
        dest='bdn_arguments',
        required=False,
        type=__get_bdn_arguments,
        help='''Command line arguments to be passed to the BenchmarkDotNet '''
             '''harness.''',
    )

    return parser


def __process_arguments(args: list) -> Tuple[list, bool]:
    parser = ArgumentParser(
        description="Builds the benchmarks.",
        allow_abbrev=False)

    parser.add_argument(
        '-v', '--verbose',
        required=False,
        default=False,
        action='store_true',
        help='Turns on verbosity (default "False")',
    )

    parser = add_arguments(parser)
    return parser.parse_args(args)


def build(
        configuration: str,
        frameworks: list,
        incremental: str,
        verbose: bool) -> None:
    '''Restores and builds the benchmarks'''

    packages = path.join(get_repo_root_path(), 'packages')

    if incremental == 'no':
        __log_script_header("Removing packages, bin and obj folders.")
        binary_folders = [
            packages,
            path.join(BENCHMARKS_CSPROJ.working_directory, 'bin'),
            path.join(BENCHMARKS_CSPROJ.working_directory, 'obj'),
        ]
        for binary_folder in binary_folders:
            remove_directory(path=binary_folder)

    # dotnet restore
    __log_script_header("Restoring .NET micro benchmarks")
    BENCHMARKS_CSPROJ.restore(packages_path=packages, verbose=verbose)

    # dotnet build
    build_title = "Building .NET micro benchmarks for '{}'".format(
        ' '.join(frameworks))
    __log_script_header(build_title)
    BENCHMARKS_CSPROJ.build(configuration, frameworks, verbose)


def run(
        configuration: str,
        framework: str,
        verbose: bool,
        *args) -> None:
    '''Builds the benchmarks'''
    __log_script_header("Running .NET micro benchmarks for '{}'".format(
        framework
    ))
    # dotnet run
    BENCHMARKS_CSPROJ.run(configuration, framework, verbose, *args)


def __log_script_header(message: str):
    getLogger().info('-' * len(message))
    getLogger().info(message)
    getLogger().info('-' * len(message))


BENCHMARKS_CSPROJ = dotnet.CSharpProject(
    working_directory=path.join(
        get_repo_root_path(), 'src', 'benchmarks', 'micro'),
    csproj_file='MicroBenchmarks.csproj'
)


def __main(args: list) -> int:
    try:
        validate_supported_runtime()
        args = __process_arguments(args)

        configuration = args.configuration
        frameworks = args.frameworks
        incremental = args.incremental
        verbose = args.verbose

        setup_loggers(verbose=verbose)

        # dotnet --info
        dotnet.info(verbose)

        # dotnet build
        build(configuration, frameworks, incremental, verbose)

        for framework in frameworks:
            run_args = ['--']
            if args.category:
                run_args += ['--allCategories', args.category]
            if args.corerun:
                run_args += ['--coreRun', args.corerun]
            if args.cli:
                run_args += ['--cli', args.cli]
            if args.enable_pmc:
                run_args += [
                    '--counters',
                    'BranchMispredictions+CacheMisses+InstructionRetired',
                ]
            if args.filter:
                run_args += ['--filter'] + args.filter

            # Extra BenchmarkDotNet cli arguments.
            if args.bdn_arguments:
                run_args += args.bdn_arguments

            # dotnet run
            run(configuration, framework, verbose, *run_args)

        return 0
    except CalledProcessError as ex:
        getLogger().error(
            'Command: "%s", exited with status: %s', ex.cmd, ex.returncode)
    except IOError as ex:
        getLogger().error(
            "I/O error (%s): %s: %s", ex.errno, ex.strerror, ex.filename)
    except SystemExit:  # Argparse throws this exception when it exits.
        pass
    except Exception:
        getLogger().error('Unexpected error: %s', sys.exc_info()[0])
        getLogger().error(format_exc())
    return 1


if __name__ == "__main__":
    exit(__main(sys.argv[1:]))
