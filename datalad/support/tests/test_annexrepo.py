# emacs: -*- mode: python; py-indent-offset: 4; tab-width: 4; indent-tabs-mode: nil -*-
# ex: set sts=4 ts=4 sw=4 noet:
# ## ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
#
#   See COPYING file distributed along with the datalad package for the
#   copyright and license terms.
#
# ## ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
"""Test implementation of class AnnexRepo

"""

from datalad.tests.utils import known_failure_v6

import logging
from functools import partial
from glob import glob
import os
from os import mkdir
from os.path import join as opj
from os.path import basename
from os.path import realpath
from os.path import relpath
from os.path import curdir
from os.path import pardir
from os.path import exists
from shutil import copyfile
from nose.tools import assert_not_is_instance

from six import text_type

from six.moves.urllib.parse import urljoin
from six.moves.urllib.parse import urlsplit

import git
from git import GitCommandError
from mock import patch
import gc

from datalad.cmd import Runner

from datalad.support.external_versions import external_versions
from datalad.support import path as op

from datalad.support.sshconnector import get_connection_hash

from datalad.utils import on_windows
from datalad.utils import chpwd
from datalad.utils import rmtree
from datalad.utils import linux_distribution_name
from datalad.utils import unlink

from datalad.tests.utils import assert_cwd_unchanged
from datalad.tests.utils import with_testrepos
from datalad.tests.utils import with_tempfile
from datalad.tests.utils import with_tree
from datalad.tests.utils import create_tree
from datalad.tests.utils import with_parametric_batch
from datalad.tests.utils import assert_dict_equal as deq_
from datalad.tests.utils import assert_is_instance
from datalad.tests.utils import assert_false
from datalad.tests.utils import assert_in
from datalad.tests.utils import assert_is
from datalad.tests.utils import assert_not_in
from datalad.tests.utils import assert_re_in
from datalad.tests.utils import assert_raises
from datalad.tests.utils import assert_not_equal
from datalad.tests.utils import assert_equal
from datalad.tests.utils import assert_true
from datalad.tests.utils import eq_
from datalad.tests.utils import ok_
from datalad.tests.utils import ok_git_config_not_empty
from datalad.tests.utils import ok_annex_get
from datalad.tests.utils import ok_clean_git
from datalad.tests.utils import ok_file_under_git
from datalad.tests.utils import ok_file_has_content
from datalad.tests.utils import swallow_logs
from datalad.tests.utils import swallow_outputs
from datalad.tests.utils import local_testrepo_flavors
from datalad.tests.utils import serve_path_via_http
from datalad.tests.utils import get_most_obscure_supported_name
from datalad.tests.utils import OBSCURE_FILENAME
from datalad.tests.utils import SkipTest
from datalad.tests.utils import skip_if
from datalad.tests.utils import skip_ssh
from datalad.tests.utils import find_files
from datalad.tests.utils import slow

from datalad.support.exceptions import CommandError
from datalad.support.exceptions import CommandNotAvailableError
from datalad.support.exceptions import FileNotInRepositoryError
from datalad.support.exceptions import FileNotInAnnexError
from datalad.support.exceptions import FileInGitError
from datalad.support.exceptions import OutOfSpaceError
from datalad.support.exceptions import RemoteNotAvailableError
from datalad.support.exceptions import OutdatedExternalDependency
from datalad.support.exceptions import MissingExternalDependency
from datalad.support.exceptions import InsufficientArgumentsError
from datalad.support.exceptions import AnnexBatchCommandError
from datalad.support.exceptions import IncompleteResultsError

from datalad.support.gitrepo import GitRepo

# imports from same module:
from datalad.support.annexrepo import (
    AnnexRepo,
    ProcessAnnexProgressIndicators,
    _get_size_from_perc_complete,
)
from .utils import check_repo_deals_with_inode_change


@assert_cwd_unchanged
@with_testrepos('.*annex.*')
@with_tempfile
def test_AnnexRepo_instance_from_clone(src, dst):

    ar = AnnexRepo.clone(src, dst)
    assert_is_instance(ar, AnnexRepo, "AnnexRepo was not created.")
    ok_(os.path.exists(os.path.join(dst, '.git', 'annex')))

    # do it again should raise GitCommandError since git will notice
    # there's already a git-repo at that path and therefore can't clone to `dst`
    with swallow_logs(new_level=logging.WARN) as cm:
        assert_raises(GitCommandError, AnnexRepo.clone, src, dst)
        if git.__version__ != "1.0.2" and git.__version__ != "2.0.5":
            assert("already exists" in cm.out)


@assert_cwd_unchanged
@with_testrepos('.*annex.*', flavors=local_testrepo_flavors)
def test_AnnexRepo_instance_from_existing(path):

    ar = AnnexRepo(path)
    assert_is_instance(ar, AnnexRepo, "AnnexRepo was not created.")
    ok_(os.path.exists(os.path.join(path, '.git')))


@assert_cwd_unchanged
@with_tempfile
def test_AnnexRepo_instance_brand_new(path):

    GitRepo(path)
    assert_raises(RuntimeError, AnnexRepo, path, create=False)

    ar = AnnexRepo(path)
    assert_is_instance(ar, AnnexRepo, "AnnexRepo was not created.")
    ok_(os.path.exists(os.path.join(path, '.git')))


@assert_cwd_unchanged
@with_testrepos('.*annex.*')
@with_tempfile
def test_AnnexRepo_crippled_filesystem(src, dst):

    ar = AnnexRepo.clone(src, dst)

    # fake git-annex entries in .git/config:
    writer = ar.repo.config_writer()
    writer.set_value("annex", "crippledfilesystem", True)
    writer.release()
    ok_(ar.is_crippled_fs())
    writer.set_value("annex", "crippledfilesystem", False)
    writer.release()
    assert_false(ar.is_crippled_fs())
    # since we can't remove the entry, just rename it to fake its absence:
    writer.rename_section("annex", "removed")
    writer.set_value("annex", "something", "value")
    writer.release()
    assert_false(ar.is_crippled_fs())


@assert_cwd_unchanged
@with_testrepos('.*annex.*', flavors=local_testrepo_flavors)
def test_AnnexRepo_is_direct_mode(path):

    ar = AnnexRepo(path)
    eq_(ar.config.getbool("annex", "direct", False),
        ar.is_direct_mode())


@with_tempfile()
def test_AnnexRepo_is_direct_mode_gitrepo(path):
    repo = GitRepo(path, create=True)
    # artificially make .git/annex so no annex section gets initialized
    # in .git/config.  We did manage somehow to make this happen (via publish)
    # but didn't reproduce yet, so just creating manually
    mkdir(opj(repo.path, '.git', 'annex'))
    ar = AnnexRepo(path, init=False, create=False)
    # It is unlikely though that annex would be in direct mode (requires explicit)
    # annex magic, without having annex section under .git/config
    dm = ar.is_direct_mode()

    if ar.is_crippled_fs() or on_windows:
        ok_(dm)
    else:
        assert_false(dm)


@assert_cwd_unchanged
@with_testrepos('.*annex.*', flavors=local_testrepo_flavors)
@with_tempfile
def test_AnnexRepo_get_file_key(src, annex_path):

    ar = AnnexRepo.clone(src, annex_path)

    # test-annex.dat should return the correct key:
    test_annex_key = \
        'SHA256E-s28' \
        '--2795fb26981c5a687b9bf44930cc220029223f472cea0f0b17274f4473181e7b.dat'
    eq_(ar.get_file_key("test-annex.dat"), test_annex_key)

    # and should take a list with an empty string as result, if a file wasn't
    # in annex:
    eq_(
        ar.get_file_key(["filenotpresent.wtf", "test-annex.dat"]),
        ['', test_annex_key]
    )

    # test.dat is actually in git
    # should raise Exception; also test for polymorphism
    assert_raises(IOError, ar.get_file_key, "test.dat")
    assert_raises(FileNotInAnnexError, ar.get_file_key, "test.dat")
    assert_raises(FileInGitError, ar.get_file_key, "test.dat")

    # filenotpresent.wtf doesn't even exist
    assert_raises(IOError, ar.get_file_key, "filenotpresent.wtf")

    # if we force batch mode, no failure for not present or not annexed files
    eq_(ar.get_file_key("filenotpresent.wtf", batch=True), '')
    eq_(ar.get_file_key("test.dat", batch=True), '')
    eq_(ar.get_file_key("test-annex.dat", batch=True), test_annex_key)



@with_tempfile(mkdir=True)
def test_AnnexRepo_get_outofspace(annex_path):
    ar = AnnexRepo(annex_path, create=True)

    def raise_cmderror(*args, **kwargs):
        raise CommandError(
            cmd="whatever",
            stderr="junk around not enough free space, need 905.6 MB more and after"
        )

    with patch.object(AnnexRepo, '_run_annex_command', raise_cmderror) as cma, \
            assert_raises(OutOfSpaceError) as cme:
        ar.get("file")
    exc = cme.exception
    eq_(exc.sizemore_msg, '905.6 MB')
    assert_re_in(".*annex (find|get). needs 905.6 MB more", str(exc))


@with_testrepos('basic_annex', flavors=['local'])
def test_AnnexRepo_get_remote_na(path):
    ar = AnnexRepo(path)

    with assert_raises(RemoteNotAvailableError) as cme:
        ar.get('test-annex.dat', options=["--from=NotExistingRemote"])
    eq_(cme.exception.remote, "NotExistingRemote")

    # and similar one whenever invoking with remote parameter
    with assert_raises(RemoteNotAvailableError) as cme:
        ar.get('test-annex.dat', remote="NotExistingRemote")
    eq_(cme.exception.remote, "NotExistingRemote")


# 1 is enough to test file_has_content
@with_parametric_batch
@with_testrepos('.*annex.*', flavors=['local'], count=1)
@with_tempfile
def test_AnnexRepo_file_has_content(batch, src, annex_path):
    ar = AnnexRepo.clone(src, annex_path)
    testfiles = ["test-annex.dat", "test.dat"]

    eq_(ar.file_has_content(testfiles), [False, False])

    ok_annex_get(ar, "test-annex.dat")
    eq_(ar.file_has_content(testfiles, batch=batch), [True, False])
    eq_(ar.file_has_content(testfiles[:1], batch=batch), [True])

    eq_(ar.file_has_content(testfiles + ["bogus.txt"], batch=batch),
        [True, False, False])

    assert_false(ar.file_has_content("bogus.txt", batch=batch))
    ok_(ar.file_has_content("test-annex.dat", batch=batch))

    ar.unlock(["test-annex.dat"])
    eq_(ar.file_has_content(["test-annex.dat"], batch=batch),
        [ar.supports_unlocked_pointers])
    with open(opj(annex_path, "test-annex.dat"), "a") as ofh:
        ofh.write("more")
    eq_(ar.file_has_content(["test-annex.dat"], batch=batch),
        [False])


