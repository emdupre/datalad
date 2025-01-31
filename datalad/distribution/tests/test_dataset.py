# ex: set sts=4 ts=4 sw=4 noet:
# ## ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
#
#   See COPYING file distributed along with the datalad package for the
#   copyright and license terms.
#
# ## ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
"""Test Dataset class

"""

import os
import shutil
import os.path as op
from os.path import join as opj, abspath, normpath, relpath, exists

from six import text_type

from ..dataset import Dataset, EnsureDataset, resolve_path, require_dataset
from ..dataset import rev_resolve_path
from datalad import cfg
from datalad.api import create
from datalad.api import get
import datalad.utils as ut
from datalad.utils import chpwd, getpwd, rmtree
from datalad.utils import _path_
from datalad.utils import get_dataset_root
from datalad.utils import on_windows
from datalad.utils import Path
from datalad.support.gitrepo import GitRepo
from datalad.support.annexrepo import AnnexRepo

from nose.tools import ok_, eq_, assert_false, assert_equal, assert_true, \
    assert_is_instance, assert_is_none, assert_is_not, assert_is_not_none
from datalad.tests.utils import SkipTest
from datalad.tests.utils import with_tempfile, assert_in, with_tree, with_testrepos
from datalad.tests.utils import assert_cwd_unchanged
from datalad.tests.utils import assert_raises
from datalad.tests.utils import known_failure_windows
from datalad.tests.utils import assert_is
from datalad.tests.utils import assert_not_equal
from datalad.tests.utils import assert_result_count

from datalad.support.exceptions import InsufficientArgumentsError
from datalad.support.exceptions import PathKnownToRepositoryError


def test_EnsureDataset():

    c = EnsureDataset()

    # fails with anything else than a string or a Dataset:
    assert_raises(ValueError, c, 1)
    assert_raises(ValueError, c, ['a', 'list'])
    assert_raises(ValueError, c, (1, 2, 3))
    assert_raises(ValueError, c, {"what": "ever"})

    # let's a Dataset instance pass, but leaves a path untouched
    test_path = opj("some", "path")
    ok_(isinstance(c(test_path), type(test_path)))
    ok_(isinstance(Dataset(test_path), Dataset))

    # Note: Ensuring that string is valid path is not
    # part of the constraint itself, so not explicitly tested here.


@assert_cwd_unchanged
@with_tempfile(mkdir=True)
def test_resolve_path(somedir):

    abs_path = abspath(somedir)  # just to be sure
    rel_path = "some"
    expl_path_cur = opj(os.curdir, rel_path)
    expl_path_par = opj(os.pardir, rel_path)

    eq_(resolve_path(abs_path), abs_path)

    current = getpwd()
    # no Dataset => resolve using cwd:
    eq_(resolve_path(abs_path), abs_path)
    eq_(resolve_path(rel_path), opj(current, rel_path))
    eq_(resolve_path(expl_path_cur), normpath(opj(current, expl_path_cur)))
    eq_(resolve_path(expl_path_par), normpath(opj(current, expl_path_par)))

    # now use a Dataset as reference:
    ds = Dataset(abs_path)
    eq_(resolve_path(abs_path, ds), abs_path)
    eq_(resolve_path(rel_path, ds), opj(abs_path, rel_path))
    eq_(resolve_path(expl_path_cur, ds), normpath(opj(current, expl_path_cur)))
    eq_(resolve_path(expl_path_par, ds), normpath(opj(current, expl_path_par)))


# TODO: test remember/recall more extensive?


