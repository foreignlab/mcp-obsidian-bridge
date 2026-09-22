from pathlib import Path
from unittest.mock import patch

import pytest

from policy import VaultPolicy


@pytest.fixture
def vault(tmp_path):
    for name in ('000_Inbox', '020_Projects', '040_Literature_Notes'):
        (tmp_path / name).mkdir()
    return tmp_path


@pytest.mark.parametrize('folder', ['000_Inbox', '020_Projects'])
@pytest.mark.parametrize('tool', ['obsidian_put_content', 'obsidian_append_content', 'obsidian_patch_content', 'obsidian_delete_file'])
def test_writes_reach_both_allowed_folders(vault, folder, tool):
    note = f'{folder}/project/note.md'
    if tool == 'obsidian_delete_file':
        (vault / note).parent.mkdir()
        (vault / note).write_text('test')
    args = {'filepath': note, 'content': 'test', 'confirm': True}
    assert VaultPolicy(vault).prepare(tool, args)['filepath'] == note


@pytest.mark.parametrize('tool', ['obsidian_put_content', 'obsidian_append_content', 'obsidian_patch_content', 'obsidian_delete_file'])
@pytest.mark.parametrize('path', [
    '040_Literature_Notes/note.md', 'root.md', '000_Inbox-extra/note.md',
    '020_Projects2/note.md', '000_Inbox/../040_Literature_Notes/note.md',
    '000_Inbox/a/../../outside.md', '/000_Inbox/note.md',
    '000_Inbox/%2e%2e/outside.md', '000_Inbox/%252e%252e/outside.md',
    '000_Inbox%2f../outside.md', '000_Inbox\\..\\outside.md',
    '000_Inbox//note.md', '000_Inbox/./note.md', '000_Inbox/note.md/',
    '000_Inbox/.git/config', '020_Projects/.env',
    '020_Projects/.obsidian/plugins/evil/main.js', '000_Inbox/note\x00.md',
    '000_Inbox/note\n.md', '000_Inbox', '', None, 123,
])
def test_rejects_outside_or_ambiguous_write_paths(vault, tool, path):
    with pytest.raises((ValueError, PermissionError)):
        VaultPolicy(vault).prepare(tool, {'filepath': path, 'content': 'test', 'confirm': True})


@pytest.mark.parametrize('folder', ['040_Literature_Notes', '020_Projects', '000_Inbox'])
def test_read_access_is_not_limited_to_write_folders(vault, folder):
    assert VaultPolicy(vault).prepare('obsidian_get_file_contents', {'filepath': f'{folder}/note.md'})['filepath'] == f'{folder}/note.md'


def test_paths_are_url_encoded_not_interpreted_as_queries(vault):
    path = '020_Projects/日本語 #1?.md'
    got = VaultPolicy(vault).prepare('obsidian_put_content', {'filepath': path, 'content': 'test'})
    assert got['filepath'] == '020_Projects/%E6%97%A5%E6%9C%AC%E8%AA%9E%20%231%3F.md'


def test_symlink_directory_cannot_escape_write_boundary(vault):
    (vault / '000_Inbox/link').symlink_to(vault / '040_Literature_Notes', target_is_directory=True)
    with pytest.raises(PermissionError):
        VaultPolicy(vault).prepare('obsidian_put_content', {'filepath': '000_Inbox/link/note.md'})


def test_symlink_file_cannot_escape_write_boundary(vault):
    (vault / '040_Literature_Notes/note.md').write_text('unchanged')
    (vault / '020_Projects/link.md').symlink_to(vault / '040_Literature_Notes/note.md')
    with pytest.raises(PermissionError):
        VaultPolicy(vault).prepare('obsidian_put_content', {'filepath': '020_Projects/link.md'})
    assert (vault / '040_Literature_Notes/note.md').read_text() == 'unchanged'


def test_hardlink_cannot_modify_outside_file(vault):
    target = vault / '040_Literature_Notes/note.md'
    target.write_text('unchanged')
    (vault / '000_Inbox/link.md').hardlink_to(target)
    with pytest.raises(PermissionError):
        VaultPolicy(vault).prepare('obsidian_append_content', {'filepath': '000_Inbox/link.md'})


