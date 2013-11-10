#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import (unicode_literals, division, absolute_import,
                        print_function)

import base64, copy, cStringIO, hashlib, locale, os, posixpath, re, sqlite3, time
from datetime import datetime
from lxml import etree, html

from calibre import guess_type
from calibre.constants import islinux, isosx, iswindows
from calibre.devices.errors import UserFeedback
from calibre.ebooks.BeautifulSoup import BeautifulStoneSoup, Tag
from calibre.ebooks.chardet import xml_to_unicode
from calibre.ebooks.oeb.parse_utils import RECOVER_PARSER
from calibre.gui2 import Application
from calibre.utils.config import prefs
from calibre.utils.icu import sort_key
from calibre.utils.magick.draw import thumbnail
from calibre.utils.zipfile import ZipFile

from calibre_plugins.ios_reader_apps import (Book, BookList,
    DatabaseMalformedException, DatabaseNotFoundException, InvalidEpub,
    iOSReaderApp, ReaderAppSignals)

IOS_COMMUNICATION_ERROR_DETAILS = (
    "Calibre is unable to communicate with your iDevice.\n\n" +
    "Try the following:\n" +
    " • Disconnect your iDevice\n" +
    " • Perform a hard reset on your iDevice by holding " +
    "the Home and Power buttons until you see the Apple " +
    "logo, then release both buttons\n\n" +
    " • Restart your computer\n" +
    "Reconnect your iDevice and try again.\n\n" +
    "If that does not resolve the problem, refer to the " +
    "iOS reader applications plugin thread in the calibre " +
    "Plugins forum at MobileRead.com for instructions " +
    "on reporting an issue.")

OCF_NS = 'urn:oasis:names:tc:opendocument:xmlns:container'
OPF_NS = 'http://www.idpf.org/2007/opf'