@with_testrepos('submodule_annex')
@with_tempfile(mkdir=True)
def test_is_installed(src, path):
    ds = Dataset(path)
    assert_false(ds.is_installed())

    # get a clone:
    AnnexRepo.clone(src, path)
    ok_(ds.is_installed())
    # submodule still not installed:
    subds = Dataset(opj(path, 'subm 1'))
    assert_false(subds.is_installed())
    # We must not be able to create a new repository under a known
    # subdataset path.
    # Note: Unfortunately we would still be able to generate it under
    # subdirectory within submodule, e.g. `subm 1/subdir` but that is
    # not checked here. `create` provides that protection though.
    res = subds.create(on_failure='ignore',
                       return_type='list',
                       result_filter=None,
                       result_xfm=None)
    assert_result_count(res, 1)
    assert_result_count(
        res, 1, status='error', path=subds.path,
        message=(
            'collision with content in parent dataset at %s: %s',
            ds.path, [subds.path]))
    # get the submodule
    # This would init so there is a .git file with symlink info, which is
    # as we agreed is more pain than gain, so let's use our install which would
    # do it right, after all we are checking 'is_installed' ;)
    # from datalad.cmd import Runner
    # Runner().run(['git', 'submodule', 'update', '--init', 'subm 1'], cwd=path)
    with chpwd(path):
        get('subm 1')
    ok_(subds.is_installed())
    # wipe it out
    rmtree(ds.path)
    assert_false(ds.is_installed())


@with_tempfile(mkdir=True)
def test_dataset_contructor(path):
    # dataset needs a path
    assert_raises(TypeError, Dataset)
    assert_raises(AttributeError, Dataset, None)
    dsabs = Dataset(path)
    # always abspath
    ok_(os.path.isabs(dsabs.path))
    eq_(path, dsabs.path)
    # no repo
    eq_(dsabs.repo, None)
    # same result when executed in that path and using relative paths
    with chpwd(path):
        dsrel = Dataset('.')
        eq_(dsrel.path, dsabs.path)
        # no repo either, despite directory existing now
        eq_(dsrel.repo, None)


@with_tempfile(mkdir=True)
def test_repo_cache(path):
    ds = Dataset(path)
    # none by default
    eq_(ds.repo, None)
    # make Git repo manually
    git = GitRepo(path=path, create=True)
    repo = ds.repo
    # got one
    assert_false(repo is None)
    # stays that one
    assert_true(ds.repo is repo)
    # now turn into an annex
    annex = AnnexRepo(path=path, create=True)
    # repo instance must change
    assert_false(ds.repo is repo)
    assert_true(isinstance(ds.repo, AnnexRepo))


@known_failure_windows  # leaves modified .gitmodules behind
@with_tempfile(mkdir=True)
def test_subdatasets(path):
    # from scratch
    ds = Dataset(path)
    assert_false(ds.is_installed())
    eq_(ds.subdatasets(), [])
    ds = ds.create()
    assert_true(ds.is_installed())
    eq_(ds.subdatasets(), [])
    # create some file and commit it
    open(os.path.join(ds.path, 'test'), 'w').write('some')
    ds.save(path='test', message="Hello!", version_tag=1)
    assert_true(ds.is_installed())
    # Assuming that tmp location was not under a super-dataset
    eq_(ds.get_superdataset(), None)
    eq_(ds.get_superdataset(topmost=True), ds)

    # add itself as a subdataset (crazy, isn't it?)
    subds = ds.install('subds', source=path,
        result_xfm='datasets', return_type='item-or-list')
    assert_true(subds.is_installed())
    eq_(subds.get_superdataset(), ds)
    eq_(subds.get_superdataset(topmost=True), ds)

    subdss = ds.subdatasets()
    eq_(len(subdss), 1)
    eq_(subds.path, ds.subdatasets(result_xfm='paths')[0])
    eq_(subdss, ds.subdatasets(recursive=True))
    eq_(subdss, ds.subdatasets(fulfilled=True))
    ds.save(message="with subds", version_tag=2)
    ds.recall_state(1)
    assert_true(ds.is_installed())
    eq_(ds.subdatasets(), [])

    # very nested subdataset to test topmost
    subsubds = subds.install(
        _path_('d1/subds'), source=path,
        result_xfm='datasets', return_type='item-or-list')
    assert_true(subsubds.is_installed())
    eq_(subsubds.get_superdataset(), subds)
    # by default, it will only report a subperdataset that actually
    # has the queries dataset as a registered true subdataset
    eq_(subsubds.get_superdataset(topmost=True), subds)
    # by we can also ask for a dataset that is merely above
    eq_(subsubds.get_superdataset(topmost=True, registered_only=False), ds)

    # verify that '^' alias would work
    with chpwd(subsubds.path):
        dstop = Dataset('^')
        eq_(dstop, subds)
        # and while in the dataset we still can resolve into central one
        dscentral = Dataset('///')
        eq_(dscentral.path,
            cfg.obtain('datalad.locations.default-dataset'))

    with chpwd(ds.path):
        dstop = Dataset('^')
        eq_(dstop, ds)

    # TODO actual submodule checkout is still there


