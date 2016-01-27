"""Commands for interacting with a remote MAAS."""

__all__ = [
    "colorized",
    "Command",
    "CommandError",
    "OriginCommand",
    "OriginTableCommand",
    "PROFILE_DEFAULT",
    "PROFILE_NAMES",
    "TableCommand",
]

from abc import (
    ABCMeta,
    abstractmethod,
)
import argparse
from importlib import import_module
import sys
from typing import (
    Optional,
    Sequence,
    Tuple,
)

import argcomplete
import colorclass

from . import tabular
from .. import (
    bones,
    utils,
    viscera,
)
from ..utils.profiles import (
    Profile,
    ProfileStore,
)


def colorized(text):
    if sys.stdout.isatty():
        # Don't return value_colors; returning the Color instance allows
        # terminaltables to correctly calculate alignment and padding.
        return colorclass.Color(text)
    else:
        return colorclass.Color(text).value_no_colors


def get_profile_names_and_default() -> Tuple[Sequence[str], Optional[Profile]]:
    """Return the list of profile names and the default profile object.

    The list of names is sorted.
    """
    with ProfileStore.open() as config:
        return sorted(config), config.default


# Get profile names and the default profile now to avoid repetition when
# defining arguments (e.g. default and choices). Doing this as module-import
# time is imperfect but good enough for now.
PROFILE_NAMES, PROFILE_DEFAULT = get_profile_names_and_default()


class ArgumentParser(argparse.ArgumentParser):
    """Specialisation of argparse's parser with better support for subparsers.

    Specifically, the one-shot `add_subparsers` call is disabled, replaced by
    a lazily evaluated `subparsers` property.
    """

    def add_subparsers(self):
        raise NotImplementedError(
            "add_subparsers has been disabled")

    @property
    def subparsers(self):
        """Obtain the subparser's object."""
        try:
            return self.__subparsers
        except AttributeError:
            parent = super(ArgumentParser, self)
            self.__subparsers = parent.add_subparsers(title="drill down")
            self.__subparsers.metavar = "COMMAND"
            return self.__subparsers

    def __getitem__(self, name):
        """Return the named subparser."""
        return self.subparsers.choices[name]

    def error(self, message):
        """Make the default error messages more helpful

        Override default ArgumentParser error method to print the help menu
        generated by ArgumentParser instead of just printing out a list of
        valid arguments.
        """
        self.exit(2, colorized("{autored}Error:{/autored} ") + message + "\n")


class CommandError(Exception):
    """A command has failed during execution."""


class Command(metaclass=ABCMeta):
    """A base class for composing commands.

    This adheres to the expectations of `register`.
    """

    def __init__(self, parser):
        super(Command, self).__init__()
        self.parser = parser

    @abstractmethod
    def __call__(self, options):
        """Execute this command."""

    @classmethod
    def name(cls):
        """Return the preferred name as which this command will be known."""
        name = cls.__name__.replace("_", "-").lower()
        name = name[4:] if name.startswith("cmd-") else name
        return name

    @classmethod
    def register(cls, parser, name=None):
        """Register this command as a sub-parser of `parser`.

        :type parser: An instance of `ArgumentParser`.
        :return: The sub-parser created.
        """
        help_title, help_body = utils.parse_docstring(cls)
        command_parser = parser.subparsers.add_parser(
            cls.name() if name is None else name, help=help_title,
            description=help_title, epilog=help_body)
        command_parser.set_defaults(execute=cls(command_parser))
        return command_parser


class TableCommand(Command):

    def __init__(self, parser):
        super(TableCommand, self).__init__(parser)
        if sys.stdout.isatty():
            default_target = tabular.RenderTarget.pretty
        else:
            default_target = tabular.RenderTarget.plain
        parser.add_argument(
            "--output-format", type=tabular.RenderTarget,
            choices=tabular.RenderTarget, default=default_target, help=(
                "Output tabular data as a formatted table (pretty), a "
                "formatted table using only ASCII for borders (plain), or "
                "one of several dump formats. Default: %(default)s."
            ),
        )


