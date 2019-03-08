# This file is part of Buildbot.  Buildbot is free software: you can
# redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation, version 2.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Copyright Buildbot Team Members

import os

from twisted.internet import defer
from twisted.trial import unittest

from buildbot.changes import hgpoller
from buildbot.test.util import changesource
from buildbot.test.util import gpo

ENVIRON_2116_KEY = 'TEST_THAT_ENVIRONMENT_GETS_PASSED_TO_SUBPROCESSES'
LINESEP_BYTES = os.linesep.encode("ascii")
PATHSEP_BYTES = os.pathsep.encode("ascii")


class TestHgPoller(gpo.GetProcessOutputMixin,
                   changesource.ChangeSourceMixin,
                   unittest.TestCase):
    usetimestamps = True

    @defer.inlineCallbacks
    def setUp(self):
        # To test that environment variables get propagated to subprocesses
        # (See #2116)
        os.environ[ENVIRON_2116_KEY] = 'TRUE'
        self.setUpGetProcessOutput()
        yield self.setUpChangeSource()
        self.remote_repo = 'ssh://example.com/foo/baz'
        self.repo_ready = True

        def _isRepositoryReady():
            return self.repo_ready

        self.poller = hgpoller.HgPoller(self.remote_repo,
                                        usetimestamps=self.usetimestamps,
                                        workdir='/some/dir')
        self.poller.setServiceParent(self.master)
        self.poller._isRepositoryReady = _isRepositoryReady

        yield self.master.db.setup()

    def tearDown(self):
        del os.environ[ENVIRON_2116_KEY]
        return self.tearDownChangeSource()

    def gpoFullcommandPattern(self, commandName, *expected_args):
        """Match if the command is commandName and arg list start as expected.

        This allows to test a bit more if expected GPO are issued, be it
        by obscure failures due to the result not being given.
        """
        def matchesSubcommand(bin, given_args, **kwargs):
            return bin == commandName and tuple(
                given_args[:len(expected_args)]) == expected_args
        return matchesSubcommand

    def test_describe(self):
        self.assertSubstring("HgPoller", self.poller.describe())

    def test_name(self):
        self.assertEqual(self.remote_repo, self.poller.name)

        # and one with explicit name...
        other = hgpoller.HgPoller(
            self.remote_repo, name="MyName", workdir='/some/dir')
        self.assertEqual("MyName", other.name)

    def test_hgbin_default(self):
        self.assertEqual(self.poller.hgbin, "hg")

    @defer.inlineCallbacks
    def test_poll_initial(self):
        self.repo_ready = False
        # Test that environment variables get propagated to subprocesses
        # (See #2116)
        expected_env = {ENVIRON_2116_KEY: 'TRUE'}
        self.addGetProcessOutputExpectEnv(expected_env)
        self.expectCommands(
            gpo.Expect('hg', 'init', '/some/dir'),
            gpo.Expect('hg', 'pull', '-b', 'default',
                       'ssh://example.com/foo/baz')
            .path('/some/dir'),
            gpo.Expect(
                'hg', 'heads', '-r', 'default', '--template={rev}' + os.linesep)
            .path('/some/dir').stdout(b"73591"),
        )

        # do the poll
        yield self.poller.poll()

        # check the results
        self.assertEqual(len(self.master.data.updates.changesAdded), 0)

        yield self.check_current_rev(73591)

    @defer.inlineCallbacks
    def check_current_rev(self, wished, branch='default'):
        rev = yield self.poller._getCurrentRev(branch)
        self.assertEqual(rev, str(wished))

    @defer.inlineCallbacks
    def test_poll_several_heads(self):
        # If there are several heads on the named branch, the poller mustn't
        # climb (good enough for now, ideally it should even go to the common
        # ancestor)
        self.expectCommands(
            gpo.Expect('hg', 'pull', '-b', 'default',
                       'ssh://example.com/foo/baz')
            .path('/some/dir'),
            gpo.Expect(
                'hg', 'heads', '-r', 'default', '--template={rev}' + os.linesep)
            .path('/some/dir').stdout(b'5' + LINESEP_BYTES + b'6' + LINESEP_BYTES)
        )

        yield self.poller._setCurrentRev(3)

        # do the poll: we must stay at rev 3
        yield self.poller.poll()
        yield self.check_current_rev(3)

    @defer.inlineCallbacks
    def test_poll_regular(self):
        # normal operation. There's a previous revision, we get a new one.
        self.expectCommands(
            gpo.Expect('hg', 'pull', '-b', 'default',
                       'ssh://example.com/foo/baz')
            .path('/some/dir'),
            gpo.Expect(
                'hg', 'heads', '-r', 'default', '--template={rev}' + os.linesep)
            .path('/some/dir').stdout(b'5' + LINESEP_BYTES),
            gpo.Expect('hg', 'log', '-r', '4::5',
                       '--template={rev}:{node}\\n')
            .path('/some/dir').stdout(LINESEP_BYTES.join([
                        b'4:1aaa5',
                        b'5:784bd',
                    ])),
            gpo.Expect('hg', 'log', '-r', '784bd',
                       '--template={date|hgdate}' + os.linesep +
                       '{author}' + os.linesep +
                       "{files % '{file}" +
                       os.pathsep + "'}" +
                       os.linesep + '{desc|strip}')
            .path('/some/dir').stdout(LINESEP_BYTES.join([
                b'1273258009.0 -7200',
                b'Joe Test <joetest@example.org>',
                b'file1 file2',
                b'Comment for rev 5',
                b''])),
        )

        yield self.poller._setCurrentRev(4)

        yield self.poller.poll()
        yield self.check_current_rev(5)

        self.assertEqual(len(self.master.data.updates.changesAdded), 1)
        change = self.master.data.updates.changesAdded[0]
        self.assertEqual(change['revision'], '784bd')
        self.assertEqual(change['comments'], 'Comment for rev 5')


class HgPollerNoTimestamp(TestHgPoller):
    """ Test HgPoller() without parsing revision commit timestamp """

    usetimestamps = False
