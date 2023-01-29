#!/usr/bin/env python3
#
# Unit test cases for buku
#
import math
import os
import re
import shutil
import sqlite3
import sys
import unittest
import urllib
import zipfile
from tempfile import NamedTemporaryFile, TemporaryDirectory
from unittest import mock

import pytest
import yaml
from genericpath import exists
from hypothesis import example, given, settings
from hypothesis import strategies as st

from buku import BukuDb, parse_tags, prompt


def get_temp_dir_path():
    with TemporaryDirectory(prefix="bukutest_") as dir_obj:
        return dir_obj

TEST_TEMP_DIR_PATH = get_temp_dir_path()
TEST_TEMP_DBDIR_PATH = os.path.join(TEST_TEMP_DIR_PATH, "buku")
TEST_TEMP_DBFILE_PATH = os.path.join(TEST_TEMP_DBDIR_PATH, "bookmarks.db")
MAX_SQLITE_INT = int(math.pow(2, 63) - 1)
TEST_PRINT_REC = ("https://example.com", "", parse_tags(["cat,ant,bee,1"]), "")

TEST_BOOKMARKS = [
    [
        "http://slashdot.org",
        "SLASHDOT",
        parse_tags(["old,news"]),
        "News for old nerds, stuff that doesn't matter",
    ],
    [
        "http://www.zażółćgęśląjaźń.pl/",
        "ZAŻÓŁĆ",
        parse_tags(["zażółć,gęślą,jaźń"]),
        "Testing UTF-8, zażółć gęślą jaźń.",
    ],
    [
        "http://example.com/",
        "test",
        parse_tags(["test,tes,est,es"]),
        "a case for replace_tag test",
    ],
]

only_python_3_5 = pytest.mark.skipif(
    sys.version_info < (3, 5), reason="requires Python 3.5 or later"
)


@pytest.fixture(scope="module")
def vcr_cassette_dir(request):
    # Put all cassettes in vhs/{module}/{test}.yaml
    return os.path.join("tests", "vcr_cassettes", request.module.__name__)


@pytest.fixture()
def setup():
    os.environ["XDG_DATA_HOME"] = TEST_TEMP_DIR_PATH

    # start every test from a clean state
    if exists(TEST_TEMP_DBFILE_PATH):
        os.remove(TEST_TEMP_DBFILE_PATH)


class PrettySafeLoader(
    yaml.SafeLoader
):  # pylint: disable=too-many-ancestors,too-few-public-methods
    def construct_python_tuple(self, node):
        return tuple(self.construct_sequence(node))


PrettySafeLoader.add_constructor(
    "tag:yaml.org,2002:python/tuple", PrettySafeLoader.construct_python_tuple
)