# 1 is enough to test
@with_parametric_batch
@with_testrepos('.*annex.*', flavors=['local'], count=1)
@with_tempfile
def test_AnnexRepo_is_under_annex(batch, src, annex_path):
    ar = AnnexRepo.clone(src, annex_path)

    with open(opj(annex_path, 'not-committed.txt'), 'w') as f:
        f.write("aaa")

    testfiles = ["test-annex.dat", "not-committed.txt", "INFO.txt"]
    # wouldn't change
    target_value = [True, False, False]
    eq_(ar.is_under_annex(testfiles, batch=batch), target_value)

    ok_annex_get(ar, "test-annex.dat")
    eq_(ar.is_under_annex(testfiles, batch=batch), target_value)
    eq_(ar.is_under_annex(testfiles[:1], batch=batch), target_value[:1])
    eq_(ar.is_under_annex(testfiles[1:], batch=batch), target_value[1:])

    eq_(ar.is_under_annex(testfiles + ["bogus.txt"], batch=batch),
                 target_value + [False])

    assert_false(ar.is_under_annex("bogus.txt", batch=batch))
    ok_(ar.is_under_annex("test-annex.dat", batch=batch))

    ar.unlock(["test-annex.dat"])
    eq_(ar.is_under_annex(["test-annex.dat"], batch=batch),
        [ar.supports_unlocked_pointers])
    with open(opj(annex_path, "test-annex.dat"), "a") as ofh:
        ofh.write("more")
    eq_(ar.is_under_annex(["test-annex.dat"], batch=batch),
        [False])


@with_tree(tree=(('about.txt', 'Lots of abouts'),
                 ('about2.txt', 'more abouts'),
                 ('d', {'sub.txt': 'more stuff'})))
@serve_path_via_http()
@with_tempfile
def test_AnnexRepo_web_remote(sitepath, siteurl, dst):

    ar = AnnexRepo(dst, create=True)
    testurl = urljoin(siteurl, 'about.txt')
    testurl2 = urljoin(siteurl, 'about2.txt')
    testurl3 = urljoin(siteurl, 'd/sub.txt')
    url_file_prefix = urlsplit(testurl).netloc.split(':')[0]
    testfile = '%s_about.txt' % url_file_prefix
    testfile2 = '%s_about2.txt' % url_file_prefix
    testfile3 = opj('d', 'sub.txt')

    # get the file from remote
    with swallow_outputs() as cmo:
        ar.add_urls([testurl])
    l = ar.whereis(testfile)
    assert_in(ar.WEB_UUID, l)
    eq_(len(l), 2)
    ok_(ar.file_has_content(testfile))

    # output='full'
    lfull = ar.whereis(testfile, output='full')
    eq_(set(lfull), set(l))  # the same entries
    non_web_remote = l[1 - l.index(ar.WEB_UUID)]
    assert_in('urls', lfull[non_web_remote])
    eq_(lfull[non_web_remote]['urls'], [])
    assert_not_in('uuid', lfull[ar.WEB_UUID])  # no uuid in the records
    eq_(lfull[ar.WEB_UUID]['urls'], [testurl])

    # --all and --key are incompatible
    assert_raises(CommandError, ar.whereis, [], options='--all', output='full', key=True)

    # output='descriptions'
    ldesc = ar.whereis(testfile, output='descriptions')
    eq_(set(ldesc), set([v['description'] for v in lfull.values()]))

    # info w/ and w/o fast mode
    for fast in [True, False]:
        info = ar.info(testfile, fast=fast)
        eq_(info['size'], 14)
        assert(info['key'])  # that it is there
        info_batched = ar.info(testfile, batch=True, fast=fast)
        eq_(info, info_batched)
        # while at it ;)
        with swallow_outputs() as cmo:
            eq_(ar.info('nonexistent', batch=False), None)
            eq_(ar.info('nonexistent-batch', batch=True), None)
            eq_(cmo.out, '')
            eq_(cmo.err, '')
            ar.precommit()  # to stop all the batched processes for swallow_outputs

    # annex repo info
    repo_info = ar.repo_info(fast=False)
    eq_(repo_info['local annex size'], 14)
    eq_(repo_info['backend usage'], {'SHA256E': 1})
    # annex repo info in fast mode
    repo_info_fast = ar.repo_info(fast=True)
    # doesn't give much testable info, so just comparing a subset for match with repo_info info
    eq_(repo_info_fast['semitrusted repositories'], repo_info['semitrusted repositories'])
    #import pprint; pprint.pprint(repo_info)

    # remove the remote
    ar.rm_url(testfile, testurl)
    l = ar.whereis(testfile)
    assert_not_in(ar.WEB_UUID, l)
    eq_(len(l), 1)

    # now only 1 copy; drop should fail
    res = ar.drop(testfile)
    eq_(res['command'], 'drop')
    eq_(res['success'], False)
    assert_in('adjust numcopies', res['note'])

    # read the url using different method
    ar.add_url_to_file(testfile, testurl)
    l = ar.whereis(testfile)
    assert_in(ar.WEB_UUID, l)
    eq_(len(l), 2)
    ok_(ar.file_has_content(testfile))

    # 2 known copies now; drop should succeed
    ar.drop(testfile)
    l = ar.whereis(testfile)
    assert_in(ar.WEB_UUID, l)
    eq_(len(l), 1)
    assert_false(ar.file_has_content(testfile))
    lfull = ar.whereis(testfile, output='full')
    assert_not_in(non_web_remote, lfull) # not present -- so not even listed

    # multiple files/urls
    # get the file from remote
    with swallow_outputs() as cmo:
        ar.add_urls([testurl2])

    # TODO: if we ask for whereis on all files, we should get for all files
    lall = ar.whereis('.')
    eq_(len(lall), 2)
    for e in lall:
        assert(isinstance(e, list))
    # but we don't know which one for which file. need a 'full' one for that
    lall_full = ar.whereis('.', output='full')
    ok_(ar.file_has_content(testfile2))
    ok_(lall_full[testfile2][non_web_remote]['here'])
    eq_(set(lall_full), {testfile, testfile2})

    # add a bogus 2nd url to testfile

    someurl = "http://example.com/someurl"
    ar.add_url_to_file(testfile, someurl, options=['--relaxed'])
    lfull = ar.whereis(testfile, output='full')
    eq_(set(lfull[ar.WEB_UUID]['urls']), {testurl, someurl})

    # and now test with a file in subdirectory
    subdir = opj(dst, 'd')
    os.mkdir(subdir)
    with swallow_outputs() as cmo:
        ar.add_url_to_file(testfile3, url=testurl3)
    ok_file_has_content(opj(dst, testfile3), 'more stuff')
    eq_(set(ar.whereis(testfile3)), {ar.WEB_UUID, non_web_remote})
    eq_(set(ar.whereis(testfile3, output='full').keys()), {ar.WEB_UUID, non_web_remote})

    # and if we ask for both files
    info2 = ar.info([testfile, testfile3])
    eq_(set(info2), {testfile, testfile3})
    eq_(info2[testfile3]['size'], 10)

    full = ar.whereis([], options='--all', output='full')
    eq_(len(full.keys()), 3)  # we asked for all files -- got 3 keys
    assert_in(ar.WEB_UUID, full['SHA256E-s10--a978713ea759207f7a6f9ebc9eaebd1b40a69ae408410ddf544463f6d33a30e1.txt'])

    # which would work even if we cd to that subdir, but then we should use explicit curdir
    with chpwd(subdir):
        cur_subfile = opj(curdir, 'sub.txt')
        eq_(set(ar.whereis(cur_subfile)), {ar.WEB_UUID, non_web_remote})
        eq_(set(ar.whereis(cur_subfile, output='full').keys()), {ar.WEB_UUID, non_web_remote})
        testfiles = [cur_subfile, opj(pardir, testfile)]
        info2_ = ar.info(testfiles)
        # Should maintain original relative file names
        eq_(set(info2_), set(testfiles))
        eq_(info2_[cur_subfile]['size'], 10)


@with_tree(tree={"a.txt": "a",
                 "b": "b",
                 OBSCURE_FILENAME: "c",
                 "subdir": {"d": "d", "e": "e"}})
def test_find_batch_equivalence(path):
    ar = AnnexRepo(path)
    files = ["a.txt", "b", OBSCURE_FILENAME]
    ar.add(files + ["subdir"])
    ar.commit("add files")
    query = ["not-there"] + files
    expected = {f: f for f in files}
    expected.update({"not-there": ""})
    eq_(expected, ar.find(query, batch=True))
    eq_(expected, ar.find(query))
    # If we give a subdirectory, we split that output.
    eq_(set(ar.find(["subdir"])["subdir"]), {"subdir/d", "subdir/e"})
    eq_(ar.find(["subdir"]), ar.find(["subdir"], batch=True))


@with_tempfile(mkdir=True)
def test_repo_info(path):
    repo = AnnexRepo(path)
    info = repo.repo_info()  # works in empty repo without crashing
    eq_(info['local annex size'], 0)
    eq_(info['size of annexed files in working tree'], 0)

    def get_custom(custom={}):
        """Need a helper since repo_info modifies in place so we should generate
        new each time
        """
        custom_json = {
            'available local disk space': 'unknown',
            'size of annexed files in working tree': "0",
            'success': True,
            'command': 'info',
        }
        if custom:
            custom_json.update(custom)
        return [custom_json]

    with patch.object(
            repo, '_run_annex_command_json',
            return_value=get_custom()):
        info = repo.repo_info()
        eq_(info['available local disk space'], None)

    with patch.object(
        repo, '_run_annex_command_json',
        return_value=get_custom({
            "available local disk space": "19193986496 (+100000 reserved)"})):
        info = repo.repo_info()
        eq_(info['available local disk space'], 19193986496)