@with_tempfile(mkdir=True)
def test_require_dataset(path):
    with chpwd(path):
        assert_raises(
            InsufficientArgumentsError,
            require_dataset,
            None)
        create('.')
        # in this folder by default
        assert_equal(
            require_dataset(None).path,
            path)

        assert_equal(
            require_dataset('some', check_installed=False).path,
            abspath('some'))
        assert_raises(
            ValueError,
            require_dataset,
            'some',
            check_installed=True)


@with_tempfile(mkdir=True)
def test_dataset_id(path):
    ds = Dataset(path)
    assert_equal(ds.id, None)
    ds.create()
    dsorigid = ds.id
    # ID is always a UUID
    assert_equal(ds.id.count('-'), 4)
    assert_equal(len(ds.id), 36)
    # creating a new object for the same path
    # yields the same ID

    # Note: Since we switched to singletons, a reset is required in order to
    # make sure we get a new object
    # TODO: Reconsider the actual intent of this assertion. Clearing the flyweight
    # dict isn't a nice approach. May be create needs a fix/RF?
    Dataset._unique_instances.clear()
    newds = Dataset(path)
    assert_false(ds is newds)
    assert_equal(ds.id, newds.id)
    # recreating the dataset does NOT change the id
    #
    # Note: Since we switched to singletons, a reset is required in order to
    # make sure we get a new object
    # TODO: Reconsider the actual intent of this assertion. Clearing the flyweight
    # dict isn't a nice approach. May be create needs a fix/RF?
    Dataset._unique_instances.clear()
    ds.create(no_annex=True, force=True)
    assert_equal(ds.id, dsorigid)
    # even adding an annex doesn't
    #
    # Note: Since we switched to singletons, a reset is required in order to
    # make sure we get a new object
    # TODO: Reconsider the actual intent of this assertion. Clearing the flyweight
    # dict isn't a nice approach. May be create needs a fix/RF?
    Dataset._unique_instances.clear()
    AnnexRepo._unique_instances.clear()
    ds.create(force=True)
    assert_equal(ds.id, dsorigid)
    # dataset ID and annex UUID have nothing to do with each other
    # if an ID was already generated
    assert_true(ds.repo.uuid != ds.id)
    # creating a new object for the same dataset with an ID on record
    # yields the same ID
    #
    # Note: Since we switched to singletons, a reset is required in order to
    # make sure we get a new object
    # TODO: Reconsider the actual intent of this assertion. Clearing the flyweight
    # dict isn't a nice approach. May be create needs a fix/RF?
    Dataset._unique_instances.clear()
    newds = Dataset(path)
    assert_false(ds is newds)
    assert_equal(ds.id, newds.id)
    # even if we generate a dataset from scratch with an annex UUID right away,
    # this is also not the ID
    annexds = Dataset(opj(path, 'scratch')).create()
    assert_true(annexds.id != annexds.repo.uuid)


@with_tempfile(mkdir=True)
@with_tempfile(mkdir=True)
def test_Dataset_flyweight(path1, path2):

    ds1 = Dataset(path1)
    assert_is_instance(ds1, Dataset)
    # instantiate again:
    ds2 = Dataset(path1)
    assert_is_instance(ds2, Dataset)
    # the very same object:
    ok_(ds1 is ds2)

    # reference the same via relative path:
    with chpwd(path1):
        ds3 = Dataset(relpath(path1, start=path2))
        ok_(ds1 == ds3)
        ok_(ds1 is ds3)

    # on windows as symlink is not what you think it is
    if not on_windows:
        # reference the same via symlink:
        with chpwd(path2):
            os.symlink(path1, 'linked')
            ds3 = Dataset('linked')
            ok_(ds3 == ds1)
            ok_(ds3 is not ds1)