class TestBukuDb(unittest.TestCase):
    def setUp(self):
        os.environ["XDG_DATA_HOME"] = TEST_TEMP_DIR_PATH

        # start every test from a clean state
        if exists(TEST_TEMP_DBFILE_PATH):
            os.remove(TEST_TEMP_DBFILE_PATH)

        self.bookmarks = TEST_BOOKMARKS
        self.bdb = BukuDb()

    def tearDown(self):
        os.environ["XDG_DATA_HOME"] = TEST_TEMP_DIR_PATH

    @pytest.mark.non_tox
    def test_get_default_dbdir(self):
        dbdir_expected = TEST_TEMP_DBDIR_PATH
        dbdir_local_expected = os.path.join(
            os.path.expanduser("~"), ".local", "share", "buku"
        )
        dbdir_relative_expected = os.path.abspath(".")

        # desktop linux
        self.assertEqual(dbdir_expected, BukuDb.get_default_dbdir())

        # desktop generic
        os.environ.pop("XDG_DATA_HOME")
        self.assertEqual(dbdir_local_expected, BukuDb.get_default_dbdir())

        # no desktop

        # -- home is defined differently on various platforms.
        # -- keep a copy and set it back once done
        originals = {}
        for env_var in ["HOME", "HOMEPATH", "HOMEDIR"]:
            try:
                originals[env_var] = os.environ.pop(env_var)
            except KeyError:
                pass
        self.assertEqual(dbdir_relative_expected, BukuDb.get_default_dbdir())
        for key, value in list(originals.items()):
            os.environ[key] = value

    # # not sure how to test this in nondestructive manner
    # def test_move_legacy_dbfile(self):
    #     self.fail()

    def test_initdb(self):
        if exists(TEST_TEMP_DBFILE_PATH):
            os.remove(TEST_TEMP_DBFILE_PATH)
        self.assertIs(False, exists(TEST_TEMP_DBFILE_PATH))
        conn, curr = BukuDb.initdb()
        self.assertIsInstance(conn, sqlite3.Connection)
        self.assertIsInstance(curr, sqlite3.Cursor)
        self.assertIs(True, exists(TEST_TEMP_DBFILE_PATH))
        curr.close()
        conn.close()

    def test_get_rec_by_id(self):
        for bookmark in self.bookmarks:
            # adding bookmark from self.bookmarks
            self.bdb.add_rec(*bookmark)

        # the expected bookmark
        expected = (
            1,
            "http://slashdot.org",
            "SLASHDOT",
            ",news,old,",
            "News for old nerds, stuff that doesn't matter",
            0,
        )
        bookmark_from_db = self.bdb.get_rec_by_id(1)
        # asserting bookmark matches expected
        self.assertEqual(expected, bookmark_from_db)
        # asserting None returned if index out of range
        self.assertIsNone(self.bdb.get_rec_by_id(len(self.bookmarks[0]) + 1))

    def test_get_rec_id(self):
        for idx, bookmark in enumerate(self.bookmarks):
            # adding bookmark from self.bookmarks to database
            self.bdb.add_rec(*bookmark)
            # asserting index is in order
            idx_from_db = self.bdb.get_rec_id(bookmark[0])
            self.assertEqual(idx + 1, idx_from_db)

        # asserting None is returned for nonexistent url
        idx_from_db = self.bdb.get_rec_id("http://nonexistent.url")
        self.assertIsNone(idx_from_db)

    def test_add_rec(self):
        for bookmark in self.bookmarks:
            # adding bookmark from self.bookmarks to database
            self.bdb.add_rec(*bookmark)
            # retrieving bookmark from database
            index = self.bdb.get_rec_id(bookmark[0])
            from_db = self.bdb.get_rec_by_id(index)
            self.assertIsNotNone(from_db)
            # comparing data
            for pair in zip(from_db[1:], bookmark):
                self.assertEqual(*pair)

        # TODO: tags should be passed to the api as a sequence...

    def test_suggest_tags(self):
        for bookmark in self.bookmarks:
            self.bdb.add_rec(*bookmark)

        tagstr = ",test,old,"
        with mock.patch("builtins.input", return_value="1 2 3"):
            expected_results = ",es,est,news,old,test,"
            suggested_results = self.bdb.suggest_similar_tag(tagstr)
            self.assertEqual(expected_results, suggested_results)

        # returns user supplied tags if none are in the DB
        tagstr = ",uniquetag1,uniquetag2,"
        expected_results = tagstr
        suggested_results = self.bdb.suggest_similar_tag(tagstr)
        self.assertEqual(expected_results, suggested_results)

    def test_update_rec(self):
        old_values = self.bookmarks[0]
        new_values = self.bookmarks[1]

        # adding bookmark and getting index
        self.bdb.add_rec(*old_values)
        index = self.bdb.get_rec_id(old_values[0])
        # updating with new values
        self.bdb.update_rec(index, *new_values)
        # retrieving bookmark from database
        from_db = self.bdb.get_rec_by_id(index)
        self.assertIsNotNone(from_db)
        # checking if values are updated
        for pair in zip(from_db[1:], new_values):
            self.assertEqual(*pair)

    def test_append_tag_at_index(self):
        for bookmark in self.bookmarks:
            self.bdb.add_rec(*bookmark)

        # tags to add
        old_tags = self.bdb.get_rec_by_id(1)[3]
        new_tags = ",foo,bar,baz"
        self.bdb.append_tag_at_index(1, new_tags)
        # updated list of tags
        from_db = self.bdb.get_rec_by_id(1)[3]

        # checking if new tags were added to the bookmark
        self.assertTrue(split_and_test_membership(new_tags, from_db))
        # checking if old tags still exist
        self.assertTrue(split_and_test_membership(old_tags, from_db))

    def test_append_tag_at_all_indices(self):
        for bookmark in self.bookmarks:
            self.bdb.add_rec(*bookmark)

        # tags to add
        new_tags = ",foo,bar,baz"
        # record of original tags for each bookmark
        old_tagsets = {
            i: self.bdb.get_rec_by_id(i)[3]
            for i in inclusive_range(1, len(self.bookmarks))
        }

        with mock.patch("builtins.input", return_value="y"):
            self.bdb.append_tag_at_index(0, new_tags)
            # updated tags for each bookmark
            from_db = [
                (i, self.bdb.get_rec_by_id(i)[3])
                for i in inclusive_range(1, len(self.bookmarks))
            ]
            for index, tagset in from_db:
                # checking if new tags added to bookmark
                self.assertTrue(split_and_test_membership(new_tags, tagset))
                # checking if old tags still exist for bookmark
                self.assertTrue(split_and_test_membership(old_tagsets[index], tagset))

    def test_delete_tag_at_index(self):
        # adding bookmarks
        for bookmark in self.bookmarks:
            self.bdb.add_rec(*bookmark)

        get_tags_at_idx = lambda i: self.bdb.get_rec_by_id(i)[3]
        # list of two-tuples, each containing bookmark index and corresponding tags
        tags_by_index = [
            (i, get_tags_at_idx(i)) for i in inclusive_range(1, len(self.bookmarks))
        ]

        for i, tags in tags_by_index:
            # get the first tag from the bookmark
            to_delete = re.match(",.*?,", tags).group(0)
            self.bdb.delete_tag_at_index(i, to_delete)
            # get updated tags from db
            from_db = get_tags_at_idx(i)
            self.assertNotIn(to_delete, from_db)

    def test_search_keywords_and_filter_by_tags(self):
        # adding bookmark
        for bookmark in self.bookmarks:
            self.bdb.add_rec(*bookmark)

        with mock.patch("buku.prompt"):
            expected = [
                (
                    3,
                    "http://example.com/",
                    "test",
                    ",es,est,tes,test,",
                    "a case for replace_tag test",
                    0,
                )
            ]
            results = self.bdb.search_keywords_and_filter_by_tags(
                ["News", "case"],
                False,
                False,
                False,
                ["est"],
            )
            self.assertIn(expected[0], results)
            expected = [
                (
                    3,
                    "http://example.com/",
                    "test",
                    ",es,est,tes,test,",
                    "a case for replace_tag test",
                    0,
                ),
                (
                    2,
                    "http://www.zażółćgęśląjaźń.pl/",
                    "ZAŻÓŁĆ",
                    ",gęślą,jaźń,zażółć,",
                    "Testing UTF-8, zażółć gęślą jaźń.",
                    0,
                ),
            ]
            results = self.bdb.search_keywords_and_filter_by_tags(
                ["UTF-8", "case"],
                False,
                False,
                False,
                "jaźń, test",
            )
            self.assertIn(expected[0], results)
            self.assertIn(expected[1], results)

    def test_searchdb(self):
        # adding bookmarks
        for bookmark in self.bookmarks:
            self.bdb.add_rec(*bookmark)

        get_first_tag = lambda x: "".join(x[2].split(",")[:2])
        for i, bookmark in enumerate(self.bookmarks):
            tag_search = get_first_tag(bookmark)
            # search by the domain name for url
            url_search = re.match(r"https?://(.*)?\..*", bookmark[0]).group(1)
            title_search = bookmark[1]
            # Expect a five-tuple containing all bookmark data
            # db index, URL, title, tags, description
            expected = [(i + 1,) + tuple(bookmark)]
            expected[0] += tuple([0])
            # search db by tag, url (domain name), and title
            for keyword in (tag_search, url_search, title_search):
                with mock.patch("buku.prompt"):
                    # search by keyword
                    results = self.bdb.searchdb([keyword])
                    self.assertEqual(results, expected)

    def test_search_by_tag(self):
        # adding bookmarks
        for bookmark in self.bookmarks:
            self.bdb.add_rec(*bookmark)

        with mock.patch("buku.prompt"):
            get_first_tag = lambda x: "".join(x[2].split(",")[:2])
            for i, bookmark in enumerate(self.bookmarks):
                # search for bookmark with a tag that is known to exist
                results = self.bdb.search_by_tag(get_first_tag(bookmark))
                # Expect a five-tuple containing all bookmark data
                # db index, URL, title, tags, description
                expected = [(i + 1,) + tuple(bookmark)]
                expected[0] += tuple([0])
                self.assertEqual(results, expected)

    @pytest.mark.vcr("tests/vcr_cassettes/test_search_by_multiple_tags_search_any.yaml")
    def test_search_by_multiple_tags_search_any(self):
        # adding bookmarks
        for bookmark in self.bookmarks:
            self.bdb.add_rec(*bookmark)

        new_bookmark = [
            "https://newbookmark.com",
            "New Bookmark",
            parse_tags(["test,old,new"]),
            "additional bookmark to test multiple tag search",
            0,
        ]

        self.bdb.add_rec(*new_bookmark)

        with mock.patch("buku.prompt"):
            # search for bookmarks matching ANY of the supplied tags
            results = self.bdb.search_by_tag("test, old")
            # Expect a list of five-element tuples containing all bookmark data
            # db index, URL, title, tags, description, ordered by records with
            # the most number of matches.
            expected = [
                (
                    4,
                    "https://newbookmark.com",
                    "New Bookmark",
                    parse_tags([",test,old,new,"]),
                    "additional bookmark to test multiple tag search",
                    0,
                ),
                (
                    1,
                    "http://slashdot.org",
                    "SLASHDOT",
                    parse_tags([",news,old,"]),
                    "News for old nerds, stuff that doesn't matter",
                    0,
                ),
                (
                    3,
                    "http://example.com/",
                    "test",
                    ",es,est,tes,test,",
                    "a case for replace_tag test",
                    0,
                ),
            ]
            self.assertEqual(results, expected)

    @pytest.mark.vcr("tests/vcr_cassettes/test_search_by_multiple_tags_search_all.yaml")
    def test_search_by_multiple_tags_search_all(self):
        # adding bookmarks
        for bookmark in self.bookmarks:
            self.bdb.add_rec(*bookmark)

        new_bookmark = [
            "https://newbookmark.com",
            "New Bookmark",
            parse_tags(["test,old,new"]),
            "additional bookmark to test multiple tag search",
        ]

        self.bdb.add_rec(*new_bookmark)

        with mock.patch("buku.prompt"):
            # search for bookmarks matching ALL of the supplied tags
            results = self.bdb.search_by_tag("test + old")
            # Expect a list of five-element tuples containing all bookmark data
            # db index, URL, title, tags, description
            expected = [
                (
                    4,
                    "https://newbookmark.com",
                    "New Bookmark",
                    parse_tags([",test,old,new,"]),
                    "additional bookmark to test multiple tag search",
                    0,
                )
            ]
            self.assertEqual(results, expected)

    def test_search_by_tags_enforces_space_seprations_search_all(self):

        bookmark1 = [
            "https://bookmark1.com",
            "Bookmark One",
            parse_tags(["tag, two,tag+two"]),
            "test case for bookmark with '+' in tag",
        ]

        bookmark2 = [
            "https://bookmark2.com",
            "Bookmark Two",
            parse_tags(["tag,two, tag-two"]),
            "test case for bookmark with hyphenated tag",
        ]

        self.bdb.add_rec(*bookmark1)
        self.bdb.add_rec(*bookmark2)

        with mock.patch("buku.prompt"):
            # check that space separation for ' + ' operator is enforced
            results = self.bdb.search_by_tag("tag+two")
            # Expect a list of five-element tuples containing all bookmark data
            # db index, URL, title, tags, description
            expected = [
                (
                    1,
                    "https://bookmark1.com",
                    "Bookmark One",
                    parse_tags([",tag,two,tag+two,"]),
                    "test case for bookmark with '+' in tag",
                    0,
                )
            ]
            self.assertEqual(results, expected)
            results = self.bdb.search_by_tag("tag + two")
            # Expect a list of five-element tuples containing all bookmark data
            # db index, URL, title, tags, description
            expected = [
                (
                    1,
                    "https://bookmark1.com",
                    "Bookmark One",
                    parse_tags([",tag,two,tag+two,"]),
                    "test case for bookmark with '+' in tag",
                    0,
                ),
                (
                    2,
                    "https://bookmark2.com",
                    "Bookmark Two",
                    parse_tags([",tag,two,tag-two,"]),
                    "test case for bookmark with hyphenated tag",
                    0,
                ),
            ]
            self.assertEqual(results, expected)

    def test_search_by_tags_exclusion(self):
        # adding bookmarks
        for bookmark in self.bookmarks:
            self.bdb.add_rec(*bookmark)

        new_bookmark = [
            "https://newbookmark.com",
            "New Bookmark",
            parse_tags(["test,old,new"]),
            "additional bookmark to test multiple tag search",
        ]

        self.bdb.add_rec(*new_bookmark)

        with mock.patch("buku.prompt"):
            # search for bookmarks matching ANY of the supplied tags
            # while excluding bookmarks from results that match a given tag
            results = self.bdb.search_by_tag("test, old - est")
            # Expect a list of five-element tuples containing all bookmark data
            # db index, URL, title, tags, description
            expected = [
                (
                    4,
                    "https://newbookmark.com",
                    "New Bookmark",
                    parse_tags([",test,old,new,"]),
                    "additional bookmark to test multiple tag search",
                    0,
                ),
                (
                    1,
                    "http://slashdot.org",
                    "SLASHDOT",
                    parse_tags([",news,old,"]),
                    "News for old nerds, stuff that doesn't matter",
                    0,
                ),
            ]
            self.assertEqual(results, expected)

    @pytest.mark.vcr("tests/vcr_cassettes/test_search_by_tags_enforces_space_seprations_exclusion.yaml")
    def test_search_by_tags_enforces_space_seprations_exclusion(self):

        bookmark1 = [
            "https://bookmark1.com",
            "Bookmark One",
            parse_tags(["tag, two,tag+two"]),
            "test case for bookmark with '+' in tag",
        ]

        bookmark2 = [
            "https://bookmark2.com",
            "Bookmark Two",
            parse_tags(["tag,two, tag-two"]),
            "test case for bookmark with hyphenated tag",
        ]

        bookmark3 = [
            "https://bookmark3.com",
            "Bookmark Three",
            parse_tags(["tag, tag three"]),
            "second test case for bookmark with hyphenated tag",
        ]

        self.bdb.add_rec(*bookmark1)
        self.bdb.add_rec(*bookmark2)
        self.bdb.add_rec(*bookmark3)

        with mock.patch("buku.prompt"):
            # check that space separation for ' - ' operator is enforced
            results = self.bdb.search_by_tag("tag-two")
            # Expect a list of five-element tuples containing all bookmark data
            # db index, URL, title, tags, description
            expected = [
                (
                    2,
                    "https://bookmark2.com",
                    "Bookmark Two",
                    parse_tags([",tag,two,tag-two,"]),
                    "test case for bookmark with hyphenated tag",
                    0,
                ),
            ]
            self.assertEqual(results, expected)
            results = self.bdb.search_by_tag("tag - two")
            # Expect a list of five-element tuples containing all bookmark data
            # db index, URL, title, tags, description
            expected = [
                (
                    3,
                    "https://bookmark3.com",
                    "Bookmark Three",
                    parse_tags([",tag,tag three,"]),
                    "second test case for bookmark with hyphenated tag",
                    0,
                ),
            ]
            self.assertEqual(results, expected)

    def test_search_and_open_in_broswer_by_range(self):
        # adding bookmarks
        for bookmark in self.bookmarks:
            self.bdb.add_rec(*bookmark)

        # simulate user input, select range of indices 1-3
        index_range = "1-%s" % len(self.bookmarks)
        with mock.patch("builtins.input", side_effect=[index_range]):
            with mock.patch("buku.browse") as mock_browse:
                try:
                    # search the db with keywords from each bookmark
                    # searching using the first tag from bookmarks
                    get_first_tag = lambda x: x[2].split(",")[1]
                    results = self.bdb.searchdb(
                        [get_first_tag(bm) for bm in self.bookmarks]
                    )
                    prompt(self.bdb, results)
                except StopIteration:
                    # catch exception thrown by reaching the end of the side effect iterable
                    pass

                # collect arguments passed to browse
                arg_list = [args[0] for args, _ in mock_browse.call_args_list]
                # expect a list of one-tuples that are bookmark URLs
                expected = [x[0] for x in self.bookmarks]
                # checking if browse called with expected arguments
                self.assertEqual(arg_list, expected)

    @pytest.mark.vcr("tests/vcr_cassettes/test_search_and_open_all_in_browser.yaml")
    def test_search_and_open_all_in_browser(self):
        # adding bookmarks
        for bookmark in self.bookmarks:
            self.bdb.add_rec(*bookmark)

        # simulate user input, select 'a' to open all bookmarks in results
        with mock.patch("builtins.input", side_effect=["a"]):
            with mock.patch("buku.browse") as mock_browse:
                try:
                    # search the db with keywords from each bookmark
                    # searching using the first tag from bookmarks
                    get_first_tag = lambda x: x[2].split(",")[1]
                    results = self.bdb.searchdb(
                        [get_first_tag(bm) for bm in self.bookmarks[:2]]
                    )
                    prompt(self.bdb, results)
                except StopIteration:
                    # catch exception thrown by reaching the end of the side effect iterable
                    pass

                # collect arguments passed to browse
                arg_list = [args[0] for args, _ in mock_browse.call_args_list]
                # expect a list of one-tuples that are bookmark URLs
                expected = [x[0] for x in self.bookmarks][:2]
                # checking if browse called with expected arguments
                self.assertEqual(arg_list, expected)

    def test_delete_rec(self):
        # adding bookmark and getting index
        self.bdb.add_rec(*self.bookmarks[0])
        index = self.bdb.get_rec_id(self.bookmarks[0][0])
        # deleting bookmark
        self.bdb.delete_rec(index)
        # asserting it doesn't exist
        from_db = self.bdb.get_rec_by_id(index)
        self.assertIsNone(from_db)

    def test_delete_rec_yes(self):
        # checking that "y" response causes delete_rec to return True
        with mock.patch("builtins.input", return_value="y"):
            self.assertTrue(self.bdb.delete_rec(0))

    def test_delete_rec_no(self):
        # checking that non-"y" response causes delete_rec to return None
        with mock.patch("builtins.input", return_value="n"):
            self.assertFalse(self.bdb.delete_rec(0))

    def test_cleardb(self):
        # adding bookmarks
        self.bdb.add_rec(*self.bookmarks[0])
        # deleting all bookmarks
        with mock.patch("builtins.input", return_value="y"):
            self.bdb.cleardb()
        # assert table has been dropped
        assert self.bdb.get_rec_by_id(0) is None

    def test_replace_tag(self):
        indices = []
        for bookmark in self.bookmarks:
            # adding bookmark, getting index
            self.bdb.add_rec(*bookmark)
            index = self.bdb.get_rec_id(bookmark[0])
            indices += [index]

        # replacing tags
        with mock.patch("builtins.input", return_value="y"):
            self.bdb.replace_tag("news", ["__01"])
        with mock.patch("builtins.input", return_value="y"):
            self.bdb.replace_tag("zażółć", ["__02,__03"])

        # replacing tag which is also a substring of other tag
        with mock.patch("builtins.input", return_value="y"):
            self.bdb.replace_tag("es", ["__04"])

        # removing tags
        with mock.patch("builtins.input", return_value="y"):
            self.bdb.replace_tag("gęślą")
        with mock.patch("builtins.input", return_value="y"):
            self.bdb.replace_tag("old")

        # removing non-existent tag
        with mock.patch("builtins.input", return_value="y"):
            self.bdb.replace_tag("_")

        # removing nonexistent tag which is also a substring of other tag
        with mock.patch("builtins.input", return_value="y"):
            self.bdb.replace_tag("e")

        for url, title, _, _ in self.bookmarks:
            # retrieving from db
            index = self.bdb.get_rec_id(url)
            from_db = self.bdb.get_rec_by_id(index)
            # asserting tags were replaced
            if title == "SLASHDOT":
                self.assertEqual(from_db[3], parse_tags(["__01"]))
            elif title == "ZAŻÓŁĆ":
                self.assertEqual(from_db[3], parse_tags(["__02,__03,jaźń"]))
            elif title == "test":
                self.assertEqual(from_db[3], parse_tags(["test,tes,est,__04"]))

    def test_tnyfy_url(self):
        # shorten a well-known url
        shorturl = self.bdb.tnyfy_url(url="https://www.google.com", shorten=True)
        self.assertEqual(shorturl, "http://tny.im/yt")

        # expand a well-known short url
        url = self.bdb.tnyfy_url(url="http://tny.im/yt", shorten=False)
        self.assertEqual(url, "https://www.google.com")

    # def test_browse_by_index(self):
    # self.fail()

    def test_close_quit(self):
        # quitting with no args
        try:
            self.bdb.close_quit()
        except SystemExit as err:
            self.assertEqual(err.args[0], 0)
        # quitting with custom arg
        try:
            self.bdb.close_quit(1)
        except SystemExit as err:
            self.assertEqual(err.args[0], 1)

    # def test_import_bookmark(self):
    # self.fail()


