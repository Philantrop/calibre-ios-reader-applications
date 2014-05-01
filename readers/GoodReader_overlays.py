#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import (unicode_literals, division, absolute_import,
                        print_function)

import base64, cStringIO, os, posixpath, sqlite3, subprocess, time
from datetime import datetime

from calibre.constants import islinux, isosx, iswindows
from calibre.devices.usbms.books import BookList
from calibre.utils.zipfile import ZipFile

from calibre_plugins.ios_reader_apps import Book, iOSReaderApp

if True:
    '''
    Overlay methods for GoodReader driver

    *** NB: Do not overlay open() ***
    '''
    def _initialize_overlay(self):
        '''
        General initialization that would have occurred in __init__()
        '''
        from calibre.ptempfile import PersistentTemporaryDirectory

        self._log_location(self.ios_reader_app)

        # ~~~~~~~~~ Calibre constants ~~~~~~~~~
        # None indicates that the driver supports backloading from device to library
        self.BACKLOADING_ERROR_MESSAGE = None
        self.COVER_WIDTH = 180
        self.COVER_HEIGHT = 270


        # Plugboards
        self.CAN_DO_DEVICE_DB_PLUGBOARD = False
        self.DEVICE_PLUGBOARD_NAME = 'GOODREADER'

        # Which metadata on books can be set via the GUI.
        self.CAN_SET_METADATA = ['title', 'authors']

        # ~~~~~~~~~ Variables ~~~~~~~~~
        self.busy = False
        self.documents_folder = b'/Documents'
        self.format_map = ['pdf']
        self.ios_connection = {
            'app_installed': False,
            'connected': False,
            'device_name': None,
            'ejected': False,
            'udid': 0
            }
        self.path_template = '{0}.pdf'
        self.local_metadata = None
        self.remote_metadata = '/Library/calibre_metadata.sqlite'


    def add_books_to_metadata(self, locations, metadata, booklists):
        '''
        Add locations to the booklists. This function must not communicate with
        the device.
        @param locations: Result of a call to L{upload_books}
        @param metadata: List of MetaInformation objects, same as for
        :method:`upload_books`.
        @param booklists: A tuple containing the result of calls to
                                (L{books}(oncard=None), L{books}(oncard='carda'),
                                L{books}(oncard='cardb')).
        '''
        self._log_location()
        for new_book in locations[0]:
            booklists[0].append(new_book)

    def books(self, oncard=None, end_session=True):
        '''
        Return a list of ebooks on the device.
        @param oncard:  If 'carda' or 'cardb' return a list of ebooks on the
                        specific storage card, otherwise return list of ebooks
                        in main memory of device. If a card is specified and no
                        books are on the card return empty list.
        @return: A BookList.

        '''
        from calibre.ebooks.metadata import authors_to_string

        # Entry point
        booklist = BookList(oncard, None, None)
        if not oncard:
            self._log_location()
            start_time = time.time()
            cached_books = {}

            # Get a local copy of metadata db. If it doesn't exist on device, create it
            db_profile = self._localize_database_path(self.remote_metadata)
            self.local_metadata = db_profile['path']
            con = sqlite3.connect(self.local_metadata)
            with con:
                con.row_factory = sqlite3.Row
                cur = con.cursor()

                # Get the last saved set of installed filenames from the db
                cur.execute('''SELECT
                                filename
                               FROM metadata
                            ''')
                rows = cur.fetchall()
                cached_books = [row[b'filename'] for row in rows]
                #cached_books = [self._quote_sqlite_identifier(row[b'filename']) for row in rows]
                if self.prefs.get('development_mode', False):
                    self._log("~~~ cached_books: ~~~")
                    for b in sorted(cached_books):
                        self._log("%s %s" % (b, repr(b)))

                # Get the currently installed filenames from the documents folder
                installed_books = self._get_nested_folder_contents(self.documents_folder)
                if self.prefs.get('development_mode', False):
                    self._log("~~~ installed_books: ~~~")
                    for b in sorted(installed_books):
                        self._log("%s %s" % (b, repr(b)))

                moved_books = []
                for i, book in enumerate(installed_books):
                    book_moved = False
                    if book in cached_books:
                        # Retrieve the cached metadata
                        this_book = self._get_cached_metadata(cur, book)
                        booklist.add_book(this_book, False)
                    else:
                        # Check to see if a known book has been moved
                        for cb in cached_books:
                            if cb.rpartition('/')[2] == book.rpartition('/')[2]:
                                # Retrieve the cached metadata with the new location
                                self._log("%s moved to %s" % (repr(cb), repr(book)))
                                this_book = self._get_cached_metadata(cur, cb)
                                this_book.path = book
                                booklist.add_book(this_book, False)
                                # Update metadata with new location
                                cur.execute('''
                                            UPDATE metadata
                                            SET filename = {0}
                                            WHERE filename = {1}
                                            '''.format(self._quote_sqlite_identifier(book),
                                                       self._quote_sqlite_identifier(cb)))
                                con.commit()
                                book_moved = True
                                moved_books.append(cb)
                                break
                        if book_moved:
                            continue

                        # Make a local copy of the book, get the stats
                        remote_path = '/'.join([self.documents_folder, book])
                        stats = self.ios.stat(remote_path)
                        local_path = self._localize_pdf('/'.join([self.documents_folder, book]))
                        pdf_stats = {'path': local_path, 'stats': stats}
                        try:
                            this_book = self._get_metadata(book, pdf_stats)
                            os.remove(local_path)
                        except:
                            import traceback
                            traceback.print_exc()
                            self._log("ERROR reading metadata from %s" % book)
                            os.remove(local_path)
                            continue

                        booklist.add_book(this_book, False)
                        cached_books.append(book)
                        # Add to calibre_metadata db
                        cur.execute('''
                                        INSERT OR REPLACE INTO metadata
                                         (authors,
                                          author_sort,
                                          dateadded,
                                          filename,
                                          size,
                                          thumb_data,
                                          title,
                                          title_sort,
                                          uuid)
                                        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                                        (unicode(' & '.join(this_book.authors)),
                                         unicode(this_book.author_sort),
                                         this_book.dateadded,
                                         this_book.path,
                                         this_book.size,
                                         this_book.thumb_data,
                                         unicode(this_book.title),
                                         unicode(this_book.title_sort),
                                         this_book.uuid)
                                        )
                    if self.report_progress is not None:
                        self.report_progress(float((i + 1)*100 / len(installed_books))/100,
                            '%(num)d of %(tot)d' % dict(num=i + 1, tot=len(installed_books)))

                # Remove orphans (books no longer in GoodReader) from db
                ib = set(installed_books)
                mb = set(moved_books)
                orphans = [x for x in cached_books if x not in ib and x not in mb]

                if orphans:
                    for book in orphans:
                        # Remove from db, update device copy
                        self._log("Removing orphan %s from metadata" % self._quote_sqlite_identifier(book))
                        cur.execute('''DELETE FROM metadata
                                       WHERE filename = {0}
                                    '''.format(self._quote_sqlite_identifier(book)))
                    con.execute('''VACUUM''')
                cur.close()
                con.commit()

                # Copy the updated db to the iDevice
                self._log("updating remote_metadata")
                self.ios.copy_to_idevice(str(self.local_metadata), str(self.remote_metadata))

            if self.report_progress is not None:
                self.report_progress(1.0, 'finished')

            self.cached_books = cached_books

            metrics = {'book_count': len(booklist),
                       'load_time': time.time() - start_time}
            self._log_metrics(metrics=metrics)

        return booklist

    def can_handle(self, device_info, debug=False):
        '''
        OSX/linux version of :method:`can_handle_windows`

        :param device_info: Is a tuple of (vid, pid, bcd, manufacturer, product,
        serial number)

        This gets called ~1x/second while device fingerprint is sensed

        libiMobileDevice instantiated in initialize()
        self.connected_path is path to Documents/calibre/connected.xml
        self.ios_connection {'udid': <udid>, 'app_installed': True|False, 'connected': True|False}

        iDevice disconnected:
            self.ios_connection: udid:<device>, ejected:False, device_name:<name>,
                                 connected:False, app_installed:True
        iDevice connected:
            self.ios_connection: udid:<device>, ejected:False, device_name:<name>,
                                 connected:True, app_installed:True
        iDevice ejected:
            self.ios_connection: udid:<device>, ejected:True, device_name:<name>,
                                 connected:True, app_installed:True

        '''
        def _show_current_connection():
            return("connected:{0:1} ejected:{1:1} app_installed:{2:1}".format(
                self.ios_connection['connected'],
                self.ejected,
                self.ios_connection['app_installed'])
                )

        # ~~~ Entry point ~~~

        DEBUG_CAN_HANDLE = False

        if DEBUG_CAN_HANDLE:
            self._log_location(_show_current_connection())

        # Set a flag so eject doesn't interrupt communication with iDevice
        self.busy = True

        # 0: If we've already discovered a connected device without GoodReader, exit
        if self.ios_connection['udid'] and self.ios_connection['app_installed'] is False:
            if DEBUG_CAN_HANDLE:
                self._log("self.ios_connection['udid']: %s" % self.ios_connection['udid'])
                self._log("self.ios_connection['app_installed']: %s" % self.ios_connection['app_installed'])
                self._log("0: returning %s" % self.ios_connection['app_installed'])
            self.busy = False
            return self.ios_connection['app_installed']

        # 0. If user ejected, exit
        if self.ios_connection['udid'] and self.ejected is True:
            if DEBUG_CAN_HANDLE:
                self._log("'%s' ejected" % self.ios_connection['device_name'])
            self.busy = False
            return False

        # 1: Is there a (single) connected iDevice?
        if False and DEBUG_CAN_HANDLE:
            self._log("1. self.ios_connection: %s" % _show_current_connection())

        connected_ios_devices = self.ios.get_device_list()

        if len(connected_ios_devices) == 1:
            '''
            If we have an existing USB connection, determine state
             Three possible outcomes:
              a) connected.xml exists (<state> = 'online')
              b) connected.xml exists (<state> = 'offline')
              c) connected.xml does not exist (User not in connection mode)
            '''
            """
            if self.ios_connection['connected']:
                connection_live = False
                if self.ios.exists(self.connected_fs):
                    # Parse the connection data for state
                    connection = etree.fromstring(self.ios.read(self.connected_fs))
                    connection_state = connection.find('state').text
                    if connection_state == 'online':
                        connection_live = True
                        if DEBUG_CAN_HANDLE:
                            self._log("1a. <state> = online")
                    else:
                        connection_live = False
                        if DEBUG_CAN_HANDLE:
                            self._log("1b. <state> = offline")

                    # Show the connection initiation time
                    self.connection_timestamp = float(connection.get('timestamp'))
                    d = datetime.fromtimestamp(self.connection_timestamp)
                    if DEBUG_CAN_HANDLE:
                        self._log("   connection last refreshed %s" % (d.strftime('%Y-%m-%d %H:%M:%S')))

                else:
                    if DEBUG_CAN_HANDLE:
                        self._log("1c. user exited connection mode")

                if not connection_live:
                    # Lost the connection, reset
                    #self._reset_ios_connection(udid=connected_ios_devices[0])
                    self.ios_connection['connected'] = False

                if DEBUG_CAN_HANDLE:
                    self._log("1d: returning %s" % connection_live)
                self.busy = False
                return connection_live

            elif self.ios_connection['udid'] != connected_ios_devices[0]:
                self._reset_ios_connection(udid=connected_ios_devices[0], verbose=DEBUG_CAN_HANDLE)
            """

            # 2. Is GoodReader installed on this iDevice?
            if not self.ios_connection['app_installed']:
                if DEBUG_CAN_HANDLE:
                    self._log("2. GoodReader installed, attempting connection")
                self.ios_connection['app_installed'] = self.ios.mount_ios_app(app_id=self.app_id)
                self.ios_connection['device_name'] = self.ios.device_name
                if DEBUG_CAN_HANDLE:
                    self._log("2a. self.ios_connection: %s" % _show_current_connection())

                # If no GoodReader, we can't handle, so exit
                if not self.ios_connection['app_installed']:
                    if DEBUG_CAN_HANDLE:
                        self._log("2. GoodReader not installed")
                    self.busy = False
                    return self.ios_connection['app_installed']

            # 3. Check to see if connected.xml exists in staging folder
            if DEBUG_CAN_HANDLE:
                self._log("3. Looking for calibre connection mode")

            connection_live = True
            self.ios_connection['connected'] = connection_live

        elif len(connected_ios_devices) == 0:
            self._log_location("no connected devices")
            self._reset_ios_connection()
            self.ios.disconnect_idevice()

        elif len(connected_ios_devices) > 1:
            self._log_location()
            self._log("%d iDevices detected. Driver supports a single connected iDevice." %
                                len(connected_ios_devices))
            self._reset_ios_connection()
            self.ios.disconnect_idevice()

        # 4. show connection
        if DEBUG_CAN_HANDLE:
            self._log("4. self.ios_connection: %s" % _show_current_connection())

        self.busy = False
        return self.ios_connection['connected']

    def can_handle_windows(self, device_info, debug=False):
        '''
        See comments in can_handle()
        '''

        #self._log_location()
        result = self.can_handle(device_info, debug)
        #self._log_location("returning %s from can_handle()" % repr(result))
        return result

    def delete_books(self, paths, end_session=True):
        '''
        Delete books at paths on device.
        '''
        self._log_location()
        if self.prefs.get('development_mode', False):
            self._log("cached_books: %s" % self.cached_books)

        file_count = float(len(paths))

        for i, path in enumerate(paths):
            self._log("removing %s" % repr(path))
            ios_path = '/'.join([self.documents_folder, path])
            self.ios.remove(ios_path)

        # Update the db
        con = sqlite3.connect(self.local_metadata)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        with con:
            for book in paths:
                # Remove from db, update device copy
                self._log("Removing %s from local_metadata" % self._quote_sqlite_identifier(book))
                cur.execute('''DELETE FROM metadata
                               WHERE filename = {0}
                            '''.format(self._quote_sqlite_identifier(book)))
            con.execute('''VACUUM''')
            con.commit()

        # Copy the updated db to the iDevice
        self._log("updating remote_metadata")
        self.ios.copy_to_idevice(str(self.local_metadata), str(self.remote_metadata))

    def eject(self):
        '''
        Unmount/eject the device
        post_yank_cleanup() handles the dismount
        '''
        self._log_location()

        # If busy in critical IO operation, wait for completion before returning
        while self.busy:
            time.sleep(0.10)
        self.ejected = True

    def get_file(self, path, outfile, end_session=True):
        '''
        Read the file at path on the device and write it to provided outfile.

        outfile: file object (result of an open() call)
        '''
        self._log_location()
        self.ios.copy_from_idevice('/'.join([self.documents_folder, path]), outfile)

    def is_usb_connected(self, devices_on_system, debug=False, only_presence=False):
        '''
        Return (True, device_info) if a device handled by this plugin is currently connected,
        else (False, None)
        '''
        if iswindows:
            return self.is_usb_connected_windows(devices_on_system,
                    debug=debug, only_presence=only_presence)


        # >>> Entry point
        #self._log_location(self.ios_connection)

        # If we were ejected, test to see if we're still physically connected
        if self.ejected:
            for dev in devices_on_system:
                if isosx:
                    # dev: (1452L, 4779L, 592L, u'Apple Inc.', u'iPad', u'<udid>')
                    if self.ios_connection['udid'] == dev[5]:
                        self._log_location("iDevice physically connected, but ejected")
                        break
                elif islinux:
                    '''
                    dev: USBDevice(busnum=1, devnum=17, vendor_id=0x05ac, product_id=0x12ab,
                                   bcd=0x0250, manufacturer=Apple Inc., product=iPad,
                                   serial=<udid>)
                    '''
                    if self.ios_connection['udid'] == dev.serial:
                        self._log_location("iDevice physically connected, but ejected")
                        break

            else:
                self._log_location("iDevice physically disconnected, resetting ios_connection")
                self._reset_ios_connection()
                self.ejected = False
            return False, None

        vendors_on_system = set([x[0] for x in devices_on_system])
        vendors = self.VENDOR_ID if hasattr(self.VENDOR_ID, '__len__') else [self.VENDOR_ID]
        if hasattr(self.VENDOR_ID, 'keys'):
            products = []
            for ven in self.VENDOR_ID:
                products.extend(self.VENDOR_ID[ven].keys())
        else:
            products = self.PRODUCT_ID if hasattr(self.PRODUCT_ID, '__len__') else [self.PRODUCT_ID]

        for vid in vendors:
            if vid in vendors_on_system:
                for dev in devices_on_system:
                    cvid, pid, bcd = dev[:3]
                    if cvid == vid:
                        if pid in products:
                            if hasattr(self.VENDOR_ID, 'keys'):
                                try:
                                    cbcd = self.VENDOR_ID[vid][pid]
                                except KeyError:
                                    # Vendor vid does not have product pid, pid
                                    # exists for some other vendor in this
                                    # device
                                    continue
                            else:
                                cbcd = self.BCD
                            if self.test_bcd(bcd, cbcd):
                                if self.can_handle(dev, debug=debug):
                                    return True, dev

        return False, None

    def is_usb_connected_windows(self, devices_on_system, debug=False, only_presence=False):
        '''
        Called from is_usb_connected()
        Windows-specific implementation
        See comments in is_usb_connected()
        '''

        def id_iterator():
            if hasattr(self.VENDOR_ID, 'keys'):
                for vid in self.VENDOR_ID:
                    vend = self.VENDOR_ID[vid]
                    for pid in vend:
                        bcd = vend[pid]
                        yield vid, pid, bcd
            else:
                vendors = self.VENDOR_ID if hasattr(self.VENDOR_ID, '__len__') else [self.VENDOR_ID]
                products = self.PRODUCT_ID if hasattr(self.PRODUCT_ID, '__len__') else [self.PRODUCT_ID]
                for vid in vendors:
                    for pid in products:
                        yield vid, pid, self.BCD

        # >>> Entry point
        #self._log_location(self.ios_connection)

        # If we were ejected, test to see if we're still physically connected
        # dev:  u'usb\\vid_05ac&pid_12ab&rev_0250'
        if self.ejected:
            _vid = "%04x" % self.vid
            _pid = "%04x" % self.pid
            for dev in devices_on_system:
                if re.search('.*vid_%s&pid_%s.*' % (_vid, _pid), dev):
                    self._log_location("iDevice physically connected, but ejected")
                    break
            else:
                self._log_location("iDevice physically disconnected, resetting ios_connection")
                self._reset_ios_connection()
                self.ejected = False
            return False, None

        # When iDevice disconnects, this throws an error, so exit cleanly
        try:
            for vendor_id, product_id, bcd in id_iterator():
                vid, pid = 'vid_%4.4x'%vendor_id, 'pid_%4.4x'%product_id
                vidd, pidd = 'vid_%i'%vendor_id, 'pid_%i'%product_id
                for device_id in devices_on_system:
                    if (vid in device_id or vidd in device_id) and \
                       (pid in device_id or pidd in device_id) and \
                       self.test_bcd_windows(device_id, bcd):
                            if False and self.verbose:
                                self._log("self.print_usb_device_info():")
                                self.print_usb_device_info(device_id)
                            if only_presence or self.can_handle_windows(device_id, debug=debug):
                                try:
                                    bcd = int(device_id.rpartition(
                                                'rev_')[-1].replace(':', 'a'), 16)
                                except:
                                    bcd = None
                                goodreader_connected = self.can_handle((vendor_id, product_id, bcd, None, None, None))
                                if goodreader_connected:
                                    return True, (vendor_id, product_id, bcd, None, None, None)
        except:
            pass

        return False, None

    def post_yank_cleanup(self):
        '''
        Called after device disconnects - can_handle() returns False
        We don't know if the device was ejected cleanly, or disconnected cleanly.
        User may have simply pulled the USB cable. If so, USBMUXD may complain of a
        broken pipe upon physical reconnection.
        '''
        self._log_location()
        self.ios_connection['connected'] = False

    def prepare_addable_books(self, paths):
        '''
        Given a list of paths, returns another list of paths. These paths
        point to addable versions of the books.

        If there is an error preparing a book, then instead of a path, the
        position in the returned list for that book should be a three tuple:
        (original_path, the exception instance, traceback)
        Modeled on calibre.devices.mtp.driver:prepare_addable_books() #304
        '''
        from calibre import sanitize_file_name
        from calibre.ptempfile import PersistentTemporaryDirectory

        self._log_location()
        tdir = PersistentTemporaryDirectory('_prep_gr')
        ans = []
        for path in paths:
            if not self.ios.exists('/'.join([self.documents_folder, path])):
                ans.append((path, 'File not found', 'File not found'))
                continue

            base = tdir
            if iswindows:
                from calibre.utils.filenames import shorten_components_to
                plen = len(base)
                bfn = path.split('/')[-1]
                dest = ''.join(shorten_components_to(245-plen, [bfn]))
            else:
                dest = path

            out_path = os.path.normpath(os.path.join(base, sanitize_file_name(dest)))
            with open(out_path, 'wb') as out:
                try:
                    self.get_file(path, out)
                except Exception as e:
                    import traceback
                    ans.append((dest, e, traceback.format_exc()))
                else:
                    ans.append(out.name)

        return ans

    def remove_books_from_metadata(self, paths, booklists):
        '''
        Remove books from the metadata list. This function must not communicate
        with the device.
        @param paths: paths to books on the device.
        @param booklists:  A tuple containing the result of calls to
                                (L{books}(oncard=None), L{books}(oncard='carda'),
                                L{books}(oncard='cardb')).

        '''
        self._log_location()
        for path in paths:
            for i, bl_book in enumerate(booklists[0]):
                if bl_book.path == path:
                    self._log("matched path: %s" % repr(bl_book.path))
                    booklists[0].pop(i)

    def shutdown(self):
        '''
        If silent switch goodreader_caching_disabled is true, remove remote cached
        '''
        self._log_location()
        if self.prefs.get('goodreader_caching_disabled', False):
            self._log("deleting remote metadata cache")
            self.ios.remove(str(self.remote_metadata))

    def sync_booklists(self, booklists, end_session=True):
        '''
        Update metadata on device.
        @param booklists: A tuple containing the result of calls to
                                (L{books}(oncard=None), L{books}(oncard='carda'),
                                L{books}(oncard='cardb')).

        prefs['manage_device_metadata']: ['manual'|'on_send'|'on_connect']

        booklist will reflect library metadata only when
        manage_device_metadata=='on_connect', otherwise booklist metadata comes from
        device
        '''
        from calibre.ebooks.metadata import author_to_author_sort, authors_to_string, title_sort

        self._log_location()

        for booklist in booklists:
            if not booklist:
                continue

            # Update db title/author from booklist title/author
            con = sqlite3.connect(self.local_metadata)
            with con:
                con.row_factory = sqlite3.Row
                cur = con.cursor()
                for book in booklist:
                    cur.execute('''SELECT
                                    authors,
                                    filename,
                                    title
                                   FROM metadata
                                   WHERE filename = {0}
                                '''.format(
                        self._quote_sqlite_identifier(book.path)))
                    cached_book = cur.fetchone()
                    if cached_book:
                        if (book.title != cached_book[b'title'] or
                            book.authors != [cached_book[b'authors']]):
                            self._log("updating metadata for %s" % repr(book.path))
                            cur.execute('''UPDATE metadata
                                           SET authors = "{0}",
                                               author_sort = "{1}",
                                               title = "{2}",
                                               title_sort = "{3}"
                                           WHERE filename = {4}
                                        '''.format(self._escape_delimiters(' & '.join(book.authors)),
                                                   self._escape_delimiters(author_to_author_sort(book.authors[0])),
                                                   self._escape_delimiters(book.title),
                                                   self._escape_delimiters(title_sort(book.title)),
                                                   self._quote_sqlite_identifier(book.path)))

                con.commit()

            # Copy the updated db to the iDevice
            self._log("updating remote_metadata")
            self.ios.copy_to_idevice(str(self.local_metadata), str(self.remote_metadata))

    def upload_books(self, files, names, on_card=None, end_session=True, metadata=None):
        '''
        Upload a list of books to the device. If a file already
        exists on the device, it should be replaced.
        This method should raise a L{FreeSpaceError} if there is not enough
        free space on the device. The text of the FreeSpaceError must contain the
        word "card" if C{on_card} is not None otherwise it must contain the word "memory".
        :files: A list of paths and/or file-like objects.
        :names: A list of file names that the books should have
        once uploaded to the device. len(names) == len(files)
        :return: A list of 3-element tuples. The list is meant to be passed
        to L{add_books_to_metadata}.
        :metadata: If not None, it is a list of :class:`Metadata` objects.
        The idea is to use the metadata to determine where on the device to
        put the book. len(metadata) == len(files). Apart from the regular
        cover (path to cover), there may also be a thumbnail attribute, which should
        be used in preference. The thumbnail attribute is of the form
        (width, height, cover_data as jpeg).

        '''
        from calibre.ebooks.metadata.pdf import get_metadata

        new_booklist = []
        con = sqlite3.connect(self.local_metadata)
        with con:
            cur = con.cursor()

            for (i, fpath) in enumerate(files):
                thumb = self._cover_to_thumb(metadata[i])
                this_book = self._create_new_book(fpath, metadata[i], thumb)
                new_booklist.append(this_book)
                destination = '/'.join([self.documents_folder, self.path_template.format(metadata[i].title)])
                self.ios.copy_to_idevice(str(fpath), destination)

                # Add to calibre_metadata db
                cur.execute('''
                                INSERT OR REPLACE INTO metadata
                                 (authors,
                                  author_sort,
                                  dateadded,
                                  filename,
                                  size,
                                  thumb_data,
                                  title,
                                  title_sort,
                                  uuid)
                                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                                (unicode(' & '.join(this_book.authors)),
                                 unicode(this_book.author_sort),
                                 this_book.dateadded,
                                 this_book.path,
                                 this_book.size,
                                 this_book.thumb_data,
                                 unicode(this_book.title),
                                 unicode(this_book.title_sort),
                                 this_book.uuid)
                                )
                if self.report_progress is not None:
                    self.report_progress(float((i + 1)*100 / len(files))/100,
                        '%(num)d of %(tot)d' % dict(num=i + 1, tot=len(files)))

            cur.close()
            con.commit()

        # Copy the updated db to the iDevice
        self._log("updating remote_metadata")
        self.ios.copy_to_idevice(str(self.local_metadata), str(self.remote_metadata))

        if self.report_progress is not None:
            self.report_progress(1.0, 'finished')

        return (new_booklist, [], [])

    # ~~~~~~~~~~~~~~~~~~~~ Helpers ~~~~~~~~~~~~~~~~~~~~
    def _cover_to_thumb(self, metadata):
        '''
        Generate a cover thumb in base64 encoding
        SmallCoverJpg: 180x270
        '''
        from PIL import Image as PILImage

        self._log_location(metadata.title)

        thumb = None

        if hasattr(metadata, 'has_cover'):
            self._log("using existing cover")
            try:
                im = PILImage.open(metadata.cover)
                im = im.resize((self.COVER_WIDTH, self.COVER_HEIGHT), PILImage.ANTIALIAS)
                of = cStringIO.StringIO()
                im.convert('RGB').save(of, 'JPEG')
                thumb = of.getvalue()
                of.close()
            except:
                self._log("ERROR converting thumb for '%s'" % (metadata.title))
                import traceback
                traceback.print_exc()

        elif metadata.cover_data[1] is not None:
            self._log(repr(metadata.cover_data))
            self._log("generating cover from cover_data")
            try:
                # Resize for local thumb
                img_data = cStringIO.StringIO(metadata.cover_data[1])
                im = PILImage.open(img_data)
                #im = PILImage.fromstring("RGBA", (180,270), str(metadata.cover_data[1]))
                im = im.resize((self.COVER_WIDTH, self.COVER_HEIGHT), PILImage.ANTIALIAS)
                of = cStringIO.StringIO()
                im.convert('RGB').save(of, 'JPEG')
                thumb = of.getvalue()
                of.close()

            except:
                self._log("ERROR converting thumb for '%s'" % (metadata.title))
                import traceback
                traceback.print_exc()
        else:
            self._log("ERROR: no cover available for '%s'" % metadata.title)
        return thumb

    def _create_new_book(self, fpath, metadata, thumb):
        '''
        '''
        from calibre.ebooks.metadata import authors_to_string

        self._log_location(metadata.title)
        this_book = Book(metadata.title, ' & '.join(metadata.authors))
        this_book.author_sort = metadata.author_sort
        this_book.dateadded = time.mktime(time.gmtime())
        this_book.datetime = datetime.fromtimestamp(this_book.dateadded).timetuple()
        this_book.path = self.path_template.format(metadata.title)
        this_book.size = os.path.getsize(fpath)
        this_book.thumbnail = self._cover_to_thumb(metadata)
        this_book.thumb_data = base64.b64encode(this_book.thumbnail)
        this_book.title_sort = metadata.title_sort
        this_book.uuid = metadata.uuid

        if False:
            self._log("%s by %s" % (this_book.title, ' & '.join(this_book.authors)))
            self._log("path: %s" % this_book.path)
            self._log("author_sort: %s" % this_book.author_sort)
            self._log("title_sort: %s" % this_book.title_sort)
        return this_book

    def _escape_delimiters(self, s):
        '''
        Switch double quotes to single quotes, escape single quotes, return as unicode
        '''
        #self._log_location(repr(s))
        s = s.replace("'", "\'")
        s = s.replace('"', '\'')
        return unicode(s)

    def _get_cached_metadata(self, cur, book):
        '''
        Return a populated Book object from a cached book's metadata
        '''
        self._log_location(book)

        cur.execute('''
                        SELECT
                         authors,
                         author_sort,
                         dateadded,
                         filename,
                         size,
                         thumb_data,
                         title,
                         title_sort,
                         uuid
                        FROM metadata
                        WHERE filename={0}
                    '''.format(self._quote_sqlite_identifier(book))
                   )

        cached_book = cur.fetchone()
        if cached_book:
            #self._log(cached_book.keys())

            this_book = Book(cached_book[b'title'], cached_book[b'authors'])
            this_book.author_sort = cached_book[b'author_sort']
            this_book.datetime = datetime.fromtimestamp(cached_book[b'dateadded']).timetuple()
            this_book.path = cached_book[b'filename']
            this_book.size = cached_book[b'size']
            if cached_book[b'thumb_data']:
                this_book.thumbnail = base64.b64decode(cached_book[b'thumb_data'])
            else:
                this_book.thumbnail = None
            this_book.title_sort = cached_book[b'title_sort']
            this_book.uuid = cached_book[b'uuid']
            return this_book
        else:
            self._log("***Error: unable to find '%s' in db" % book)
            return None

    def _get_goodreader_thumb(self, remote_path):
        '''
        remote_path is relative to /Documents
        GoodReader caches small thumbs of book covers. If we didn't send the book, fetch
        the cached copy from the iDevice. These thumbs will be scaled up to the size we
        use when sending from calibre for consistency.
        '''
        from PIL import Image as PILImage
        from calibre import fit_image

        def _build_local_path():
            '''
            GoodReader stores individual dbs for each book, matching the folder and
            name structure in the Documents folder. Make a local version, renamed to .db
            '''
            path = remote_db_path.split('/')[-1]
            if iswindows:
                from calibre.utils.filenames import shorten_components_to
                plen = len(self.temp_dir)
                path = ''.join(shorten_components_to(245-plen, [path]))

            full_path = os.path.join(self.temp_dir, path)
            base = os.path.splitext(full_path)[0]
            full_path = base + ".db"
            return os.path.normpath(full_path)

        self._log_location(remote_path)
        remote_db_path = '/'.join(['/Library','Application Support', 'com.goodiware.GoodReader.ASRoot',
                                   'Previews', '0', remote_path])

        thumb_data = None

        db_stats = self.ios.stat(remote_db_path)
        if db_stats:
            full_path = _build_local_path()
            with open(full_path, 'wb') as out:
                self.ios.copy_from_idevice(remote_db_path, out)
            local_db_path = out.name
            con = sqlite3.connect(local_db_path)
            with con:
                con.row_factory = sqlite3.Row
                cur = con.cursor()
                cur.execute('''SELECT
                                thumb
                               FROM Pages WHERE pageNum = "1"
                            ''')
                row = cur.fetchone()
                if row:
                    img_data = cStringIO.StringIO(row[b'thumb'])
                    im = PILImage.open(img_data)
                    scaled, width, height = fit_image(im.size[0], im.size[1], self.COVER_WIDTH, self.COVER_HEIGHT)
                    im = im.resize((self.COVER_WIDTH, self.COVER_HEIGHT), PILImage.NEAREST)
                    thumb = cStringIO.StringIO()
                    im.convert('RGB').save(thumb, 'JPEG')
                    thumb_data = thumb.getvalue()
                    img_data.close()
                    thumb.close()

        return thumb_data

    def _get_metadata(self, book, pdf_stats):
        '''
        Return a populated Book object with available metadata
        '''
        from calibre.ebooks.metadata import author_to_author_sort, authors_to_string, title_sort
        from calibre.ebooks.metadata.pdf import get_metadata
        self._log_location(repr(book))

        pdf_path = os.path.join(self.temp_dir, pdf_stats['path'])
        with open(pdf_path, 'rb') as f:
            stream = cStringIO.StringIO(f.read())

        mi = get_metadata(stream, cover=False)
        this_book = Book(mi.title, ' & '.join(mi.authors))
        this_book.author_sort = author_to_author_sort(mi.authors[0])
        this_book.datetime = datetime.fromtimestamp(int(pdf_stats['stats']['st_birthtime'])).timetuple()
        this_book.dateadded = int(pdf_stats['stats']['st_birthtime'])
        this_book.path = book
        this_book.size = int(pdf_stats['stats']['st_size'])
        this_book.thumbnail = self._get_goodreader_thumb(book)
        if this_book.thumbnail:
            this_book.thumb_data = base64.b64encode(this_book.thumbnail)
        else:
            this_book.thumb_data = None
        this_book.title_sort = title_sort(mi.title)
        this_book.uuid = None

        return this_book

    def _get_nested_folder_contents(self, top_folder):
        '''
        Walk the contents of documents folder iteratively to get all nested files
        '''
        def _get_nested_files(folder, stats, file_list):
            files = self.ios.listdir('/'.join([top_folder, folder]))
            for f in files:
                if files[f]['st_ifmt'] == 'S_IFREG':
                    file_list.append('/'.join([folder, f]))
                elif files[f]['st_ifmt'] == 'S_IFDIR':
                    file_list = _get_nested_files(f, files[f], file_list)
            return file_list

        self._log_location(top_folder)

        file_list = []
        files = self.ios.listdir(top_folder)
        for f in files:
            if files[f]['st_ifmt'] == 'S_IFREG':
                file_list.append(posixpath.normpath(f))
            elif files[f]['st_ifmt'] == 'S_IFDIR':
                file_list = _get_nested_files(f, files[f], file_list)
        return file_list

    def _localize_database_path(self, remote_db_path):
        '''
        Copy remote_db_path from iOS to local storage as needed
        If it doesn't exist, create a local db
        '''
        def _build_local_path():
            path = remote_db_path.split('/')[-1]
            if iswindows:
                from calibre.utils.filenames import shorten_components_to
                plen = len(self.temp_dir)
                path = ''.join(shorten_components_to(245-plen, [path]))

            full_path = os.path.join(self.temp_dir, path)
            return os.path.normpath(full_path)

        self._log_location("remote_db_path: '%s'" % (remote_db_path))

        local_db_path = None
        db_stats = {}

        db_stats = self.ios.stat(remote_db_path)
        if db_stats:
            full_path = _build_local_path()
            if os.path.exists(full_path):
                lfs = os.stat(full_path)
                if (int(db_stats['st_mtime']) == lfs.st_mtime and
                    int(db_stats['st_size']) == lfs.st_size):
                    local_db_path = full_path

            if not local_db_path:
                with open(full_path, 'wb') as out:
                    self.ios.copy_from_idevice(remote_db_path, out)
                local_db_path = out.name
        else:
            local_db_path = _build_local_path()
            self._log("creating local metadata db '%s'" % local_db_path)
            conn = sqlite3.connect(local_db_path)
            conn.row_factory = sqlite3.Row
            conn.execute('''PRAGMA user_version={0}'''.format(1))
            conn.executescript('''
                                CREATE TABLE IF NOT EXISTS metadata
                                    (
                                    authors TEXT,
                                    author_sort TEXT,
                                    dateadded INTEGER,
                                    filename TEXT UNIQUE,
                                    size INTEGER,
                                    thumb_data BLOB,
                                    title TEXT,
                                    title_sort TEXT,
                                    uuid TEXT
                                    );
                                ''')
            conn.commit()
            conn.close()

        return {'path': local_db_path, 'stats': db_stats}

    def _localize_pdf(self, remote_path):
        '''
        Copy remote_path from iOS to local storage as needed
        '''
        self._log_location("remote_path: '%s'" % (remote_path))

        local_path = None
        path = remote_path.split('/')[-1]
        if iswindows:
            from calibre.utils.filenames import shorten_components_to
            plen = len(self.temp_dir)
            path = ''.join(shorten_components_to(245-plen, [path]))

        full_path = os.path.join(self.temp_dir, path)
        full_path = os.path.normpath(full_path)

        with open(full_path, 'wb') as out:
            self.ios.copy_from_idevice(remote_path, out)
        local_path = out.name

        return local_path

    def _reset_ios_connection(self,
                              app_installed=False,
                              device_name=None,
                              ejected=False,
                              udid=0):
        if self.prefs.get('development_mode', False):
            connection_state = ("connected:{0:1} app_installed:{1:1} device_name:{2} udid:{3}".format(
                self.ios_connection['connected'],
                self.ios_connection['app_installed'],
                self.ios_connection['device_name'],
                self.ios_connection['udid'])
                )

            self._log_location(connection_state)

        self.ios_connection['app_installed'] = app_installed
        self.ios_connection['connected'] = False
        self.ios_connection['device_name'] = device_name
        self.ios_connection['udid'] = udid