@with_tempfile
def test_property_reevaluation(repo1):

    from os.path import lexists
    from datalad.tests.utils import ok_clean_git

    ds = Dataset(repo1)
    assert_is_none(ds.repo)
    assert_is_not_none(ds.config)
    first_config = ds.config
    assert_false(ds._cfg_bound)
    assert_is_none(ds.id)

    ds.create()
    ok_clean_git(repo1)
    # after creation, we have `repo`, and `config` was reevaluated to point
    # to the repo's config:
    assert_is_not_none(ds.repo)
    assert_is_not_none(ds.config)
    second_config = ds.config
    assert_true(ds._cfg_bound)
    assert_is(ds.config, ds.repo.config)
    assert_is_not(first_config, second_config)
    assert_is_not_none(ds.id)
    first_id = ds.id

    ds.remove()
    # repo is gone, and config is again reevaluated to only provide user/system
    # level config:
    assert_false(lexists(ds.path))
    assert_is_none(ds.repo)
    assert_is_not_none(ds.config)
    third_config = ds.config
    assert_false(ds._cfg_bound)
    assert_is_not(second_config, third_config)
    assert_is_none(ds.id)

    ds.create()
    ok_clean_git(repo1)
    # after recreation everything is sane again:
    assert_is_not_none(ds.repo)
    assert_is_not_none(ds.config)
    assert_is(ds.config, ds.repo.config)
    forth_config = ds.config
    assert_true(ds._cfg_bound)
    assert_is_not(third_config, forth_config)
    assert_is_not_none(ds.id)
    assert_not_equal(ds.id, first_id)


# While os.symlink does work on windows (since vista), os.path.realpath
# doesn't resolve such symlinks. This has all kinds of implications.
# Hopefully this can be dealt with, when we switch to using pathlib
# (see datalad-revolution).
@known_failure_windows
@with_tempfile
@with_tempfile
@with_tempfile
@with_tempfile(mkdir=True)
@with_tempfile
def test_symlinked_dataset_properties(repo1, repo2, repo3, non_repo, symlink):

    ds = Dataset(repo1).create()

    # now, let ds be a symlink and change that symlink to point to different
    # things:
    ar2 = AnnexRepo(repo2)
    ar3 = AnnexRepo(repo3)
    assert_true(os.path.isabs(non_repo))

    os.symlink(repo1, symlink)
    ds_link = Dataset(symlink)
    assert_is(ds_link.repo, ds.repo)  # same Repo instance
    assert_is_not(ds_link, ds)  # but not the same Dataset instance
    assert_is(ds_link.config, ds.repo.config)
    assert_true(ds_link._cfg_bound)
    assert_is_not_none(ds_link.id)
    # same id, although different Dataset instance:
    assert_equal(ds_link.id, ds.id)

    os.unlink(symlink)
    os.symlink(repo2, symlink)

    assert_is(ds_link.repo, ar2)  # same Repo instance
    assert_is(ds_link.config, ar2.config)
    assert_true(ds_link._cfg_bound)
    # id is None again, since this repository is an annex but there was no
    # Dataset.create() called yet.
    assert_is_none(ds_link.id)

    os.unlink(symlink)
    os.symlink(repo3, symlink)

    assert_is(ds_link.repo, ar3)  # same Repo instance
    assert_is(ds_link.config, ar3.config)
    assert_true(ds_link._cfg_bound)
    # id is None again, since this repository is an annex but there was no
    # Dataset.create() called yet.
    assert_is_none(ds_link.id)

    os.unlink(symlink)
    os.symlink(non_repo, symlink)

    assert_is_none(ds_link.repo)
    assert_is_not(ds_link.config, ar3.config)
    assert_false(ds_link._cfg_bound)
    assert_is_none(ds_link.id)