@pytest.fixture(scope="function")
def refreshdb_fixture():
    # Setup
    os.environ["XDG_DATA_HOME"] = TEST_TEMP_DIR_PATH

    # start every test from a clean state
    if exists(TEST_TEMP_DBFILE_PATH):
        os.remove(TEST_TEMP_DBFILE_PATH)

    bdb = BukuDb()

    yield bdb

    # Teardown
    os.environ["XDG_DATA_HOME"] = TEST_TEMP_DIR_PATH


@pytest.mark.parametrize(
    "title_in, exp_res",
    [
        ["?", "Example Domain"],
        [None, "Example Domain"],
        ["", "Example Domain"],
        ["random title", "Example Domain"],
    ],
)
def test_refreshdb(refreshdb_fixture, title_in, exp_res):
    bdb = refreshdb_fixture
    args = ["http://example.com"]
    if title_in:
        args.append(title_in)
    bdb.add_rec(*args)
    bdb.refreshdb(1, 1)
    from_db = bdb.get_rec_by_id(1)
    assert from_db[2] == exp_res, "from_db: {}".format(from_db)


@pytest.fixture
def test_print_caplog(caplog):
    caplog.handler.records.clear()
    caplog.records.clear()
    yield caplog


@pytest.mark.parametrize(
    "kwargs, rec, exp_res",
    [
        [{}, TEST_PRINT_REC, (True, [])],
        [{"is_range": True}, TEST_PRINT_REC, (True, [])],
        [{"index": 0}, TEST_PRINT_REC, (True, [])],
        [{"index": -1}, TEST_PRINT_REC, (True, [])],
        [{"index": -2}, TEST_PRINT_REC, (True, [])],
        [{"index": 2}, TEST_PRINT_REC, (False, [("root", 40, "No matching index 2")])],
        [{"low": -1, "high": -1}, TEST_PRINT_REC, (True, [])],
        [
            {"low": -1, "high": -1, "is_range": True},
            TEST_PRINT_REC,
            (False, [("root", 40, "Negative range boundary")]),
        ],
        [{"low": 0, "high": 0, "is_range": True}, TEST_PRINT_REC, (True, [])],
        [{"low": 0, "high": 1, "is_range": True}, TEST_PRINT_REC, (True, [])],
        [{"low": 0, "high": 2, "is_range": True}, TEST_PRINT_REC, (True, [])],
        [{"low": 2, "high": 2, "is_range": True}, TEST_PRINT_REC, (True, [])],
        [{"low": 2, "high": 3, "is_range": True}, TEST_PRINT_REC, (True, [])],
        # empty database
        [{"is_range": True}, None, (True, [])],
        [{"index": 0}, None, (True, [("root", 40, "0 records")])],
        [{"index": -1}, None, (False, [("root", 40, "Empty database")])],
        [{"index": 1}, None, (False, [("root", 40, "No matching index 1")])],
        [{"low": -1, "high": -1}, TEST_PRINT_REC, (True, [])],
        [
            {"low": -1, "high": -1, "is_range": True},
            None,
            (False, [("root", 40, "Negative range boundary")]),
        ],
        [{"low": 0, "high": 0, "is_range": True}, None, (True, [])],
        [{"low": 0, "high": 1, "is_range": True}, None, (True, [])],
        [{"low": 0, "high": 2, "is_range": True}, None, (True, [])],
        [{"low": 2, "high": 2, "is_range": True}, None, (True, [])],
        [{"low": 2, "high": 3, "is_range": True}, None, (True, [])],
    ],
)
def test_print_rec(setup, kwargs, rec, exp_res, tmp_path, caplog):
    bdb = BukuDb(dbfile=tmp_path / "tmp.db")
    if rec:
        bdb.add_rec(*rec)
    # run the function
    assert (bdb.print_rec(**kwargs), caplog.record_tuples) == exp_res