class OriginCommandBase(Command):

    def __init__(self, parser):
        super(OriginCommandBase, self).__init__(parser)
        parser.add_argument(
            "--profile-name", metavar="NAME", choices=PROFILE_NAMES,
            required=(PROFILE_DEFAULT is None), help=(
                "The name of the remote MAAS instance to use. Use "
                "`list-profiles` to obtain a list of valid profiles." +
                ("" if PROFILE_DEFAULT is None else " [default: %(default)s]")
            ))
        if PROFILE_DEFAULT is not None:
            parser.set_defaults(profile_name=PROFILE_DEFAULT.name)


class OriginCommand(OriginCommandBase):

    def __call__(self, options):
        session = bones.SessionAPI.fromProfileName(options.profile_name)
        origin = viscera.Origin(session)
        return self.execute(origin, options)

    def execute(self, options, origin):
        raise NotImplementedError(
            "Implement execute() in subclasses.")


class OriginTableCommand(OriginCommandBase, TableCommand):

    def __call__(self, options):
        session = bones.SessionAPI.fromProfileName(options.profile_name)
        origin = viscera.Origin(session)
        return self.execute(origin, options, target=options.output_format)

    def execute(self, options, origin, *, target):
        raise NotImplementedError(
            "Implement execute() in subclasses.")


def prepare_parser(program):
    """Create and populate an argument parser."""
    parser = ArgumentParser(
        description="Interact with a remote MAAS server.", prog=program,
        epilog="http://maas.ubuntu.com/")

    # Create sub-parsers for various command groups. These are all verbs.
    parser.subparsers.add_parser(
        "acquire", help="Acquire nodes or other resources.")
    parser.subparsers.add_parser(
        "launch", help="Launch nodes or other resources.")
    parser.subparsers.add_parser(
        "list", help="List nodes, files, tags, and other resources.")
    parser.subparsers.add_parser(
        "release", help="Release nodes or other resources.")

    # Register sub-commands.
    submodules = (
        # These modules are expected to register verb-like commands into the
        # sub-parsers created above, e.g. for "list files", "launch node".
        "files", "nodes", "tags", "users",
        # These modules are different: they are collections of commands around
        # a topic, or miscellaneous conveniences.
        "profiles", "shell",
    )
    for submodule in submodules:
        module = import_module("." + submodule, __name__)
        module.register(parser)

    # Register global options.
    parser.add_argument(
        '--debug', action='store_true', default=False,
        help=argparse.SUPPRESS)

    return parser


def post_mortem(traceback):
    """Work with an exception in a post-mortem debugger.

    Try to use `ipdb` first, falling back to `pdb`.
    """
    try:
        from ipdb import post_mortem
    except ImportError:
        from pdb import post_mortem

    message = "Entering post-mortem debugger. Type `help` for help."
    redline = colorized("{autored}%s{/autored}") % "{0:=^{1}}"

    print()
    print(redline.format(" CRASH! ", len(message)))
    print(message)
    print(redline.format("", len(message)))
    print()

    post_mortem(traceback)


def main(argv=sys.argv):
    program, *arguments = argv
    parser, options = None, None

    try:
        parser = prepare_parser(program)
        argcomplete.autocomplete(parser, exclude=("-h", "--help"))
        options = parser.parse_args(arguments)
        try:
            execute = options.execute
        except AttributeError:
            parser.error("Argument missing.")
        else:
            execute(options)
    except KeyboardInterrupt:
        raise SystemExit(1)
    except Exception as error:
        # This is unexpected. Why? Because the CLI code raises SystemExit or
        # invokes something that raises SystemExit when it chooses to exit.
        # SystemExit does not subclass Exception, and so it would not be
        # handled here, hence this is not a deliberate exit.
        if parser is None or options is None or options.debug:
            # The user has either chosen to debug OR we crashed before/while
            # parsing arguments. Either way, let's not be terse.
            if sys.stdin.isatty() and sys.stdout.isatty():
                # We're at a fully interactive terminal so let's post-mortem.
                *_, exc_traceback = sys.exc_info()
                post_mortem(exc_traceback)
                # Exit non-zero, but quietly; dumping the traceback again on
                # the way out is confusing after doing a post-mortem.
                raise SystemExit(1)
            else:
                # Re-raise so the traceback is dumped and we exit non-zero.
                raise
        else:
            # Display a terse error message. Note that parser.error() will
            # raise SystemExit(>0) after printing its message.
            parser.error("%s" % error)