@with_testrepos('.*annex.*', flavors=['local', 'network'])
@with_tempfile
def test_AnnexRepo_migrating_backends(src, dst):
    ar = AnnexRepo.clone(src, dst, backend='MD5')
    eq_(ar.default_backends, ['MD5'])
    # GitPython has a bug which causes .git/config being wiped out
    # under Python3, triggered by collecting its config instance I guess
    gc.collect()
    ok_git_config_not_empty(ar)  # Must not blow, see https://github.com/gitpython-developers/GitPython/issues/333

    filename = get_most_obscure_supported_name()
    filename_abs = os.path.join(dst, filename)
    f = open(filename_abs, 'w')
    f.write("What to write?")
    f.close()

    ar.add(filename, backend='MD5')
    eq_(ar.get_file_backend(filename), 'MD5')
    eq_(ar.get_file_backend('test-annex.dat'), 'SHA256E')

    # migrating will only do, if file is present
    ok_annex_get(ar, 'test-annex.dat')

    eq_(ar.get_file_backend('test-annex.dat'), 'SHA256E')
    ar.migrate_backend('test-annex.dat')
    eq_(ar.get_file_backend('test-annex.dat'), 'MD5')

    ar.migrate_backend('', backend='SHA1')
    eq_(ar.get_file_backend(filename), 'SHA1')
    eq_(ar.get_file_backend('test-annex.dat'), 'SHA1')


tree1args = dict(
    tree=(
        ('firstfile', 'whatever'),
        ('secondfile', 'something else'),
        ('remotefile', 'pretends to be remote'),
        ('faraway', 'incredibly remote')),
)

# keys for files if above tree is generated and added to annex with MD5E backend
tree1_md5e_keys = {
    'firstfile': 'MD5E-s8--008c5926ca861023c1d2a36653fd88e2',
    'faraway': 'MD5E-s17--5b849ed02f914d3bbb5038fe4e3fead9',
    'secondfile': 'MD5E-s14--6c7ba9c5a141421e1c03cb9807c97c74',
    'remotefile': 'MD5E-s21--bf7654b3de20d5926d407ea7d913deb0'
}


@with_tree(**tree1args)
def __test_get_md5s(path):
    # was used just to generate above dict
    annex = AnnexRepo(path, init=True, backend='MD5E')
    files = [basename(f) for f in find_files('.*', path)]
    annex.add(files)
    annex.commit()
    print({f: annex.get_file_key(f) for f in files})


@with_parametric_batch
@with_tree(**tree1args)
def test_dropkey(batch, path):
    kw = {'batch': batch}
    annex = AnnexRepo(path, init=True, backend='MD5E')
    files = list(tree1_md5e_keys)
    annex.add(files)
    annex.commit()
    # drop one key
    annex.drop_key(tree1_md5e_keys[files[0]], **kw)
    # drop multiple
    annex.drop_key([tree1_md5e_keys[f] for f in files[1:3]], **kw)
    # drop already dropped -- should work as well atm
    # https://git-annex.branchable.com/bugs/dropkey_--batch_--json_--force_is_always_succesfull
    annex.drop_key(tree1_md5e_keys[files[0]], **kw)
    # and a mix with already dropped or not
    annex.drop_key(list(tree1_md5e_keys.values()), **kw)


@with_tree(**tree1args)
@serve_path_via_http()
def test_AnnexRepo_backend_option(path, url):
    ar = AnnexRepo(path, backend='MD5')

    # backend recorded in .gitattributes
    eq_(ar.get_gitattributes('.')['.']['annex.backend'], 'MD5')

    ar.add('firstfile', backend='SHA1')
    ar.add('secondfile')
    eq_(ar.get_file_backend('firstfile'), 'SHA1')
    eq_(ar.get_file_backend('secondfile'), 'MD5')

    with swallow_outputs() as cmo:
        # must be added under different name since annex 20160114
        ar.add_url_to_file('remotefile2', url + 'remotefile', backend='SHA1')
    eq_(ar.get_file_backend('remotefile2'), 'SHA1')

    with swallow_outputs() as cmo:
        ar.add_urls([url + 'faraway'], backend='SHA1')
    # TODO: what's the annex-generated name of this?
    # For now, workaround:
    ok_(ar.get_file_backend(f) == 'SHA1'
        for f in ar.get_indexed_files() if 'faraway' in f)


@with_testrepos('.*annex.*', flavors=local_testrepo_flavors)
@with_tempfile
def test_AnnexRepo_get_file_backend(src, dst):
    #init local test-annex before cloning:
    AnnexRepo(src)

    ar = AnnexRepo.clone(src, dst)

    eq_(ar.get_file_backend('test-annex.dat'), 'SHA256E')
    # no migration
    ok_annex_get(ar, 'test-annex.dat', network=False)
    ar.migrate_backend('test-annex.dat', backend='SHA1')
    eq_(ar.get_file_backend('test-annex.dat'), 'SHA1')


@with_tempfile
def test_AnnexRepo_always_commit(path):

    repo = AnnexRepo(path)
    runner = Runner(cwd=path)
    file1 = get_most_obscure_supported_name() + "_1"
    file2 = get_most_obscure_supported_name() + "_2"
    with open(opj(path, file1), 'w') as f:
        f.write("First file.")
    with open(opj(path, file2), 'w') as f:
        f.write("Second file.")

    # always_commit == True is expected to be default
    repo.add(file1)

    # Now git-annex log should show the addition:
    out, err = repo._run_annex_command('log')
    out_list = out.rstrip(os.linesep).splitlines()
    eq_(len(out_list), 1)
    assert_in(file1, out_list[0])
    # check git log of git-annex branch:
    # expected: initial creation, update (by annex add) and another
    # update (by annex log)
    out, err = runner.run(['git', 'log', 'git-annex'])
    num_commits = len([commit
                       for commit in out.rstrip(os.linesep).split('\n')
                       if commit.startswith('commit')])
    eq_(num_commits, 3)

    repo.always_commit = False
    repo.add(file2)

    # No additional git commit:
    out, err = runner.run(['git', 'log', 'git-annex'])
    num_commits = len([commit
                       for commit in out.rstrip(os.linesep).split('\n')
                       if commit.startswith('commit')])
    eq_(num_commits, 3)

    repo.always_commit = True

    # Still one commit only in git-annex log,
    # but 'git annex log' was called when always_commit was true again,
    # so it should commit the addition at the end. Calling it again should then
    # show two commits.
    out, err = repo._run_annex_command('log')
    out_list = out.rstrip(os.linesep).splitlines()
    eq_(len(out_list), 2, "Output:\n%s" % out_list)
    assert_in(file1, out_list[0])
    assert_in("recording state in git", out_list[1])

    out, err = repo._run_annex_command('log')
    out_list = out.rstrip(os.linesep).splitlines()
    eq_(len(out_list), 2, "Output:\n%s" % out_list)
    assert_in(file1, out_list[0])
    assert_in(file2, out_list[1])

    # Now git knows as well:
    out, err = runner.run(['git', 'log', 'git-annex'])
    num_commits = len([commit
                       for commit in out.rstrip(os.linesep).split('\n')
                       if commit.startswith('commit')])
    eq_(num_commits, 4)


@with_testrepos('basic_annex', flavors=['local'])
@with_tempfile
def test_AnnexRepo_on_uninited_annex(origin, path):
    # "Manually" clone to avoid initialization:
    from datalad.cmd import Runner
    runner = Runner()
    _ = runner(["git", "clone", origin, path], expect_stderr=True)

    assert_false(exists(opj(path, '.git', 'annex'))) # must not be there for this test to be valid
    annex = AnnexRepo(path, create=False, init=False)  # so we can initialize without
    # and still can get our things
    assert_false(annex.file_has_content('test-annex.dat'))
    with swallow_outputs():
        annex.get('test-annex.dat')
        ok_(annex.file_has_content('test-annex.dat'))


@assert_cwd_unchanged
@with_tempfile
def test_AnnexRepo_commit(path):

    ds = AnnexRepo(path, create=True)
    filename = opj(path, get_most_obscure_supported_name())
    with open(filename, 'w') as f:
        f.write("File to add to git")
    ds.add(filename, git=True)

    assert_raises(AssertionError, ok_clean_git, path, annex=True)

    ds.commit("test _commit")
    ok_clean_git(path, annex=True)

    # nothing to commit doesn't raise by default:
    ds.commit()
    # but does with careless=False:
    assert_raises(CommandError, ds.commit, careless=False)

    # committing untracked file raises:
    with open(opj(path, "untracked"), "w") as f:
        f.write("some")
    assert_raises(FileNotInRepositoryError, ds.commit, files="untracked")
    # not existing file as well:
    assert_raises(FileNotInRepositoryError, ds.commit, files="not-existing")


@with_testrepos('.*annex.*', flavors=['clone'])
def test_AnnexRepo_add_to_annex(path):

    # Note: Some test repos appears to not be initialized.
    #       Therefore: 'init=True'
    # TODO: Fix these repos finally!
    # clone as provided by with_testrepos:
    repo = AnnexRepo(path, create=False, init=True)

    ok_clean_git(repo, annex=True, ignore_submodules=True)
    filename = get_most_obscure_supported_name()
    filename_abs = opj(repo.path, filename)
    with open(filename_abs, "w") as f:
        f.write("some")

    out_json = repo.add(filename)
    # file is known to annex:
    assert_true(os.path.islink(filename_abs),
                "Annexed file is not a link.")
    assert_in('key', out_json)
    key = repo.get_file_key(filename)
    assert_false(key == '')
    assert_equal(key, out_json['key'])
    ok_(repo.file_has_content(filename))

    # uncommitted:
    ok_(repo.dirty)

    repo.commit("Added file to annex.")
    ok_clean_git(repo, annex=True, ignore_submodules=True)

    # now using commit/msg options:
    filename = "another.txt"
    with open(opj(repo.path, filename), "w") as f:
        f.write("something else")

    repo.add(filename)
    repo.commit(msg="Added another file to annex.")
    # known to annex:
    ok_(repo.get_file_key(filename))
    ok_(repo.file_has_content(filename))

    # and committed:
    ok_clean_git(repo, annex=True, ignore_submodules=True)