def test_list_tags(capsys, setup):
    bdb = BukuDb()

    # adding bookmarks
    bdb.add_rec("http://one.com", "", parse_tags(["cat,ant,bee,1"]), "")
    bdb.add_rec("http://two.com", "", parse_tags(["Cat,Ant,bee,1"]), "")
    bdb.add_rec("http://three.com", "", parse_tags(["Cat,Ant,3,Bee,2"]), "")

    # listing tags, asserting output
    out, err = capsys.readouterr()
    prompt(bdb, None, True, listtags=True)
    out, err = capsys.readouterr()
    exp_out = "     1. 1 (2)\n     2. 2 (1)\n     3. 3 (1)\n     4. ant (3)\n     5. bee (3)\n     6. cat (3)\n\n"
    assert out == exp_out
    assert err == ""


def test_compactdb(setup):
    bdb = BukuDb()

    # adding bookmarks
    for bookmark in TEST_BOOKMARKS:
        bdb.add_rec(*bookmark)

    # manually deleting 2nd index from db, calling compactdb
    bdb.cur.execute("DELETE FROM bookmarks WHERE id = ?", (2,))
    bdb.compactdb(2)

    # asserting bookmarks have correct indices
    assert bdb.get_rec_by_id(1) == (
        1,
        "http://slashdot.org",
        "SLASHDOT",
        ",news,old,",
        "News for old nerds, stuff that doesn't matter",
        0,
    )
    assert bdb.get_rec_by_id(2) == (
        2,
        "http://example.com/",
        "test",
        ",es,est,tes,test,",
        "a case for replace_tag test",
        0,
    )
    assert bdb.get_rec_by_id(3) is None


