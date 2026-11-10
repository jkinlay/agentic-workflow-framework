"""Read-only preservation inventory and filesystem metadata digests."""
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

from .canonical import load_yaml
from .installer import CONFIG

FORMAT = 'awf-upgrade-preservation-1'
EXCLUDED = {'.git', '.agentic/.venv'}


def _permissions(path):
    """Retain only a digest of ownership and access control, never raw SIDs."""
    if os.name != 'nt':
        info = path.stat()
        raw = json.dumps([info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)]).encode()
        return {'verified': True, 'method': 'posix-owner-group-mode',
                'sha256': hashlib.sha256(raw).hexdigest()}
    method = 'windows-owner-group-dacl-sddl'
    try:
        import ctypes
        from ctypes import wintypes
        advapi = ctypes.WinDLL('advapi32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        get = advapi.GetFileSecurityW
        get.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p,
                        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        get.restype = wintypes.BOOL
        convert = advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW
        convert.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                           ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.ULONG)]
        convert.restype = wintypes.BOOL
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        needed = wintypes.DWORD()
        flags = 0x1 | 0x2 | 0x4  # OWNER, GROUP, DACL; no SACL privilege required.
        get(str(path), flags, None, 0, ctypes.byref(needed))
        if not needed.value:
            raise OSError('Security descriptor unavailable')
        descriptor = ctypes.create_string_buffer(needed.value)
        if not get(str(path), flags, descriptor, needed.value, ctypes.byref(needed)):
            raise OSError('Security descriptor unavailable')
        sddl = wintypes.LPWSTR()
        if not convert(descriptor, 1, flags, ctypes.byref(sddl), None):
            raise OSError('Security descriptor conversion unavailable')
        try:
            digest = hashlib.sha256(sddl.value.encode('utf-8')).hexdigest()
        finally:
            kernel.LocalFree(ctypes.cast(sddl, ctypes.c_void_p))
        return {'verified': True, 'method': method, 'sha256': digest}
    except (OSError, AttributeError):
        return {'verified': False, 'method': method, 'diagnostic': 'permission capture unavailable'}


