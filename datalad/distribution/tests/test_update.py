# ex: set sts=4 ts=4 sw=4 noet:
# ## ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
#
#   See COPYING file distributed along with the datalad package for the
#   copyright and license terms.
#
# ## ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
"""Test update action

"""



import os
import os.path as op
from os.path import join as opj, exists
from ..dataset import Dataset
from datalad.api import install
from datalad.api import update
from datalad.api import remove
from datalad.utils import knows_annex
from datalad.utils import rmtree
from datalad.utils import chpwd
from datalad.support.gitrepo import (
    GitRepo,
    GitCommandError,
)
from datalad.support.annexrepo import AnnexRepo

from nose.tools import eq_, assert_false, assert_is_instance, ok_
from datalad.tests.utils import with_tempfile, assert_in, \
    with_testrepos, assert_not_in
from datalad.tests.utils import create_tree
from datalad.tests.utils import ok_file_has_content
from datalad.tests.utils import ok_clean_git
from datalad.tests.utils import assert_status
from datalad.tests.utils import assert_result_count
from datalad.tests.utils import assert_in_results
from datalad.tests.utils import slow
from datalad.tests.utils import known_failure_windows


@slow
@with_testrepos('submodule_annex', flavors=['local'])  #TODO: Use all repos after fixing them
@with_tempfile(mkdir=True)
@with_tempfile(mkdir=True)
def test_update_simple(origin, src_path, dst_path):

    # prepare src
    source = install(src_path, source=origin, recursive=True)
    # forget we cloned it (provide no 'origin' anymore), which should lead to
    # setting tracking branch to target:
    source.repo.remove_remote("origin")

    # dataset without sibling will not need updates
    assert_status('notneeded', source.update())
    # deprecation message doesn't ruin things
    assert_status('notneeded', source.update(fetch_all=True))
    # but error if unknown sibling is given
    assert_status('impossible', source.update(sibling='funky', on_failure='ignore'))

    # get a clone to update later on:
    dest = install(dst_path, source=src_path, recursive=True)
    # test setup done;
    # assert all fine
    ok_clean_git(dst_path)
    ok_clean_git(src_path)

    # update yields nothing => up-to-date
    assert_status('ok', dest.update())
    ok_clean_git(dst_path)

    # modify origin:
    with open(opj(src_path, "update.txt"), "w") as f:
        f.write("Additional content")
    source.save(path="update.txt", message="Added update.txt")
    ok_clean_git(src_path)

    # fail when asked to update a non-dataset
    assert_status(
        'impossible',
        source.update("update.txt", on_failure='ignore'))
    # fail when asked to update a something non-existent
    assert_status(
        'impossible',
        source.update("nothere", on_failure='ignore'))

    # update without `merge` only fetches:
    assert_status('ok', dest.update())
    # modification is not known to active branch:
    assert_not_in("update.txt",
                  dest.repo.get_files(dest.repo.get_active_branch()))
    # modification is known to branch origin/master
    assert_in("update.txt", dest.repo.get_files("origin/master"))

    # merge:
    assert_status('ok', dest.update(merge=True))
    # modification is now known to active branch:
    assert_in("update.txt",
              dest.repo.get_files(dest.repo.get_active_branch()))
    # it's known to annex, but has no content yet:
    dest.repo.get_file_key("update.txt")  # raises if unknown
    eq_([False], dest.repo.file_has_content(["update.txt"]))

    # smoke-test if recursive update doesn't fail if submodule is removed
    # and that we can run it from within a dataset without providing it
    # explicitly
    assert_result_count(
        dest.remove('subm 1'), 1,
        status='ok', action='remove', path=opj(dest.path, 'subm 1'))
    with chpwd(dest.path):
        assert_result_count(
            update(recursive=True), 2,
            status='ok', type='dataset')
    assert_result_count(
        dest.update(merge=True, recursive=True), 2,
        status='ok', type='dataset')

    # and now test recursive update with merging in differences
    create_tree(opj(source.path, '2'), {'load.dat': 'heavy'})
    source.save(opj('2', 'load.dat'),
                message="saving changes within subm2",
                recursive=True)
    assert_result_count(
        dest.update(merge=True, recursive=True), 2,
        status='ok', type='dataset')
    # and now we can get new file
    dest.get('2/load.dat')
    ok_file_has_content(opj(dest.path, '2', 'load.dat'), 'heavy')