@pytest.mark.vcr()
@pytest.mark.parametrize(
    "low, high, delay_commit, input_retval, exp_res",
    [
        #  delay_commit, y input_retval
        [0, 0, True, "y", (True, [])],
        #  delay_commit, non-y input_retval
        [
            0,
            0,
            True,
            "x",
            (
                False,
                [tuple([x] + y + [0]) for x, y in zip(range(1, 4), TEST_BOOKMARKS)],
            ),
        ],
        #  non delay_commit, y input_retval
        [0, 0, False, "y", (True, [])],
        #  non delay_commit, non-y input_retval
        [
            0,
            0,
            False,
            "x",
            (
                False,
                [tuple([x] + y + [0]) for x, y in zip(range(1, 4), TEST_BOOKMARKS)],
            ),
        ],
    ],
)
def test_delete_rec_range_and_delay_commit(
    setup, tmp_path, low, high, delay_commit, input_retval, exp_res
):
    """test delete rec, range and delay commit."""
    bdb = BukuDb(dbfile=tmp_path / "tmp.db")
    kwargs = {"is_range": True, "low": low, "high": high, "delay_commit": delay_commit}
    kwargs["index"] = 0

    # Fill bookmark
    for bookmark in TEST_BOOKMARKS:
        bdb.add_rec(*bookmark)

    with mock.patch("builtins.input", return_value=input_retval):
        res = bdb.delete_rec(**kwargs)

    assert (res, bdb.get_rec_all()) == exp_res

    # teardown
    os.environ["XDG_DATA_HOME"] = TEST_TEMP_DIR_PATH


