"""Conservative wheel filename compatibility checks without packaging imports."""
from __future__ import annotations

import platform
from itertools import product
import re
import sys
import sysconfig

from . import ValidationError


def _platform_matches(tag):
    if tag == 'any':
        return True
    native = sysconfig.get_platform().translate(str.maketrans('-.', '__'))
    if tag == native:
        return True
    machine = platform.machine().lower()
    if sys.platform == 'linux':
        # manylinux wheels declare a minimum glibc, not the build host's libc.
        legacy = {'manylinux1': (2, 5), 'manylinux2010': (2, 12), 'manylinux2014': (2, 17)}
        for prefix, minimum in legacy.items():
            if tag == prefix + '_' + machine:
                break
        else:
            match = re.fullmatch(r'manylinux_(\d+)_(\d+)_' + re.escape(machine), tag)
            if match is None:
                return False
            minimum = tuple(map(int, match.groups()))
        libc, version = platform.libc_ver()
        return (libc == 'glibc' and re.fullmatch(r'\d+(?:\.\d+)+', version) is not None
                and tuple(map(int, version.split('.'))) >= minimum)
    if sys.platform == 'darwin':
        match = re.fullmatch(r'macosx_(\d+)_(\d+)_(.+)', tag)
        if match is None:
            return False
        major, minor, architecture = match.groups()
        current = tuple(map(int, platform.mac_ver()[0].split('.')[:2]))
        architectures = {machine, 'universal2'} if machine in {'arm64', 'x86_64'} else {machine}
        return architecture in architectures and current >= (int(major), int(minor))
    return False


def validate_host_tags(artifacts):
    """Reject incompatible or ambiguous Python/ABI/platform tags before staging."""
    host = sys.version_info[:2]
    host_tag = 'cp' + ''.join(map(str, host))
    for artifact in artifacts.values():
        filename = artifact['filename']
        parts = filename.removesuffix('.whl').rsplit('-', 3)
        if len(parts) != 4:
            raise ValidationError(f'Host Python {host[0]}.{host[1]} cannot resolve '
                                  'ambiguous wheelhouse tag: ' + filename)
        _, python_tag, abi, platforms = parts
        filename_tags = {'-'.join(fields) for fields in product(
            python_tag.split('.'), abi.split('.'), platforms.split('.'))}
        if filename_tags != artifact['wheel_tags']:
            raise ValidationError('Wheel filename tags differ from hash-bound WHEEL Tag set: ' + filename)
        compatible = False
        match = re.fullmatch(r'cp(\d)(\d+)', python_tag)
        if match:
            target = tuple(map(int, match.groups()))
            compatible = (sys.implementation.name == 'cpython' and
                          ((abi == python_tag and target == host) or
                           (abi == 'abi3' and target[0] == host[0] and target <= host)))
            # Free-threaded hosts cannot consume the ordinary CPython ABI.
            compatible = compatible and not sysconfig.get_config_var('Py_GIL_DISABLED')
        elif python_tag in {'py3', 'py2.py3', 'py3.py2', 'py' + ''.join(map(str, host))}:
            compatible = host[0] == 3 and abi == 'none'
        if not compatible:
            raise ValidationError(f'Host Python {host[0]}.{host[1]} ({host_tag}) does not match '
                                  f'wheelhouse tag {python_tag}-{abi}: {filename}')
        if not any(_platform_matches(tag) for tag in platforms.split('.')):
            raise ValidationError('Wheelhouse platform tag ' + platforms +
                                  ' does not match host ' + sysconfig.get_platform() + ': ' + filename)