@with_testrepos('.*annex.*', flavors=['clone'])
def test_AnnexRepo_add_to_git(path):

    # Note: Some test repos appears to not be initialized.
    #       Therefore: 'init=True'
    # TODO: Fix these repos finally!

    # clone as provided by with_testrepos:
    repo = AnnexRepo(path, create=False, init=True)

    ok_clean_git(repo, annex=True, ignore_submodules=True)
    filename = get_most_obscure_supported_name()
    with open(opj(repo.path, filename), "w") as f:
        f.write("some")
    repo.add(filename, git=True)

    # not in annex, but in git:
    assert_raises(FileInGitError, repo.get_file_key, filename)
    # uncommitted:
    ok_(repo.dirty)
    repo.commit("Added file to annex.")
    ok_clean_git(repo, annex=True, ignore_submodules=True)

    # now using commit/msg options:
    filename = "another.txt"
    with open(opj(repo.path, filename), "w") as f:
        f.write("something else")

    repo.add(filename, git=True)
    repo.commit(msg="Added another file to annex.")
    # not in annex, but in git:
    assert_raises(FileInGitError, repo.get_file_key, filename)

    # and committed:
    ok_clean_git(repo, annex=True, ignore_submodules=True)


@with_testrepos('.*annex.*', flavors=['local'])
# TODO: flavor 'network' has wrong content for test-annex.dat!
@with_tempfile
def test_AnnexRepo_get(src, dst):

    annex = AnnexRepo.clone(src, dst)
    assert_is_instance(annex, AnnexRepo, "AnnexRepo was not created.")
    testfile = 'test-annex.dat'
    testfile_abs = opj(dst, testfile)
    assert_false(annex.file_has_content("test-annex.dat"))
    with swallow_outputs():
        annex.get(testfile)
    ok_(annex.file_has_content("test-annex.dat"))
    ok_file_has_content(testfile_abs, "content to be annex-addurl'd", strip=True)

    called = []
    # for some reason yoh failed mock to properly just call original func
    orig_run = annex._run_annex_command

    def check_run(cmd, annex_options, **kwargs):
        called.append(cmd)
        if cmd == 'find':
            assert_not_in('-J5', annex_options)
        elif cmd == 'get':
            assert_in('-J5', annex_options)
        else:
            raise AssertionError(
                "no other commands so far should be ran. Got %s, %s" %
                (cmd, annex_options)
            )
        return orig_run(cmd, annex_options=annex_options, **kwargs)

    annex.drop(testfile)
    with patch.object(AnnexRepo, '_run_annex_command',
                      side_effect=check_run, auto_spec=True), \
            swallow_outputs():
        annex.get(testfile, jobs=5)
    eq_(called, ['find', 'get'])
    ok_file_has_content(testfile_abs, "content to be annex-addurl'd", strip=True)


# TODO:
#def init_remote(self, name, options):
#def enable_remote(self, name):

@with_testrepos('basic_annex$', flavors=['clone'])
@with_tempfile
def _test_AnnexRepo_get_contentlocation(batch, path, work_dir_outside):
    annex = AnnexRepo(path, create=False, init=False)
    fname = 'test-annex.dat'
    key = annex.get_file_key(fname)
    # TODO: see if we can avoid this or specify custom exception
    eq_(annex.get_contentlocation(key, batch=batch), '')

    with swallow_outputs() as cmo:
        annex.get(fname)
    key_location = annex.get_contentlocation(key, batch=batch)
    assert(key_location)
    # they both should point to the same location eventually
    eq_(os.path.realpath(opj(annex.path, fname)),
        os.path.realpath(opj(annex.path, key_location)))

    # test how it would look if done under a subdir of the annex:
    with chpwd(opj(annex.path, 'subdir'), mkdir=True):
        key_location = annex.get_contentlocation(key, batch=batch)
        # they both should point to the same location eventually
        eq_(os.path.realpath(opj(annex.path, fname)),
            os.path.realpath(opj(annex.path, key_location)))

    # test how it would look if done under a dir outside of the annex:
    with chpwd(work_dir_outside, mkdir=True):
        key_location = annex.get_contentlocation(key, batch=batch)
        # they both should point to the same location eventually
        eq_(os.path.realpath(opj(annex.path, fname)),
            os.path.realpath(opj(annex.path, key_location)))


def test_AnnexRepo_get_contentlocation():
    for batch in (False, True):
        yield _test_AnnexRepo_get_contentlocation, batch


@with_tree(tree=(('about.txt', 'Lots of abouts'),
                 ('about2.txt', 'more abouts'),
                 ('about2_.txt', 'more abouts_'),
                 ('d', {'sub.txt': 'more stuff'})))
@serve_path_via_http()
@with_tempfile
def test_AnnexRepo_addurl_to_file_batched(sitepath, siteurl, dst):

    if os.environ.get('DATALAD_FAKE__DATES'):
        raise SkipTest(
            "Faked dates are enabled; skipping batched addurl tests")

    ar = AnnexRepo(dst, create=True)
    testurl = urljoin(siteurl, 'about.txt')
    testurl2 = urljoin(siteurl, 'about2.txt')
    testurl2_ = urljoin(siteurl, 'about2_.txt')
    testurl3 = urljoin(siteurl, 'd/sub.txt')
    url_file_prefix = urlsplit(testurl).netloc.split(':')[0]
    testfile = 'about.txt'
    testfile2 = 'about2.txt'
    testfile2_ = 'about2_.txt'
    testfile3 = opj('d', 'sub.txt')

    # add to an existing but not committed file
    # TODO: __call__ of the BatchedAnnex must be checked to be called
    copyfile(opj(sitepath, 'about.txt'), opj(dst, testfile))
    # must crash sensibly since file exists, we shouldn't addurl to non-annexed files
    with assert_raises(AnnexBatchCommandError):
        ar.add_url_to_file(testfile, testurl, batch=True)

    # Remove it and re-add
    unlink(opj(dst, testfile))
    ar.add_url_to_file(testfile, testurl, batch=True)

    info = ar.info(testfile)
    eq_(info['size'], 14)
    assert(info['key'])
    # not even added to index yet since we this repo is with default batch_size
    assert_not_in(ar.WEB_UUID, ar.whereis(testfile))

    # TODO: none of the below should re-initiate the batch process

    # add to an existing and staged annex file
    copyfile(opj(sitepath, 'about2.txt'), opj(dst, testfile2))
    ar.add(testfile2)
    ar.add_url_to_file(testfile2, testurl2, batch=True)
    assert(ar.info(testfile2))
    # not committed yet
    # assert_in(ar.WEB_UUID, ar.whereis(testfile2))

    # add to an existing and committed annex file
    copyfile(opj(sitepath, 'about2_.txt'), opj(dst, testfile2_))
    ar.add(testfile2_)
    if ar.is_direct_mode():
        assert_in(ar.WEB_UUID, ar.whereis(testfile))
    else:
        assert_not_in(ar.WEB_UUID, ar.whereis(testfile))
    ar.commit("added about2_.txt and there was about2.txt lingering around")
    # commit causes closing all batched annexes, so testfile gets committed
    assert_in(ar.WEB_UUID, ar.whereis(testfile))
    assert(not ar.dirty)
    ar.add_url_to_file(testfile2_, testurl2_, batch=True)
    assert(ar.info(testfile2_))
    assert_in(ar.WEB_UUID, ar.whereis(testfile2_))

    # add into a new file
    # filename = 'newfile.dat'
    filename = get_most_obscure_supported_name()

    # Note: The following line was necessary, since the test setup just
    # doesn't work with singletons
    # TODO: Singleton mechanic needs a general solution for this
    AnnexRepo._unique_instances.clear()
    ar2 = AnnexRepo(dst, batch_size=1)

    with swallow_outputs():
        eq_(len(ar2._batched), 0)
        ar2.add_url_to_file(filename, testurl, batch=True)
        eq_(len(ar2._batched), 1)  # we added one more with batch_size=1
        ar2.precommit()  # to possibly stop batch process occupying the stdout
    ar2.commit("added new file")  # would do nothing ATM, but also doesn't fail
    assert_in(filename, ar2.get_files())
    assert_in(ar.WEB_UUID, ar2.whereis(filename))

    ar.commit("actually committing new files")
    assert_in(filename, ar.get_files())
    assert_in(ar.WEB_UUID, ar.whereis(filename))
    # this poor bugger still wasn't added since we used default batch_size=0 on him

    # and closing the pipes now shoudn't anyhow affect things
    eq_(len(ar._batched), 1)
    ar._batched.close()
    eq_(len(ar._batched), 1)  # doesn't remove them, just closes
    assert(not ar.dirty)

    ar._batched.clear()
    eq_(len(ar._batched), 0)  # .clear also removes

    raise SkipTest("TODO: more, e.g. add with a custom backend")
    # TODO: also with different modes (relaxed, fast)
    # TODO: verify that file is added with that backend and that we got a new batched process


@with_tree(tree={"foo": "foo content"})
@serve_path_via_http()
@with_tree(tree={"bar": "bar content"})
def test_annexrepo_fake_dates_disables_batched(sitepath, siteurl, dst):
    ar = AnnexRepo(dst, create=True, fake_dates=True)

    with swallow_logs(new_level=logging.DEBUG) as cml:
        ar.add_url_to_file("foo-dst", urljoin(siteurl, "foo"), batch=True)
        cml.assert_logged(
            msg="Not batching addurl call because fake dates are enabled",
            level="DEBUG",
            regex=False)

    ar.add("bar")
    ar.commit("add bar")

    with swallow_logs(new_level=logging.DEBUG) as cml:
        ar.drop_key(ar.get_file_key(["bar"]), batch=True)
        cml.assert_logged(
            msg="Not batching drop_key call because fake dates are enabled",
            level="DEBUG",
            regex=False)


@with_tempfile(mkdir=True)
def test_annex_backends(path):
    repo = AnnexRepo(path)
    eq_(repo.default_backends, None)

    rmtree(path)

    repo = AnnexRepo(path, backend='MD5E')
    eq_(repo.default_backends, ['MD5E'])

    # persists
    repo = AnnexRepo(path)
    eq_(repo.default_backends, ['MD5E'])