@pytest.mark.parametrize(
    "index, delay_commit, input_retval",
    [
        [-1, False, False],
        [0, False, False],
        [1, False, True],
        [1, False, False],
        [1, True, True],
        [1, True, False],
        [100, False, True],
    ],
)
def test_delete_rec_index_and_delay_commit(index, delay_commit, input_retval):
    """test delete rec, index and delay commit."""
    bdb = BukuDb()
    bdb_dc = BukuDb()  # instance for delay_commit check.

    # Fill bookmark
    for bookmark in TEST_BOOKMARKS:
        bdb.add_rec(*bookmark)
    db_len = len(TEST_BOOKMARKS)

    n_index = index

    with mock.patch("builtins.input", return_value=input_retval):
        res = bdb.delete_rec(index=index, delay_commit=delay_commit)

    if n_index < 0:
        assert not res
    elif n_index > db_len:
        assert not res
        assert len(bdb.get_rec_all()) == db_len
    elif index == 0 and input_retval != "y":
        assert not res
        assert len(bdb.get_rec_all()) == db_len
    else:
        assert res
        assert len(bdb.get_rec_all()) == db_len - 1
        if delay_commit:
            assert len(bdb_dc.get_rec_all()) == db_len
        else:
            assert len(bdb_dc.get_rec_all()) == db_len - 1

    # teardown
    os.environ["XDG_DATA_HOME"] = TEST_TEMP_DIR_PATH


@pytest.mark.parametrize(
    "index, is_range, low, high",
    [
        # range on non zero index
        (0, True, 1, 1),
        # range on zero index
        (0, True, 0, 0),
        # zero index only
        (0, False, 0, 0),
    ],
)
def test_delete_rec_on_empty_database(setup, index, is_range, low, high):
    """test delete rec, on empty database."""
    bdb = BukuDb()
    with mock.patch("builtins.input", return_value="y"):
        res = bdb.delete_rec(index, is_range, low, high)

    if (is_range and any([low == 0, high == 0])) or (not is_range and index == 0):
        assert res
        # teardown
        os.environ["XDG_DATA_HOME"] = TEST_TEMP_DIR_PATH
        return

    if is_range and low > 1 and high > 1:
        assert not res

    # teardown
    os.environ["XDG_DATA_HOME"] = TEST_TEMP_DIR_PATH


@pytest.mark.parametrize(
    "kwargs, exp_res, raise_error",
    [
        [dict(index="a", low="a", high=1, is_range=True), None, True],
        [dict(index="a", low="a", high=1, is_range=False), None, True],
        [dict(index="a", low=1, high="a", is_range=True), None, True],
        [dict(index="a", is_range=False), None, True],
        [dict(index="a", is_range=True), None, True],
    ],
)
def test_delete_rec_on_non_integer(
    setup, tmp_path, monkeypatch, kwargs, exp_res, raise_error
):
    """test delete rec on non integer arg."""
    import buku

    bdb = BukuDb(dbfile=tmp_path / "tmp.db")

    for bookmark in TEST_BOOKMARKS:
        bdb.add_rec(*bookmark)

    def mockreturn():
        return "y"

    exp_res = None
    res = None
    monkeypatch.setattr(buku, "read_in", mockreturn)
    if raise_error:
        with pytest.raises(TypeError):
            res = bdb.delete_rec(**kwargs)
    else:
        res = bdb.delete_rec(**kwargs)
    assert res == exp_res


@pytest.mark.parametrize("url", ["", False, None, 0])
def test_add_rec_add_invalid_url(caplog, url):
    """test method."""
    bdb = BukuDb()
    res = bdb.add_rec(url=url)
    assert res is None
    caplog.records[0].levelname == "ERROR"
    caplog.records[0].getMessage() == "Invalid URL"


@pytest.mark.parametrize(
    "kwargs, exp_arg",
    [
        [{"url": "example.com"}, ("example.com", "Example Domain", ",", "", False)],
        [
            {"url": "http://example.com"},
            ("http://example.com", "Example Domain", ",", "", False),
        ],
        [
            {"url": "http://example.com", "immutable": True},
            ("http://example.com", "Example Domain", ",", "", True),
        ],
        [
            {"url": "http://example.com", "desc": "randomdesc"},
            ("http://example.com", "Example Domain", ",", "randomdesc", False),
        ],
        [
            {"url": "http://example.com", "title_in": "randomtitle"},
            ("http://example.com", "randomtitle", ",", "", False),
        ],
        [
            {"url": "http://example.com", "tags_in": "tag1"},
            ("http://example.com", "Example Domain", ",tag1,", "", False),
        ],
        [
            {"url": "http://example.com", "tags_in": ",tag1"},
            ("http://example.com", "Example Domain", ",tag1,", "", False),
        ],
        [
            {"url": "http://example.com", "tags_in": ",tag1,"},
            ("http://example.com", "Example Domain", ",tag1,", "", False),
        ],
    ],
)
def test_add_rec_exec_arg(kwargs, exp_arg):
    """test func."""
    bdb = BukuDb()
    bdb.cur = mock.Mock()
    bdb.get_rec_id = mock.Mock(return_value=None)
    bdb.add_rec(**kwargs)
    assert bdb.cur.execute.call_args[0][1] == exp_arg


