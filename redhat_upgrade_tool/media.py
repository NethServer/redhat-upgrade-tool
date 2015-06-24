# media.py - check for installable media, mount/umount devices, etc.
#
# Copyright (C) 2012 Red Hat Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# Author: Will Woods <wwoods@redhat.com>

import os, stat, re
from collections import namedtuple
from os.path import exists, join, realpath
from .util import check_output, call, STDOUT, CalledProcessError
from tempfile import mkdtemp

import logging
log = logging.getLogger(__package__+".media")

def check_call(cmd, stderr=STDOUT, *args, **kwargs):
    # Use stderr=STDOUT so CalledProcessError has mount output in e.output
    check_output(cmd, stderr=stderr, *args, **kwargs)

class FstabEntry(namedtuple('FstabEntry','dev rawmnt type opts freq passno')):
    __slots__ = ()
    @property
    def mnt(self):
        '''unescaped mountpoint'''
        return self.rawmnt.decode('string_escape')

def mounts(fstab="/proc/mounts"):
    for line in open(fstab):
        if line.strip().startswith('#') or line.isspace():
            continue
        yield FstabEntry(*line.split())

def ismedia(mountpoint):
    return exists(join(mountpoint, ".treeinfo"))

def isblock(dev):
    return exists(dev) and stat.S_ISBLK(os.stat(dev).st_mode)

def iscd(dev):
    typefile = "/sys/class/block/%s/device/type" % os.path.basename(dev)
    return os.path.exists(typefile) and int(open(typefile).read()) == 5

def isiso(filename):
    try:
        iso = open(filename)
        iso.seek(32769)
        magic = iso.read(5)
    except IOError:
        magic = ''
    return magic == 'CD001'

def isloop(dev):
    return exists(dev) and os.major(os.stat(dev).st_rdev) == 7

def find():
    return [m for m in mounts() if isblock(m.dev) and ismedia(m.mnt)]

def removable():
    '''Yield mounted block devices that don't have entries in /etc/fstab'''
    for m in mounts():
        if not isblock(m.dev):
            continue
        if not any(m.mnt == d.mnt for d in mounts("/etc/fstab")):
            yield m

def loopmount(filename, mntpoint=None):
    if mntpoint is None:
        mntpoint = mkdtemp(prefix=__package__+'.mnt.')
    check_call(['mount', '-oloop', filename, mntpoint])
    for m in mounts():
        if m.mnt == mntpoint:
            return m

def fix_loop_entry(mnt, iso):
    '''return new FstabEntry with dev=backing_file and "loop" added to opts'''
    # losetup, due to the limitations of LOOP_GET_STATUS64 and the lack of backing_file
    # in sysfs, truncates the backing file output to < 64 characters. The backing file
    # must come from the caller.

    opts = ','.join([mnt.opts, "loop"])
    return mnt._replace(dev=realpath(iso), opts=opts)

def umount(mntpoint):
    try:
        check_call(['umount', '-d', mntpoint])
    except CalledProcessError:
        log.warn('umount %s failed: %s', mntpoint, e.output)
        log.warn('trying lazy umount')
        call(['umount', '-l', mntpoint])

# see systemd/src/shared/unit-name.c:do_escape()
validchars='0123456789'\
           'abcdefghijklmnopqrstuvwxyz'\
           'ABCDEFGHIJKLMNOPQRSTUVWXYZ'\
           ':-_.\\'

def systemd_escape_char(ch):
    if ch == '/':
        return '-'
    elif ch == '-' or ch == '\\' or ch not in validchars:
        return '\\x%x' % ord(ch)
    else:
        return ch

def systemd_escape(path):
    if path == '/':
        return '-'
    newpath = ''
    path = path.strip('/')
    if path[0] == '.':
        newpath += '\\x2e'
        path = path[1:]
    for ch in path:
        newpath += systemd_escape_char(ch)
    return newpath

def shell_escape(string):
    return "'%s'" % string.replace("'", "'\\''")

def write_prep_mount(mount, unitdir, iso, desc=None, unitopts=""):
    if desc is None:
        desc = "Upgrade Media"
    if isloop(mount.dev):
        mount = fix_loop_entry(mount, iso)
    unit = join(unitdir, systemd_escape(mount.mnt)+'.mount')
    with open(unit, 'w') as u:
        u.write("mount -t %s -o %s %s %s\n" %
                (shell_escape(mount.type),
                 shell_escape(mount.opts),
                 shell_escape(mount.dev),
                 shell_escape(mount.mnt)))
    os.chmod(unit, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    return unit