@skip_ssh
@with_tempfile
@with_testrepos('basic_annex', flavors=['local'])
@with_testrepos('basic_annex', flavors=['local'])
def test_annex_ssh(repo_path, remote_1_path, remote_2_path):
    from datalad import ssh_manager
    # create remotes:
    rm1 = AnnexRepo(remote_1_path, create=False)
    rm2 = AnnexRepo(remote_2_path, create=False)

    # check whether we are the first to use these sockets:
    socket_1 = opj(text_type(ssh_manager.socket_dir),
                   get_connection_hash('datalad-test', bundled=True))
    socket_2 = opj(text_type(ssh_manager.socket_dir),
                   get_connection_hash('localhost', bundled=True))
    datalad_test_was_open = exists(socket_1)
    localhost_was_open = exists(socket_2)

    # repo to test:AnnexRepo(repo_path)
    # At first, directly use git to add the remote, which should be recognized
    # by AnnexRepo's constructor
    gr = GitRepo(repo_path, create=True)
    AnnexRepo(repo_path)
    gr.add_remote("ssh-remote-1", "ssh://datalad-test" + remote_1_path)

    # Now, make it an annex:
    ar = AnnexRepo(repo_path, create=False)

    # connection to 'datalad-test' should be known to ssh manager:
    assert_in(socket_1, list(map(text_type, ssh_manager._connections)))
    # but socket was not touched:
    if datalad_test_was_open:
        ok_(exists(socket_1))
    else:
        ok_(not exists(socket_1))

    from datalad import lgr
    # remote interaction causes socket to be created:
    try:
        # Note: For some reason, it hangs if log_stdout/err True
        # TODO: Figure out what's going on
        #  yoh: I think it is because of what is "TODOed" within cmd.py --
        #       trying to log/obtain both through PIPE could lead to lock
        #       downs.
        # here we use our swallow_logs to overcome a problem of running under
        # nosetests without -s, when nose then tries to swallow stdout by
        # mocking it with StringIO, which is not fully compatible with Popen
        # which needs its .fileno()
        with swallow_outputs():
            ar._run_annex_command('sync',
                                  expect_stderr=True,
                                  log_stdout=False,
                                  log_stderr=False,
                                  expect_fail=True)
    # sync should return exit code 1, since it can not merge
    # doesn't matter for the purpose of this test
    except CommandError as e:
        if e.code == 1:
            pass

    ok_(exists(socket_1))

    # add another remote:
    ar.add_remote('ssh-remote-2', "ssh://localhost" + remote_2_path)

    # now, this connection to localhost was requested:
    assert_in(socket_2, list(map(text_type, ssh_manager._connections)))
    # but socket was not touched:
    if localhost_was_open:
        # FIXME: occasionally(?) fails in V6:
        if not ar.supports_unlocked_pointers:
            ok_(exists(socket_2))
    else:
        ok_(not exists(socket_2))

    # sync with the new remote:
    try:
        with swallow_outputs():
            ar._run_annex_command('sync', annex_options=['ssh-remote-2'],
                                  expect_stderr=True,
                                  log_stdout=False,
                                  log_stderr=False,
                                  expect_fail=True)
    # sync should return exit code 1, since it can not merge
    # doesn't matter for the purpose of this test
    except CommandError as e:
        if e.code == 1:
            pass

    ok_(exists(socket_2))
    ssh_manager.close(ctrl_path=[socket_1, socket_2])


@with_testrepos('basic_annex', flavors=['clone'])
@with_tempfile(mkdir=True)
def test_annex_remove(path1, path2):
    repo = AnnexRepo(path1, create=False)

    file_list = repo.get_annexed_files()
    assert len(file_list) >= 1
    # remove a single file
    out = repo.remove(file_list[0])
    assert_not_in(file_list[0], repo.get_annexed_files())
    eq_(out[0], file_list[0])

    with open(opj(repo.path, "rm-test.dat"), "w") as f:
        f.write("whatever")

    # add it
    repo.add("rm-test.dat")

    # remove without '--force' should fail, due to staged changes:
    if repo.is_direct_mode():
        assert_raises(CommandError, repo.remove, "rm-test.dat")
    else:
        assert_raises(GitCommandError, repo.remove, "rm-test.dat")
    assert_in("rm-test.dat", repo.get_annexed_files())

    # now force:
    out = repo.remove("rm-test.dat", force=True)
    assert_not_in("rm-test.dat", repo.get_annexed_files())
    eq_(out[0], "rm-test.dat")


@with_tempfile
@with_tempfile
@with_tempfile
def test_repo_version(path1, path2, path3):
    annex = AnnexRepo(path1, create=True, version=6)
    ok_clean_git(path1, annex=True)
    version = annex.repo.config_reader().get_value('annex', 'version')
    # TODO: Since git-annex 7.20181031, v6 repos upgrade to v7. Once that
    # version or later is our minimum required version, update this test and
    # the one below to eq_(version, 7).
    assert_in(version, [6, 7])

    # default from config item (via env var):
    with patch.dict('os.environ', {'DATALAD_REPO_VERSION': '6'}):
        annex = AnnexRepo(path2, create=True)
        version = annex.repo.config_reader().get_value('annex', 'version')
        assert_in(version, [6, 7])

        # parameter `version` still has priority over default config:
        annex = AnnexRepo(path3, create=True, version=5)
        version = annex.repo.config_reader().get_value('annex', 'version')
        eq_(version, 5)


@with_testrepos('.*annex.*', flavors=['clone'])
@with_tempfile(mkdir=True)
def test_annex_copy_to(origin, clone):
    repo = AnnexRepo(origin, create=False)
    remote = AnnexRepo.clone(origin, clone, create=True)
    repo.add_remote("target", clone)

    assert_raises(IOError, repo.copy_to, "doesnt_exist.dat", "target")
    assert_raises(FileInGitError, repo.copy_to, "INFO.txt", "target")
    assert_raises(ValueError, repo.copy_to, "test-annex.dat", "invalid_target")

    # test-annex.dat has no content to copy yet:
    eq_(repo.copy_to("test-annex.dat", "target"), [])

    repo.get("test-annex.dat")
    # now it has:
    eq_(repo.copy_to("test-annex.dat", "target"), ["test-annex.dat"])
    # and will not be copied again since it was already copied
    eq_(repo.copy_to(["INFO.txt", "test-annex.dat"], "target"), [])

    # Test that if we pass a list of items and annex processes them nicely,
    # we would obtain a list back. To not stress our tests even more -- let's mock
    def ok_copy(command, **kwargs):
        # Check that we do pass to annex call only the list of files which we
        #  asked to be copied
        assert_in('copied1', kwargs['files'])
        assert_in('copied2', kwargs['files'])
        assert_in('existed', kwargs['files'])
        return """
{"command":"copy","note":"to target ...", "success":true, "key":"akey1", "file":"copied1"}
{"command":"copy","note":"to target ...", "success":true, "key":"akey2", "file":"copied2"}
{"command":"copy","note":"checking target ...", "success":true, "key":"akey3", "file":"existed"}
""", ""
    # Note that we patch _run_annex_command, which is also invoked by _run_annex_command_json
    # which is in turn invoked first by copy_to for "find" operation.
    # TODO: provide a dedicated handling within above ok_copy for 'find' command
    with patch.object(repo, '_run_annex_command', ok_copy):
        eq_(repo.copy_to(["copied2", "copied1", "existed"], "target"),
            ["copied1", "copied2"])

    # now let's test that we are correctly raising the exception in case if
    # git-annex execution fails
    orig_run = repo._run_annex_command

    # Kinda a bit off the reality since no nonex* would not be returned/handled
    # by _get_expected_files, so in real life -- wouldn't get report about Incomplete!?
    def fail_to_copy(command, **kwargs):
        if command == 'copy':
            # That is not how annex behaves
            # http://git-annex.branchable.com/bugs/copy_does_not_reflect_some_failed_copies_in_--json_output/
            # for non-existing files output goes into stderr
            raise CommandError(
                "Failed to run ...",
                stdout=
                    '{"command":"copy","note":"to target ...", "success":true, "key":"akey1", "file":"copied"}\n'
                    '{"command":"copy","note":"checking target ...", "success":true, "key":"akey2", "file":"existed"}\n',
                stderr=
                    'git-annex: nonex1 not found\n'
                    'git-annex: nonex2 not found\n'
            )
        else:
            return orig_run(command, **kwargs)

    def fail_to_copy_get_expected(files, expr):
        assert files == ["copied", "existed", "nonex1", "nonex2"]
        return {'akey1': 10}, ["copied"]

    with patch.object(repo, '_run_annex_command', fail_to_copy), \
            patch.object(repo, '_get_expected_files', fail_to_copy_get_expected):
        with assert_raises(IncompleteResultsError) as cme:
            repo.copy_to(["copied", "existed", "nonex1", "nonex2"], "target")
    eq_(cme.exception.results, ["copied"])
    eq_(cme.exception.failed, ['nonex1', 'nonex2'])



@with_testrepos('.*annex.*', flavors=['local'])
# TODO: flavor 'network' has wrong content for test-annex.dat!
@with_tempfile
def test_annex_drop(src, dst):
    ar = AnnexRepo.clone(src, dst)
    testfile = 'test-annex.dat'
    assert_false(ar.file_has_content(testfile))
    ar.get(testfile)
    ok_(ar.file_has_content(testfile))

    # drop file by name:
    result = ar.drop([testfile])
    assert_false(ar.file_has_content(testfile))
    ok_(isinstance(result, list))
    eq_(len(result), 1)
    eq_(result[0]['command'], 'drop')
    eq_(result[0]['success'], True)
    eq_(result[0]['file'], testfile)

    ar.get(testfile)

    # drop file by key:
    testkey = ar.get_file_key(testfile)
    result = ar.drop([testkey], key=True)
    assert_false(ar.file_has_content(testfile))
    ok_(isinstance(result, list))
    eq_(len(result), 1)
    eq_(result[0]['command'], 'drop')
    eq_(result[0]['success'], True)
    eq_(result[0]['key'], testkey)

    # insufficient arguments:
    assert_raises(TypeError, ar.drop)
    assert_raises(InsufficientArgumentsError, ar.drop, [], options=["--jobs=5"])
    assert_raises(InsufficientArgumentsError, ar.drop, [])

    # too much arguments:
    assert_raises(CommandError, ar.drop, ['.'], options=['--all'])


@with_tree({"a.txt": "a", "b.txt": "b", "c.py": "c", "d": "d"})
def test_annex_get_annexed_files(path):
    repo = AnnexRepo(path)
    repo.add(".")
    repo.commit()
    eq_(set(repo.get_annexed_files()), {"a.txt", "b.txt", "c.py", "d"})

    repo.drop("a.txt", options=["--force"])
    eq_(set(repo.get_annexed_files()), {"a.txt", "b.txt", "c.py", "d"})
    eq_(set(repo.get_annexed_files(with_content_only=True)),
        {"b.txt", "c.py", "d"})

    eq_(set(repo.get_annexed_files(patterns=["*.txt"])),
        {"a.txt", "b.txt"})
    eq_(set(repo.get_annexed_files(with_content_only=True,
                                   patterns=["*.txt"])),
        {"b.txt"})

    eq_(set(repo.get_annexed_files(patterns=["*.txt", "*.py"])),
        {"a.txt", "b.txt", "c.py"})

    eq_(set(repo.get_annexed_files()),
        set(repo.get_annexed_files(patterns=["*"])))

    eq_(set(repo.get_annexed_files(with_content_only=True)),
        set(repo.get_annexed_files(with_content_only=True, patterns=["*"])))