def test_update_rec_index_0(caplog):
    """test method."""
    bdb = BukuDb()
    res = bdb.update_rec(index=0, url="http://example.com")
    assert not res
    assert caplog.records[0].getMessage() == "All URLs cannot be same"
    assert caplog.records[0].levelname == "ERROR"


@pytest.mark.parametrize(
    "kwargs, exp_res",
    [
        [dict(index=1), False],
        [dict(index=1, url="url"), False],
        [dict(index=1, url=""), False],
    ],
)
def test_update_rec(tmp_path, kwargs, exp_res):
    bdb = BukuDb(tmp_path / "tmp.db")
    res = bdb.update_rec(**kwargs)
    assert res == exp_res


@pytest.mark.parametrize("invalid_tag", ["+,", "-,"])
def test_update_rec_invalid_tag(caplog, invalid_tag):
    """test method."""
    url = "http://example.com"
    bdb = BukuDb()
    res = bdb.update_rec(index=1, url=url, tags_in=invalid_tag)
    assert not res
    try:
        assert caplog.records[0].getMessage() == "Please specify a tag"
        assert caplog.records[0].levelname == "ERROR"
    except IndexError as e:
        if (sys.version_info.major, sys.version_info.minor) == (3, 4):
            print("caplog records: {}".format(caplog.records))
            for idx, record in enumerate(caplog.records):
                print(
                    "idx:{};{};message:{};levelname:{}".format(
                        idx, record, record.getMessage(), record.levelname
                    )
                )
        else:
            raise e


@pytest.mark.parametrize(
    "read_in_retval, exp_res, record_tuples",
    [
        ["y", False, [("root", 40, "No matching index 0")]],
        ["n", False, []],
        ["", False, []],
    ],
)
def test_update_rec_update_all_bookmark(
    caplog, tmp_path, setup, read_in_retval, exp_res, record_tuples
):
    """test method."""
    with mock.patch("buku.read_in", return_value=read_in_retval):
        bdb = BukuDb(tmp_path / "tmp.db")
        res = bdb.update_rec(index=0, tags_in="tags1")
        assert (res, caplog.record_tuples) == (exp_res, record_tuples)


@pytest.mark.parametrize(
    "get_system_editor_retval, index, exp_res",
    [
        ["none", 0, False],
        ["nano", -2, False],
    ],
)
def test_edit_update_rec_with_invalid_input(get_system_editor_retval, index, exp_res):
    """test method."""
    with mock.patch("buku.get_system_editor", return_value=get_system_editor_retval):
        import buku

        bdb = buku.BukuDb()
        res = bdb.edit_update_rec(index=index)
        assert res == exp_res


@pytest.mark.vcr("tests/vcr_cassettes/test_browse_by_index.yaml")
@given(
    low=st.integers(min_value=-2, max_value=3),
    high=st.integers(min_value=-2, max_value=3),
    index=st.integers(min_value=-2, max_value=3),
    is_range=st.booleans(),
    empty_database=st.booleans(),
)
@example(low=0, high=0, index=0, is_range=False, empty_database=True)
@settings(max_examples=2, deadline=None)
def test_browse_by_index(low, high, index, is_range, empty_database):
    """test method."""
    n_low, n_high = (high, low) if low > high else (low, high)
    with mock.patch("buku.browse"):
        import buku

        bdb = buku.BukuDb()
        bdb.delete_rec_all()
        db_len = 0
        if not empty_database:
            bdb.add_rec("https://www.google.com/ncr", "?")
            db_len += 1
        res = bdb.browse_by_index(index=index, low=low, high=high, is_range=is_range)
        if is_range and (low < 0 or high < 0):
            assert not res
        elif is_range and n_low > 0 and n_high > 0:
            assert res
        elif is_range:
            assert not res
        elif not is_range and index < 0:
            assert not res
        elif not is_range and index > db_len:
            assert not res
        elif not is_range and index >= 0 and empty_database:
            assert not res
        elif not is_range and 0 <= index <= db_len and not empty_database:
            assert res
        else:
            raise ValueError
        bdb.delete_rec_all()


@pytest.fixture()
def chrome_db():
    # compatibility
    dir_path = os.path.dirname(os.path.realpath(__file__))
    res_yaml_file = os.path.join(dir_path, "test_bukuDb", "25491522_res.yaml")
    res_nopt_yaml_file = os.path.join(dir_path, "test_bukuDb", "25491522_res_nopt.yaml")
    json_file = os.path.join(dir_path, "test_bukuDb", "Bookmarks")
    return json_file, res_yaml_file, res_nopt_yaml_file


@pytest.mark.parametrize("add_pt", [True, False])
def test_load_chrome_database(chrome_db, add_pt):
    """test method."""
    # compatibility
    json_file = chrome_db[0]
    res_yaml_file = chrome_db[1] if add_pt else chrome_db[2]
    dump_data = False  # NOTE: change this value to dump data
    if not dump_data:
        with open(res_yaml_file, "r", encoding="utf8", errors="surrogateescape") as f:
            try:
                res_yaml = yaml.load(f, Loader=yaml.FullLoader)
            except RuntimeError:
                res_yaml = yaml.load(f, Loader=PrettySafeLoader)
    # init
    import buku

    bdb = buku.BukuDb()
    bdb.add_rec = mock.Mock()
    bdb.load_chrome_database(json_file, None, add_pt)
    call_args_list_dict = dict(bdb.add_rec.call_args_list)
    # test
    if not dump_data:
        assert call_args_list_dict == res_yaml
    # dump data for new test
    if dump_data:
        with open(res_yaml_file, "w", encoding="utf8", errors="surrogateescape") as f:
            yaml.dump(call_args_list_dict, f)
        print("call args list dict dumped to:{}".format(res_yaml_file))


