#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import (unicode_literals, division, absolute_import,
                        print_function)

import base64, cStringIO, json, os, sqlite3, time
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

        # ~~~~~~~~~ Constants ~~~~~~~~~
        # None indicates that the driver supports backloading from device to library
        self.BACKLOADING_ERROR_MESSAGE = None

        # Plugboards
        self.CAN_DO_DEVICE_DB_PLUGBOARD = False
        self.DEVICE_PLUGBOARD_NAME = 'GOODREADER'

        # Which metadata on books can be set via the GUI.
        self.CAN_SET_METADATA = ['title', 'authors']

        # ~~~~~~~~~ Variables ~~~~~~~~~
        self.busy = False
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
            self._log("adding %s to booklists[0]" % new_book)
            booklists[0].append(new_book)
        if False:
            for book in booklists[0]:
                self._log(" '%s' by %s %s" % (book.title, book.authors, book.path))

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
            cached_books = {}

            # Get a local copy of metadata db. If it doesn't exist on device, create it
            db_profile = self._localize_database_path(self.remote_metadata)
            self.local_metadata = db_profile['path']
            con = sqlite3.connect(self.local_metadata)
            with con:
                con.row_factory = sqlite3.Row
                cur = con.cursor()
                cur.execute('''SELECT
                                filename
                               FROM metadata
                            ''')
                rows = cur.fetchall()
                cached_books = [row[b'filename'] for row in rows]

                # Fetch installed books from /Documents
                installed_books = self.ios.listdir(b'/Documents')
                for i, book in enumerate(installed_books):
                    if book in cached_books:
                        # Retrieve the cached metadata
                        this_book = self._get_cached_metadata(cur, book)
                        booklist.add_book(this_book, False)
                    else:
                        # Make a local copy of the book, get the stats
                        pdf_stats = self._localize_pdf('/'.join(['/Documents', book]))
                        this_book = self._get_metadata(book, pdf_stats)
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
                                        (' & '.join(this_book.authors),
                                         this_book.author_sort,
                                         this_book.dateadded,
                                         this_book.path,
                                         this_book.size,
                                         this_book.thumb_data,
                                         this_book.title,
                                         this_book.title_sort,
                                         this_book.uuid)
                                        )
                    if self.report_progress is not None:
                        self.report_progress(float((i + 1)*100 / len(installed_books))/100,
                            '%(num)d of %(tot)d' % dict(num=i + 1, tot=len(installed_books)))

                # Remove orphans (books no longer in GoodReader) from db
                s = set(installed_books)
                orphans = [x for x in cached_books if x not in s]

                if orphans:
                    for book in orphans:
                        # Remove from db, update device copy
                        self._log("Removing orphan %s from metadata" % json.dumps(book))
                        cur.execute('''DELETE FROM metadata
                                       WHERE filename = {0}
                                    '''.format(json.dumps(book)))

                cur.close()
                con.commit()

                # Copy the updated db to the iDevice
                self.ios.copy_to_idevice(str(self.local_metadata), str(self.remote_metadata))

            if self.report_progress is not None:
                self.report_progress(1.0, 'finished')

            self.cached_books = cached_books

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
                self.ios_connection['app_installed'] = self.ios.mount_ios_app(app_id=self.preferred_app_id)
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
        self._log("cached_books: %s" % self.cached_books)

        file_count = float(len(paths))

        for i, path in enumerate(paths):
            self._log("removing %s" % repr(path))
            ios_path = '/'.join(['/Documents', path])
            self.ios.remove(ios_path)

        # Update the db
        con = sqlite3.connect(self.local_metadata)
        with con:
            for book in paths:
                # Remove from db, update device copy
                self._log("Removing %s from local_metadata" % json.dumps(book))
                with con:
                    con.row_factory = sqlite3.Row
                    cur = con.cursor()
                    cur.execute('''DELETE FROM metadata
                                   WHERE filename = {0}
                                '''.format(json.dumps(book)))
            con.commit()

        # Copy the updated db to the iDevice
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
        self.ios.copy_from_idevice('/'.join(['/Documents', path]), outfile)

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
        tdir = PersistentTemporaryDirectory('_prepare_goodreader')
        ans = []
        for path in paths:
            if not self.ios.exists('/'.join(['/Documents', path])):
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

        '''
        self._log_location()
        for path in paths:
            for i, bl_book in enumerate(booklists[0]):
                if bl_book.path == path:
                    self._log("matched path: %s" % repr(bl_book.path))
                    booklists[0].pop(i)

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
        from calibre.ebooks.metadata import authors_to_string

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
                                   WHERE filename = {}
                                '''.format(json.dumps(book.path)))
                    cached_book = cur.fetchall()[0]
                    if (book.title != cached_book[b'title'] or
                        book.authors != [cached_book[b'authors']]):
                        self._log("%s: metadata has been updated" % book.path)
                        self._log("booklist: %s %s" % (book.title, book.authors))
                        self._log("database: %s %s" % (cached_book[b'title'], [cached_book[b'authors']]))

                        cur.execute('''UPDATE metadata
                                       SET authors = {0},
                                           title = {1}
                                       WHERE filename = {2}
                                    '''.format(json.dumps(' & '.join(book.authors)),
                                               json.dumps(book.title),
                                               json.dumps(book.path)))
                con.commit()

            # Copy the updated db to the iDevice
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
                destination = '/'.join(['/Documents', self.path_template.format(metadata[i].title)])
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
                                (' & '.join(this_book.authors),
                                 this_book.author_sort,
                                 this_book.dateadded,
                                 this_book.path,
                                 this_book.size,
                                 this_book.thumb_data,
                                 this_book.title,
                                 this_book.title_sort,
                                 this_book.uuid)
                                )
                if self.report_progress is not None:
                    self.report_progress(float((i + 1)*100 / len(files))/100,
                        '%(num)d of %(tot)d' % dict(num=i + 1, tot=len(files)))

            cur.close()
            con.commit()

        # Copy the updated db to the iDevice
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

        COVER_WIDTH = 180
        COVER_HEIGHT = 270

        self._log_location(metadata.title)

        thumb = None

        if hasattr(metadata, 'has_cover'):
            self._log("using existing cover")
            try:
                im = PILImage.open(metadata.cover)
                im = im.resize((COVER_WIDTH, COVER_HEIGHT), PILImage.ANTIALIAS)
                of = cStringIO.StringIO()
                im.convert('RGB').save(of, 'JPEG')
                thumb = of.getvalue()
                of.close()
            except:
                self._log("ERROR converting thumb for '%s'" % (metadata.title))
                import traceback
                traceback.print_exc()

        elif metadata.cover_data is not None:
            #self._log(repr(metadata.cover_data))
            self._log("generating cover from cover_data")
            try:
                # Resize for local thumb
                img_data = cStringIO.StringIO(metadata.cover_data[1])
                im = PILImage.open(img_data)
                #im = PILImage.fromstring("RGBA", (180,270), str(metadata.cover_data[1]))
                im = im.resize((COVER_WIDTH, COVER_HEIGHT), PILImage.ANTIALIAS)
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

    def _get_cached_metadata(self, cur, book):
        '''
        Return a populated Book object from a cached book's metadata
        '''
        self._log_location(json.dumps(book))

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
                        WHERE filename = {0}
                    '''.format(json.dumps(book)))
        cached_book = cur.fetchall()[0]
        #self._log(cached_book.keys())

        this_book = Book(cached_book[b'title'], cached_book[b'authors'])
        this_book.author_sort = cached_book[b'author_sort']
        this_book.datetime = datetime.fromtimestamp(cached_book[b'dateadded']).timetuple()
        this_book.path = cached_book[b'filename']
        this_book.size = cached_book[b'size']
        this_book.thumbnail = base64.b64decode(cached_book[b'thumb_data'])
        this_book.title_sort = cached_book[b'title_sort']
        this_book.uuid = cached_book[b'uuid']
        return this_book

    def _get_metadata(self, book, pdf_stats):
        '''
        Return a populated Book object with available metadata
        '''
        from calibre.ebooks.metadata import author_to_author_sort, authors_to_string, title_sort
        from calibre.ebooks.metadata.pdf import get_metadata
        self._log_location(book)

        with open(os.path.join(self.temp_dir, pdf_stats['path']), 'rb') as f:
            stream = cStringIO.StringIO(f.read())
            mi = get_metadata(stream)
        this_book = Book(mi.title, ' & '.join(mi.authors))
        this_book.author_sort = author_to_author_sort(mi.authors)
        this_book.datetime = datetime.fromtimestamp(int(pdf_stats['stats']['st_birthtime'])).timetuple()
        this_book.dateadded = int(pdf_stats['stats']['st_birthtime'])
        this_book.path = book
        this_book.size = int(pdf_stats['stats']['st_size'])
        this_book.thumbnail = self._cover_to_thumb(mi)
        this_book.thumb_data = base64.b64encode(this_book.thumbnail)
        this_book.title_sort = title_sort(mi.title)
        this_book.uuid = None

        if False:
            self._log("%s" % repr(this_book.path))
            self._log(" %s" % repr(this_book.authors))
            self._log(" %s" % repr(this_book.dateadded))
        return this_book

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
            return full_path

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
        pdf_stats = {}

        pdf_stats = self.ios.stat(remote_path)
        if pdf_stats:
            path = remote_path.split('/')[-1]
            if iswindows:
                from calibre.utils.filenames import shorten_components_to
                plen = len(self.temp_dir)
                path = ''.join(shorten_components_to(245-plen, [path]))

            full_path = os.path.join(self.temp_dir, path)
            if os.path.exists(full_path):
                lfs = os.stat(full_path)
                if (int(pdf_stats['st_mtime']) == lfs.st_mtime and
                    int(pdf_stats['st_size']) == lfs.st_size):
                    local_db_path = full_path

            if not local_path:
                with open(full_path, 'wb') as out:
                    self.ios.copy_from_idevice(remote_path, out)
                local_path = out.name
        else:
            self._log_location("'%s' not found" % remote_path)
            raise DatabaseNotFoundException

        return {'path': local_path, 'stats': pdf_stats}