def _named_streams(path):
    """Digest every named Windows stream, including directory streams; fail closed."""
    if os.name != 'nt':
        return {'verified': True, 'method': 'posix-no-windows-streams', 'streams': {}}
    method = 'windows-find-streams'
    try:
        import ctypes
        from ctypes import wintypes

        class StreamData(ctypes.Structure):
            _fields_ = [('size', ctypes.c_longlong), ('name', wintypes.WCHAR * 296)]

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        first = kernel.FindFirstStreamW
        first.argtypes = [wintypes.LPCWSTR, wintypes.INT, ctypes.POINTER(StreamData), wintypes.DWORD]
        first.restype = wintypes.HANDLE
        next_stream = kernel.FindNextStreamW
        next_stream.argtypes = [wintypes.HANDLE, ctypes.POINTER(StreamData)]
        next_stream.restype = wintypes.BOOL
        close = kernel.FindClose
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        data = StreamData()
        handle = first(str(path), 0, ctypes.byref(data), 0)
        streams = {}
        if handle == ctypes.c_void_p(-1).value:
            if ctypes.get_last_error() != 38:  # ERROR_HANDLE_EOF: no streams.
                raise OSError('Stream enumeration unavailable')
        else:
            try:
                while True:
                    if data.name != '::$DATA':
                        raw = Path(str(path) + data.name).read_bytes()
                        if len(raw) != data.size:
                            raise OSError('Stream changed during capture')
                        name_hash = hashlib.sha256(data.name.encode('utf-8')).hexdigest()
                        streams[name_hash] = {'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
                    if not next_stream(handle, ctypes.byref(data)):
                        if ctypes.get_last_error() != 38:
                            raise OSError('Stream enumeration incomplete')
                        break
            finally:
                close(handle)
        return {'verified': True, 'method': method, 'streams': streams}
    except (OSError, AttributeError, ValueError):
        return {'verified': False, 'method': method, 'diagnostic': 'named stream capture unavailable'}


def inventory(project, *, evidence_kind='SYNTHETIC_OWNER_CUSTOMIZATION'):
    try:
        return _inventory(project, evidence_kind=evidence_kind)
    except OSError as exc:
        try:
            relative = Path(exc.filename).absolute().relative_to(Path(project).absolute()).as_posix()
        except (ValueError, TypeError):
            relative = '.'
        raise ValueError('Preservation inventory incomplete: ' + relative +
                         '; preserve the baseline, restore readable contained regular content, then repeat snapshot') from exc


def _inventory(project, *, evidence_kind):
    """Hash a quiescent disposable project without opening any database for write.

    Runtime/Git trees are excluded explicitly. Ledgers (including WAL/SHM),
    reservations, archives, receipts, owner files and permissions are included.
    Exclusive ownership is required; this is not a snapshot of a live writer.
    """
    project = Path(project).resolve(strict=True)
    files = {}
    runtime_backups = {}
    directories = {'/': {'mode': stat.S_IMODE(project.stat().st_mode),
                         'permissions': _permissions(project), 'named_streams': _named_streams(project)}}

    def incomplete(error):
        relative = Path(error.filename).relative_to(project).as_posix() if error.filename else '.'
        raise ValueError('Preservation inventory incomplete: ' + relative +
                         '; restore readable regular files from the retained baseline and repeat snapshot') from error

    def scope_key(relative):
        return relative.casefold() if os.name == 'nt' else relative

    protected_names = {scope_key(path) for path in (
        '.agentic', '.agentic-state', '.agentic-install', '.agentic-backup', '.github',
        'AGENTS.md', 'PROJECT_INSTRUCTIONS.md', 'OPERATING_CONFIG.yaml',
        '.github/CODEOWNERS', '.github/PULL_REQUEST_TEMPLATE.md', 'CODEOWNERS', 'docs/CODEOWNERS')}
    owner_names = {scope_key(path) for path in (
        '.github/CODEOWNERS', '.github/PULL_REQUEST_TEMPLATE.md', 'docs/CODEOWNERS')}

    for directory, dirs, names in os.walk(project, followlinks=False, onerror=incomplete):
        parent = Path(directory)
        # Match installer inputs. Record root owner files and directory metadata,
        # but never descend through unrelated product trees (including junctions).
        for name in [*dirs, *names]:
            path = parent / name
            relative = path.relative_to(project).as_posix()
            key = scope_key(relative)
            if key == '.git':
                continue
            info = path.lstat()
            protected = key.startswith(('.agentic/', '.agentic-state/', '.agentic-install/', '.agentic-backup/')) or key in protected_names
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_reparse_tag', 0):
                if not protected:
                    continue
                raise ValueError('Linked/reparse inventory member refused: ' + relative +
                                 '; preserve the target externally, replace this link with contained regular content, then repeat snapshot')
            if protected and stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                raise ValueError('Hard-linked inventory member refused: ' + relative +
                                 '; preserve the file externally, replace it with an independent regular copy, then repeat snapshot')
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                if not protected:
                    continue
                raise ValueError('Non-regular inventory member refused: ' + relative +
                                 '; preserve externally and replace with regular content before snapshot')
        # Delegate only exact transaction backup roots to the runtime inventory.
        # Keep their complete records in files/directories: this is not a delta
        # exemption, and pre-existing backups remain ordinary preservation inputs.
        for name in dirs:
            path = parent / name
            relative = path.relative_to(project).as_posix()
            prefix = '.agentic/.venv.backup-'
            key = scope_key(relative)
            if key.startswith(prefix):
                identity = key[len(prefix):]
                try:
                    valid = str(uuid.UUID(identity)) == identity
                except ValueError:
                    valid = False
                if valid:
                    runtime_backups[relative] = runtime_inventory(path)
        dirs[:] = [name for name in dirs
                   if (parent / name).relative_to(project).as_posix() not in runtime_backups]
        dirs[:] = sorted(name for name in dirs
                         if scope_key((parent / name).relative_to(project).as_posix()) not in EXCLUDED
                         and not (stat.S_ISLNK((parent / name).lstat().st_mode)
                                  or getattr((parent / name).lstat(), 'st_reparse_tag', 0)))
        for name in dirs:
            path = parent / name
            directories[path.relative_to(project).as_posix() + '/'] = {
                'mode': stat.S_IMODE(path.stat().st_mode), 'permissions': _permissions(path),
                'named_streams': _named_streams(path)}
        if parent == project:
            dirs[:] = [name for name in dirs if scope_key(name) in {
                '.agentic', '.agentic-state', '.agentic-install', '.agentic-backup', '.github', 'docs'}]
        elif scope_key(parent.relative_to(project).as_posix()) in {'.github', 'docs'}:
            dirs[:] = []
        for name in sorted(names):
            path = parent / name
            relative = path.relative_to(project).as_posix()
            if scope_key(relative) == '.git':  # Only the actual root worktree pointer.
                continue
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_reparse_tag', 0):
                continue  # Unrelated root link; protected links refused above.
            if not stat.S_ISREG(info.st_mode):
                continue  # Unrelated product special file; never open it.
            if scope_key(parent.relative_to(project).as_posix()) in {'.github', 'docs'} and scope_key(relative) not in owner_names:
                continue
            raw = path.read_bytes()
            files[relative] = {'sha256': hashlib.sha256(raw).hexdigest(),
                               'mode': stat.S_IMODE(path.stat().st_mode), 'size': len(raw),
                               'permissions': _permissions(path), 'named_streams': _named_streams(path)}
    for prefix, runtime in runtime_backups.items():
        for name, entry in runtime['entries'].items():
            relative = prefix + ('/' if name == '/' else '/' + name)
            (directories if name.endswith('/') else files)[relative] = entry
    try:
        config = load_yaml((project / CONFIG).read_bytes())
        github = config['github']
        if not isinstance(github, dict):
            raise ValueError('Invalid github identity')
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError('Snapshot configuration refused: ' + CONFIG +
                         '; restore the reviewed baseline configuration and repeat snapshot') from exc
    identity = {key: github.get(key) for key in ('repository', 'repository_id', 'base_branch')}
    if any(value is None or value == '' for value in identity.values()):
        raise ValueError('Complete repository identity is required: ' + CONFIG +
                         '; reconcile github.repository/repository_id/base_branch with the accepted baseline, then repeat snapshot')
    return {'format': FORMAT, 'evidence_kind': evidence_kind,
            'identity': identity, 'files': files, 'directories': directories,
            'excluded_paths': sorted(EXCLUDED),
            'runtime': runtime_inventory(project / '.agentic/.venv'),
            'execution_authority': False}


def runtime_inventory(path):
    """Exact bytes and metadata of a quiescent runtime, for backup comparison."""
    from .installer import _contained_cleanup_link
    from .safeio import Tree
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return None
    before = runtime_tree_sha256(path)
    entries = {}
    def capture(node, relative):
        info = node.lstat()
        if getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Runtime preservation refuses reparse points')
        state = dict(mode=stat.S_IMODE(info.st_mode), permissions=_permissions(node),
                     named_streams=_named_streams(node))
        if stat.S_ISDIR(info.st_mode):
            entries[relative + '/'] = state
            for child in sorted(node.iterdir()):
                capture(child, child.name if not relative else relative + '/' + child.name)
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            with Tree(node.parent) as tree:
                raw = tree.read(node.name)
            entries[relative] = dict(state, sha256=hashlib.sha256(raw).hexdigest(), size=len(raw))
        elif stat.S_ISLNK(info.st_mode) and _contained_cleanup_link(relative, os.readlink(node)):
            entries[relative] = dict(state, link=os.readlink(node))
        else:
            raise ValueError('Runtime preservation requires contained independent regular content')
    capture(path, '')
    if before != runtime_tree_sha256(path):
        raise ValueError('Runtime changed during preservation snapshot')
    return dict(sha256=before, entries=entries)


def runtime_tree_sha256(path):
    """Read-only journal-compatible runtime hash; links contribute text, never targets.

    Requires exclusive ownership. Confined Tree reads reject hard links/reparse
    points and retain the existing per-file read bound. No lock, mkdir or cleanup
    is performed. A hash match authenticates an endpoint, not recovery authority.
    """
    from .safeio import Tree
    path = Path(path).absolute()
    digest = hashlib.sha256()

    def add(value):
        raw = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        digest.update(len(raw).to_bytes(8, 'big'))
        digest.update(raw)

    with Tree(path) as tree:
        root = path.lstat()
        if not stat.S_ISDIR(root.st_mode) or stat.S_ISLNK(root.st_mode) or getattr(root, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Runtime hash requires an independent directory root')
        add(['.', 'directory', stat.S_IMODE(root.st_mode)])

        def visit(directory, prefix=''):
            # Pin every directory before enumerating it, without following links.
            if prefix:
                tree.parent(prefix + '/.runtime-hash-pin')
            with os.scandir(directory) as scan:
                entries = sorted(scan, key=lambda item: item.name)
            for entry in entries:
                relative = entry.name if not prefix else prefix + '/' + entry.name
                info = entry.stat(follow_symlinks=False)
                mode = stat.S_IMODE(info.st_mode)
                if stat.S_ISLNK(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400:
                    add([relative, 'link', mode, os.readlink(entry.path)])
                elif getattr(info, 'st_file_attributes', 0) & 0x400:
                    raise ValueError('Runtime hash refuses reparse points: ' + relative)
                elif stat.S_ISDIR(info.st_mode):
                    add([relative, 'directory', mode])
                    visit(entry.path, relative)
                elif stat.S_ISREG(info.st_mode):
                    raw = tree.read(relative)
                    if len(raw) != info.st_size:
                        raise ValueError('Runtime changed during hashing: ' + relative)
                    add([relative, 'file', mode, len(raw), hashlib.sha256(raw).hexdigest()])
                else:
                    raise ValueError('Runtime hash refuses nonregular or linked files: ' + relative)
        visit(path)
    return digest.hexdigest()