@with_testrepos('basic_annex', flavors=['clone'])
def test_annex_remove(path):
    repo = AnnexRepo(path, create=False)

    file_list = repo.get_annexed_files()
    assert len(file_list) >= 1
    # remove a single file
    out = repo.remove(file_list[0])
    assert_not_in(file_list[0], repo.get_annexed_files())
    eq_(out[0], file_list[0])

    with open(opj(repo.path, "rm-test.dat"), "w") as f:
        f.write("whatever")

    # add it
    repo.add("rm-test.dat")

    # remove without '--force' should fail, due to staged changes:
    assert_raises(CommandError, repo.remove, "rm-test.dat")
    assert_in("rm-test.dat", repo.get_annexed_files())

    # now force:
    out = repo.remove("rm-test.dat", force=True)
    assert_not_in("rm-test.dat", repo.get_annexed_files())
    eq_(out[0], "rm-test.dat")


@with_parametric_batch
@with_testrepos('basic_annex', flavors=['clone'], count=1)
def test_is_available(batch, p):
    annex = AnnexRepo(p)

    # bkw = {'batch': batch}
    if batch:
        is_available = partial(annex.is_available, batch=batch)
    else:
        is_available = annex.is_available

    fname = 'test-annex.dat'
    key = annex.get_file_key(fname)

    # explicit is to verify data type etc
    assert is_available(key, key=True) is True
    assert is_available(fname) is True

    # known remote but doesn't have it
    assert is_available(fname, remote='origin') is False
    # it is on the 'web'
    assert is_available(fname, remote='web') is True
    # not effective somehow :-/  may be the process already running or smth
    # with swallow_logs(), swallow_outputs():  # it will complain!
    assert is_available(fname, remote='unknown') is False
    assert_false(is_available("boguskey", key=True))

    # remove url
    urls = annex.get_urls(fname) #, **bkw)
    assert(len(urls) == 1)
    eq_(urls, annex.get_urls(annex.get_file_key(fname), key=True))
    annex.rm_url(fname, urls[0])

    assert is_available(key, key=True) is False
    assert is_available(fname) is False
    assert is_available(fname, remote='web') is False


@with_tempfile(mkdir=True)
def test_get_urls_none(path):
    ar = AnnexRepo(path, create=True)
    with open(opj(ar.path, "afile"), "w") as f:
        f.write("content")
    eq_(ar.get_urls("afile"), [])


@with_tempfile(mkdir=True)
def test_annex_add_no_dotfiles(path):
    ar = AnnexRepo(path, create=True)
    print(ar.path)
    assert_true(os.path.exists(ar.path))
    assert_false(ar.dirty)
    os.makedirs(opj(ar.path, '.datalad'))
    # we don't care about empty directories
    assert_false(ar.dirty)
    with open(opj(ar.path, '.datalad', 'somefile'), 'w') as f:
        f.write('some content')
    # make sure the repo is considered dirty now
    assert_true(ar.dirty)  # TODO: has been more detailed assertion (untracked file)
    # no file is being added, as dotfiles/directories are ignored by default
    ar.add('.', git=False)
    # double check, still dirty
    assert_true(ar.dirty)  # TODO: has been more detailed assertion (untracked file)
    # now add to git, and it should work
    ar.add('.', git=True)
    # all in index
    assert_true(ar.dirty)
    # TODO: has been more specific:
    # assert_false(ar.repo.is_dirty(
    #     index=False, working_tree=True, untracked_files=True, submodules=True))
    ar.commit(msg="some")
    # all committed
    assert_false(ar.dirty)
    # not known to annex
    assert_false(ar.is_under_annex(opj(ar.path, '.datalad', 'somefile')))


@with_tempfile
def test_annex_version_handling(path):
    with patch.object(AnnexRepo, 'git_annex_version', None) as cmpov, \
         patch.object(AnnexRepo, '_check_git_annex_version',
                      auto_spec=True,
                      side_effect=AnnexRepo._check_git_annex_version) \
            as cmpc, \
         patch.object(external_versions, '_versions',
                      {'cmd:annex': AnnexRepo.GIT_ANNEX_MIN_VERSION}):
            eq_(AnnexRepo.git_annex_version, None)
            ar1 = AnnexRepo(path, create=True)
            assert(ar1)
            eq_(AnnexRepo.git_annex_version, AnnexRepo.GIT_ANNEX_MIN_VERSION)
            eq_(cmpc.call_count, 1)
            # 2nd time must not be called
            try:
                # Note: Remove to cause creation of a new instance
                rmtree(path)
            except OSError:
                pass
            ar2 = AnnexRepo(path)
            assert(ar2)
            eq_(AnnexRepo.git_annex_version, AnnexRepo.GIT_ANNEX_MIN_VERSION)
            eq_(cmpc.call_count, 1)
    with patch.object(AnnexRepo, 'git_annex_version', None) as cmpov, \
            patch.object(AnnexRepo, '_check_git_annex_version',
                         auto_spec=True,
                         side_effect=AnnexRepo._check_git_annex_version):
        # no git-annex at all
        with patch.object(
                external_versions, '_versions', {'cmd:annex': None}):
            eq_(AnnexRepo.git_annex_version, None)
            with assert_raises(MissingExternalDependency) as cme:
                try:
                    # Note: Remove to cause creation of a new instance
                    rmtree(path)
                except OSError:
                    pass
                AnnexRepo(path)
            if linux_distribution_name == 'debian':
                assert_in("http://neuro.debian.net", str(cme.exception))
            eq_(AnnexRepo.git_annex_version, None)

        # outdated git-annex at all
        with patch.object(
                external_versions, '_versions', {'cmd:annex': '6.20160505'}):
            eq_(AnnexRepo.git_annex_version, None)
            try:
                # Note: Remove to cause creation of a new instance
                rmtree(path)
            except OSError:
                pass
            assert_raises(OutdatedExternalDependency, AnnexRepo, path)
            # and we don't assign it
            eq_(AnnexRepo.git_annex_version, None)
            # so we could still fail
            try:
                # Note: Remove to cause creation of a new instance
                rmtree(path)
            except OSError:
                pass
            assert_raises(OutdatedExternalDependency, AnnexRepo, path)


def test_ProcessAnnexProgressIndicators():
    irrelevant_lines = (
        'abra',
        '{"some_json": "sure thing"}'
    )
    # regular lines, without completion for known downloads
    success_lines = (
        '{"command":"get","note":"","success":true,"key":"key1","file":"file1"}',
        '{"command":"comm","note":"","success":true,"key":"backend-s10--key2"}',
    )
    progress_lines = (
        '{"byte-progress":10,"action":{"command":"get","note":"from web...",'
            '"key":"key1","file":"file1"},"percent-progress":"10%"}',
    )

    # without providing expected entries
    proc = ProcessAnnexProgressIndicators()
    # when without any target downloads, there is no total_pbar
    assert_is(proc.total_pbar, None)
    # for regular lines -- should just return them without side-effects
    for l in irrelevant_lines + success_lines:
        with swallow_outputs() as cmo:
            eq_(proc(l), l)
            eq_(proc.pbars, {})
            eq_(cmo.out, '')
            eq_(cmo.err, '')
    # should process progress lines
    eq_(proc(progress_lines[0]), None)
    eq_(len(proc.pbars), 1)
    # but when we finish download -- should get cleared
    eq_(proc(success_lines[0]), success_lines[0])
    eq_(proc.pbars, {})
    # and no side-effect of any kind in finish
    eq_(proc.finish(), None)

    proc = ProcessAnnexProgressIndicators(expected={'key1': 100, 'key2': None})
    # when without any target downloads, there is no total_pbar
    assert(proc.total_pbar is not None)
    eq_(proc.total_pbar.total, 100)  # as much as it knows at this point
    eq_(proc.total_pbar.current, 0)
    # for regular lines -- should still just return them without side-effects
    for l in irrelevant_lines:
        with swallow_outputs() as cmo:
            eq_(proc(l), l)
            eq_(proc.pbars, {})
            eq_(cmo.out, '')
            eq_(cmo.err, '')
    # should process progress lines
    # it doesn't swallow everything -- so there will be side-effects in output
    with swallow_outputs() as cmo:
        eq_(proc(progress_lines[0]), None)
        eq_(len(proc.pbars), 1)
        # but when we finish download -- should get cleared
        eq_(proc(success_lines[0]), success_lines[0])
        eq_(proc.pbars, {})
        out = cmo.out

    from datalad.ui import ui
    from datalad.ui.dialog import QuietConsoleLog

    assert out \
        if not isinstance(ui.ui, QuietConsoleLog) else not out
    assert proc.total_pbar is not None
    # and no side-effect of any kind in finish
    with swallow_outputs() as cmo:
        eq_(proc.finish(), None)
        eq_(proc.total_pbar, None)


@with_tempfile
@with_tempfile
def test_get_description(path1, path2):
    annex1 = AnnexRepo(path1, create=True)
    # some content for git-annex branch
    create_tree(path1, {'1.dat': 'content'})
    annex1.add('1.dat', git=False)
    annex1.commit("msg")
    annex1_description = annex1.get_description()
    assert_not_equal(annex1_description, path1)

    annex2 = AnnexRepo(path2, create=True, description='custom 2')
    eq_(annex2.get_description(), 'custom 2')
    # not yet known
    eq_(annex2.get_description(uuid=annex1.uuid), None)

    annex2.add_remote('annex1', path1)
    annex2.fetch('annex1')
    # it will match the remote name
    eq_(annex2.get_description(uuid=annex1.uuid),
        annex1_description + ' [annex1]')
    # add a little probe file to make sure it stays untracked
    create_tree(path1, {'probe': 'probe'})
    assert_not_in('probe', annex2.get_indexed_files())
    annex2.merge_annex('annex1')
    assert_not_in('probe', annex2.get_indexed_files())
    # but let's remove the remote
    annex2.remove_remote('annex1')
    eq_(annex2.get_description(uuid=annex1.uuid), annex1_description)