@with_tempfile
@with_tempfile
def test_update_git_smoke(src_path, dst_path):
    # Apparently was just failing on git repos for basic lack of coverage, hence this quick test
    ds = Dataset(src_path).create(no_annex=True)
    target = install(
        dst_path, source=src_path,
        result_xfm='datasets', return_type='item-or-list')
    create_tree(ds.path, {'file.dat': '123'})
    ds.save('file.dat')
    assert_result_count(
        target.update(recursive=True, merge=True), 1,
        status='ok', type='dataset')
    ok_file_has_content(opj(target.path, 'file.dat'), '123')


@slow  # 20.6910s
@with_testrepos('.*annex.*', flavors=['clone'])
@with_tempfile(mkdir=True)
@with_tempfile(mkdir=True)
def test_update_fetch_all(src, remote_1, remote_2):
    rmt1 = AnnexRepo.clone(src, remote_1)
    rmt2 = AnnexRepo.clone(src, remote_2)

    ds = Dataset(src)
    ds.siblings('add', name="sibling_1", url=remote_1)
    ds.siblings('add', name="sibling_2", url=remote_2)

    # modify the remotes:
    with open(opj(remote_1, "first.txt"), "w") as f:
        f.write("some file load")
    rmt1.add("first.txt")
    rmt1.commit()
    # TODO: Modify an already present file!

    with open(opj(remote_2, "second.txt"), "w") as f:
        f.write("different file load")
    rmt2.add("second.txt", git=True)
    rmt2.commit(msg="Add file to git.")

    # Let's init some special remote which we couldn't really update/fetch
    if not os.environ.get('DATALAD_TESTS_DATALADREMOTE'):
        ds.repo.init_remote(
            'datalad',
            ['encryption=none', 'type=external', 'externaltype=datalad'])
    # fetch all remotes
    assert_result_count(
        ds.update(), 1, status='ok', type='dataset')

    # no merge, so changes are not in active branch:
    assert_not_in("first.txt",
                  ds.repo.get_files(ds.repo.get_active_branch()))
    assert_not_in("second.txt",
                  ds.repo.get_files(ds.repo.get_active_branch()))
    # but we know the changes in remote branches:
    assert_in("first.txt", ds.repo.get_files("sibling_1/master"))
    assert_in("second.txt", ds.repo.get_files("sibling_2/master"))

    # no merge strategy for multiple remotes yet:
    # more clever now, there is a tracking branch that provides a remote
    #assert_raises(NotImplementedError, ds.update, merge=True)

    # merge a certain remote:
    assert_result_count(
        ds.update(
            sibling='sibling_1', merge=True), 1, status='ok', type='dataset')

    # changes from sibling_2 still not present:
    assert_not_in("second.txt",
                  ds.repo.get_files(ds.repo.get_active_branch()))
    # changes from sibling_1 merged:
    assert_in("first.txt",
              ds.repo.get_files(ds.repo.get_active_branch()))
    # it's known to annex, but has no content yet:
    ds.repo.get_file_key("first.txt")  # raises if unknown
    eq_([False], ds.repo.file_has_content(["first.txt"]))