if True:
    '''
    Overlay methods for Marvin driver

    *** NB: Do not overlay open() ***
    '''

    def _initialize_overlay(self):
        '''
        General initialization that would have occurred in __init__()
        '''
        from calibre.ptempfile import PersistentTemporaryDirectory
        from PyQt4.QtCore import pyqtSignal

        self._log_location(self.ios_reader_app)

        # ~~~~~~~~~ Constants ~~~~~~~~~
        # None indicates that the driver supports backloading from device to library
        self.BACKLOADING_ERROR_MESSAGE = None

        self.CAN_DO_DEVICE_DB_PLUGBOARD = True

        # Which metadata on books can be set via the GUI.
        # authors, titles changes invoke call to sync_booklists()
        # collections changes invoke call to BookList:rebuild_collections()
        #self.CAN_SET_METADATA = ['title', 'authors', 'collections']
        if self.prefs.get('marvin_edit_collections_cb', False):
            self.CAN_SET_METADATA = ['collections']
        else:
            self.CAN_SET_METADATA = []

        self.COMMAND_XML = b'''\xef\xbb\xbf<?xml version='1.0' encoding='utf-8'?>
        <{0} timestamp=\'{1}\'>
        <manifest>
        </manifest>
        </{0}>'''

        self.DEBUG_CAN_HANDLE = self.prefs.get('debug_can_handle', False)
        self.DEVICE_PLUGBOARD_NAME = 'MARVIN'

        # Height for thumbnails on the device
        self.THUMBNAIL_HEIGHT = 675
        self.WANTS_UPDATED_THUMBNAILS = True

        # ~~~~~~~~~ Variables ~~~~~~~~~
        self.__busy = False

        # Initialize the IO components with iOS path separator
        self.staging_folder = '/'.join(['/Library', 'calibre'])

        self.books_subpath = '/Library/mainDb.sqlite'
        self.covers_subpath = '/Library/Caches/com.appstafarian.marvin.covers'
        self.connected_fs = '/'.join([self.staging_folder, 'connected.xml'])
        self.flags = {
            'new': 'NEW',
            'read': 'READ',
            'reading_list': 'READING LIST'
            }
        self.format_map = ['epub']
        self.ios_connection = {
            'app_installed': False,
            'connected': False,
            'device_name': None,
            'ejected': False,
            'udid': 0
            }
        self.operation_timed_out = False
        self.path_template = '{0}.epub'
        self.status_fs = '/'.join([self.staging_folder, 'status.xml'])
        self.update_list = []

        # ~~~~~~~~~ Confirm/create thumbs archive ~~~~~~~~~
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir)

        if not os.path.exists(self.archive_path):
            self._log("creating zip archive")
            zfw = ZipFile(self.archive_path, mode='w')
            zfw.writestr("Marvin Thumbs Archive", '')
            zfw.close()
        else:
            self._log("existing thumb cache at '%s'" % self.archive_path)

        # ~~~~~~~~~ Create a device signal class for Marvin Manager ~~~~~~~~~
        self.marvin_device_signals = ReaderAppSignals()

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
        if False:
            self._log("locations: %s" % repr(locations))
            self._log("metadata:")
            for mi in metadata:
                self._log("  %s" % mi.title)
            self._log("booklists:")
            for book in booklists[0]:
                self._log(" '%s' by %s %s" % (book.title, book.authors, book.uuid))
            self._log("metadata_updates:")
            for book in self.metadata_updates:
                self._log(" '%s' by %s %s" % (book['title'], book['authors'], book['uuid']))

            metadata_update_uuids = [book['uuid'] for book in self.metadata_updates]
            self._log("metadata_update_uuids: %s" % repr(metadata_update_uuids))

        # Delete any obsolete copies of the book from the booklist
        if self.update_list:
            for j, p_book in enumerate(self.update_list):
                # Purge the booklist, self.cached_books
                for i, bl_book in enumerate(booklists[0]):
                    if bl_book.uuid == p_book['uuid']:
                        # Remove from booklists[0]
                        booklists[0].pop(i)

                        # If >1 matching uuid, remove old title
                        matching_uuids = 0
                        for cb in self.cached_books:
                            if self.cached_books[cb]['uuid'] == p_book['uuid']:
                                matching_uuids += 1
                        if matching_uuids > 1:
                            for cb in self.cached_books:
                                if self.cached_books[cb]['uuid'] == p_book['uuid']:
                                    if (self.cached_books[cb]['title'] == p_book['title'] and
                                        self.cached_books[cb]['author'] == p_book['author']):
                                        self.cached_books.pop(cb)
                                        break

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
        from calibre import strftime

        def _get_marvin_cover(book_hash):
            '''
            Given book_hash, retrieve the associated small jpg cover from
            covers_subpath
            '''
            cover_path = '/'.join([self.covers_subpath, '%s.jpg' % book_hash])
            cover_bytes = None
            stats = self.ios.exists(cover_path)
            if stats:
                cover_bytes = self.ios.read(cover_path, mode='rb')
            return cover_bytes

        def _get_marvin_genres(cur, book_id):
            # Get the genre(s) for this book
            genre_cur = con.cursor()
            genre_cur.execute('''SELECT
                                    Subject
                                 FROM BookSubjects
                                 WHERE BookID = '{0}'
                              '''.format(book_id))
            genres = []
            genre_rows = genre_cur.fetchall()
            if genre_rows is not None:
                genres = sorted([genre[b'Subject'] for genre in genre_rows])
            genre_cur.close()
            return genres

        def _get_marvin_collections(cur, book_id, row):
            # Get the collection assignments
            ca_cur = con.cursor()
            ca_cur.execute('''SELECT
                                BookID,
                                CollectionID
                              FROM BookCollections
                              WHERE BookID = '{0}'
                           '''.format(book_id))
            collections = []
            if row[b'NewFlag']:
                collections.append(self.flags['new'])
            if row[b'ReadingList']:
                collections.append(self.flags['reading_list'])
            if row[b'IsRead']:
                collections.append(self.flags['read'])

            collection_rows = ca_cur.fetchall()
            if collection_rows is not None:
                collection_assignments = [collection[b'CollectionID']
                                          for collection in collection_rows]
                collections += [collection_map[item] for item in collection_assignments]
                collections = sorted(collections, key=sort_key)
            ca_cur.close()
            return collections

        # Entry point
        booklist = BookList(self)
        if not oncard:
            self._log_location()
            cached_books = {}

            # Fetch current metadata from Marvin's DB
            local_db_path = self._localize_database_path(self.books_subpath)
            con = sqlite3.connect(local_db_path)

            with con:
                con.row_factory = sqlite3.Row

                # Build a collection map
                collections_cur = con.cursor()
                collections_cur.execute('''SELECT
                                            ID,
                                            Name
                                           FROM Collections
                                        ''')
                rows = collections_cur.fetchall()
                collection_map = {}
                for row in rows:
                    collection_map[row[b'ID']] = row[b'Name']
                collections_cur.close()

                # Get the books
                cur = con.cursor()
                try:
                    cur.execute('''SELECT
                                    Author,
                                    AuthorSort,
                                    Books.ID as id_,
                                    CalibreCoverHash,
                                    CalibreSeries,
                                    CalibreSeriesIndex,
                                    CalibreTitleSort,
                                    DateAdded,
                                    DatePublished,
                                    Description,
                                    FileName,
                                    Hash,
                                    IsRead,
                                    NewFlag,
                                    Publisher,
                                    ReadingList,
                                    Title,
                                    UUID
                                  FROM Books
                                ''')
                except:
                    cur.close()
                    # Invalidate local_db_path so Marvin Manager knows
                    self.local_db_path = None
                    self.cached_books = {}
                    raise DatabaseMalformedException("Marvin database is damaged")

                rows = cur.fetchall()
                book_count = len(rows)
                for i, row in enumerate(rows):
                    book_id = row[b'id_']

                    # Get the primary metadata from Books
                    this_book = Book(row[b'Title'], row[b'Author'])
                    this_book.author_sort = row[b'AuthorSort']
                    this_book.cover_hash = row[b'CalibreCoverHash']
                    _date_added = row[b'DateAdded']
                    this_book.datetime = datetime.fromtimestamp(int(_date_added)).timetuple()
                    this_book.description = row[b'Description']
                    this_book.device_collections = _get_marvin_collections(cur, book_id, row)
                    this_book.path = row[b'FileName']

                    try:
                        pubdate = datetime.utcfromtimestamp(int(row[b'DatePublished']))
                        pubdate = pubdate.replace(hour=0, minute=0, second=0)
                    except:
                        pubdate = None
                    this_book.pubdate = pubdate

                    this_book.publisher = row[b'Publisher']
                    this_book.series = row[b'CalibreSeries']
                    if this_book.series == '':
                        this_book.series = None
                    try:
                        this_book.series_index = float(row[b'CalibreSeriesIndex'])
                    except:
                        this_book.series_index = 0.0
                    if this_book.series_index == 0.0 and this_book.series is None:
                        this_book.series_index = None

                    try:
                        _file_size = self.ios.stat('/'.join(['/Documents', this_book.path]))['st_size']
                    except:
                        raise UserFeedback("Error communicating with iDevice",
                            details = IOS_COMMUNICATION_ERROR_DETAILS,
                            level=UserFeedback.ERROR)

                    this_book.size = int(_file_size)
                    this_book.thumbnail = _get_marvin_cover(row[b'Hash'])
                    this_book.tags = _get_marvin_genres(cur, book_id)
                    this_book.title_sort = row[b'CalibreTitleSort']
                    this_book.uuid = row[b'UUID']

                    booklist.add_book(this_book, False)

                    if self.report_progress is not None:
                        self.report_progress(float((i + 1)*100 / book_count)/100,
                            '%(num)d of %(tot)d' % dict(num=i + 1, tot=book_count))

                    # Manage collections may change this_book.device_collections,
                    # so we need to make a copy of it for testing during rebuild_collections
                    cached_books[this_book.path] = {
                        'author': this_book.author,
                        'authors': this_book.authors,
                        'author_sort': this_book.author_sort,
                        'cover_hash': this_book.cover_hash,
                        'description': this_book.description,
                        'device_collections': copy.copy(this_book.device_collections),
                        'pubdate': this_book.pubdate,
                        'publisher': this_book.publisher,
                        'series': this_book.series,
                        'series_index': this_book.series_index,
                        'tags': this_book.tags,
                        'title': this_book.title,
                        'title_sort': this_book.title_sort,
                        'uuid': this_book.uuid,
                        }

                cur.close()

            if self.report_progress is not None:
                self.report_progress(1.0, 'finished')

            self.cached_books = cached_books

            if self.prefs.get('development_mode', False):
                self._log("cached %d books from Marvin:" % len(cached_books))
                for p, v in self.cached_books.iteritems():
                    self._log(" {0} {1:30} {2} {3}".format(
                        p, repr(v['title'][0:26]), v['authors'], v['device_collections']))

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

        Marvin disconnected:
            self.ios_connection: udid:<device>, ejected:False, device_name:<name>,
                                 connected:False, app_installed:True
        Marvin connected:
            self.ios_connection: udid:<device>, ejected:False, device_name:<name>,
                                 connected:True, app_installed:True
        Marvin ejected:
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

        if self.DEBUG_CAN_HANDLE:
            self._log_location(_show_current_connection())

        # If another libiMobileDevice client is talking, return True
        if self.__busy:
            return True

        self.__busy = True

        # 0: If we've already discovered a connected device without Marvin, exit
        if self.ios_connection['udid'] and self.ios_connection['app_installed'] is False:
            if self.DEBUG_CAN_HANDLE:
                self._log("self.ios_connection['udid']: %s" % self.ios_connection['udid'])
                self._log("self.ios_connection['app_installed']: %s" % self.ios_connection['app_installed'])
                self._log("0: returning %s" % self.ios_connection['app_installed'])
            self.__busy = False
            return self.ios_connection['app_installed']

        # 0. If user ejected, exit
        if self.ios_connection['udid'] and self.ejected is True:
            if self.DEBUG_CAN_HANDLE:
                self._log("'%s' ejected" % self.ios_connection['device_name'])
            self.__busy = False
            return False

        # 1: Is there a (single) connected iDevice?
        if False and self.DEBUG_CAN_HANDLE:
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
            if self.ios_connection['connected']:
                connection_live = False
                if self.ios.exists(self.connected_fs):
                    # Parse the connection data for state
                    connection = etree.fromstring(self.ios.read(self.connected_fs))
                    connection_state = connection.find('state').text
                    if connection_state == 'online':
                        connection_live = True
                        if self.DEBUG_CAN_HANDLE:
                            self._log("1a. <state> = online")
                    else:
                        connection_live = False
                        if self.DEBUG_CAN_HANDLE:
                            self._log("1b. <state> = offline")

                    # Show the connection initiation time
                    self.connection_timestamp = float(connection.get('timestamp'))
                    d = datetime.fromtimestamp(self.connection_timestamp)
                    if self.DEBUG_CAN_HANDLE:
                        self._log("   connection last refreshed %s" % (d.strftime('%Y-%m-%d %H:%M:%S')))

                else:
                    if self.DEBUG_CAN_HANDLE:
                        self._log("1c. user exited connection mode")

                if not connection_live:
                    # Lost the connection, reset
                    #self._reset_ios_connection(udid=connected_ios_devices[0])
                    self.ios_connection['connected'] = False

                if self.DEBUG_CAN_HANDLE:
                    self._log("1d: returning %s" % connection_live)
                self.__busy = False
                return connection_live

            elif self.ios_connection['udid'] != connected_ios_devices[0]:
                self._reset_ios_connection(udid=connected_ios_devices[0], verbose=self.DEBUG_CAN_HANDLE)

            # 2. Is Marvin installed on this iDevice?
            if not self.ios_connection['app_installed']:
                if self.DEBUG_CAN_HANDLE:
                    self._log("2. Marvin installed, attempting connection")
                self.ios_connection['app_installed'] = self.ios.mount_ios_app(app_id=self.app_id)
                self.ios_connection['device_name'] = self.ios.device_name
                if self.DEBUG_CAN_HANDLE:
                    self._log("2a. self.ios_connection: %s" % _show_current_connection())

                # If no Marvin, we can't handle, so exit
                if not self.ios_connection['app_installed']:
                    if self.DEBUG_CAN_HANDLE:
                        self._log("2. Marvin not installed")
                    self.__busy = False
                    return self.ios_connection['app_installed']

            # 3. Check to see if connected.xml exists in staging folder
            if self.DEBUG_CAN_HANDLE:
                self._log("3. Looking for calibre connection mode")

            connection_live = False
            if self.ios.exists(self.connected_fs):
                # Parse the connection data for state
                connection = etree.fromstring(self.ios.read(self.connected_fs))
                connection_state = connection.find('state').text
                if connection_state == 'online':
                    connection_live = True
                    if self.DEBUG_CAN_HANDLE:
                        self._log("3a. <state> = online")
                else:
                    connection_live = False
                    if self.DEBUG_CAN_HANDLE:
                        self._log("3b. <state> = offline")

                # Show the connection initiation time
                self.connection_timestamp = float(connection.get('timestamp'))
                d = datetime.fromtimestamp(self.connection_timestamp)
                if self.DEBUG_CAN_HANDLE:
                    self._log("   connection last refreshed %s" % (d.strftime('%Y-%m-%d %H:%M:%S')))

                self.ios_connection['connected'] = connection_live

            else:
                self.ios_connection['connected'] = False
                if self.DEBUG_CAN_HANDLE:
                    self._log("3d. Marvin not in calibre connection mode")

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
        if self.DEBUG_CAN_HANDLE:
            self._log("4. self.ios_connection: %s" % _show_current_connection())

        self.__busy = False

        # Signal MM if disconnected
        if not self.ios_connection['connected']:
            self.marvin_device_signals.reader_app_status_changed.emit("disconnected")

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
        self._log_location(paths)

        command_name = 'delete_books'
        command_element = 'deletebooks'
        command_soup = BeautifulStoneSoup(self.COMMAND_XML.format(
            command_element, time.mktime(time.localtime())))

        file_count = float(len(paths))

        for i, path in enumerate(paths):
            # Add book to command file
            if path in self.cached_books:
                book_tag = Tag(command_soup, 'book')
                book_tag['author'] = ', '.join(self.cached_books[path]['authors'])
                book_tag['title'] = self.cached_books[path]['title']
                book_tag['uuid'] = self.cached_books[path]['uuid']
                book_tag['filename'] = path
                command_soup.manifest.insert(i, book_tag)
            else:
                self._log("trying to delete book not in cache '%s'" % path)
                self._log("cached_paths:\n%s" % self.cached_books.keys())
                continue

        # Copy the command file to the staging folder
        self._stage_command_file(command_name, command_soup, show_command=self.prefs.get('development_mode', False))

        # Wait for completion
        self._wait_for_command_completion(command_name)

        # Update local copy of mainDb
        self._localize_database_path(self.books_subpath)

    def eject(self):
        '''
        Unmount/eject the device
        post_yank_cleanup() handles the dismount
        '''
        self._log_location()

        # If busy in critical IO operation, wait for completion before returning
        while self.__busy:
            time.sleep(0.10)
            Application.processEvents()
        self.ejected = True

    def get_busy_flag(self):
        return self.__busy

    def get_file(self, path, outfile, end_session=True):
        '''
        Read the file at path on the device and write it to provided outfile.

        outfile: file object (result of an open() call)
        '''
        self._log_location()
        self.ios.copy_from_idevice('/'.join(['Documents', path]), outfile)

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

        # When Marvin disconnects, this throws an error, so exit cleanly
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
                                marvin_connected = self.can_handle((vendor_id, product_id, bcd, None, None, None))
                                if marvin_connected:
                                    return True, (vendor_id, product_id, bcd, None, None, None)
        except:
            pass

        return False, None

        '''
        no_connection = ((False, None))
        usb_connection = super(iOSReaderApp, self).is_usb_connected_windows(devices_on_system, debug, only_presence)
        #self._log_location(usb_connection)
        marvin_connected = self.can_handle(usb_connection[1])
        if marvin_connected:
            return usb_connection
        else:
            return no_connection
        '''

    def post_yank_cleanup(self):
        '''
        Called after device disconnects - can_handle() returns False
        We don't know if the device was ejected cleanly, or disconnected cleanly.
        User may have simply pulled the USB cable. If so, USBMUXD may complain of a
        broken pipe upon physical reconnection.
        '''
        self._log_location()
        self.ios_connection['connected'] = False
        self.marvin_device_signals.reader_app_status_changed.emit("yanked")

    def prepare_addable_books(self, paths):
        '''
        Given a list of paths, returns another list of paths. These paths
        point to addable versions of the books.

        If there is an error preparing a book, then instead of a path, the
        position in the returned list for that book should be a three tuple:
        (original_path, the exception instance, traceback)
        Modeled on calibre.devices.mtp.driver:prepare_addable_books() #304
        '''
        from calibre.ptempfile import PersistentTemporaryDirectory
        from calibre.utils.filenames import shorten_components_to

        self._log_location()
        tdir = PersistentTemporaryDirectory('_prepare_marvin')
        ans = []
        for path in paths:
            if not self.ios.exists('/'.join(['Documents', path])):
                ans.append((path, 'File not found', 'File not found'))
                continue

            base = tdir
            if iswindows:
                plen = len(base)
                name = ''.join(shorten_components_to(245-plen, [path]))
            with open(os.path.join(base, path), 'wb') as out:
                try:
                    self.get_file(path, out)
                except Exception as e:
                    import traceback
                    ans.append((path, e, traceback.format_exc()))
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

        NB: This will not find books that were added by a different installation of calibre
            as uuids are different
        '''
        self._log_location(paths)
        for path in paths:
            for i, bl_book in enumerate(booklists[0]):
                found = False
                if bl_book.uuid and bl_book.uuid == self.cached_books[path]['uuid']:
                    self._log("'%s' matched uuid" % bl_book.title)
                    booklists[0].pop(i)
                    found = True
                elif bl_book.title == self.cached_books[path]['title'] and \
                     bl_book.author == self.cached_books[path]['author']:
                    self._log("'%s' matched title + author" % bl_book.title)
                    booklists[0].pop(i)
                    found = True

                if found:
                    # Remove from self.cached_books
                    for cb in self.cached_books:
                        if (self.cached_books[cb]['uuid'] == self.cached_books[path]['uuid'] and
                            self.cached_books[cb]['author'] == self.cached_books[path]['author'] and
                            self.cached_books[cb]['title'] == self.cached_books[path]['title']):
                            self.cached_books.pop(cb)
                            break
                    else:
                        self._log("'%s' not found in self.cached_books" % self.cached_books[path]['title'])

                    break
            else:
                self._log("  unable to find '%s' by '%s' (%s)" %
                                (self.cached_books[path]['title'],
                                 self.cached_books[path]['author'],
                                 self.cached_books[path]['uuid']))

    def set_busy_flag(self, value):
        '''
        Another libiMobileDevice wants to talk to the connected iDevice uninterrupted
        '''
        self.__busy = value

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
        # Automatic metadata management is disabled 2013-06-03 v0.1.11
        if True:
            self._log_location("automatic metadata management disabled")
            return

        from xml.sax.saxutils import escape
        from calibre import strftime

        manage_device_metadata = prefs['manage_device_metadata']
        self._log_location(manage_device_metadata)
        if manage_device_metadata != 'on_connect':
            self._log("automatic metadata management disabled")
            return

        command_name = "update_metadata"
        command_element = "updatemetadata"
        command_soup = BeautifulStoneSoup(self.COMMAND_XML.format(
            command_element, time.mktime(time.localtime())))

        root = command_soup.find(command_element)
        root['cleanupcollections'] = 'yes'

        for booklist in booklists:
            '''
            Evaluate author, author_sort, collections, cover, description, published,
            publisher, series, series_number, tags, title, and title_sort for changes.
            If anything has changed, send refreshed metadata.
            Always send <collections>, <subjects> with current values
            Send <cover>, <description> only on changes.
            '''

            if not booklist:
                continue

            changed = 0
            for book in booklist:
                if not book.in_library:
                    continue

                filename = self.path_template.format(book.uuid)

                if filename not in self.cached_books:
                    for fn in self.cached_books:
                        if (book.uuid == self.cached_books[fn]['uuid'] or
                            (book.title == self.cached_books[fn]['title'] and
                             book.authors == self.cached_books[fn]['authors'])):
                            filename = fn
                            break
                    else:
                        self._log("ERROR: '%s' by %s not found in cached_books" %
                                              (book.title, repr(book.authors)))
                        continue

                # Test for changes to title, author, tags, collections
                cover_updated = False
                metadata_updated = False

                # >>> Attributes <<<
                # ~~~~~~~~~~ author ~~~~~~~~~~
                if self.cached_books[filename]['author'] != book.author:
                    self._log("%s (%s)" % (book.title, book.in_library))
                    self._log(" author: (device) %s != (library) %s" %
                                         (self.cached_books[filename]['author'], book.author))
                    self.cached_books[filename]['author'] = book.author
                    metadata_updated = True

                # ~~~~~~~~~~ author_sort ~~~~~~~~~~
                if self.cached_books[filename]['author_sort'] != book.author_sort:
                    self._log("%s (%s)" % (book.title, book.in_library))
                    self._log(" author_sort: (device) %s != (library) %s" %
                                         (self.cached_books[filename]['author_sort'], book.author_sort))
                    self.cached_books[filename]['author_sort'] = book.author_sort
                    metadata_updated = True

                # ~~~~~~~~~~ pubdate ~~~~~~~~~~
                # This is probably broken. See Marvin_Manager:book_status #1588
                if self.cached_books[filename]['pubdate'] != strftime('%Y-%m-%d', t=book.pubdate):
                    self._log("%s (%s)" % (book.title, book.in_library))
                    self._log(" pubdate: (device) %s != (library) %s" %
                                         (repr(self.cached_books[filename]['pubdate']),
                                          repr(strftime('%Y-%m-%d', t=book.pubdate))))
                                          #repr(strftime('%Y-%m-%d %H:%M:%S %z', t=book.pubdate))))
                    self.cached_books[filename]['pubdate'] = book.pubdate
                    metadata_updated = True

                # ~~~~~~~~~~ publisher ~~~~~~~~~~
                if self.cached_books[filename]['publisher'] != book.publisher:
                    self._log("%s (%s)" % (book.title, book.in_library))
                    self._log(" publisher: (device) %s != (library) %s" %
                                         (repr(self.cached_books[filename]['publisher']), repr(book.publisher)))
                    self.cached_books[filename]['publisher'] = book.publisher
                    metadata_updated = True

                # ~~~~~~~~~~ series ~~~~~~~~~~
                if self.cached_books[filename]['series'] != book.series:
                    self._log("%s (%s)" % (book.title, book.in_library))
                    self._log(" series: (device) %s != (library) %s" %
                                         (repr(self.cached_books[filename]['series']), repr(book.series)))
                    self.cached_books[filename]['series'] = book.series
                    metadata_updated = True

                # ~~~~~~~~~~ series_index ~~~~~~~~~~
                if self.cached_books[filename]['series_index'] != book.series_index:
                    self._log("%s (%s)" % (book.title, book.in_library))
                    self._log(" series_index: (device) %s != (library) %s" %
                        (repr(self.cached_books[filename]['series_index']), repr(book.series_index)))
                    self.cached_books[filename]['series_index'] = book.series_index
                    metadata_updated = True

                # ~~~~~~~~~~ title ~~~~~~~~~~
                if self.cached_books[filename]['title'] != book.title:
                    self._log("%s (%s)" % (book.title, book.in_library))
                    self._log(" title: (device) %s != (library) %s" %
                        (repr(self.cached_books[filename]['title']), repr(book.title)))
                    self.cached_books[filename]['title'] = book.title
                    metadata_updated = True

                # ~~~~~~~~~~ title_sort ~~~~~~~~~~
                if self.cached_books[filename]['title_sort'] != book.title_sort:
                    self._log("%s (%s)" % (book.title, book.in_library))
                    self._log(" title_sort: (device) %s != (library) %s" %
                        (repr(self.cached_books[filename]['title_sort']), repr(book.title_sort)))
                    self.cached_books[filename]['title_sort'] = book.title_sort
                    metadata_updated = True


                # >>> Additional elements <<<
                # ~~~~~~~~~~ description ~~~~~~~~~~
                if self.cached_books[filename]['description'] != book.description:
                    self._log("%s (%s)" % (book.title, book.in_library))
                    self._log(" description: (device) %s != (library) %s" %
                        (self.cached_books[filename]['description'], book.description))
                    self.cached_books[filename]['description'] = book.description
                    metadata_updated = True

                # ~~~~~~~~~~ subjects ~~~~~~~~~~
                if self.cached_books[filename]['tags'] != sorted(book.tags):
                    self._log("%s (%s)" % (book.title, book.in_library))
                    self._log(" tags: (device) %s != (library) %s" %
                        (repr(self.cached_books[filename]['tags']), repr(book.tags)))
                    self.cached_books[filename]['tags'] = book.tags
                    metadata_updated = True

                # ~~~~~~~~~~ collections ~~~~~~~~~~
                collection_assignments = self._get_field_items(book)
                cached_assignments = self.cached_books[filename]['device_collections']

                if cached_assignments != collection_assignments:
                    self._log("%s (%s)" % (book.title, book.in_library))
                    self._log(" collections: (device) %s != (library) %s" %
                        (cached_assignments, collection_assignments))
                    self.cached_books[filename]['device_collections'] = sorted(collection_assignments)
                    metadata_updated = True

                # ~~~~~~~~~~ cover ~~~~~~~~~~
                cover = book.get('thumbnail')
                if cover:
                    #self._log("thumb_width: %s" % cover[0])
                    #self._log("thumb_height: %s" % cover[1])
                    cover_hash = hashlib.md5(cover[2]).hexdigest()
                    if self.cached_books[filename]['cover_hash'] != cover_hash:
                        self._log("%s (%s)" % (book.title, book.in_library))
                        self._log(" cover: (device) %s != (library) %s" %
                                             (self.cached_books[filename]['cover_hash'], cover_hash))
                        self.cached_books[filename]['cover_hash'] = cover_hash
                        cover_updated = True
                        metadata_updated = True
                else:
                    self._log(">>>no cover available for '%s'<<<" % book.title)


                # Generate the delta description
                if metadata_updated:
                    # Add the book to command file
                    book_tag = Tag(command_soup, 'book')
                    book_tag['author'] = escape(', '.join(book.authors))
                    book_tag['authorsort'] = escape(book.author_sort)
                    book_tag['filename'] = escape(filename)
                    #book_tag['pubdate'] = book.pubdate
                    #self._log("book.pubdate: %s" % repr(book.pubdate))
                    #self._log("pubdate: %s" % repr(time.mktime(book.pubdate.timetuple())))

                    book_tag['pubdate'] = strftime('%Y-%m-%d', t=book.pubdate)
                    book_tag['publisher'] = ''
                    if book.publisher is not None:
                        book_tag['publisher'] = escape(book.publisher)
                    book_tag['series'] = ''
                    if book.series:
                        book_tag['series'] = escape(book.series)
                    book_tag['seriesindex'] = ''
                    if book.series_index:
                       book_tag['seriesindex'] = book.series_index
                    book_tag['title'] = escape(book.title)
                    book_tag['titlesort'] = escape(book.title_sort)
                    book_tag['uuid'] = book.uuid

                    # Add the cover
                    if cover_updated:
                        cover_tag = Tag(command_soup, 'cover')
                        cover_tag['hash'] = cover_hash
                        cover_tag['encoding'] = 'base64'
                        cover_tag.insert(0, base64.b64encode(cover[2]))
                        book_tag.insert(0, cover_tag)

                    # Add the subjects
                    subjects_tag = Tag(command_soup, 'subjects')
                    for tag in sorted(book.tags, reverse=True):
                        subject_tag = Tag(command_soup, 'subject')
                        subject_tag.insert(0, escape(tag))
                        subjects_tag.insert(0, subject_tag)
                    book_tag.insert(0, subjects_tag)

                    # Add the collections
                    collections_tag = Tag(command_soup, 'collections')
                    if collection_assignments:
                        for tag in collection_assignments:
                            c_tag = Tag(command_soup, 'collection')
                            c_tag.insert(0, escape(tag))
                            collections_tag.insert(0, c_tag)
                    book_tag.insert(0, collections_tag)

                    # Add the description
                    try:
                        description_tag = Tag(command_soup, 'description')
                        description_tag.insert(0, escape(book.comments))
                        book_tag.insert(0, description_tag)
                    except:
                        pass

                    command_soup.manifest.insert(0, book_tag)

                    changed += 1

            if changed:
                self._log_location("sending update_metadata() command, %d changes detected" % changed)

                # Stage the command file
                self._stage_command_file(command_name, command_soup, show_command=self.prefs.get('development_mode', False))

                # Wait for completion
                self._wait_for_command_completion(command_name)
            else:
                self._log("no metadata changes detected")

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

        Progress is reported in two phases:
            1) Transfer of files to Marvin's staging area
            2) Marvin's completion of imports
        '''
        self._log_location()

        # Init the upload_books command file
        # <command>, <timestamp>, <overwrite existing>
        command_element = "uploadbooks"
        upload_soup = BeautifulStoneSoup(self.COMMAND_XML.format(
            command_element, time.mktime(time.localtime())))
        root = upload_soup.find(command_element)
        root['overwrite'] = 'yes' if self.prefs.get('marvin_replace_rb', False) else 'no'

        # Init the update_metadata command file
        command_element = "updatemetadata"
        update_soup = BeautifulStoneSoup(self.COMMAND_XML.format(
            command_element, time.mktime(time.localtime())))
        root = update_soup.find(command_element)
        root['cleanupcollections'] = 'yes'

        # Process the selected files
        file_count = float(len(files))
        new_booklist = []
        self.active_flags = {}
        self.malformed_books = []
        self.metadata_updates = []
        self.rejected_books = []
        self.replaced_books = []
        self.skipped_books = []
        self.update_list = []
        self.user_feedback_after_callback = None

        replaced_covers = 0
        for (i, fpath) in enumerate(files):

            # Selective processing flag
            metadata_only = False

            # Test if target_epub exists
            target_epub = self.path_template.format(metadata[i].uuid)
            target_epub_exists = False
            if target_epub in self.cached_books:
                # Test for UUID match
                target_epub_exists = True
                self._log("'%s' already exists in Marvin (UUID match)" % metadata[i].title)
            else:
                # Test for author/title match
                for book in self.cached_books:
                    if 'download_pending' in self.cached_books[book]:
                        continue
                    if (self.cached_books[book]['title'] == metadata[i].title
                        and self.cached_books[book]['authors'] == metadata[i].authors):
                        self._log("'%s' already exists in Marvin (author match)" % metadata[i].title)
                        target_epub = book
                        target_epub_exists = True
                        break
                else:
                    self._log("'%s' by %s does not exist in Marvin" % (metadata[i].title, metadata[i].authors))

            if target_epub_exists:
                if self.prefs.get('marvin_protect_rb', True):
                    '''
                    self._log("fpath: %s" % fpath)
                    with open(fpath, 'rb') as f:
                        stream = cStringIO.StringIO(f.read())
                    mi = get_metadata(stream, extract_cover=False)
                    self._log(mi)
                    '''
                    #self._log(self.cached_books.keys())
                    self._log("'%s' exists on device, skipping (overwrites disabled)" % target_epub)
                    self.skipped_books.append({'title': metadata[i].title,
                                               'authors': metadata[i].authors,
                                               'uuid': metadata[i].uuid})
                    continue
                elif self.prefs.get('marvin_update_rb', False):
                    # Save active flags for this book
                    active_flags = []
                    for flag in self.flags.values():
                        if flag in self.cached_books[target_epub]['device_collections']:
                            active_flags.append(flag)
                    self.active_flags[metadata[i].uuid] = active_flags

                    # Schedule metadata update
                    self.metadata_updates.append({'title': metadata[i].title,
                        'authors': metadata[i].authors, 'uuid': metadata[i].uuid})
                    self._schedule_metadata_update(target_epub, metadata[i], update_soup)
                    self.update_list.append(self.cached_books[target_epub])
                    metadata_only = True

            # Normal upload begins here
            # Update the book at fpath with metadata xform
            try:
                mi_x = self._update_epub_metadata(fpath, metadata[i])
            except:
                self.malformed_books.append({'title': metadata[i].title,
                                             'authors': metadata[i].authors,
                                             'uuid': metadata[i].uuid})
                self._log("error updating epub metadata for '%s'" % metadata[i].title)
                import traceback
                self._log(traceback.format_exc())
                continue

            # Generate thumb for calibre Device view
            thumb = self._cover_to_thumb(mi_x)

            if not metadata_only:
                # If this book on device, remove and add to update_list
                path = self.path_template.format(metadata[i].uuid)
                self._remove_existing_copy(path, metadata[i])

            # Populate Book object for new_booklist
            this_book = self._create_new_book(fpath, metadata[i], mi_x, thumb, metadata_only)

            if not metadata_only:
                # Create <book> for manifest with filename=, coverhash=
                book_tag = Tag(upload_soup, 'book')
                book_tag['filename'] = this_book.path
                book_tag['coverhash'] = this_book.cover_hash
                if this_book.word_count:
                    book_tag['wordcount'] = this_book.word_count

                # Add <collections> to <book>
                if this_book.device_collections:
                    collections_tag = Tag(upload_soup, 'collections')
                    for tag in this_book.device_collections:
                        c_tag = Tag(upload_soup, 'collection')
                        c_tag.insert(0, tag)
                        collections_tag.insert(0, c_tag)
                    book_tag.insert(0, collections_tag)

                # Detect unreplaceable covers, send <cover> if necessary
                replaceable_cover = self._evaluate_replaceable_cover(fpath)
                if not replaceable_cover:
                    #original_cover = self._evaluate_original_cover(metadata[i])
                    #if not original_cover:
                    if True:
                        cover_tag = self._create_cover_element(metadata[i], upload_soup)
                        if cover_tag:
                            self._log("sending replacement cover for %s" % metadata[i].title)
                            replaced_covers += 1
                            book_tag.insert(0, cover_tag)
                            del book_tag['coverhash']
                else:
                    # Make sure we have a cover.jpg
                    if not metadata[i].has_cover:
                        del book_tag['coverhash']

                upload_soup.manifest.insert(i, book_tag)

            new_booklist.append(this_book)

            if not metadata_only:
                # Copy the book file to the staging folder
                destination = '/'.join([self.staging_folder, book_tag['filename']])
                self.ios.copy_to_idevice(str(fpath), str(destination))
                if target_epub_exists:
                    self.replaced_books.append({'title': metadata[i].title,
                                                'authors': metadata[i].authors,
                                                'uuid': metadata[i].uuid})

            # Add new book to cached_books
            self.cached_books[this_book.path] = {
                    'author': this_book.author,
                    'authors': this_book.authors,
                    'author_sort': this_book.author_sort,
                    'cover_hash': this_book.cover_hash,
                    'description': this_book.description,
                    'device_collections': this_book.device_collections,
                    'pubdate': this_book.pubdate,
                    'publisher': this_book.publisher,
                    'series': this_book.series,
                    'series_index': this_book.series_index,
                    'tags': this_book.tags,
                    'title': this_book.title,
                    'title_sort': this_book.title_sort,
                    'uuid': this_book.uuid,
                    }

            if not target_epub_exists:
                self.cached_books[this_book.path]['download_pending'] = True

            if self.prefs.get('development_mode', False):
                self._log("self.cached_books:")
                for p,v in self.cached_books.iteritems():
                    self._log(" {0} {1}".format(p, repr(v['title'])))

            # Report progress
            if self.report_progress is not None:
                self.report_progress((i + 1) / (file_count * 2),
                    '%(num)d of %(tot)d transferred to Marvin' % dict(num=i + 1, tot=file_count))

        manifest_count = len(upload_soup.manifest.findAll(True))
        if manifest_count:
            # Report replaced_covers
            if replaced_covers:
                self._log("Sending {0} replacement {1}".format(replaced_covers,
                    'cover' if replaced_covers == 1 else 'covers'))

            # Copy the command file to the staging folder
            self._stage_command_file("upload_books", upload_soup,
                show_command=self.prefs.get('development_mode', False))

            # Wait for completion
            self._wait_for_command_completion("upload_books")

        # Perform metadata updates
        if self.metadata_updates:
            self._log("Sending metadata updates")

            # Copy the command file to the staging folder
            self._stage_command_file("update_metadata", update_soup,
                show_command=self.prefs.get('development_mode', False))

            # Wait for completion
            self._wait_for_command_completion("update_metadata")

        # Remove download_pending flags
        for v in self.cached_books.itervalues():
            if 'download_pending' in v:
                del v['download_pending']

        # Update local copy of mainDb
        self._localize_database_path(self.books_subpath)

        if (self.malformed_books or self.skipped_books or
            self.metadata_updates or self.rejected_books or self.replaced_books):
            self._report_upload_results(len(files))

        # Remove rejected books
        for rb in self.rejected_books:
            del self.cached_books[rb]
            for nb in new_booklist:
                if nb.path == rb:
                    new_booklist.remove(nb)
                    break

        return (new_booklist, [], [])

    # helpers
    def _cover_to_thumb(self, metadata):
        '''
        Generate a cover thumb matching the size retrieved from Marvin's cover cache
        SmallCoverJpg: 180x270
        LargeCoverJpg: 450x675
        '''
        from PIL import Image as PILImage

        MARVIN_COVER_WIDTH = 180
        MARVIN_COVER_HEIGHT = 270

        self._log_location(metadata.title)

        thumb = None

        if metadata.cover:
            try:
                # Resize for local thumb
                im = PILImage.open(metadata.cover)
                im = im.resize((MARVIN_COVER_WIDTH, MARVIN_COVER_HEIGHT), PILImage.ANTIALIAS)
                of = cStringIO.StringIO()
                im.convert('RGB').save(of, 'JPEG')
                thumb = of.getvalue()
                of.close()

            except:
                self._log("ERROR converting '%s' to thumb for '%s'" % (metadata.cover, metadata.title))
                import traceback
                traceback.print_exc()
        else:
            self._log("ERROR: no cover available for '%s'" % metadata.title)
        return thumb

    def _create_cover_element(self, mi, soup):
        '''
        Return a <cover> element from mi
        '''
        #self._log_location()
        cover_tag = None
        if mi.has_cover and mi.cover:
            with open(mi.cover, 'rb') as f:
                cover_bytes = f.read()
            sized_thumb = thumbnail(cover_bytes,
                                    self.THUMBNAIL_HEIGHT,
                                    self.THUMBNAIL_HEIGHT)
            cover_hash = hashlib.md5(sized_thumb[2]).hexdigest()

            cover_tag = Tag(soup, 'cover')
            cover_tag['hash'] = cover_hash
            cover_tag['encoding'] = 'base64'
            cover_tag.insert(0, base64.b64encode(cover_bytes))
        return cover_tag

    def _create_new_book(self, fpath, metadata, metadata_x, thumb, metadata_only):
        '''
        Need original metadata for id, uuid
        Need metadata_x for transformed title, author
        metadata.cover: (tmp) path to cover (jpg)
        '''
        from calibre import strftime
        from calibre.ebooks.metadata import authors_to_string

        self._log_location("title: %s uuid: %s" % (repr(metadata_x.title), repr(metadata.uuid)))

        this_book = Book(metadata_x.title, authors_to_string(metadata_x.authors))
        this_book.author_sort = metadata_x.author_sort
        this_book.uuid = metadata.uuid

        cover_hash = 0

        if metadata.has_cover and metadata.cover:
            # Generate cover_hash from cover.jpg
            with open(metadata.cover, 'rb') as f:
                cover_bytes = f.read()
            try:
                sized_thumb = thumbnail(cover_bytes,
                                        self.THUMBNAIL_HEIGHT,
                                        self.THUMBNAIL_HEIGHT)
                cover_hash = hashlib.md5(sized_thumb[2]).hexdigest()
            except:
                if cover_bytes:
                    self._log("error calculating cover_hash")
                else:
                    self._log("no cover available for %s" % this_book.title)

        this_book.cover_hash = cover_hash

        this_book.datetime = time.gmtime()
        #this_book.cid = metadata.id
        this_book.description = metadata_x.comments
        this_book.device_collections = self._get_field_items(metadata)
        if not metadata_only:
            this_book.device_collections.append('NEW')

        if this_book.uuid in self.active_flags.keys():
            this_book.device_collections = sorted(self.active_flags[this_book.uuid] +
                                                  this_book.device_collections,
                                                  key=sort_key)

        this_book.format = format
        this_book.path = self.path_template.format(metadata.uuid)
        #this_book.pubdate = strftime("%Y-%m-%d", t=metadata_x.pubdate)
        this_book.pubdate = metadata_x.pubdate
        this_book.publisher = metadata_x.publisher
        this_book.series = metadata_x.series
        this_book.series_index = metadata_x.series_index
        this_book.size = os.stat(fpath).st_size
        this_book.tags = metadata_x.tags
        this_book.thumbnail = thumb
        this_book.title_sort = metadata_x.title_sort
        this_book.word_count = None
        wc_lookup = self.prefs.get('marvin_word_count_lookup')
        if wc_lookup:
            try:
                this_book.word_count = metadata.metadata_for_field("#%s" % wc_lookup)['#value#']
            except:
                pass
        return this_book

    def _evaluate_original_cover(self, mi):
        '''
        Guess whether available cover is original or user-replaced based on timestamps
        '''
        self._log_location()

        epub_path = mi.format_metadata['EPUB']['path']
        cover_path = os.path.join(os.path.dirname(epub_path), "cover.jpg")
        if not os.path.exists(cover_path):
            self._log("no cover found")
            return None

        cover_mtime = datetime.utcfromtimestamp(os.stat(cover_path).st_mtime)
        epub_mtime = mi.format_metadata['EPUB']['mtime'].replace(tzinfo=None)

        diff = cover_mtime - epub_mtime
        cover_is_original = (diff.seconds == 0)
        if False:
            self._log(" epub mtime: %s" % repr(epub_mtime))
            self._log("cover mtime: %s" % repr(cover_mtime))
            self._log("seconds: %s" % repr(diff.seconds))
            self._log("returning: %s" % (diff.seconds == 0))

        if cover_is_original:
            self._log("original cover in calibre db")
        else:
            self._log("cover has been replaced in calibre db")

        return cover_is_original

    def _evaluate_replaceable_cover(self, path_to_book):
        '''
        Return True if cover is replaceable
        '''
        def _raster_cover(opf_xml):
            covers = opf_xml.xpath(r'child::opf:metadata/opf:meta[@name="cover" and @content]',
                                   namespaces={'opf':OPF_NS})
            if covers:
                cover_id = covers[0].get('content')
                items = opf_xml.xpath(r'child::opf:manifest/opf:item',
                                      namespaces={'opf':OPF_NS})
                for item in items:
                    if item.get('id', None) == cover_id:
                        mt = item.get('media-type', '')
                        if 'xml' not in mt:
                            return item.get('href', None)
                for item in items:
                    if item.get('href', None) == cover_id:
                        mt = item.get('media-type', '')
                        if mt.startswith('image/'):
                            return item.get('href', None)

        self._log_location()
        try:
            with ZipFile(path_to_book, 'r') as zf:
                opf_name = self._get_opf_xml(path_to_book, zf)
                if not opf_name:
                    self._log('No OPF file')
                    return False
                opf_xml = self._get_opf_tree(zf, opf_name)
                rcover = _raster_cover(opf_xml)
                if not rcover:
                    self._log('No supported meta tag or non-xml cover')
                    return False
                cpath = posixpath.join(posixpath.dirname(opf_name), rcover)
                image_extension = os.path.splitext(cpath)[1].lower()
                if image_extension not in ('.png', '.jpg', '.jpeg'):
                    self._log('Invalid cover image extension (%s)' % image_extension)
                    return False
            self._log('cover is replaceable')
            return True

        except InvalidEpub as e:
            self._log('Invalid epub')
            return False
        except:
            self._log('ERROR parsing book')
            import traceback
            self._log(traceback.format_exc())
            return False

    def _get_field_items(self, mi):
        '''
        Return the metadata from collection_fields for mi

        Collection fields may be supported custom fields:
            'Comma separated text, like tags, shown in the browser'
            'Text, column shown in the tag browser'
            'Text, but with a fixed set of permitted values'
        '''
        verbose = self.prefs.get('development_mode', False)
        self._log_location(mi.title)

        field_items = []
        collection_field = self.prefs.get('marvin_collection_field', None)
        if collection_field:
            if verbose:
                self._log("collection_field: %s" % collection_field)

            # Build a map of name:field for eligible custom fields
            eligible_custom_fields = {}
            for cf in mi.get_all_user_metadata(False):
                if mi.metadata_for_field(cf)['datatype'] in ['enumeration', 'text']:
                    eligible_custom_fields[mi.metadata_for_field(cf)['name'].lower()] = cf

            # Collect the field items for the specified collection field
            if collection_field.lower() in eligible_custom_fields:
                value = mi.get(eligible_custom_fields[collection_field.lower()])
                if value:
                    if type(value) is list:
                        field_items += value
                    elif type(value) in [str, unicode]:
                        field_items.append(value)
                    else:
                        self._log_location("Unexpected type: '%s'" % type(value))
            else:
                self._log_location("'%s': Invalid metadata field specified as collection source: '%s'" %
                                   (mi.title, collection_field))

            # Strip flag value, managed only in Marvin
            flags_to_strip = []
            for item in field_items:
                if item.upper() in self.flags.values():
                    flags_to_strip.append(item)
            for flag in flags_to_strip:
                field_items.remove(flag)

            if verbose:
                self._log("collections: %s" % field_items)

        return field_items

    def _get_opf_tree(self, zf, opf_name):
        data = zf.read(opf_name)
        data = re.sub(r'http://openebook.org/namespaces/oeb-package/1.0/',
                OPF_NS, data)
        return self._parse_xml(data)

    def _get_opf_xml(self, path_to_book, zf):
        contents = zf.namelist()
        if 'META-INF/container.xml' not in contents:
            raise InvalidEpub('Missing container.xml from %s' % path_to_book)
        container = self._parse_xml(zf.read('META-INF/container.xml'))
        opf_files = container.xpath((r'child::ocf:rootfiles/ocf:rootfile'
                                      '[@media-type="%s" and @full-path]'%guess_type('a.opf')[0]
                                     ), namespaces={'ocf':OCF_NS})
        if not opf_files:
            raise InvalidEpub('Could not find OPF in %s' % path_to_book)
        opf_name = opf_files[0].attrib['full-path']
        if opf_name not in contents:
            raise InvalidEpub('OPF file in container.xml not found in:%s'%path_to_book)
        return opf_name

    def _localize_database_path(self, remote_db_path):
        '''
        Copy remote_db_path from iOS to local storage as needed
        '''
        self._log_location()

        local_db_path = None
        db_stats = {}

        db_stats = self.ios.stat(remote_db_path)
        if db_stats:
            path = remote_db_path.split('/')[-1]
            if iswindows:
                from calibre.utils.filenames import shorten_components_to
                plen = len(self.temp_dir)
                path = ''.join(shorten_components_to(245-plen, [path]))

            full_path = os.path.join(self.temp_dir, path)

            # Test remote file metadata to confirm we're up to date - size and mtime
            if os.path.exists(full_path):
                lfs = os.stat(full_path)
                if int(db_stats['st_mtime']) == lfs.st_mtime:
                    self._log('st_mtime matches: %d' % lfs.st_mtime)
                    if int(db_stats['st_size']) == lfs.st_size:
                        self._log('st_size matches: {:,}'.format(lfs.st_size))
                        local_db_path = full_path
                        self._log("local_db is current")