@pytest.mark.parametrize('confirm', ['false', 'true', False, 1, None])
def test_delete_requires_boolean_true(vault, confirm):
    (vault / '000_Inbox/note.md').write_text('keep')
    with pytest.raises((PermissionError, ValueError)):
        VaultPolicy(vault).prepare('obsidian_delete_file', {'filepath': '000_Inbox/note.md', 'confirm': confirm})


def test_directory_delete_is_rejected(vault):
    (vault / '020_Projects/sub').mkdir()
    with pytest.raises(PermissionError):
        VaultPolicy(vault).prepare('obsidian_delete_file', {'filepath': '020_Projects/sub', 'confirm': True})


@pytest.mark.parametrize('tool', ['new_future_tool', 'obsidian_complex_search', 'obsidian_get_periodic_note'])
def test_unreviewed_tools_are_denied(vault, tool):
    with pytest.raises(PermissionError):
        VaultPolicy(vault).prepare(tool, {})


def test_batch_read_checks_every_path(vault):
    with pytest.raises(PermissionError):
        VaultPolicy(vault).prepare('obsidian_batch_get_file_contents', {'filepaths': ['020_Projects/note.md', '../outside.md']})


def test_root_directory_listing_is_allowed(vault):
    assert VaultPolicy(vault).prepare('obsidian_list_files_in_dir', {'dirpath': ''}) == {'dirpath': ''}


def test_write_without_vault_fails_closed(tmp_path):
    with pytest.raises((ValueError, PermissionError)):
        VaultPolicy(tmp_path / 'missing')


@pytest.mark.parametrize('tool', ['obsidian_put_content', 'obsidian_append_content', 'obsidian_patch_content', 'obsidian_delete_file'])
def test_configured_folder_allows_writes_and_replaces_defaults(vault, tool):
    (vault / '045_LLM_WIKI/sub').mkdir(parents=True)
    (vault / '045_LLM_WIKI/sub/note.md').write_text('test')
    policy = VaultPolicy(vault, write_folders=['045_LLM_WIKI'])
    args = {'filepath': '045_LLM_WIKI/sub/note.md', 'confirm': True}
    assert policy.prepare(tool, args)['filepath'] == args['filepath']
    for path in ('000_Inbox/note.md', '045_LLM_WIKI-extra/note.md',
                 '045_LLM_WIKI/.hidden.md', '045_LLM_WIKI/../root.md'):
        with pytest.raises(PermissionError):
            policy.prepare(tool, {'filepath': path, 'confirm': True})


def test_empty_allowlist_disables_writes_but_preserves_reads(vault):
    policy = VaultPolicy(vault, write_folders=[])
    with pytest.raises(PermissionError):
        policy.path('000_Inbox/note.md', write=True)
    assert policy.path('000_Inbox/note.md') == '000_Inbox/note.md'


@pytest.mark.parametrize('folders', [
    None, '', '045_LLM_WIKI', {}, 42, [None], [42], [''], ['.obsidian'],
    ['..'], ['/045_LLM_WIKI'], ['045_LLM_WIKI/'], ['parent/child'],
    ['a\\b'], ['%2e%2e'], ['a:b'], ['a\n'], ['a\x00'], ['a\x7f'],
    ['*'], ['045_*'], ['a?'], [' 045_LLM_WIKI'], ['045_LLM_WIKI '],
])
def test_invalid_allowlist_fails_closed(vault, folders):
    with pytest.raises(ValueError):
        VaultPolicy(vault, write_folders=folders)


def test_configured_root_symlink_and_hardlink_are_rejected(vault):
    (vault / '045_LLM_WIKI').symlink_to(vault / '040_Literature_Notes', target_is_directory=True)
    policy = VaultPolicy(vault, write_folders=['045_LLM_WIKI'])
    with pytest.raises(PermissionError):
        policy.path('045_LLM_WIKI/note.md', write=True)
    (vault / '045_LLM_WIKI').unlink()
    (vault / '045_LLM_WIKI').mkdir()
    target = vault / '040_Literature_Notes/note.md'
    target.write_text('protected')
    (vault / '045_LLM_WIKI/link.md').hardlink_to(target)
    with pytest.raises(PermissionError):
        policy.path('045_LLM_WIKI/link.md', write=True)


def test_unavailable_configured_folder_is_rejected(vault):
    with pytest.raises(PermissionError):
        VaultPolicy(vault, write_folders=['missing']).path('missing/note.md', write=True)