@known_failure_windows  #FIXME
@with_tempfile(mkdir=True)
@with_tempfile(mkdir=True)
def test_newthings_coming_down(originpath, destpath):
    origin = GitRepo(originpath, create=True)
    create_tree(originpath, {'load.dat': 'heavy'})
    Dataset(originpath).save('load.dat')
    ds = install(
        source=originpath, path=destpath,
        result_xfm='datasets', return_type='item-or-list')
    assert_is_instance(ds.repo, GitRepo)
    assert_in('origin', ds.repo.get_remotes())
    # turn origin into an annex
    origin = AnnexRepo(originpath, create=True)
    # clone doesn't know yet
    assert_false(knows_annex(ds.path))
    # but after an update it should
    # no merge, only one sibling, no parameters should be specific enough
    assert_result_count(ds.update(), 1, status='ok', type='dataset')
    assert(knows_annex(ds.path))
    # no branches appeared
    eq_(ds.repo.get_branches(), ['master'])
    # now merge, and get an annex
    assert_result_count(ds.update(merge=True), 1, status='ok', type='dataset')
    assert_in('git-annex', ds.repo.get_branches())
    assert_is_instance(ds.repo, AnnexRepo)
    # should be fully functional
    testfname = opj(ds.path, 'load.dat')
    assert_false(ds.repo.file_has_content(testfname))
    ds.get('.')
    ok_file_has_content(opj(ds.path, 'load.dat'), 'heavy')
    # check that a new tag comes down
    origin.tag('first!')
    assert_result_count(ds.update(), 1, status='ok', type='dataset')
    eq_(ds.repo.get_tags(output='name')[0], 'first!')

    # and now we destroy the remote annex
    origin._git_custom_command([], ['git', 'config', '--remove-section', 'annex'])
    rmtree(opj(origin.path, '.git', 'annex'), chmod_files=True)
    origin._git_custom_command([], ['git', 'branch', '-D', 'git-annex'])
    origin = GitRepo(originpath)
    assert_false(knows_annex(originpath))

    # and update the local clone
    # for now this should simply not fail (see gh-793), later might be enhanced to a
    # graceful downgrade
    before_branches = ds.repo.get_branches()
    assert_result_count(ds.update(), 1, status='ok', type='dataset')
    eq_(before_branches, ds.repo.get_branches())
    # annex branch got pruned
    eq_(['origin/HEAD', 'origin/master'], ds.repo.get_remote_branches())
    # check that a new tag comes down even if repo types mismatch
    origin.tag('second!')
    assert_result_count(ds.update(), 1, status='ok', type='dataset')
    eq_(ds.repo.get_tags(output='name')[-1], 'second!')


@known_failure_windows  #FIXME
@with_tempfile(mkdir=True)
@with_tempfile(mkdir=True)
@with_tempfile(mkdir=True)
def test_update_volatile_subds(originpath, otherpath, destpath):
    origin = Dataset(originpath).create()
    ds = install(
        source=originpath, path=destpath,
        result_xfm='datasets', return_type='item-or-list')
    # as a submodule
    sname = 'subm 1'
    osm1 = origin.create(sname)
    assert_result_count(ds.update(), 1, status='ok', type='dataset')
    # nothing without a merge, no inappropriate magic
    assert_not_in(sname, ds.subdatasets(result_xfm='relpaths'))
    assert_result_count(ds.update(merge=True), 1, status='ok', type='dataset')
    # and we should be able to do update with recursive invocation
    assert_result_count(ds.update(merge=True, recursive=True), 1, status='ok', type='dataset')
    # known, and placeholder exists
    assert_in(sname, ds.subdatasets(result_xfm='relpaths'))
    ok_(exists(opj(ds.path, sname)))

    # remove from origin
    origin.remove(sname)
    assert_result_count(ds.update(merge=True), 1, status='ok', type='dataset')
    # gone locally, wasn't checked out
    assert_not_in(sname, ds.subdatasets(result_xfm='relpaths'))
    assert_false(exists(opj(ds.path, sname)))

    # re-introduce at origin
    osm1 = origin.create(sname)
    create_tree(osm1.path, {'load.dat': 'heavy'})
    origin.save(opj(osm1.path, 'load.dat'))
    assert_result_count(ds.update(merge=True), 1, status='ok', type='dataset')
    # grab new content of uninstall subdataset, right away
    ds.get(opj(ds.path, sname, 'load.dat'))
    ok_file_has_content(opj(ds.path, sname, 'load.dat'), 'heavy')

    # modify ds and subds at origin
    create_tree(origin.path, {'mike': 'this', sname: {'probe': 'little'}})
    origin.save(recursive=True)
    ok_clean_git(origin.path)

    # updates for both datasets should come down the pipe
    assert_result_count(ds.update(merge=True, recursive=True),
                        2, status='ok', type='dataset')
    ok_clean_git(ds.path)

    # now remove just-installed subdataset from origin again
    origin.remove(sname, check=False)
    assert_not_in(sname, origin.subdatasets(result_xfm='relpaths'))
    assert_in(sname, ds.subdatasets(result_xfm='relpaths'))
    # merge should disconnect the installed subdataset, but leave the actual
    # ex-subdataset alone
    assert_result_count(ds.update(merge=True, recursive=True),
                        1, type='dataset')
    assert_not_in(sname, ds.subdatasets(result_xfm='relpaths'))
    ok_file_has_content(opj(ds.path, sname, 'load.dat'), 'heavy')
    ok_(Dataset(opj(ds.path, sname)).is_installed())

    # now remove the now disconnected subdataset for further tests
    # not using a bound method, not giving a parentds, should
    # not be needed to get a clean dataset
    remove(op.join(ds.path, sname), check=False)
    ok_clean_git(ds.path)

    # new separate subdataset, not within the origin dataset
    otherds = Dataset(otherpath).create()
    # install separate dataset as a submodule
    ds.install(source=otherds.path, path='other')
    create_tree(otherds.path, {'brand': 'new'})
    otherds.save()
    ok_clean_git(otherds.path)
    # pull in changes
    res = ds.update(merge=True, recursive=True)
    assert_result_count(
        res, 2, status='ok', action='update', type='dataset')
    # the next is to check for #2858
    ok_clean_git(ds.path)


