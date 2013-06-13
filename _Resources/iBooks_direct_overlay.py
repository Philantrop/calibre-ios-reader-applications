#!/usr/bin/env python
# coding: utf-8
from __future__ import (unicode_literals, division, absolute_import,
                        print_function)

import cStringIO, os, sqlite3, sys
from datetime import datetime
from PIL import Image as PILImage

from calibre import fit_image
from calibre.devices.idevice.parse_xml import XmlPropertyListParser
from calibre.utils.zipfile import ZipFile

from calibre_plugins.ios_reader_apps import Book, BookList

NSTimeIntervalSince1970 = 978307200.0

def books(self, oncard=None, end_session=True):
    '''
    Return a list of ebooks on the device.
    @param oncard:  If 'carda' or 'cardb' return a list of ebooks on the
                    specific storage card, otherwise return list of ebooks
                    in main memory of device. If a card is specified and no
                    books are on the card return empty list.
    @return: A BookList.

    '''
    if oncard:
        return BookList()

    self._log_location()
    booklist = BookList()
    cached_books = {}

    # Fetch current assets from Media folder
    assets_profile = self._localize_database_path(self.assets_subpath)

    #Fetch current metadata from iBooks's DB
    db_profile = self._localize_database_path(self.books_subpath)
    con = sqlite3.connect(db_profile['path'])

    # Mount the Media folder
    self.ios.mount_ios_media_folder()

    # Get Books.plist so we can find the covers
    books_plist = {}

    if True:
        raw_plist = XmlPropertyListParser().parse(self.ios.read('/Books/Sync/Books.plist'))['Books']
        for book in raw_plist:
            if not 'Path' in book:
                print(" No 'Path' element found for '%s' by '%s'" % (book['Name'], book['Artist']))
                #print(book)
                #print
                continue

            if 'Cover Path' in book:
                    books_plist['/'.join(['/Books', book['Path']])] = unicode('/'.join(['/Books', book['Path'], book['Cover Path']]))
            else:
                books_plist['/'.join(['/Books', book['Path']])] = unicode('/'.join(['/Books', 'Sync', 'Artwork', book['Persistent ID']]))

        # Process any outliers
        raw_plist = XmlPropertyListParser().parse(self.ios.read('/Books/Books.plist'))['Books']
        for book in raw_plist:
            if not 'Path' in book:
                print(" No 'Path' element found for '%s' by '%s'" % (book['Name'], book['Artist']))
                #print(book)
                #print
                continue

            # Don't overwrite existing cover_paths
            if not '/'.join(['/Books', book['Path']]) in books_plist:
                if 'Cover Path' in book and not ['/'.join(['/Books', book['Path']])] in book_plist:
                        books_plist['/'.join(['/Books', book['Path']])] = unicode('/'.join(['/Books', book['Path'], book['Cover Path']]))
                else:
                    books_plist['/'.join(['/Books', book['Path']])] = unicode('/'.join(['/Books', 'Sync', 'Artwork', book['Persistent ID']]))

        raw_plist = XmlPropertyListParser().parse(self.ios.read('/Books/Purchases/Purchases.plist'))['Books']
        for book in raw_plist:
            if not 'Path' in book:
                print(" No 'Path' element found for '%s' by '%s'" % (book['Name'], book['Artist']))
                print(book)
                print
                continue

            # Don't overwrite existing cover_paths
            if not '/'.join(['/Books', book['Path']]) in books_plist:
                if 'Cover Path' in book:
                        books_plist['/'.join(['/Books/Purchases', book['Path']])] = unicode('/'.join(['/Books/Purchases', book['Path'], book['Cover Path']]))
                else:
                    books_plist['/'.join(['/Books/Purchases', book['Path']])] = unicode('/'.join(['/Books', 'Sync', 'Artwork', book['Persistent ID']]))

    else:
        raw_plist = XmlPropertyListParser().parse(self.ios.read('/Books/Books.plist'))['Books']
        for book in raw_plist:
            if not 'Path' in book:
                print(" No 'Path' element found for '%s' by '%s'" % (book['Name'], book['Artist']))
                print(book)
                print
                continue

            if 'Cover Path' in book:
                    books_plist['/'.join(['/Books', book['Path']])] = unicode('/'.join(['/Books', book['Path'], book['Cover Path']]))
            else:
                books_plist['/'.join(['/Books', book['Path']])] = unicode('/'.join(['/Books', 'Sync', 'Artwork', book['Persistent ID']]))

        raw_plist = XmlPropertyListParser().parse(self.ios.read('/Books/Purchases/Purchases.plist'))['Books']
        for book in raw_plist:
            if not 'Path' in book:
                print(" No 'Path' element found for '%s' by '%s'" % (book['Name'], book['Artist']))
                print(book)
                print
                continue

            if 'Cover Path' in book:
                    books_plist['/'.join(['/Books/Purchases', book['Path']])] = unicode('/'.join(['/Books/Purchases', book['Path'], book['Cover Path']]))
            else:
                books_plist['/'.join(['/Books/Purchases', book['Path']])] = unicode('/'.join(['/Books', 'Sync', 'Artwork', book['Persistent ID']]))

    print(books_plist)

    with con:
        con.row_factory = sqlite3.Row
        # Build a collection map
        collections_map = {}

        # Get the books
        cur = con.cursor()
        #cur.execute("ATTACH DATABASE '{0}' as 'ASSETS'".format(assets_profile['path'])

        cur.execute('''SELECT ZASSETURL,
                              ZBOOKAUTHOR,
                              ZSORTAUTHOR,
                              ZBOOKTITLE,
                              ZSORTTITLE,
                              ZDATABASEKEY,
                              ZDATEADDED
                       FROM ZBKBOOKINFO
                       WHERE ZASSETURL LIKE 'file://localhost%' AND
                             ZASSETURL LIKE '%.epub/'
                    ''')
        rows = cur.fetchall()
        book_count = len(rows)
        for i, row in enumerate(rows):
            book_id = row[b'ZDATABASEKEY']

            # Get the collection assignments
            collections = []

            # Get the primary metadata
            this_book = Book(row[b'ZBOOKTITLE'], row[b'ZBOOKAUTHOR'])
            original_path = row[b'ZASSETURL']
            path = original_path[original_path.find('Media/') + len('Media'):-1]
            this_book.path = path.replace('%20', ' ')
            timestamp = int(row[b'ZDATEADDED']) + NSTimeIntervalSince1970
            this_book.datetime = datetime.fromtimestamp(timestamp).timetuple()
            this_book.device_collections = collections
            this_book.uuid = None
            this_book.thumbnail = self._generate_thumbnail(this_book, books_plist[this_book.path])

            # Retrieve folder size from cache or compute and cache
            try:
                zfr = ZipFile(self.folder_archive_path)
                file_size = zfr.read(this_book.path)
                this_book.size = int(file_size)
                self._log_diagnostic("returning folder size from cache")
            except:
                self._log_diagnostic("opening folder cache for appending")
                zfw = ZipFile(self.folder_archive_path, mode='a')
                stats = self.ios.stat(this_book.path)
                this_book.size = self.ios.get_folder_size(this_book.path)
                zfw.writestr(this_book.path, str(this_book.size))
                zfw.close()
            finally:
                zfr.close()

            booklist.add_book(this_book, False)

            if self.report_progress is not None:
                self.report_progress(float((i + 1)*100 / book_count)/100,
                    '%(num)d of %(tot)d' % dict(num=i + 1, tot=book_count))

            cached_books[this_book.path] = {
                'title': this_book.title,
                'author': this_book.author,
                'authors': this_book.author.split(' & '),
                'uuid': this_book.uuid
                }
        cur.close()

    # Close the connection
    self.ios.dismount_ios_media_folder()

    if self.report_progress is not None:
        self.report_progress(1.0, _('finished'))

    self.cached_books = cached_books
    return booklist

