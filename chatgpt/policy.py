"""Fail-closed path policy for the ChatGPT-only Obsidian MCP entry point."""

from pathlib import Path
import stat
from urllib.parse import quote

WRITE_FOLDERS = frozenset({'000_Inbox', '020_Projects'})
WRITE_TOOLS = frozenset({
    'obsidian_put_content', 'obsidian_append_content',
    'obsidian_patch_content', 'obsidian_delete_file',
})
READ_TOOLS = frozenset({
    'obsidian_list_files_in_vault', 'obsidian_list_files_in_dir',
    'obsidian_get_file_contents', 'obsidian_batch_get_file_contents',
    'obsidian_simple_search', 'obsidian_search_by_tag',
    'obsidian_get_frontmatter', 'obsidian_get_recent_changes',
})
ALLOWED_TOOLS = WRITE_TOOLS | READ_TOOLS


class VaultPolicy:
    def __init__(self, vault: Path):
        if not vault.is_dir():
            raise ValueError('Configured Vault directory is unavailable')
        self.vault = vault.resolve(strict=True)

    def path(self, value, *, write=False, directory=False, delete=False):
        if not isinstance(value, str):
            raise ValueError('Vault path must be a string')
        if directory:
            value = value.rstrip('/') if not value.startswith('/') else value
            if value == '':
                return ''
        if not value or any(c in value for c in ('\\', '%', ':')) or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise PermissionError('Ambiguous or unsupported Vault path')
        parts = value.split('/')
        if any(p in ('', '.', '..') for p in parts):
            raise PermissionError('Use a relative Vault path without traversal')
        if write:
            if len(parts) < 2 or parts[0] not in WRITE_FOLDERS:
                raise PermissionError('Writes are limited to 000_Inbox/ and 020_Projects/')
            if any(p.startswith('.') for p in parts):
                raise PermissionError('Hidden files and configuration directories are not writable')
            if not (self.vault / parts[0]).is_dir():
                raise PermissionError('Allowed write folder is unavailable')
        current = self.vault
        for index, part in enumerate(parts):
            current = current / part
            try:
                info = current.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode):
                raise PermissionError('Symbolic links are not supported')
            is_directory = stat.S_ISDIR(info.st_mode)
            if index < len(parts) - 1 and not is_directory:
                raise PermissionError('Path parent is not a directory')
            if index == len(parts) - 1:
                if directory and not is_directory:
                    raise PermissionError('Directory path refers to a file')
                if not directory and not stat.S_ISREG(info.st_mode):
                    raise PermissionError('Only regular files may be read or written')
                if write and info.st_nlink > 1:
                    raise PermissionError('Hard-linked files are not writable')
        if delete and not current.is_file():
            raise PermissionError('Deletion is limited to an existing regular file')
        # Encode URL metacharacters instead of allowing them to alter the REST route.
        return quote(value, safe='/')

    def prepare(self, name, arguments):
        if name not in ALLOWED_TOOLS:
            raise PermissionError('Tool is not enabled for ChatGPT')
        if not isinstance(arguments, dict):
            raise ValueError('Tool arguments must be an object')
        args = dict(arguments)
        if name in WRITE_TOOLS or name in {'obsidian_get_file_contents', 'obsidian_get_frontmatter'}:
            delete = name == 'obsidian_delete_file'
            if delete and args.get('confirm') is not True:
                raise PermissionError('File deletion requires confirm=true (boolean)')
            args['filepath'] = self.path(args.get('filepath'), write=name in WRITE_TOOLS, delete=delete)
        elif name == 'obsidian_batch_get_file_contents':
            values = args.get('filepaths')
            if not isinstance(values, list) or not 1 <= len(values) <= 50:
                raise ValueError('Batch reads require between 1 and 50 file paths')
            args['filepaths'] = [self.path(value) for value in values]
        elif name == 'obsidian_list_files_in_dir':
            args['dirpath'] = self.path(args.get('dirpath'), directory=True)
        elif name == 'obsidian_search_by_tag' and 'dirpath' in args:
            # This handler uses the raw path as a search filter, not as a URL.
            self.path(args['dirpath'], directory=True)
        return args