@known_failure_windows  #FIXME
@with_tempfile(mkdir=True)
@with_tempfile(mkdir=True)
def test_reobtain_data(originpath, destpath):
    origin = Dataset(originpath).create()
    ds = install(
        source=originpath, path=destpath,
        result_xfm='datasets', return_type='item-or-list')
    # no harm
    assert_result_count(ds.update(merge=True, reobtain_data=True), 1)
    # content
    create_tree(origin.path, {'load.dat': 'heavy'})
    origin.save(opj(origin.path, 'load.dat'))
    # update does not bring data automatically
    assert_result_count(ds.update(merge=True, reobtain_data=True), 1)
    assert_in('load.dat', ds.repo.get_annexed_files())
    assert_false(ds.repo.file_has_content('load.dat'))
    # now get data
    ds.get('load.dat')
    ok_file_has_content(opj(ds.path, 'load.dat'), 'heavy')
    # new content at origin
    create_tree(origin.path, {'novel': 'but boring'})
    origin.save()
    # update must not bring in data for new file
    result = ds.update(merge=True, reobtain_data=True)
    assert_in_results(result, action='get', status='notneeded')

    ok_file_has_content(opj(ds.path, 'load.dat'), 'heavy')
    assert_in('novel', ds.repo.get_annexed_files())
    assert_false(ds.repo.file_has_content('novel'))
    # modify content at origin
    os.remove(opj(origin.path, 'load.dat'))
    create_tree(origin.path, {'load.dat': 'light'})
    origin.save()
    # update must update file with existing data, but leave empty one alone
    res = ds.update(merge=True, reobtain_data=True)
    assert_result_count(res, 2)
    assert_result_count(res, 1, status='ok', type='dataset', action='update')
    assert_result_count(res, 1, status='ok', type='file', action='get')
    ok_file_has_content(opj(ds.path, 'load.dat'), 'light')
    assert_false(ds.repo.file_has_content('novel'))


@with_tempfile(mkdir=True)
def test_multiway_merge(path):
    # prepare ds with two siblings, but no tracking branch
    ds = Dataset(op.join(path, 'ds_orig')).create()
    r1 = AnnexRepo(path=op.join(path, 'ds_r1'), git_opts={'bare': True})
    r2 = GitRepo(path=op.join(path, 'ds_r2'), git_opts={'bare': True})
    ds.siblings(action='add', name='r1', url=r1.path)
    ds.siblings(action='add', name='r2', url=r2.path)
    assert_status('ok', ds.publish(to='r1'))
    assert_status('ok', ds.publish(to='r2'))
    # just a fetch should be no issue
    assert_status('ok', ds.update())
    # ATM we do not support multi-way merges
    assert_status('impossible', ds.update(merge=True, on_failure='ignore'))