@with_tempfile(mkdir=True)
@with_tempfile(mkdir=True)
def test_AnnexRepo_flyweight(path1, path2):

    repo1 = AnnexRepo(path1, create=True)
    assert_is_instance(repo1, AnnexRepo)
    # instantiate again:
    repo2 = AnnexRepo(path1, create=False)
    assert_is_instance(repo2, AnnexRepo)
    # the very same object:
    ok_(repo1 is repo2)

    # reference the same in an different way:
    with chpwd(path1):
        repo3 = AnnexRepo(relpath(path1, start=path2), create=False)
        assert_is_instance(repo3, AnnexRepo)
    # it's the same object:
    ok_(repo1 is repo3)

    # but path attribute is absolute, so they are still equal:
    ok_(repo1 == repo3)

    # Now, let's try to get a GitRepo instance from a path, we already have an
    # AnnexRepo of
    repo4 = GitRepo(path1)
    assert_is_instance(repo4, GitRepo)
    assert_not_is_instance(repo4, AnnexRepo)


@with_testrepos(flavors=local_testrepo_flavors)
@with_tempfile(mkdir=True)
@with_tempfile
def test_AnnexRepo_get_toppath(repo, tempdir, repo2):

    reporeal = realpath(repo)
    eq_(AnnexRepo.get_toppath(repo, follow_up=False), reporeal)
    eq_(AnnexRepo.get_toppath(repo), repo)
    # Generate some nested directory
    AnnexRepo(repo2, create=True)
    repo2real = realpath(repo2)
    nested = opj(repo2, "d1", "d2")
    os.makedirs(nested)
    eq_(AnnexRepo.get_toppath(nested, follow_up=False), repo2real)
    eq_(AnnexRepo.get_toppath(nested), repo2)
    # and if not under git, should return None
    eq_(AnnexRepo.get_toppath(tempdir), None)


@with_testrepos(".*basic.*", flavors=['local'])
@with_tempfile(mkdir=True)
def test_AnnexRepo_add_submodule(source, path):

    top_repo = AnnexRepo(path, create=True)

    top_repo.add_submodule('sub', name='sub', url=source)
    top_repo.commit('submodule added')
    eq_([s.name for s in top_repo.get_submodules()], ['sub'])

    ok_clean_git(top_repo, annex=True)
    ok_clean_git(opj(path, 'sub'), annex=False)


def test_AnnexRepo_update_submodule():
    raise SkipTest("TODO")


@known_failure_v6  #FIXME
def test_AnnexRepo_get_submodules():
    raise SkipTest("TODO")


@with_tempfile(mkdir=True)
def test_AnnexRepo_dirty(path):

    repo = AnnexRepo(path, create=True)
    ok_(not repo.dirty)

    # pure git operations:
    # untracked file
    with open(opj(path, 'file1.txt'), 'w') as f:
        f.write('whatever')
    ok_(repo.dirty)
    # staged file
    repo.add('file1.txt', git=True)
    ok_(repo.dirty)
    # clean again
    repo.commit("file1.txt added")
    ok_(not repo.dirty)
    # modify to be the same
    with open(opj(path, 'file1.txt'), 'w') as f:
        f.write('whatever')
    if not repo.supports_unlocked_pointers:
        ok_(not repo.dirty)
    # modified file
    with open(opj(path, 'file1.txt'), 'w') as f:
        f.write('something else')
    ok_(repo.dirty)
    # clean again
    repo.add('file1.txt', git=True)
    repo.commit("file1.txt modified")
    ok_(not repo.dirty)

    # annex operations:
    # untracked file
    with open(opj(path, 'file2.txt'), 'w') as f:
        f.write('different content')
    ok_(repo.dirty)
    # annexed file
    repo.add('file2.txt', git=False)
    ok_(repo.dirty)
    # commit
    repo.commit("file2.txt annexed")
    ok_(not repo.dirty)

    repo.unlock("file2.txt")
    # Unlocking the file is seen as a modification when we're not already in an
    # adjusted branch (for this test, that would be the case if we're on a
    # crippled filesystem).
    ok_(repo.dirty ^ repo.is_managed_branch())
    repo.save()
    ok_(not repo.dirty)


# TODO: test/utils ok_clean_git


@with_tempfile(mkdir=True)
def test_AnnexRepo_set_remote_url(path):

    ar = AnnexRepo(path, create=True)
    ar.add_remote('some', 'http://example.com/.git')
    assert_equal(ar.config['remote.some.url'],
                 'http://example.com/.git')
    assert_not_in('remote.some.annexurl', ar.config.keys())
    # change url:
    ar.set_remote_url('some', 'http://believe.it')
    assert_equal(ar.config['remote.some.url'],
                 'http://believe.it')
    assert_not_in('remote.some.annexurl', ar.config.keys())

    # set push url:
    ar.set_remote_url('some', 'ssh://whatever.ru', push=True)
    assert_equal(ar.config['remote.some.pushurl'],
                 'ssh://whatever.ru')
    assert_in('remote.some.annexurl', ar.config.keys())
    assert_equal(ar.config['remote.some.annexurl'],
                 'ssh://whatever.ru')


@with_tempfile(mkdir=True)
def test_wanted(path):
    ar = AnnexRepo(path, create=True)
    eq_(ar.get_preferred_content('wanted'), None)
    # test samples with increasing "trickiness"
    for v in ("standard",
              "include=*.nii.gz or include=*.nii",
              "exclude=archive/* and (include=*.dat or smallerthan=2b)"
              ):
        ar.set_preferred_content('wanted', expr=v)
        eq_(ar.get_preferred_content('wanted'), v)
    # give it some file so clone/checkout works without hiccups
    create_tree(ar.path, {'1.dat': 'content'})
    ar.add('1.dat')
    ar.commit(msg="blah")
    # make a clone and see if all cool there
    # intentionally clone as pure Git and do not annex init so to see if we
    # are ignoring crummy log msgs
    ar1_path = ar.path + '_1'
    GitRepo.clone(ar.path, ar1_path)
    ar1 = AnnexRepo(ar1_path, init=False)
    eq_(ar1.get_preferred_content('wanted'), None)
    eq_(ar1.get_preferred_content('wanted', 'origin'), v)
    ar1.set_preferred_content('wanted', expr='standard')
    eq_(ar1.get_preferred_content('wanted'), 'standard')


@with_tempfile(mkdir=True)
def test_AnnexRepo_metadata(path):
    # prelude
    ar = AnnexRepo(path, create=True)
    create_tree(
        path,
        {
            'up.dat': 'content',
            'd o"w n': {
                'd o w n.dat': 'lowcontent'
            }
        })
    ar.add('.', git=False)
    ar.commit('content')
    ok_clean_git(path)
    # fugue
    # doesn't do anything if there is nothing to do
    ar.set_metadata('up.dat')
    eq_([], list(ar.get_metadata(None)))
    eq_([], list(ar.get_metadata('')))
    eq_([], list(ar.get_metadata([])))
    eq_({'up.dat': {}}, dict(ar.get_metadata('up.dat')))
    # basic invocation
    eq_(1, len(ar.set_metadata(
        'up.dat',
        reset={'mike': 'awesome'},
        add={'tag': 'awesome'},
        remove={'tag': 'awesome'},  # cancels prev, just to use it
        init={'virgin': 'true'},
        purge=['nothere'])))
    # no timestamps by default
    md = dict(ar.get_metadata('up.dat'))
    deq_({'up.dat': {
        'virgin': ['true'],
        'mike': ['awesome']}},
        md)
    # matching timestamp entries for all keys
    md_ts = dict(ar.get_metadata('up.dat', timestamps=True))
    for k in md['up.dat']:
        assert_in('{}-lastchanged'.format(k), md_ts['up.dat'])
    assert_in('lastchanged', md_ts['up.dat'])
    # recursive needs a flag
    assert_raises(CommandError, ar.set_metadata, '.', purge=['virgin'])
    ar.set_metadata('.', purge=['virgin'], recursive=True)
    deq_({'up.dat': {
        'mike': ['awesome']}},
        dict(ar.get_metadata('up.dat')))
    # Use trickier tags (spaces, =)
    ar.set_metadata('.', reset={'tag': 'one and= '}, purge=['mike'], recursive=True)
    playfile = opj('d o"w n', 'd o w n.dat')
    target = {
        'up.dat': {
            'tag': ['one and= ']},
        playfile: {
            'tag': ['one and= ']}}
    deq_(target, dict(ar.get_metadata('.')))
    for batch in (True, False):
        # no difference in reporting between modes
        deq_(target, dict(ar.get_metadata(['up.dat', playfile], batch=batch)))
    # incremental work like a set
    ar.set_metadata(playfile, add={'tag': 'one and= '})
    deq_(target, dict(ar.get_metadata('.')))
    ar.set_metadata(playfile, add={'tag': ' two'})
    # returned values are sorted
    eq_([' two', 'one and= '], dict(ar.get_metadata(playfile))[playfile]['tag'])
    # init honor prior values
    ar.set_metadata(playfile, init={'tag': 'three'})
    eq_([' two', 'one and= '], dict(ar.get_metadata(playfile))[playfile]['tag'])
    ar.set_metadata(playfile, remove={'tag': ' two'})
    deq_(target, dict(ar.get_metadata('.')))
    # remove non-existing doesn't error and doesn't change anything
    ar.set_metadata(playfile, remove={'ether': 'best'})
    deq_(target, dict(ar.get_metadata('.')))
    # add works without prior existence
    ar.set_metadata(playfile, add={'novel': 'best'})
    eq_(['best'], dict(ar.get_metadata(playfile))[playfile]['novel'])


@with_tree(tree={'file.txt': 'content'})
@serve_path_via_http()
@with_tempfile
def test_AnnexRepo_addurl_batched_and_set_metadata(path, url, dest):
    ar = AnnexRepo(dest, create=True)
    fname = "file.txt"
    ar.add_url_to_file(fname, urljoin(url, fname), batch=True)
    ar.set_metadata(fname, init={"number": "one"})
    eq_(["one"], dict(ar.get_metadata(fname))[fname]["number"])


@with_tempfile(mkdir=True)
def test_change_description(path):
    # prelude
    ar = AnnexRepo(path, create=True, description='some')
    eq_(ar.get_description(), 'some')
    # try change it
    ar = AnnexRepo(path, create=False, init=True, description='someother')
    # this doesn't cut the mustard, still old
    eq_(ar.get_description(), 'some')
    # need to resort to "internal" helper
    ar._init(description='someother')
    eq_(ar.get_description(), 'someother')