@with_tempfile(mkdir=True)
def test_rev_resolve_path(path):
    if op.realpath(path) != path:
        raise SkipTest("Test assumptions require non-symlinked parent paths")
    # initially ran into on OSX https://github.com/datalad/datalad/issues/2406
    opath = op.join(path, "origin")
    os.makedirs(opath)
    if not on_windows:
        lpath = op.join(path, "linked")
        os.symlink('origin', lpath)

    ds_global = Dataset(path)
    # path resolution of absolute paths is not influenced by symlinks
    # ignore the linked path on windows, it is not a symlink in the POSIX sense
    for d in (opath,) if on_windows else (opath, lpath):
        ds_local = Dataset(d)
        # no symlink resolution
        eq_(text_type(rev_resolve_path(d)), d)
        # list comes out as a list
        eq_(rev_resolve_path([d]), [Path(d)])
        # multiple OK
        eq_(rev_resolve_path([d, d]), [Path(d), Path(d)])

        with chpwd(d):
            # be aware: knows about cwd, but this CWD has symlinks resolved
            eq_(text_type(rev_resolve_path(d).cwd()), opath)
            # using pathlib's `resolve()` will resolve any
            # symlinks
            # also resolve `opath`, as on old windows systems the path might
            # come in crippled (e.g. C:\Users\MIKE~1/...)
            # and comparison would fails unjustified
            eq_(rev_resolve_path('.').resolve(), ut.Path(opath).resolve())
            # no norming, but absolute paths, without resolving links
            eq_(rev_resolve_path('.'), ut.Path(d))
            eq_(text_type(rev_resolve_path('.')), d)

            # there is no concept of an "explicit" relative path anymore
            # relative is relative, regardless of the specific syntax
            eq_(rev_resolve_path(op.join(os.curdir, 'bu'), ds=ds_global),
                ds_global.pathobj / 'bu')
            # there is no full normpath-ing or other funky resolution of
            # parent directory back-reference
            eq_(text_type(rev_resolve_path(op.join(os.pardir, 'bu'), ds=ds_global)),
                op.join(ds_global.path, os.pardir, 'bu'))

        # resolve against a dataset given as a path/str
        # (cmdline input scenario)
        eq_(rev_resolve_path('bu', ds=ds_local.path), Path.cwd() / 'bu')
        eq_(rev_resolve_path('bu', ds=ds_global.path), Path.cwd() / 'bu')
        # resolve against a dataset given as a dataset instance
        # (object method scenario)
        eq_(rev_resolve_path('bu', ds=ds_local), ds_local.pathobj / 'bu')
        eq_(rev_resolve_path('bu', ds=ds_global), ds_global.pathobj / 'bu')
        # not being inside a dataset doesn't change the resolution result
        eq_(rev_resolve_path(op.join(os.curdir, 'bu'), ds=ds_global),
            ds_global.pathobj / 'bu')
        eq_(text_type(rev_resolve_path(op.join(os.pardir, 'bu'), ds=ds_global)),
            op.join(ds_global.path, os.pardir, 'bu'))


# little brother of the test above, but actually (must) run
# under any circumstances
@with_tempfile(mkdir=True)
def test_rev_resolve_path_symlink_edition(path):
    deepest = ut.Path(path) / 'one' / 'two' / 'three'
    deepest_str = text_type(deepest)
    os.makedirs(deepest_str)
    with chpwd(deepest_str):
        # direct absolute
        eq_(deepest, rev_resolve_path(deepest))
        eq_(deepest, rev_resolve_path(deepest_str))
        # explicit direct relative
        eq_(deepest, rev_resolve_path('.'))
        eq_(deepest, rev_resolve_path(op.join('.', '.')))
        eq_(deepest, rev_resolve_path(op.join('..', 'three')))
        eq_(deepest, rev_resolve_path(op.join('..', '..', 'two', 'three')))
        eq_(deepest, rev_resolve_path(op.join('..', '..', '..',
                                              'one', 'two', 'three')))
        # weird ones
        eq_(deepest, rev_resolve_path(op.join('..', '.', 'three')))
        eq_(deepest, rev_resolve_path(op.join('..', 'three', '.')))
        eq_(deepest, rev_resolve_path(op.join('..', 'three', '.')))
        eq_(deepest, rev_resolve_path(op.join('.', '..', 'three')))


@with_tempfile(mkdir=True)
def test_hashable(path):
    path = ut.Path(path)
    tryme = set()
    # is it considered hashable at all
    tryme.add(Dataset(path / 'one'))
    eq_(len(tryme), 1)
    # do another one, same class different path
    tryme.add(Dataset(path / 'two'))
    eq_(len(tryme), 2)
    # test whether two different types of repo instances pointing
    # to the same repo on disk are considered different
    Dataset(path).create()
    tryme.add(GitRepo(path))
    eq_(len(tryme), 3)
    tryme.add(AnnexRepo(path))
    eq_(len(tryme), 4)