#                 if (int(db_stats['st_mtime']) == lfs.st_mtime and
#                     int(db_stats['st_size']) == lfs.st_size):
#                     local_db_path = full_path

            # If we don't have a valid local copy, update from iDevice
            if not local_db_path:
                self._log("updating local_db from %s" % repr(remote_db_path))
                with open(full_path, 'wb') as out:
                    self.ios.copy_from_idevice(remote_db_path, out)
                local_db_path = out.name
        else:
            raise DatabaseNotFoundException("'%s' not found" % remote_db_path)

        self.local_db_path = local_db_path
        return local_db_path

    def _parse_xml(self, data):
        data = xml_to_unicode(data, strip_encoding_pats=True, assume_utf8=True,
                             resolve_entities=True)[0].strip()
        return etree.fromstring(data, parser=RECOVER_PARSER)

    def _remove_existing_copy(self, path, metadata):
        '''
        '''
        pop_list = []
        for book in self.cached_books:

            # Ignore books currently being downloaded
            if 'download_pending' in self.cached_books[book]:
                continue

            matched = False
            if self.cached_books[book]['uuid'] == metadata.uuid:
                matched = True
                self._log_location("'%s' matched on uuid '%s'" % (metadata.title, metadata.uuid))
            elif (self.cached_books[book]['title'] == metadata.title and
                  self.cached_books[book]['authors'] == metadata.authors):
                matched = True
                self._log_location("'%s' matched on author '%s'" % (metadata.title, metadata.authors))
            if matched:
                if path not in self.cached_books:
                    # If book was originally loaded into Marvin outside of this driver,
                    # path won't match, so we need to find the original path name
                    self._log("path not found in self.cached_books: %s" % repr(path))
                    for path in self.cached_books:
                        if (self.cached_books[path]['title'] == metadata.title and
                            self.cached_books[path]['authors'] == metadata.authors):
                            self._log("actual path: %s" % repr(path))
                            self.update_list.append(self.cached_books[path])
                            pop_list.append(book)
                            self.delete_books([path])
                            break
                    else:
                        self._log("ERROR: Unable to remove %s from self.cached_books" % repr(metadata.title))

                else:
                    self.update_list.append(self.cached_books[book])
                    self.delete_books([path])
                    break

        # Remove any books whose path changed
        for book in pop_list:
            self.cached_books.pop(book)

    def _report_upload_results(self, total_sent):
        '''
        Display results of upload operation
        We can have skipped books or replaced books, or updated metadata
        If there were errors (malformed ePubs), that takes precedence.
        '''
        self._log_location("total_sent: %d" % total_sent)

        title = "Send to device"
        total_added = (total_sent - len(self.malformed_books) - len(self.skipped_books) -
                       len(self.replaced_books) - len(self.metadata_updates) -
                       len(self.rejected_books))
        details = ''
        if total_added:
            details = "{0} {1} successfully added to Marvin.\n\n".format(total_added,
                                                          'books' if total_added > 1 else 'book')

        if self.malformed_books or self.rejected_books:
            msg = ("Warnings reported while sending to Marvin.\n" +
                            "Click 'Show details' for a summary.\n")

            if self.malformed_books:
                details += u"The following malformed {0} not added to Marvin:\n".format(
                            'books were' if len(self.malformed_books) > 1 else 'book was')
                for book in self.malformed_books:
                    details += u" - '{0}' by {1}\n".format(book['title'],
                                                          ','.join(book['authors']))

            if self.rejected_books:
                details += u"The following {0} rejected by Marvin:\n".format(
                            'books were' if len(self.rejected_books) > 1 else 'book was')
                for book in self.rejected_books:
                    details +=  u" - '{0}' by {1}\n".format(
                        self.cached_books[book]['title'],
                        ', '.join(self.cached_books[book]['authors']))

            if self.skipped_books:
                details += u"\nThe following {0} already installed in Marvin:\n".format(
                            'books were' if len(self.skipped_books) > 1 else 'book was')
                for book in self.skipped_books:
                    details += u" - '{0}' by {1}\n".format(book['title'],
                                                       ', '.join(book['authors']))
                details += "\nUpdate behavior may be changed in the plugin's Marvin Options settings."
            elif self.replaced_books:
                details += u"\nThe following {0} replaced in Marvin:\n".format(
                            'books were' if len(self.replaced_books) > 1 else 'book was')
                for book in self.replaced_books:
                    details += u" + '{0}' by {1}\n".format(book['title'],
                                                       ', '.join(book['authors']))
                details += "\nReplacement behavior may be changed in the plugin's Marvin Options settings."
            elif self.metadata_updates:
                details += u"\nMetadata was updated for the following {0}:\n".format(
                            'books' if len(self.metadata_updates) > 1 else 'book')
                for book in self.metadata_updates:
                    details += u" + '{0}' by {1}\n".format(book['title'],
                                                           ', '.join(book['authors']))
                details += "\nUpdate behavior may be changed in the plugin's Marvin Options settings."

        # If we skipped any books during upload_books due to overwrite switch, inform user
        elif self.skipped_books:
            msg = ("Replacement of existing books is disabled in the plugin's Marvin Options settings.\n"
                   "Click 'Show details' for a summary.\n")

            details += u"The following {0} already installed in Marvin:\n".format(
                            'books were' if len(self.skipped_books) > 1 else 'book was')
            for book in self.skipped_books:
                details += u" - '{0}' by {1}\n".format(book['title'],
                                                       ', '.join(book['authors']))
            details += "\nOverwrite behavior may be changed in the plugin's Marvin Options settings."

        # If we replaced any books, inform user
        elif self.replaced_books:
            msg = ("{0} {1} replaced in Marvin.\n".format(len(self.replaced_books),
                            'books were' if len(self.replaced_books) > 1 else 'book was') +
                            "Click 'Show details' for a summary.\n")

            details += u"The following {0} replaced in Marvin:\n".format(
                            'books were' if len(self.replaced_books) > 1 else 'book was')
            for book in self.replaced_books:
                details += u" + '{0}' by {1}\n".format(book['title'],
                                                       ', '.join(book['authors']))
            details += "\nReplacement behavior may be changed in the plugin's Marvin Options settings."

        # If we updated metadata, inform user
        elif self.metadata_updates:
            msg = ("Updated metadata for {0} {1}.\n".format(len(self.metadata_updates),
                                                           'books' if len(self.metadata_updates) > 1 else 'book') +
                  "Click 'Show details' for a summary.\n")

            details += u"Metadata was updated for the following {0}:\n".format(
                       'books' if len(self.metadata_updates) > 1 else 'book')
            for book in self.metadata_updates:
                details += u" + '{0}' by {1}\n".format(book['title'],
                                                   ', '.join(book['authors']))
            details += "\nUpdate behavior may be changed in the plugin's Marvin Options settings."

        self.user_feedback_after_callback = {
              'title': title,
                'msg': msg,
            'det_msg': details
            }

    def _reset_ios_connection(self,
                              app_installed=False,
                              device_name=None,
                              ejected=False,
                              udid=0,
                              verbose=False):
        if verbose:
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

    def _schedule_metadata_update(self, target_epub, book, update_soup):
        '''
        Generate metadata update content for individual book
        '''
        from xml.sax.saxutils import escape
        from calibre import strftime

        self._log_location(book.title)

        book_tag = Tag(update_soup, 'book')
        book_tag['author'] = escape(', '.join(book.authors))
        book_tag['authorsort'] = escape(book.author_sort)
        book_tag['filename'] = escape(target_epub)

        if book.pubdate is not None:
            naive = book.pubdate.replace(hour=0, minute=0, second=0, tzinfo=None)
            book_tag['pubdate'] = strftime('%Y-%m-%d', t=naive)
        book_tag['publisher'] = ''
        if book.publisher is not None:
            book_tag['publisher'] = escape(book.publisher)
        book_tag['series'] = ''
        if book.series:
            book_tag['series'] = escape(book.series)
        book_tag['seriesindex'] = ''
        if book.series_index:
           book_tag['seriesindex'] = book.series_index
        book_tag['title'] = escape(book.title)
        book_tag['titlesort'] = escape(book.title_sort)
        book_tag['uuid'] = book.uuid
        wc_lookup = self.prefs.get('marvin_word_count_lookup')
        if wc_lookup:
            try:
                book_tag['wordcount'] = book.metadata_for_field("#%s" % wc_lookup)['#value#']
            except:
                pass

        # Cover
        if book.has_cover:
            cover_tag = self._create_cover_element(book, update_soup)
            cover_hash = cover_tag['hash']

            if self.cached_books[target_epub]['cover_hash'] != cover_hash:
                self._log("%s" % (target_epub))
                self._log(" cover: (device) %s != (library) %s" %
                                     (self.cached_books[target_epub]['cover_hash'], cover_hash))
                self.cached_books[target_epub]['cover_hash'] = cover_hash
                book_tag.insert(0, cover_tag)
            else:
                self._log(" '%s': cover is up to date" % book.title)

        else:
            self._log(">>>no cover available for '%s'<<<" % book.title)

        # Add the subjects
        subjects_tag = Tag(update_soup, 'subjects')
        for tag in sorted(book.tags, reverse=True):
            subject_tag = Tag(update_soup, 'subject')
            subject_tag.insert(0, escape(tag))
            subjects_tag.insert(0, subject_tag)
        book_tag.insert(0, subjects_tag)


        # Add the collections
        # If no custom column(s) for collections, preserve existing Marvin collection assignments
        # JSON switch 'marvin_merge_collections' controls whether to merge or replace existing collections
        collection_fields = list(self.prefs.get('marvin_collection_field', ''))
        if collection_fields:
            if self.prefs.get('marvin_merge_collections', True):
                # Append calibre collection assignments to existing Marvin collections, with existing flags
                cas = set(self.cached_books[target_epub]['device_collections'] + self._get_field_items(book))
                collection_assignments = sorted(list(cas), key=sort_key)
            else:
                # Replace existing Marvin collections with calibre collection assignments
                # Preserve existing flags
                cached_assignments = self.cached_books[target_epub]['device_collections']
                active_flags = []
                for flag in self.flags.values():
                    if flag in cached_assignments:
                        active_flags.append(flag)
                collection_assignments = sorted(active_flags + self._get_field_items(book), key=sort_key)

        else:
            # Preserve existing Marvin assignments
            collection_assignments = sorted(self.cached_books[target_epub]['device_collections'], key=sort_key)

        # Update the local cache
        self.cached_books[target_epub]['device_collections'] = collection_assignments

        collections_tag = Tag(update_soup, 'collections')
        if collection_assignments:
            for tag in collection_assignments:
                c_tag = Tag(update_soup, 'collection')
                c_tag.insert(0, escape(tag))
                collections_tag.insert(0, c_tag)
        book_tag.insert(0, collections_tag)

        # Add the description
        try:
            description_tag = Tag(update_soup, 'description')
            description_tag.insert(0, escape(book.comments))
            book_tag.insert(0, description_tag)
        except:
            pass

        update_soup.manifest.insert(0, book_tag)

    def _stage_command_file(self, command_name, command_soup, show_command=False):
        fl = locale.format("%d", len(command_soup.renderContents()), grouping=True)
        self._log_location("%s: %s bytes" % (command_name, fl))

        if show_command:
            if command_name in ['update_metadata', 'upload_books']:
                soup = BeautifulStoneSoup(command_soup.renderContents())
                # <descriptions>
                descriptions = soup.findAll('description')
                for description in descriptions:
                    d_tag = Tag(soup, 'description')
                    d_tag.insert(0, "(description removed for debug stream)")
                    description.replaceWith(d_tag)
                # <covers>
                covers = soup.findAll('cover')
                for cover in covers:
                    cover_tag = Tag(soup, 'cover')
                    cover_tag['encoding'] = cover['encoding']
                    cover_tag['hash'] = cover['hash']
                    cover_tag.insert(0, "(cover data removed for debug stream)")
                    cover.replaceWith(cover_tag)
                self._log(soup.prettify())
            else:
                self._log(command_soup.prettify())

        self.ios.write(command_soup.renderContents(),
                       b'/'.join([self.staging_folder, b'%s.tmp' % command_name]))
        self.ios.rename(b'/'.join([self.staging_folder, b'%s.tmp' % command_name]),
                        b'/'.join([self.staging_folder, b'%s.xml' % command_name]))

    def _update_epub_metadata(self, fpath, metadata):
        '''
        Apply plugboard metadata transforms to book
        Return transformed metadata
        '''
        from calibre import strftime
        from calibre.ebooks.metadata.epub import set_metadata

        self._log_location(metadata.title)

        # Fetch plugboard transforms
        metadata_x = self._xform_metadata_via_plugboard(metadata, 'epub')

        # Refresh epub metadata
        with open(fpath, 'r+b') as zfo:
            if False:
                try:
                    zf_opf = ZipFile(fpath, 'r')
                    fnames = zf_opf.namelist()
                    opf = [x for x in fnames if '.opf' in x][0]
                except:
                    raise UserFeedback("'%s' is not a valid EPUB" % metadata.title,
                                       None,
                                       level=UserFeedback.WARN)

                #Touch the OPF timestamp
                opf_tree = etree.fromstring(zf_opf.read(opf))
                md_els = opf_tree.xpath('.//*[local-name()="metadata"]')
                if md_els:
                    ts = md_els[0].find('.//*[@name="calibre:timestamp"]')
                    if ts is not None:
                        timestamp = ts.get('content')
                        old_ts = parse_date(timestamp)
                        metadata.timestamp = datetime.datetime(old_ts.year, old_ts.month, old_ts.day, old_ts.hour,
                                                   old_ts.minute, old_ts.second, old_ts.microsecond + 1, old_ts.tzinfo)
                        if DEBUG:
                            logger().info("   existing timestamp: %s" % metadata.timestamp)
                    else:
                        metadata.timestamp = now()
                        if DEBUG:
                            logger().info("   add timestamp: %s" % metadata.timestamp)

                else:
                    metadata.timestamp = now()
                    if DEBUG:
                        logger().warning("   missing <metadata> block in OPF file")
                        logger().info("   add timestamp: %s" % metadata.timestamp)

                zf_opf.close()

            # If 'News' in tags, tweak the title/author for friendlier display in iBooks
            if _('News') in metadata_x.tags or \
               _('Catalog') in metadata_x.tags:
                if metadata_x.title.find('[') > 0:
                    metadata_x.title = metadata_x.title[:metadata_x.title.find('[') - 1]
                date_as_author = '%s, %s %s, %s' % (strftime('%A'), strftime('%B'), strftime('%d').lstrip('0'), strftime('%Y'))
                metadata_x.author = metadata_x.authors = [date_as_author]
                sort_author = re.sub('^\s*A\s+|^\s*The\s+|^\s*An\s+', '', metadata_x.title).rstrip()
                metadata_x.author_sort = '%s %s' % (sort_author, strftime('%Y-%m-%d'))

            if False:
                # If windows & series, nuke tags so series used as Category during _update_iTunes_metadata()
                if iswindows and metadata_x.series:
                    metadata_x.tags = None

            set_metadata(zfo, metadata_x, apply_null=True, update_timestamp=True)

        return metadata_x

    def _wait_for_command_completion(self, command_name, send_signal=True):
        '''
        Wait for Marvin to issue progress reports via status.xml
        Marvin creates status.xml upon receiving command, increments <progress>
        from 0.0 to 1.0 as command progresses.
        '''
        import traceback
        from threading import Timer

        self._log_location(command_name)
        self._log("%s: waiting for '%s'" %
                                     (datetime.now().strftime('%H:%M:%S.%f'),
                                     self.status_fs))

        # Set initial watchdog timer for ACK
        WATCHDOG_TIMEOUT = 15.0
        POLLING_DELAY = 1.0
        watchdog = Timer(WATCHDOG_TIMEOUT, self._watchdog_timed_out)
        self.operation_timed_out = False
        watchdog.start()

        while True:
            if not self.ios.exists(self.status_fs):
                # status.xml not created yet
                if self.operation_timed_out:
                    self.ios.remove(self.status_fs)
                    raise UserFeedback("Marvin operation timed out.",
                                        details=None, level=UserFeedback.WARN)
                time.sleep(POLLING_DELAY)
                Application.processEvents()

            else:
                watchdog.cancel()

                self._log("%s: monitoring progress of %s" %
                                     (datetime.now().strftime('%H:%M:%S.%f'),
                                      command_name))

                # Start a new watchdog timer per iteration
                watchdog = Timer(WATCHDOG_TIMEOUT, self._watchdog_timed_out)
                self.operation_timed_out = False
                watchdog.start()

                code = '-1'
                current_timestamp = 0.0
                while code == '-1':
                    try:
                        if self.operation_timed_out:
                            self.ios.remove(self.status_fs)
                            raise UserFeedback("Marvin operation timed out.",
                                                details=None, level=UserFeedback.WARN)

                        status = etree.fromstring(self.ios.read(self.status_fs))
                        code = status.get('code')
                        timestamp = float(status.get('timestamp'))
                        if timestamp != current_timestamp:
                            watchdog.cancel()
                            current_timestamp = timestamp
                            d = datetime.now()
                            progress = float(status.find('progress').text)
                            self._log("{0}: {1:>2} {2:>3}%".format(
                                                 d.strftime('%H:%M:%S.%f'),
                                                 code,
                                                 "%3.0f" % (progress * 100)))

                            # Report progress
                            if self.report_progress is not None:
                                self.report_progress(0.5 + progress/2, '')

                            # Reset watchdog timer
                            watchdog = Timer(WATCHDOG_TIMEOUT, self._watchdog_timed_out)
                            watchdog.start()
                        time.sleep(POLLING_DELAY)
                        Application.processEvents()

                    except:
                        watchdog.cancel()

                        formatted_lines = traceback.format_exc().splitlines()
                        current_error = formatted_lines[-1]

                        time.sleep(POLLING_DELAY)
                        Application.processEvents()

                        self._log("{0}:  retry ({1})".format(
                            datetime.now().strftime('%H:%M:%S.%f'),
                            current_error))

                        # Reset watchdog timer
                        watchdog = Timer(WATCHDOG_TIMEOUT, self._watchdog_timed_out)
                        watchdog.start()

                # Command completed
                watchdog.cancel()

                final_code = status.get('code')
                final_status = None
                if final_code != '0':
                    if final_code == '-1':
                        final_status= "in progress"
                    if final_code == '1':
                        final_status = "warnings"
                    if final_code == '2':
                        final_status = "errors"

                    messages = status.find('messages')
                    msgs = [msg.text for msg in messages]

                    # Capture the rejected epubs to report
                    for msg in msgs:
                        self.rejected_books.append(re.search('\[(.+)\]', msg).group(1))

                    details = "code: %s\n" % final_code
                    details += '\n'.join(msgs)
                    self._log(details)

                self.ios.remove(self.status_fs)

                self._log("%s: '%s' complete" %
                          (datetime.now().strftime('%H:%M:%S.%f'),
                           command_name))
                break

        if self.report_progress is not None:
            self.report_progress(1.0, _('finished'))

        # Emit a signal for Marvin Manager
        if send_signal:
            self.marvin_device_signals.reader_app_status_changed.emit(command_name)

    def _watchdog_timed_out(self):
        '''
        Set flag if I/O operation times out
        '''
        self._log_location(datetime.now().strftime('%H:%M:%S.%f'))
        self.operation_timed_out = True

    def _xform_metadata_via_plugboard(self, book, format):
        '''
        '''
        self._log_location(book.title)

        if self.plugboard_func:
            pb = self.plugboard_func(self.DEVICE_PLUGBOARD_NAME, format, self.plugboards)
            newmi = book.deepcopy_metadata()
            newmi.template_to_attribute(book, pb)
            if pb is not None and self.verbose:
                #self._log("transforming %s using %s:" % (format, pb))
                self._log("       title: %s %s" % (book.title, ">>> '%s'" %
                                           newmi.title if book.title != newmi.title else ''))
                self._log("  title_sort: %s %s" % (book.title_sort, ">>> %s" %
                                           newmi.title_sort if book.title_sort != newmi.title_sort else ''))
                self._log("     authors: %s %s" % (book.authors, ">>> %s" %
                                           newmi.authors if book.authors != newmi.authors else ''))
                self._log(" author_sort: %s %s" % (book.author_sort, ">>> %s" %
                                           newmi.author_sort if book.author_sort != newmi.author_sort else ''))
                self._log("    language: %s %s" % (book.language, ">>> %s" %
                                           newmi.language if book.language != newmi.language else ''))
                self._log("   publisher: %s %s" % (book.publisher, ">>> %s" %
                                           newmi.publisher if book.publisher != newmi.publisher else ''))
                self._log("        tags: %s %s" % (book.tags, ">>> %s" %
                                           newmi.tags if book.tags != newmi.tags else ''))
            else:
                self._log("  no matching plugboard")
        else:
            newmi = book
        return newmi