@with_testrepos('basic_annex', flavors=['clone'])
def test_AnnexRepo_get_corresponding_branch(path):

    ar = AnnexRepo(path)

    # we should be on master.
    eq_('master', ar.get_corresponding_branch())

    # special case v6 adjusted branch is not provided by a dedicated build:
    if ar.supports_unlocked_pointers:
        ar.adjust()
        # as above, we still want to get 'master', while being on
        # 'adjusted/master(unlocked)'
        eq_('adjusted/master(unlocked)', ar.get_active_branch())
        eq_('master', ar.get_corresponding_branch())


@with_testrepos('basic_annex', flavors=['clone'])
def test_AnnexRepo_get_tracking_branch(path):

    ar = AnnexRepo(path)

    # we want the relation to original branch, e.g. in v6+ adjusted branch
    eq_(('origin', 'refs/heads/master'), ar.get_tracking_branch())


@with_testrepos('basic_annex', flavors=['clone'])
def test_AnnexRepo_is_managed_branch(path):

    ar = AnnexRepo(path)

    # ATM only v6+ adjusted branches should return True.
    # Adjusted branch requires a call of git-annex-adjust and shouldn't
    # be the state of a fresh clone
    ok_(not ar.is_managed_branch())

    if ar.supports_unlocked_pointers:
        ar.adjust()
        ok_(ar.is_managed_branch())


@with_tempfile(mkdir=True)
@with_tempfile()
def test_AnnexRepo_flyweight_monitoring_inode(path, store):
    # testing for issue #1512
    check_repo_deals_with_inode_change(AnnexRepo, path, store)


@with_tempfile(mkdir=True)
def test_fake_is_not_special(path):
    ar = AnnexRepo(path, create=True)
    # doesn't exist -- we fail by default
    assert_raises(RemoteNotAvailableError, ar.is_special_annex_remote, "fake")
    assert_false(ar.is_special_annex_remote("fake", check_if_known=False))


@with_tempfile(mkdir=True)
def test_fake_dates(path):
    ar = AnnexRepo(path, create=True, fake_dates=True)
    timestamp = ar.config.obtain("datalad.fake-dates-start") + 1
    # Commits from the "git annex init" call are one second ahead.
    for commit in ar.get_branch_commits("git-annex"):
        eq_(timestamp, commit.committed_date)
    assert_in("timestamp={}s".format(timestamp),
              ar.repo.git.cat_file("blob", "git-annex:uuid.log"))


def test_get_size_from_perc_complete():
    f = _get_size_from_perc_complete
    eq_(f(0, 0), 0)
    eq_(f(0, '0'), 0)
    eq_(f(100, '0'), 0)  # we do not know better
    eq_(f(1, '1'), 100)
    # with no percentage info, we don't know better either:
    eq_(f(1, ''), 0)


# to prevent regression
# http://git-annex.branchable.com/bugs/v6_-_under_subdir__58___git_add___34__whines__34____44___git_commit___34__blows__34__/
# It is disabled because is not per se relevant to DataLad since we do not
# Since we invoke from the top of the repo, we do not hit it,
# but thought to leave it around if we want to enforce/test system-wide git being
# compatible with annex for v6 mode
@with_tempfile(mkdir=True)
def _test_add_under_subdir(path):
    ar = AnnexRepo(path, create=True, version=6)
    gr = GitRepo(path)  # "Git" view over the repository, so we force "git add"
    subdir = opj(path, 'sub')
    subfile = opj('sub', 'empty')
    # os.mkdir(subdir)
    create_tree(subdir, {'empty': ''})
    runner = Runner(cwd=subdir)
    with chpwd(subdir):
        runner(['git', 'add', 'empty'])  # should add sucesfully
        # gr.commit('important') #
        runner(['git', 'commit', '-m', 'important'])
        ar.is_under_annex(subfile)


# https://github.com/datalad/datalad/issues/2892
@with_tempfile(mkdir=True)
def test_error_reporting(path):
    ar = AnnexRepo(path, create=True)
    res = ar._run_annex_command_json('add', files='gl\\orious BS')
    eq_(
        res,
        [{
            'command': 'add',
            # whole thing, despite space, properly quotes backslash
            'file': 'gl\\orious BS',
            'note': 'not found',
            'success': False}]
    )


# http://git-annex.branchable.com/bugs/cannot_commit___34__annex_add__34__ed_modified_file_which_switched_its_largefile_status_to_be_committed_to_git_now/#comment-bf70dd0071de1bfdae9fd4f736fd1ec
# https://github.com/datalad/datalad/issues/1651
@with_tree(tree={
    '.gitattributes': "** annex.largefiles=(largerthan=4b)",
    'alwaysbig': 'a'*10,
    'willnotgetshort': 'b'*10,
    'tobechanged-git': 'a',
    'tobechanged-annex': 'a'*10,
})
def check_commit_annex_commit_changed(unlock, path):
    # Here we test commit working correctly if file was just removed
    # (not unlocked), edited and committed back

    # TODO: an additional possible interaction to check/solidify - if files
    # first get unannexed (after being optionally unlocked first)
    unannex = False

    ar = AnnexRepo(path, create=True)
    ar.add('.gitattributes')
    ar.add('.')
    ar.commit("initial commit")
    ok_clean_git(path)
    # Now let's change all but commit only some
    files = [op.basename(p) for p in glob(op.join(path, '*'))]
    if unlock:
        ar.unlock(files)
    if unannex:
        ar.unannex(files)
    create_tree(
        path
        , {
            'alwaysbig': 'a'*11,
            'willnotgetshort': 'b',
            'tobechanged-git': 'aa',
            'tobechanged-annex': 'a'*11,
            'untracked': 'unique'
        }
        , remove_existing=True
    )
    ok_clean_git(
        path
        , index_modified=files if not unannex else ['tobechanged-git']
        , untracked=['untracked'] if not unannex else
          # all but the one in git now
          ['alwaysbig', 'tobechanged-annex', 'untracked', 'willnotgetshort']
    )

    ar.commit("message", files=['alwaysbig', 'willnotgetshort'])
    ok_clean_git(
        path
        , index_modified=['tobechanged-git', 'tobechanged-annex']
        , untracked=['untracked']
    )
    ok_file_under_git(path, 'alwaysbig', annexed=True)
    # This one is actually "questionable" since might be "correct" either way
    # but it would be nice to have it at least consistent
    ok_file_under_git(path, 'willnotgetshort', annexed=True)

    ar.commit("message2", options=['-a']) # commit all changed
    ok_clean_git(
        path
        , untracked=['untracked']
    )
    ok_file_under_git(path, 'tobechanged-git', annexed=False)
    ok_file_under_git(path, 'tobechanged-annex', annexed=True)


def test_commit_annex_commit_changed():
    for unlock in True, False:
        yield check_commit_annex_commit_changed, unlock


@with_tempfile(mkdir=True)
def check_files_split_exc(cls, topdir):
    from glob import glob
    r = cls(topdir)
    # absent files -- should not crash with "too long" but some other more
    # meaningful exception
    files = ["f" * 100 + "%04d" % f for f in range(100000)]
    if isinstance(r, AnnexRepo):
        # Annex'es add first checks for what is being added and does not fail
        # for non existing files either ATM :-/  TODO: make consistent etc
        r.add(files)
    else:
        with assert_raises(Exception) as ecm:
            r.add(files)
        assert_not_in('too long', str(ecm.exception))
        assert_not_in('too many', str(ecm.exception))


def test_files_split_exc():
    for cls in GitRepo, AnnexRepo:
        yield check_files_split_exc, cls


_HEAVY_TREE = {
    # might already run into 'filename too long' on windows probably
    "d" * 98 + '%03d' % d: {
        'f' * 98 + '%03d' % f: ''
        for f in range(100)
    }
    for d in range(100)
}


@with_tree(tree=_HEAVY_TREE)
def check_files_split(cls, topdir):
    from glob import glob
    r = cls(topdir)
    dirs = glob(op.join(topdir, '*'))
    files = glob(op.join(topdir, '*', '*'))

    r.add(files)
    r.commit(files=files)

    # Let's modify and do dl.add for even a heavier test
    # Now do for real on some heavy directory
    import datalad.api as dl
    for f in files:
        os.unlink(f)
        with open(f, 'w') as f:
            f.write('1')
    dl.add(dirs)


@slow  # 313s  well -- if errors out - only 3 sec
def test_files_split():
    for cls in GitRepo, AnnexRepo:
        yield check_files_split, cls


@skip_if(cond=(on_windows or os.geteuid() == 0))  # uid and sudo not available on windows
@with_tree({
    'repo': {
        'file1': 'file1',
        'file2': 'file2'
    }
})
def test_ro_operations(path):
    # This test would function only if there is a way to run sudo
    # non-interactively, e.g. on Travis or on your local (watchout!) system
    # after you ran sudo command recently.

    from datalad.cmd import Runner
    run = Runner().run
    sudochown = lambda cmd: run(['sudo', '-n', 'chown'] + cmd)

    repo = AnnexRepo(op.join(path, 'repo'), init=True)
    repo.add('file1')
    repo.commit()

    # make a clone
    repo2 = repo.clone(repo.path, op.join(path, 'clone'))
    repo2.get('file1')

    # progress forward original repo and fetch (but nothing else) it into repo2
    repo.add('file2')
    repo.commit()
    repo2.fetch('origin')

    # Assure that regardless of umask everyone could read it all
    run(['chmod', '-R', 'a+rX', repo2.path])
    try:
        # To assure that git/git-annex really cannot acquire a lock and do
        # any changes (e.g. merge git-annex branch), we make this repo owned by root
        sudochown(['-R', 'root', repo2.path])
    except Exception as exc:
        # Exception could be CommandError or IOError when there is no sudo
        raise SkipTest("Cannot run sudo chown non-interactively: %s" % exc)

    try:
        assert not repo2.get('file1')  # should work since file is here already
        repo2.status()  # should be Ok as well
        # and we should get info on the file just fine
        assert repo2.info('file1')
        # The tricky part is the repo_info which might need to update
        # remotes UUID -- by default it should fail!
        # Oh well -- not raised on travis... whatever for now
        #with assert_raises(CommandError):
        #    repo2.repo_info()
        # but should succeed if we disallow merges
        repo2.repo_info(merge_annex_branches=False)
        # and ultimately the ls which uses it
        from datalad.interface.ls import Ls
        Ls.__call__(repo2.path, all_=True, long_=True)
    finally:
        sudochown(['-R', str(os.geteuid()), repo2.path])

    # just check that all is good again
    repo2.repo_info()