@pytest.fixture()
def firefox_db(tmpdir):
    zip_url = "https://github.com/jarun/buku/files/1319933/bookmarks.zip"
    dir_path = os.path.dirname(os.path.realpath(__file__))
    res_yaml_file = os.path.join(dir_path, "test_bukuDb", "firefox_res.yaml")
    res_nopt_yaml_file = os.path.join(dir_path, "test_bukuDb", "firefox_res_nopt.yaml")
    ff_db_path = os.path.join(dir_path, "test_bukuDb", "places.sqlite")
    if not os.path.isfile(ff_db_path):
        tmp_zip = tmpdir.join("bookmarks.zip")
        with urllib.request.urlopen(zip_url) as response, open(
            tmp_zip.strpath, "wb"
        ) as out_file:
            shutil.copyfileobj(response, out_file)
        with zipfile.ZipFile(tmp_zip.strpath) as zip_obj:
            zip_obj.extractall(path=os.path.join(dir_path, "test_bukuDb"))
    return ff_db_path, res_yaml_file, res_nopt_yaml_file


@pytest.mark.parametrize("add_pt", [True, False])
def test_load_firefox_database(firefox_db, add_pt):
    # compatibility
    ff_db_path = firefox_db[0]
    dump_data = False  # NOTE: change this value to dump data
    res_yaml_file = firefox_db[1] if add_pt else firefox_db[2]
    if not dump_data:
        with open(res_yaml_file, "r", encoding="utf8", errors="surrogateescape") as f:
            res_yaml = yaml.load(f, Loader=PrettySafeLoader)
    # init
    import buku

    bdb = buku.BukuDb()
    bdb.add_rec = mock.Mock()
    bdb.load_firefox_database(ff_db_path, None, add_pt)
    call_args_list_dict = dict(bdb.add_rec.call_args_list)
    # test
    if not dump_data:
        assert call_args_list_dict == res_yaml
    if dump_data:
        with open(res_yaml_file, "w", encoding="utf8", errors="surrogateescape") as f:
            yaml.dump(call_args_list_dict, f)
        print("call args list dict dumped to:{}".format(res_yaml_file))


@pytest.mark.parametrize(
    "keyword_results, stag_results, exp_res",
    [
        ([], [], []),
        (["item1"], ["item1", "item2"], ["item1"]),
        (["item2"], ["item1"], []),
    ],
)
def test_search_keywords_and_filter_by_tags(keyword_results, stag_results, exp_res):
    """test method."""
    # init
    import buku

    bdb = buku.BukuDb()
    bdb.searchdb = mock.Mock(return_value=keyword_results)
    bdb.search_by_tag = mock.Mock(return_value=stag_results)
    # test
    res = bdb.search_keywords_and_filter_by_tags(
        mock.Mock(), mock.Mock(), mock.Mock(), mock.Mock(), []
    )
    assert exp_res == res


@pytest.mark.parametrize(
    "search_results, exclude_results, exp_res",
    [
        ([], [], []),
        (["item1", "item2"], ["item2"], ["item1"]),
        (["item2"], ["item1"], ["item2"]),
        (["item1", "item2"], ["item1", "item2"], []),
    ],
)
def test_exclude_results_from_search(search_results, exclude_results, exp_res):
    """test method."""
    # init
    import buku

    bdb = buku.BukuDb()
    bdb.searchdb = mock.Mock(return_value=exclude_results)
    # test
    res = bdb.exclude_results_from_search(search_results, [], True)
    assert exp_res == res


def test_exportdb_empty_db():
    with NamedTemporaryFile(delete=False) as f:
        db = BukuDb(dbfile=f.name)
        with NamedTemporaryFile(delete=False) as f2:
            res = db.exportdb(f2.name)
            assert not res


def test_exportdb_single_rec(tmpdir):
    with NamedTemporaryFile(delete=False) as f:
        db = BukuDb(dbfile=f.name)
        db.add_rec("http://example.com")
        exp_file = tmpdir.join("export")
        db.exportdb(exp_file.strpath)
        with open(exp_file.strpath, encoding="utf8", errors="surrogateescape") as f:
            assert f.read()


def test_exportdb_to_db():
    with NamedTemporaryFile(delete=False) as f1, NamedTemporaryFile(
        delete=False, suffix=".db"
    ) as f2:
        db = BukuDb(dbfile=f1.name)
        db.add_rec("http://example.com")
        db.add_rec("http://google.com")
        with mock.patch("builtins.input", return_value="y"):
            db.exportdb(f2.name)
        db2 = BukuDb(dbfile=f2.name)
        assert db.get_rec_all() == db2.get_rec_all()


@pytest.mark.parametrize(
    "urls, exp_res",
    [
        [[], None],
        [["http://example.com"], 1],
        [["htttp://example.com", "http://google.com"], 2],
    ],
)
def test_get_max_id(urls, exp_res):
    with NamedTemporaryFile(delete=False) as f:
        db = BukuDb(dbfile=f.name)
        if urls:
            list(map(lambda x: db.add_rec(x), urls))
        assert db.get_max_id() == exp_res


# Helper functions for testcases


def split_and_test_membership(a, b):
    # :param a, b: comma separated strings to split
    # test everything in a in b
    return all(x in b.split(",") for x in a.split(","))


def inclusive_range(start, end):
    return list(range(start, end + 1))


def normalize_range(db_len, low, high):
    """normalize index and range.

    Args:
        db_len (int): database length.
        low (int): low limit.
        high (int): high limit.

    Returns:
        Tuple contain following normalized variables (low, high)
    """
    require_comparison = True
    # don't deal with non instance of the variable.
    if not isinstance(low, int):
        n_low = low
        require_comparison = False
    if not isinstance(high, int):
        n_high = high
        require_comparison = False

    max_value = db_len
    if low == "max" and high == "max":
        n_low = db_len
        n_high = max_value
    elif low == "max" and high != "max":
        n_low = high
        n_high = max_value
    elif low != "max" and high == "max":
        n_low = low
        n_high = max_value
    else:
        n_low = low
        n_high = high

    if require_comparison:
        if n_high < n_low:
            n_high, n_low = n_low, n_high

    return (n_low, n_high)


if __name__ == "__main__":
    unittest.main()