def _generate_thumbnail(self, book, cover_path):
    '''
    Fetch the cover image, generate a thumbnail, cache
    Specific implementation for iBooks
    '''
    self._log_location(book.title)
    self._log_diagnostic(" book_path: %s" % book.path)
    self._log_diagnostic("cover_path: %s" % repr(cover_path))

    thumb_data = None
    thumb_path = book.path.rpartition('.')[0] + '.jpg'

    # Try getting the cover from the cache
    try:
        zfr = ZipFile(self.archive_path)
        thumb_data = zfr.read(thumb_path)
        if thumb_data == 'None':
            self._log_diagnostic("returning None from cover cache")
            zfr.close()
            return None
    except:
        self._log_diagnostic("opening cover cache for appending")
        zfw = ZipFile(self.archive_path, mode='a')
    else:
        self._log_diagnostic("returning thumb from cover cache")
        return thumb_data

    '''
    # Is book.path a directory (iBooks) or an epub?
    stats = self.ios.stat(book.path)
    if stats['st_ifmt'] == 'S_IFDIR':
        # ***  This needs to fetch the cover data from the directory  ***
        self._log_diagnostic("returning None, can't read iBooks covers yet")
        return thumb_data

    # Get the cover from the book
    try:
        stream = cStringIO.StringIO(self.ios.read(book.path, mode='rb'))
        mi = get_metadata(stream)
        if mi.cover_data is not None:
            img_data = cStringIO.StringIO(mi.cover_data[1])
    except:
        if self.verbose:
            self._log_diagnostic("ERROR: unable to get cover from '%s'" % book.title)
            import traceback
            #traceback.print_exc()
            exc_type, exc_value, exc_traceback = sys.exc_info()
            self._log_diagnostic(traceback.format_exception_only(exc_type, exc_value)[0].strip())
        return thumb_data
    '''

    try:
        img_data = cStringIO.StringIO(self.ios.read(cover_path, mode='rb'))
    except:
        if self.verbose:
            self._log_diagnostic("ERROR fetching cover data for '%s', caching empty marker" % book.title)
            import traceback
            exc_type, exc_value, exc_traceback = sys.exc_info()
            self._log_diagnostic(traceback.format_exception_only(exc_type, exc_value)[0].strip())
        # Cache the empty cover
        zfw.writestr(thumb_path, 'None')
        return thumb_data


    # Generate a thumb
    try:
        im = PILImage.open(img_data)
        scaled, width, height = fit_image(im.size[0], im.size[1], 60, 80)
        im = im.resize((int(width), int(height)), PILImage.ANTIALIAS)

        thumb = cStringIO.StringIO()
        im.convert('RGB').save(thumb, 'JPEG')
        thumb_data = thumb.getvalue()
        thumb.close()
        self._log_diagnostic("SUCCESS: generated thumb for '%s', caching" % book.title)
        # Cache the tagged thumb
        zfw.writestr(thumb_path, thumb_data)
    except:
        if self.verbose:
            self._log_diagnostic("ERROR generating thumb for '%s', caching empty marker" % book.title)
            import traceback
            exc_type, exc_value, exc_traceback = sys.exc_info()
            self._log_diagnostic(traceback.format_exception_only(exc_type, exc_value)[0].strip())
        # Cache the empty cover
        zfw.writestr(thumb_path, 'None')
    finally:
        #img_data.close()
        zfw.close()

    return thumb_data

def _initialize_overlay(self):
    '''
    Perform any additional initialization
    '''
    self._log_location(self.ios_reader_app)
    self.assets_subpath = '/Media/Books/Sync/Database/OutstandingAssets_4.sqlite'
    self.books_subpath = '/Documents/BKLibrary_database/iBooks_*.sqlite'

    # Confirm/create folder size archive
    if not os.path.exists(self.cache_dir):
        self._log_diagnostic("creating folder cache at '%s'" % self.cache_dir)
        os.makedirs(self.cache_dir)

    self.folder_archive_path = os.path.join(self.cache_dir, "folders.zip")
    if not os.path.exists(self.folder_archive_path):
        self._log_diagnostic("creating folder cache")
        zfw = ZipFile(self.folder_archive_path, mode='w', compression=0)
        zfw.writestr("%s Folder Size Archive" % self.name, '')
        zfw.close()
    else:
        self._log_diagnostic("existing folder cache at '%s'" % self.folder_archive_path)
