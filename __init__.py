#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import (unicode_literals, division, absolute_import,
                        print_function)

__license__ = 'GPL v3'
__copyright__ = '2010, Gregory Riker; 2014, Wulf C. Krueger <wk@mailstation.de>'
__docformat__ = 'restructuredtext en'

"""
Source for this plugin is available as a Github repository at
https://github.com/Philantrop/calibre-apple-reader-applications,
which also includes an overview of the communication protocol in README.md
"""
import base64, cStringIO, datetime, hashlib, imp, mechanize, os, platform, re, sqlite3, sys, tempfile, time

from collections import namedtuple
from inspect import getmembers, isfunction
from PIL import Image as PILImage
from threading import Thread
from types import MethodType

from calibre import browser, fit_image
from calibre.constants import cache_dir as _cache_dir, islinux, isosx, iswindows
#from calibre.devices.idevice.libimobiledevice import libiMobileDevice, libiMobileDeviceException
from calibre.devices.interface import DevicePlugin
from calibre.devices.usbms.books import CollectionsBookList
from calibre.devices.usbms.deviceconfig import DeviceConfig
from calibre.devices.usbms.driver import debug_print
from calibre.ebooks.BeautifulSoup import BeautifulStoneSoup, Tag
from calibre.ebooks.metadata import (author_to_author_sort, authors_to_string,
    MetaInformation, title_sort)
from calibre.ebooks.metadata.epub import get_metadata, set_metadata
from calibre.ebooks.metadata.book.base import Metadata
from calibre.devices.errors import InitialConnectionError
from calibre.gui2.device import device_signals
from calibre.library import current_library_name
from calibre.ptempfile import PersistentTemporaryDirectory
from calibre.utils.config import config_dir, JSONConfig
from calibre.utils.zipfile import ZipFile

try:
    from PyQt5.Qt import QDialog, QIcon, QObject, QPixmap, pyqtSignal
    from PyQt5.uic import compileUi
except ImportError:
    from PyQt4.Qt import QDialog, QIcon, QObject, QPixmap, pyqtSignal
    from PyQt4.uic import compileUi

# Import glue from plugin if between calibre versions with glue updates
if False:
    # To enable, bundle a current copy of libimobiledevice.py, parse_xml.py in iOS_reader_applications folder
    # Disable import of XmlPropertyListParser in local copy of libimobiledevice.py (#24), replace with
    # from calibre_plugins.ios_reader_apps.parse_xml import XmlPropertyListParser
    from calibre_plugins.ios_reader_apps.libimobiledevice import libiMobileDevice, libiMobileDeviceException
else:
    from calibre.devices.idevice.libimobiledevice import libiMobileDevice, libiMobileDeviceException

plugin_prefs = JSONConfig('plugins/iOS reader applications')

# #mark ~~~ READER_APP_ALIASES ~~~
# List of app names as installed by iOS. Prefix with 'b' for libiMobileDevice.
# These are the names that appear in the Config dialog for Preferred reader application
# 'iBooks' is removed under linux
# If an app is available in separate versions for iPad/iPhone, list iPad version first
READER_APP_ALIASES = {
                      'GoodReader':   [b'com.goodiware.GoodReaderIPad', b'com.goodiware.GoodReader'],
                      'GoodReader 4': [b'com.goodiware.goodreader4'],
                      'iBooks':       [b'com.apple.iBooks'],
                      'Kindle':       [b'com.amazon.Lassen'],
                      'Marvin':       [b'com.appstafarian.MarvinIP',
                                       b'com.appstafarian.MarvinIP-free',
                                       b'com.appstafarian.Marvin']
                     }

# Default format maps for Kindle options panel
KINDLE_ENABLED_FORMATS = ['MOBI', 'PDF']
KINDLE_SUPPORTED_FORMATS = ['MOBI', 'PDF']

class Logger():
    '''
    A self-modifying class to log debug statements.
    If disabled in prefs, methods are neutered at first call for performance optimization
    '''
    LOCATION_TEMPLATE = "{cls}:{func}({arg1}) {arg2}"

    def _log(self, msg=None):
        '''
        Upon first call, switch to appropriate method
        '''
        if not plugin_prefs.get('debug_plugin', False):
            # Neuter the method
            self._log = self.__null
            self._log_location = self.__null
        else:
            # Log the message, then switch to real method
            if msg:
                debug_print(" {0}".format(str(msg)))
            else:
                debug_print()

            self._log = self.__log
            self._log_location = self.__log_location

    def __log(self, msg=None):
        '''
        The real method
        '''
        if msg:
            debug_print(" {0}".format(str(msg)))
        else:
            debug_print()

    def _log_location(self, *args):
        '''
        Upon first call, switch to appropriate method
        '''
        if not plugin_prefs.get('debug_plugin', False):
            # Neuter the method
            self._log = self.__null
            self._log_location = self.__null
        else:
            # Log the message from here so stack trace is valid
            arg1 = arg2 = ''

            if len(args) > 0:
                arg1 = str(args[0])
            if len(args) > 1:
                arg2 = str(args[1])

            debug_print(self.LOCATION_TEMPLATE.format(cls=self.__class__.__name__,
                        func=sys._getframe(1).f_code.co_name,
                        arg1=arg1, arg2=arg2))

            # Switch to real method
            self._log = self.__log
            self._log_location = self.__log_location

    def __log_location(self, *args):
        '''
        The real method
        '''
        arg1 = arg2 = ''

        if len(args) > 0:
            arg1 = str(args[0])
        if len(args) > 1:
            arg2 = str(args[1])

        debug_print(self.LOCATION_TEMPLATE.format(cls=self.__class__.__name__,
                    func=sys._getframe(1).f_code.co_name,
                    arg1=arg1, arg2=arg2))

    def __null(self, *args, **kwargs):
        '''
        Optimized method when logger is silent
        '''
        pass


class Book(Metadata):
    '''
    A simple class describing a book
    See ebooks.metadata.book.base #46
    '''
    # 13 standard field keys from Metadata
    iosra_standard_keys = ['author_sort', 'authors', 'comments', 'device_collections',
                           'pubdate', 'publisher', 'rating', 'series', 'series_index',
                           'tags', 'title', 'title_sort', 'uuid']
    # 6 private field keys
    iosra_custom_keys = ['cover_hash','datetime','description','path','size','thumbnail']

    def __eq__(self, other):
        all_mxd_keys = self.iosra_standard_keys + self.iosra_custom_keys
        for attr in all_mxd_keys:
            v1, v2 = [getattr(obj, attr, object()) for obj in [self, other]]
            if v1 is object() or v2 is object():
                return False
            elif v1 != v2:
                return False
        return True

    #def __init__(self, title, author):
    #    Metadata.__init__(self, title, authors=[author])

    def __init__(self, *args, **kwargs):
        if len(args) == 1:
            title = args[0]
            author = "Unknown"
        elif len(args) == 2:
            title, author = args
        if 'authors' in kwargs:
            authors = kwargs['authors']
        else:
            authors = [author]
        Metadata.__init__(self, title, authors=authors)

    def __ne__(self, other):
        all_mxd_keys = self.iosra_standard_keys + self.iosra_custom_keys
        for attr in all_mxd_keys:
            v1, v2 = [getattr(obj, attr, object()) for obj in [self, other]]
            if v1 is object() or v2 is object():
                return True
            elif v1 != v2:
                return True
        return False
    @property
    def title_sorter(self):
        return title_sort(self.title)


class BookList(CollectionsBookList, Logger):
    '''
    A list of books.
    Each Book object must have the fields:
      1. title
      2. authors
      3. size (file size of the book)
      4. datetime (a UTC time tuple)
      5. path (path on the device to the book)
      6. thumbnail (can be None) thumbnail is either a str/bytes object with the
         image data or it should have an attribute image_path that stores an
         absolute (platform native) path to the image
      7. tags (a list of strings, can be empty).
    '''
    __getslice__ = None
    __setslice__ = None

    def __eq__(self, other):
        all_mxd_keys = Book.iosra_standard_keys + Book.iosra_custom_keys
        for x in range(len(self)):
            for attr in all_mxd_keys:
                v1, v2 = [getattr(obj, attr, None) for obj in [self[x], other[x]]]
                if v1 is object() or v2 is object():
                    return False
                elif v1 != v2:
                    return False
        return True

    def __init__(self, parent):
        self.parent = parent
        self.verbose = parent.verbose
        #self._log_location()

    def __ne__(self, other):
        all_mxd_keys = Book.iosra_standard_keys + Book.iosra_custom_keys
        for attr in all_mxd_keys:
            v1, v2 = [getattr(obj, attr, object()) for obj in [self, other]]
            if v1 is object() or v2 is object():
                return True
            elif v1 != v2:
                return True
        return False

    def supports_collections(self):
        ''' Return True if the the device supports collections for this book list. '''
        return True

    def add_book(self, book, replace_metadata):
        '''
        Add the book to the booklist. Intent is to maintain any device-internal
        metadata. Return True if booklists must be sync'ed
        '''
        self.append(book)

    def remove_book(self, book):
        '''
        Remove a book from the booklist. Correct any device metadata at the
        same time
        '''
        self._log_location()

        raise NotImplementedError()

    def get_collections(self, collection_attributes):
        '''
        Return a dictionary of collections created from collection_attributes.
        Each entry in the dictionary is of the form collection name:[list of
        books]

        The list of books is sorted by book title, except for collections
        created from series, in which case series_index is used.

        :param collection_attributes: A list of attributes of the Book object
        '''
        self._log_location()
        return {}

    def rebuild_collections(self, booklist, oncard):
        '''
        For each book in the booklist for the card oncard, remove it from all
        its current collections, then add it to the collections specified in
        device_collections.

        oncard is None for the main memory, carda for card A, cardb for card B,
        etc.

        booklist is the object created by the :method:`books` call above.

        This is called after the user edits the 'Collections' field in the Device view
        when Metadata management is set to 'Manual'.

        '''
        self._log_location()

        command_name = "rebuild_collections"
        command_element = "rebuildcollections"
        command_soup = BeautifulStoneSoup(self.parent.COMMAND_XML.format(
            command_element, time.mktime(time.localtime())))

        LOCAL_DEBUG = False
        if booklist:
            changed = 0
            for book in booklist:
                if LOCAL_DEBUG:
                    self._log("{0:7} {1}".format(book.in_library, book.title))

                filename = self.parent.path_template.format(book.uuid)
                if filename not in self.parent.cached_books:
                    for fn in self.parent.cached_books:
                        if book.uuid and book.uuid == self.parent.cached_books[fn]['uuid']:
                            if LOCAL_DEBUG:
                                self._log("'%s' matched on uuid %s" % (book.title, book.uuid))
                            filename = fn
                            break
                        elif (book.title == self.parent.cached_books[fn]['title'] and
                              book.authors == self.parent.cached_books[fn]['authors']):
                            if LOCAL_DEBUG:
                                self._log("'%s' matched on title/author" % book.title)
                            filename = fn
                            break
                    else:
                        self._log("ERROR: file %s not found in cached_books" % repr(filename))
                        continue

                cached_collections = self.parent.cached_books[filename]['device_collections']
                if cached_collections != book.device_collections:
                    # Append the changed book info to the command file
                    book_tag = Tag(command_soup, 'book')
                    book_tag['filename'] = filename
                    book_tag['title'] = book.title
                    book_tag['author'] = ', '.join(book.authors)
                    book_tag['uuid'] = book.uuid

                    collections_tag = Tag(command_soup, 'collections')
                    for tag in book.device_collections:
                        c_tag = Tag(command_soup, 'collection')
                        c_tag.insert(0, tag)
                        collections_tag.insert(0, c_tag)
                    book_tag.insert(0, collections_tag)

                    command_soup.manifest.insert(0, book_tag)

                    # Update cache
                    self.parent.cached_books[filename]['device_collections'] = book.device_collections

                    changed += 1

            if changed:
                # Stage the command file
                self.parent._stage_command_file(command_name, command_soup,
                                                show_command=self.parent.prefs.get('development_mode', False))

                # Wait for completion
                self.parent._wait_for_command_completion(command_name)
            else:
                self._log("no collection changes detected cached_books <=> device books")

    """
    def _log(self, msg=None):
        '''
        Print msg to console
        '''
        if not self.verbose:
            return

        if msg:
            debug_print(" %s" % msg)
        else:
            debug_print()

    def _log_location(self, *args):
        '''
        Print location, args to console
        '''
        if not self.verbose:
            return

        arg1 = arg2 = ''

        if len(args) > 0:
            arg1 = args[0]
        if len(args) > 1:
            arg2 = args[1]

        debug_print(self.LOCATION_TEMPLATE.format(cls=self.__class__.__name__,
            func=sys._getframe(1).f_code.co_name,
            arg1=arg1, arg2=arg2))
    """


class CompileUI():
    '''
    Compile Qt Creator .ui files at runtime
    '''
    def __init__(self, parent):
        self.compiled_forms = {}
        self.help_file = None
        self._log = parent._log
        self._log_location = parent._log_location
        self.parent = parent
        self.verbose = parent.verbose
        self.compiled_forms = self.compile_ui()

    def compile_ui(self):
        pat = re.compile(r'''(['"]):/images/([^'"]+)\1''')
        def sub(match):
            ans = 'I(%s%s%s)'%(match.group(1), match.group(2), match.group(1))
            return ans

        # >>> Entry point
        self._log_location()

        compiled_forms = {}
        self._find_forms()

        # Cribbed from gui2.__init__:build_forms()
        for form in self.forms:
            with open(form) as form_file:
                soup = BeautifulStoneSoup(form_file.read())
                property = soup.find('property',attrs={'name' : 'windowTitle'})
                string = property.find('string')
                window_title = string.renderContents()

            compiled_form = self._form_to_compiled_form(form)
            if (not os.path.exists(compiled_form) or
                os.stat(form).st_mtime > os.stat(compiled_form).st_mtime):

                if not os.path.exists(compiled_form):
                    if self.verbose:
                        self._log(' compiling %s' % form)
                else:
                    if self.verbose:
                        self._log(' recompiling %s' % form)
                    os.remove(compiled_form)
                buf = cStringIO.StringIO()
                compileUi(form, buf)
                dat = buf.getvalue()
                dat = dat.replace('__appname__', 'calibre')
                dat = dat.replace('import images_rc', '')
                dat = re.compile(r'(?:QtGui.QApplication.translate|(?<!def )_translate)\(.+?,\s+"(.+?)(?<!\\)",.+?\)').sub(r'_("\1")', dat)
                dat = dat.replace('_("MMM yyyy")', '"MMM yyyy"')
                dat = pat.sub(sub, dat)
                with open(compiled_form, 'wb') as cf:
                    cf.write(dat)

            compiled_forms[window_title] = compiled_form.rpartition(os.sep)[2].partition('.')[0]
        return compiled_forms

    def _find_forms(self):
        forms = []
        for root, _, files in os.walk(self.parent.resources_path):
            for name in files:
                if name.endswith('.ui'):
                    forms.append(os.path.abspath(os.path.join(root, name)))
        self.forms = forms

    def _form_to_compiled_form(self, form):
        compiled_form = form.rpartition('.')[0]+'_ui.py'
        return compiled_form


class DatabaseMalformedException(Exception):
    ''' '''
    pass


class DatabaseNotFoundException(Exception):
    ''' '''
    pass


class DriverBase(DeviceConfig, DevicePlugin):

    # Specified at runtime in settings()
    FORMATS = []

    def config_widget(self):
        '''
        See devices.usbms.deviceconfig:DeviceConfig()
        '''
        self._log_location()
        from calibre_plugins.ios_reader_apps.config import ConfigWidget
        applist = READER_APP_ALIASES.keys()
        if islinux and 'iBooks' in applist:
            applist.remove('iBooks')
        if isosx and platform.mac_ver()[0] >= "10.9":
            if not plugin_prefs.get('ibooks_override', False):
                self._log("*** iBooks is not supported > OS X 10.8 ***")
                applist.remove('iBooks')
            else:
                self._log("*** ibooks_override enabled under OS X {0} ***".format(platform.mac_ver()[0]))
        self.cw = ConfigWidget(self, applist)
        return self.cw

    def save_settings(self, config_widget):
        self._log_location()
        config_widget.save_settings()


class InvalidEpub(ValueError):
    pass


class iOSReaderApp(DriverBase, Logger):
    '''

    '''

    # Flow of control when device recognized
    '''
    Launch:
        __init__()
        initialize()
    After GUI displayed:
        startup()
        is_usb_connected()
        can_handle() --or--  detect_managed_devices() depending on MANAGES_DEVICE_PRESENCE
            reset()
            open()
            card_prefix()
            set_progress_reporter()
            get_device_information()
            card_prefix()
            IF Automatic metadata management:
                sync_booklists()
            free_space()
            set_progress_reporter()
            books()
    Upload books to device:
        set_progress_reporter()
        set_plugboards()
        upload_books()
        add_books_to_metadata()
        set_plugboards()
        set_progress_reporter()
        sync_booklists()
        card_prefix()
        free_space()
    Delete book from device:
        delete_books()
        remove_books_from_metadata()
        set_plugboards()
        set_progress_reporter()
        sync_booklists()
        card_prefix()
        free_space()
    Get book from device:
        prepare_addable_books()
        get_file()
        set_plugboards()
        set_progress_reporter()
        sync_booklists()
        card_prefix()
        free_space()
    '''

    app_id = None
    author = 'Wulf C. Krueger'
    books_subpath = None
    description = 'Communicate with Apple iOS reader applications'
    device_profile = None
    ejected = None
    format_map = []
    gui_name = 'iOS reader applications'
    icon = None
    name = 'iOS reader applications'
    overlays_loaded = False
    supported_platforms = ['linux', 'osx', 'windows']
    temp_dir = None
    verbose = None
    # #mark ~~~ plugin version, minimum calibre version ~~~
    version = (1, 4, 4)
    minimum_calibre_version = (1, 37, 0)

    # #mark ~~~ USB fingerprints ~~~
    # Init the BCD and USB fingerprints sets
    _PRODUCT_ID = set([])
    _BCD = set([0x01])

    '''     iPad       '''
    if True:
        _PRODUCT_ID.add(0x129a)     # iPad1 (can't be upgraded past 5.x)

        _PRODUCT_ID.add(0x129f)     # iPad2 WiFi
        _BCD.add(0x210)

        _PRODUCT_ID.add(0x12a2)     # iPad2 GSM
        _BCD.add(0x220)

        _PRODUCT_ID.add(0x12a3)     # iPad2 CDMA
        _BCD.add(0x230)             # Verizon

        _PRODUCT_ID.add(0x12a9)     # iPad2 WiFi (2nd iteration)
        _BCD.add(0x240)

        _PRODUCT_ID.add(0x12a4)     # iPad3 WiFi
        _BCD.add(0x310)

        _PRODUCT_ID.add(0x12a5)     # iPad3 CDMA (Verizon)
        _BCD.add(0x320)

        _PRODUCT_ID.add(0x12a6)     # iPad3 GSM
        _BCD.add(0x330)

        _PRODUCT_ID.add(0x12ab)     # iPad4
        _BCD.add(0x340)             # WiFi
        _BCD.add(0x350)             # GSM (ME401LL/A)
        _BCD.add(0x360)             # GSM (Telstra AU)

        _BCD.add(0x401)             # iPad Air WiFi
        _BCD.add(0x402)             # iPad Air GSM

        _BCD.add(0x503)             # iPad Air 2 WiFi
        _BCD.add(0x504)             # iPad Air 2 GSM

    '''     iPad  Mini     (_PRODUCT_ID 0x12ab shared with iPad)   '''
    if True:
        _BCD.add(0x250)             # iPad Mini WiFi
        _BCD.add(0x260)             # iPad Mini GSM LTE Rogers (Canada)
        _BCD.add(0x270)             # iPad Mini GSM ???

        _BCD.add(0x404)             # iPad rMini WiFi
        _BCD.add(0x405)             # iPad rMini GSM (Verizon LTE)

        _BCD.add(0x407)             # iPad Mini 3 WiFi
        _BCD.add(0x408)             # iPad Mini 3 GSM

    '''     iPhone     '''
    if True:
        _PRODUCT_ID.add(0x1292)     # iPhone3G

        _PRODUCT_ID.add(0x1294)     # iPhone3GS

        _PRODUCT_ID.add(0x1297)     # iPhone4 (Telus)
        _BCD.add(0x310)

        _PRODUCT_ID.add(0x129c)     # iPhone4 (Verizon)
        _BCD.add(0x330)

        _PRODUCT_ID.add(0x12a0)     # iPhone4S GSM
        _BCD.add(0x410)

        _PRODUCT_ID.add(0x12a8)     # iPhone5 GSM
        _BCD.add(0x510)
        _BCD.add(0x520)             # GSM (Telefonica-Movistar Spain)

        _BCD.add(0x530)             # 5C (Softbank Japan)
        _BCD.add(0x540)             # 5C (???)

        _BCD.add(0x601)             # iPhone 5S (AT&T)
        _BCD.add(0x602)             # iPhone 5S (Telstra)

        _BCD.add(0x701)             # iPhone 6 Plus
        _BCD.add(0x702)             # iPhone 6

    '''     iPod     '''
    if True:
        _PRODUCT_ID.add(0x1291)     # iPod Touch

        _PRODUCT_ID.add(0x1293)     # iPod Touch 2G

        _PRODUCT_ID.add(0x1299)     # iPod Touch 3G

        _PRODUCT_ID.add(0x129e)     # iPod Touch 4G
        _BCD.add(0x410)

        _PRODUCT_ID.add(0x12aa)     # iPod Touch 5G
        _BCD.add(0x510)

    # Finalize the supported BCD and USB fingerprints
    VENDOR_ID = [0x05ac]
    PRODUCT_ID = list(_PRODUCT_ID)
    BCD = list(_BCD)

    @property
    def archive_path(self):
        return os.path.join(self.cache_dir, "thumbs.zip")

    @property
    def cache_dir(self):
        return os.path.join(_cache_dir(), self.ios_reader_app)

    def books(self, oncard=None, end_session=True):
        '''
        Return a list of ebooks on the device.
        @param oncard:  If 'carda' or 'cardb' return a list of ebooks on the
                        specific storage card, otherwise return list of ebooks
                        in main memory of device. If a card is specified and no
                        books are on the card return empty list.
        @return: A BookList.

        '''
        raise NotImplementedError()

    def can_handle(self, device_info, debug=False):
        '''
        If calibre was started without an iDevice connected, control will come here
        upon connection.
        Perform a late overlay binding, then pass control to overlay can_handle()
        '''
        self._log_location("overlays_loaded: %s" % self.overlays_loaded)

        # If reader app specified, check for installation, reconfigure
        if self.ios_reader_app:
            self.app_id = self._get_connected_device_info()
            if self.app_id is not None and not self.overlays_loaded:
                # Device connected, app installed
                self._log("performing late overlay binding")
                self._class_reconfigure()
                self.overlays_loaded = True
        else:
            if self.ios_reader_app is not None:
                self._log_location("Preferred iOS reader app '%s' not installed" % self.ios_reader_app)

        return False

    def card_prefix(self, end_session=True):
        '''
        Return a 2 element list of the prefix to paths on the cards.
        If no card is present None is set for the card's prefix.
        E.G.
        ('/place', '/place2')
        (None, 'place2')
        ('place', None)
        (None, None)
        '''
        return (None, None)

    def free_space(self, end_session=True):
        '''
        Return available space on device
        self.device_profile initialized during get_device_information()
        '''
        self._log_location()
        try:
            available_space = long(self.device_profile['FSFreeBytes'])
            return (available_space, -1, -1)
        except:
            return(-1, -1, -1)

    def get_device_information(self, end_session=True):
        '''
        Ask device for device information. See L{DeviceInfoQuery}.
        @return: (device name, device version, software version on device, mime type)
        '''
        self._log_location()

        # self.device_profile built in initialize(), this is the full code for reference
        if False:
            self._log("getting device profile late")
            self.ios.connect_idevice()
            preferences = self.ios.get_preferences()
            self.ios.disconnect_idevice()

            self.ios.mount_ios_media_folder()
            device_info = self.ios._afc_get_device_info()
            self.ios.dismount_ios_media_folder()

            device_info.pop('Model')
            self.device_profile = dict(preferences.items() + device_info.items())

        if True and self.verbose:
            for item in sorted(self.device_profile):
                if item in ['FSTotalBytes', 'FSFreeBytes']:
                    self._log(" {0:21}: {1:,}".format(item, int(self.device_profile[item])))
                else:
                    self._log(" {0:21}: {1}".format(item, self.device_profile[item]))

        device_information = (self.device_profile['DeviceName'],
                              self.device_profile['ProductType'],
                              self.device_profile['ProductVersion'],
                              'unknown mime type')
        return device_information

    def get_option(self):
        self._log_location()

    def initialize(self):
        '''
        Copy the iOS Reader App icons to our resource folder
        Copy the iOS Reader App config widgets to our resource folder
        Init the JSON prefs file
        '''
        self.prefs = plugin_prefs
        self.verbose = self.prefs.get('debug_plugin', False)

        self._log_location("v%s" % '.'.join(map(str, self.version)))

        self.resources_path = os.path.join(config_dir, 'plugins', "%s_resources" % self.name.replace(' ', '_'))

        # ~~~~~~~~~ Copy the icon files to our resource directory ~~~~~~~~~
        icons = []
        with ZipFile(self.plugin_path, 'r') as zf:
            for candidate in zf.namelist():
                if candidate.endswith('/'):
                    continue
                if candidate.startswith('icons/'):
                    icons.append(candidate)
        ir = self.load_resources(icons)
        for icon in icons:
            if not icon in ir:
                continue
            fs = os.path.join(self.resources_path, icon)
            if not os.path.exists(fs):
                if not os.path.exists(os.path.dirname(fs)):
                    os.makedirs(os.path.dirname(fs))
                with open (fs, 'wb') as f:
                    f.write(ir[icon])
            else:
                # Is the icon file current?
                update_needed = False
                with open(fs, 'rb') as f:
                    if f.read() != ir[icon]:
                        update_needed = True
                    if update_needed:
                        with open(fs, 'wb') as f:
                            f.write(ir[icon])

        # ~~~~~~~~~ Copy the widget files to our resource directory ~~~~~~~~~
        widgets = []
        with ZipFile(self.plugin_path, 'r') as zf:
            for candidate in zf.namelist():
                # Qt UI files
                if candidate.startswith('widgets/') and candidate.endswith('.ui'):
                    widgets.append(candidate)
                # Corresponding class definitions
                if candidate.startswith('widgets/') and candidate.endswith('.py'):
                    widgets.append(candidate)
        wr = self.load_resources(widgets)
        for widget in widgets:
            if not widget in wr:
                continue
            fs = os.path.join(self.resources_path, widget)
            if not os.path.exists(fs):
                # If the file doesn't exist in the resources dir, add it
                if not os.path.exists(os.path.dirname(fs)):
                    os.makedirs(os.path.dirname(fs))
                with open (fs, 'wb') as f:
                    f.write(wr[widget])
            else:
                # Is the .ui file current?
                update_needed = False
                with open(fs, 'r') as f:
                    if f.read() != wr[widget]:
                        update_needed = True
                if update_needed:
                    with open (fs, 'wb') as f:
                        f.write(wr[widget])

        # ~~~~~~~~~ Copy the help files to our resource directory ~~~~~~~~~
        help_files = []
        with ZipFile(self.plugin_path, 'r') as zf:
            for candidate in zf.namelist():
                if candidate.startswith('help/') and candidate.endswith('.html'):
                    help_files.append(candidate)
        hrs = self.load_resources(help_files)
        for hf in help_files:
            if not hf in hrs:
                continue
            fs = os.path.join(self.resources_path, hf)
            if not os.path.exists(fs):
                # If the file doesn't exist in the resources dir, add it
                if not os.path.exists(os.path.dirname(fs)):
                    os.makedirs(os.path.dirname(fs))
                with open (fs, 'wb') as f:
                    f.write(hrs[hf])
            else:
                # Is the help file current?
                update_needed = False
                with open(fs, 'r') as f:
                    if f.read() != hrs[hf]:
                        update_needed = True
                if update_needed:
                    with open (fs, 'wb') as f:
                        f.write(hrs[hf])

        # Compile .ui files as needed
        cui = CompileUI(self)

        # Init the prefs file as needed
        self._init_prefs()

        if getattr(self, 'temp_dir') is None:
            iOSReaderApp._create_temp_dir('_ios_local_db')

        # Init libiMobileDevice
        self.ios = libiMobileDevice(verbose=self.prefs.get('debug_libimobiledevice', False))

        # Confirm the installation of the preferred reader app
        self.app_id = None

        # Special case development overlay
        if (self.prefs.get('development_mode', False) and
            self.prefs.get('development_overlay', None) and
            self.prefs.get('development_app_id', None)):

            self.app_id = self.prefs.get('development_app_id', None)
            self.ios_reader_app = "development_mode"
            self._get_connected_device_info()

        else:
            self.ios_reader_app = self.prefs.get('preferred_reader_app', None)

            if self.ios_reader_app:
                self.app_id = self._get_connected_device_info()
            else:
                self._log("No preferred reader app selected in config")
                self.ios_reader_app = None

        # Device connected, app installed
        if self.app_id is not None and self.ios_reader_app is not None:
            self.ejected = False
            self._class_reconfigure()

    def is_running(self):
        self._log_location()

    def is_usb_connected(self, devices_on_system, debug=False, only_presence=False):
        ans = super(iOSReaderApp, self).is_usb_connected(devices_on_system, debug, only_presence)
        #self._log_location(ans)
        return ans

    def is_usb_connected_windows(self, devices_on_system, debug=False, only_presence=False):
        '''
        If calibre was started without an iDevice connected, control will come here
        with ans[0] = True when the device connects
        Perform a late overlay binding, mount the preferred reader app, then pass
        control to the overlays.
        Always return False, None, as the overlaid version will take over next time
        this method is called
        '''
        ans = super(iOSReaderApp, self).is_usb_connected_windows(devices_on_system, debug, only_presence)
        usb_connected = ans[0]
        #self._log_location(ans)
        if usb_connected:
            # If reader app specified, check for installation, reconfigure
            if self.ios_reader_app:
                self.app_id = self._get_connected_device_info()
                if self.app_id is not None and not self.overlays_loaded:
                    # Device connected, app installed
                    self._log("performing late overlay binding")
                    self._class_reconfigure()
                    self.overlays_loaded = True
                    # Unique to Windows - need to connect to app folder before continuing
                    self.ios_connection['app_installed'] = self.ios.mount_ios_app(app_id=self.app_id)
                    self.ios_connection['device_name'] = self.ios.device_name
            else:
                self._log("device connected, but no reader app selected")

        return False, None

    def open(self, connected_device, library_uuid):
        '''
        If the user has selected iBooks as the preferred iOS reader app,
        morph to class ITUNES, initialize and launch iTunes in can_handle(),
        call ITUNES:open(), and return. All subsequent driver calls will be
        handled by ITUNES driver.
        ITUNES.verbose is supplied from our debug_plugin value.

        Otherwise, load the class overlay methods for preferred iOS reader app,
        create/confirm thumbs archive.

        *** NB: Do not overwrite this method in reader class! ***
        self.vid, self.pid referenced in is_usb_connected_windows to determine when
        ejected device has been physically removed from the system
        '''
        self._log_location()
        self.vid = connected_device[0]
        self.pid = connected_device[1]
        self._log(" Vendor ID (vid):%04x Product ID: (pid):%04x" % (self.vid, self.pid))

    def reset(self, **kwargs):
        '''
        :key: The key to unlock the device
        :log_packets: If true the packet stream to/from the device is logged
        :report_progress: Function that is called with a % progress
                                (number between 0 and 100) for various tasks
                                If it is called with -1 that means that the
                                task does not have any progress information
        :detected_device: Device information from the device scanner
        '''
        self._log_location()
        if False:
            for key, value in kwargs.iteritems():
                self._log("%s = %s" % (key, value))
        else:
            pass

    def set_option(self):
        self._log_location()

    def set_plugboards(self, plugboards, pb_func):
        '''
        Capture the applicable plugboard for this device
        '''
        self._log_location()
        self.plugboards = plugboards
        self.plugboard_func = pb_func

    def set_progress_reporter(self, report_progress):
        '''
        @param report_progress: Function that is called with a % progress
                                (number between 0 and 100) for various tasks
                                If it is called with -1 that means that the
                                task does not have any progress information
        '''
        self._log_location()
        self.report_progress = report_progress

    def settings(self):
        '''
        Dynamically assert supported formats within opts
        '''
        opts = super(iOSReaderApp, self).settings()
        opts.format_map = self.format_map
        self._log_location("format_map for '%s': %s" % (self.ios_reader_app, opts.format_map))
        return opts

    def startup(self):
        self._log_location()
        self._dump_installed_plugins()

    def shutdown(self):
        self._log_location()
        self.ios.disconnect_idevice()

    def stop_plugin(self):
        self._log_location()

    # Helpers
    def _class_reconfigure(self):
        self._log_location("'%s'" % self.ios_reader_app)

        # Special handling for iBooks, change class completely
        if self.ios_reader_app == 'iBooks':
            # Post logging message without book_count
            #self._log_metrics()

            from calibre.devices.apple.driver import ITUNES
            ITUNES.VENDOR_ID = self.VENDOR_ID
            ITUNES.PRODUCT_ID = self.PRODUCT_ID
            ITUNES.BCD = self.BCD
            ITUNES.icon = os.path.join(self.resources_path, 'icons', '%s.png' % self.ios_reader_app)
            ITUNES.name = self.name
            ITUNES.version = self.version
            ITUNES.gui_name = self.gui_name
            ITUNES.verbose = ITUNES.verbose or self.prefs.get('debug_plugin', False)
            self._log("{:~^80}".format(" morphing class to ITUNES "))

            # Switch to ITUNES class, initialize via can_handle
            self.__class__ = ITUNES
            if iswindows:
                self.can_handle_windows(None)
            else:
                self.can_handle(None)
            return

        # Load the class overlay methods
        self._load_reader_app_overlays(self.ios_reader_app)
        if self.ios_reader_app == 'development_mode':
            self.icon = None
        else:
            self.icon = os.path.join(self.resources_path, 'icons', '%s.png' % self.ios_reader_app)

        self._initialize_overlay()
        self._log("{:~^80}".format(" switching to %s overlay " % self.ios_reader_app))

    @staticmethod
    def _create_temp_dir(suffix):
        '''
        Create a PersistentTemporaryDirectory for local copies of remote dbs
        '''
        iOSReaderApp.temp_dir = PersistentTemporaryDirectory(suffix)

    def _dump_installed_plugins(self):
        '''
        '''
        self._log_location()
        from calibre.customize.ui import initialized_plugins
        user_installed_plugins = {}
        for plugin in initialized_plugins():
            path = getattr(plugin, 'plugin_path', None)
            if path is not None:
                name = getattr(plugin, 'name', None)
                if name == self.name:
                    continue
                author = getattr(plugin, 'author', None)
                version = getattr(plugin, 'version', None)
                user_installed_plugins[name] = {'author': author, 'version': "{0}.{1}.{2}".format(*version)}

        if user_installed_plugins:
            max_name_width = max([len(v) for v in user_installed_plugins.keys()])
            max_author_width = max([len(d['author']) for d in user_installed_plugins.values()])
            max_version_width = max([len(d['version']) for d in user_installed_plugins.values()])
            self._log("{0:<{1}}  {2:<{3}}  {4:<{5}}".format('plugin', max_name_width,
                                                            'author', max_author_width,
                                                            'version', max_version_width))
            self._log("{0:-^{1}}  {2:-^{3}}  {4:-^{5}}".format('', max_name_width,
                                                               '', max_author_width,
                                                               '', max_version_width))
            for plugin, d in sorted(user_installed_plugins.iteritems(), key=lambda item: item[0].lower()):
                self._log("{0:{1}} {2:{3}} {4:{5}}".format(plugin, max_name_width + 1,
                                                           d['author'], max_author_width + 1,
                                                           d['version'], max_version_width + 1))
            self._log("{0:-^{1}}  {2:-^{3}}  {4:-^{5}}".format('', max_name_width,
                                                               '', max_author_width,
                                                               '', max_version_width))

    def _generate_thumbnail(self, book):
        '''
        Fetch the cover image, generate a thumbnail, cache
        Extracts covers from zipped epubs
        '''
        self._log_location(book.title)
        #self._log("book_path: %s" % book.path)
        #self._log("book: '%s' by %s uuid: %s" % (book.title, book.author, book.uuid))

        # Parse the cover from the connected device, model Fetch_Annotations:_get_epub_toc()

        thumb_data = None
        thumb_path = book.path.rpartition('.')[0] + '.jpg'

        # Try getting the cover from the cache
        try:
            zfr = ZipFile(self.archive_path)
            thumb_data = zfr.read(thumb_path)
            if thumb_data == 'None':
                self._log("returning None from cover cache")
                zfr.close()
                return None
        except:
            self._log("opening cover cache for appending")
            zfw = ZipFile(self.archive_path, mode='a')
        else:
            self._log("returning thumb from cover cache")
            return thumb_data

        # Get the cover from the book
        try:
            stream = cStringIO.StringIO(self.ios.read(book.path, mode='rb'))
            mi = get_metadata(stream)
            if mi.cover_data is not None:
                img_data = cStringIO.StringIO(mi.cover_data[1])
        except:
            if self.verbose:
                self._log("ERROR: unable to get cover from '%s'" % book.title)
                import traceback
                #traceback.print_exc()
                exc_type, exc_value, exc_traceback = sys.exc_info()
                self._log(traceback.format_exception_only(exc_type, exc_value)[0].strip())
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
            self._log("SUCCESS: generated thumb for '%s', caching" % book.title)
            # Cache the tagged thumb
            zfw.writestr(thumb_path, thumb_data)
        except:
            if self.verbose:
                self._log("ERROR generating thumb for '%s', caching empty marker" % book.title)
                import traceback
                exc_type, exc_value, exc_traceback = sys.exc_info()
                self._log(traceback.format_exception_only(exc_type, exc_value)[0].strip())
            # Cache the empty cover
            zfw.writestr(thumb_path, 'None')
        finally:
            img_data.close()
            zfw.close()

        return thumb_data

    def _get_connected_device_info(self):
        '''
        If we have a connected device, store a device profile
        ios.installed_apps: {<appname>: {'app_version': '1.2.3', 'app_id': 'com.apple.iBooks'}}
        Return the app_id
        If more than one alias available, refer to JSON file for preferred alias
        '''
        self._log_location()
        device_list = self.ios.get_device_list()
        if device_list is None:
            raise libiMobileDeviceException("No connected iDevices")
        app_id = None
        try:
            if len(device_list):
                if len(device_list) == 1:
                    connected = self.ios.connect_idevice()
                    if not connected:
                        raise libiMobileDeviceException("Unable to connect to iDevice. "
                                                        "If you are updating, disconnect your iDevice first.")
                    preferences = self.ios.get_preferences()
                    self.ios.disconnect_idevice()

                    # Get the device info
                    self.ios.mount_ios_media_folder()
                    device_info = self.ios._afc_get_device_info()
                    self.ios.dismount_ios_media_folder()
                    device_info.pop('Model')
                    self.device_profile = dict(preferences.items() + device_info.items())

                    # Use development_app_id if development mode
                    if self.prefs.get('development_mode', False):
                        app_id = self.prefs.get('development_app_id', None)
                    if not app_id:
                        if self.ios_reader_app in READER_APP_ALIASES:

                            # Find the first installed app (iPad version takes precedence)
                            for _app_id in READER_APP_ALIASES[self.ios_reader_app]:
                                self._log("mounting '%s'" % _app_id)
                                if self.ios.mount_ios_app(app_id=_app_id):
                                    app_id = _app_id
                                    self.ios.disconnect_idevice()
                                    break
                        else:
                            self._log("'{}' is not a valid preferred_reader_app selection".format(self.ios_reader_app))
                else:
                    self._log("Too many connected iDevices")
            else:
                self._log("No connected iDevices")
        except:
            import traceback
            traceback.print_exc()
            exc_type, exc_value, exc_traceback = sys.exc_info()
            self._log_location("ERROR: {0}".format(
                traceback.format_exception_only(exc_type, exc_value)[0].strip()))
            raise InitialConnectionError("Unable to connect to iDevice")
        return app_id

    def _init_prefs(self):
        '''
        Initialize the JSON store
        '''
        pref_map = {
            'plugin_version': b"%s" % '.'.join(map(str, self.version)),
            'development_mode': False,
            #'additional_readers': os.sep.join(['path','to','your','reader_class.py'])
            }
        for pm in pref_map:
            if not self.prefs.get(pm, None):
                self.prefs.set(pm, pref_map[pm])
        self._log_location("prefs created under v%s" % self.prefs.get('plugin_version'))
        try:
            for pref in sorted(self.prefs.keys()):
                if pref == 'plugin_version':
                    continue
                self._log("%s: %s" % (pref, repr(self.prefs.get(pref))))
        except:
            self._log(self.prefs)

    def _initialize_overlay(self):
        '''
        Perform any additional initialization
        '''
        pass

    def _load_reader_app_overlays(self, cls_name):
        '''
        Load reader app overlay methods from resource file from
        readers/<name>_overlays.py
        '''
        self._log_location("'%s'" % cls_name)

        # Store the raw source to a temp file, import it
        if cls_name == "development_mode":
            do = self.prefs.get('development_overlay', None)
            self._log("loading development_overlay %s" % repr(do))
            overlay = imp.load_source("temporary_overlay_methods", do)
        else:
            # Special-case GoodReader4 to use the same overlay as GoodReader
            if cls_name == "GoodReader 4":
                cls_name = "GoodReader"
            overlay_source = 'readers/%s_overlays.py' % cls_name
            basename = re.sub('readers/', '', overlay_source)
            tmp_file = os.path.join(self.temp_dir, basename)
            with open(tmp_file, 'w') as tf:
                tf.write(get_resources(overlay_source))
            overlay = imp.load_source("temporary_overlay_methods", tmp_file)
            os.remove(tmp_file)

        # Extend iOSReaderApp with the functions defined in the overlay
        # [(<name>, <function>), (<name>, <function>)...]
        overlays = [f for f in getmembers(overlay) if isfunction(f[1])]
        self._log("loading %d overlays" % len(overlays))
        for method in overlays:
            self._log("adding overlay '%s()'" % method[0])
            setattr(self, method[0], MethodType(method[1], self, iOSReaderApp))

        del overlay

    def _localize_database_path(self, remote_db_path):
        '''
        Copy remote_db_path from iOS to local storage as needed
        '''
        self._log_location("app_id: '%s' remote_db_path: '%s'" % (self.app_id, remote_db_path))

        using_media_folder = False
        # Is the db in the Media folder or an app sandbox?
        if remote_db_path.startswith('/Media'):
            using_media_folder = True
            self.ios.mount_ios_media_folder()
            remote_db_path = remote_db_path[len('/Media'):]
        else:
            # Mount app_id
            self.ios.mount_ios_app(app_id=self.app_id)

        local_db_path = None
        db_stats = {}

        if '*' in remote_db_path:
            # Find matching file based on wildcard
            f_els = os.path.basename(remote_db_path).split('*')
            prefix = f_els[0]
            suffix = f_els[1]
            files = self.ios.listdir(os.path.dirname(remote_db_path))
            for f in files:
                if f.startswith(prefix) and f.endswith(suffix):
                    remote_db_path = '/'.join([os.path.dirname(remote_db_path),f])
                    break

        db_stats = self.ios.stat(remote_db_path)
        if db_stats:
            path = remote_db_path.split('/')[-1]
            if iswindows:
                plen = len(self.temp_dir)
                path = ''.join(shorten_components_to(245-plen, [path]))

            full_path = os.path.join(self.temp_dir, path)
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
            self._log_location("'%s' not found" % remote_db_path)
            raise DatabaseNotFoundException

        if using_media_folder:
            self.ios.dismount_ios_media_folder()
        else:
            # Dismount ios
            self.ios.disconnect_idevice()

        return {'path': local_db_path, 'stats': db_stats}

    def _quote_sqlite_identifier(self, str):
        '''
        Replace all " with ""
        Wrap ans in double quotes
        Allows embedded double quotes in sqlite identifiers
        '''
        ans = str.replace("\"", "\"\"")
        return "\"" + ans + "\""

    def _log_metrics(self, metrics={}):
        '''
        Post logging event
        No identifying information or library metadata is included in the logging event.
        device_udid is securely encrypted before logging for an anonymous (but unique) id,
        used to determine number of unique devices using plugin.
        '''
        if self.prefs.get('plugin_diagnostics', True):
            self._log_location(self.ios_reader_app)
            br = browser()
            try:
                br.open(PluginMetricsLogger.URL)
                args = {'plugin': self.gui_name,
                        'version': "%s" % '.'.join(map(str, self.version))}
                post = PluginMetricsLogger(**args)
                post.req.add_header('DEVICE_OS', self.device_profile['ProductVersion'])
                post.req.add_header("DEVICE_MODEL", self.device_profile['ProductType'])
                # Encrypt the device udid using MD5 encryption
                m = hashlib.md5()
                m.update(self.device_profile['UniqueDeviceID'])
                post.req.add_header('DEVICE_UDID', m.hexdigest())
                post.req.add_header("PLUGIN_PREFERRED_READER_APP", self.ios_reader_app)
                post.req.add_header("PLUGIN_APP_ID", self.app_id)
                post.req.add_header("PLUGIN_BOOK_COUNT", metrics.get('book_count', -1))
                post.req.add_header("PLUGIN_LOAD_TIME", int(metrics.get('load_time', -1)))
                post.start()
            except Exception as e:
                self._log("Plugin logger unreachable: {0}".format(e))


class PluginMetricsLogger(Thread, Logger):
    '''
    Post an event to the logging server
    '''
    # #mark ~~~ logging URL ~~~
    URL = "http://calibre-plugins.com:7584"
    #URL = "http://localhost:8378"

    def __init__(self, **args):
        Thread.__init__(self)
        self.args = args
        self.construct_header()

    def construct_header(self):
        '''
        Build the default header information describing the environment,
        plus the passed plugin metadata
        '''
        import platform
        from calibre.constants import (__appname__, get_version, isportable, isosx,
                                       isfrozen, is64bit, iswindows)
        calibre_version = "{0}{1} isfrozen:{2} is64bit:{3}".format(
            get_version(), ' Portable' if isportable else '', isfrozen, is64bit)
        if isosx:
            platform_profile = "OS X {0}".format(platform.mac_ver()[0])
        else:
            platform_profile = "{0} {1} {2}".format(
                platform.platform(), platform.system(), platform.architecture())

        self.req = mechanize.Request(self.URL)
        self.req.add_header('CALIBRE_OS', platform_profile)
        self.req.add_header('CALIBRE_VERSION', calibre_version)
        self.req.add_header('CALIBRE_PLUGIN', self.args.get('plugin'))
        self.req.add_header('PLUGIN_VERSION', self.args.get('version'))

    def run(self):
        br = browser()
        try:
            ans = br.open(self.req).read().strip()
            self._log_location(ans)
        except Exception as e:
            import traceback
            self._log(traceback.format_exc())


class ReaderAppSignals(QObject):
    '''
    This class allows the device driver to emit signals to subscribed plugins.
    '''
    # This signal is emitted after I/O operations indicating content on the connected
    # device may have changed. See Marvin_overlays:initialize_overlay() and
    # _wait_for_command_completion() for typical usage.
    reader_app_status_changed = pyqtSignal(dict)


'''     Helper functions   '''
def from_json(obj):
    '''
    Models calibre.utils.config:from_json
    uses local parse_date()
    '''
    if '__class__' in obj:
        if obj['__class__'] == 'bytearray':
            return bytearray(base64.standard_b64decode(obj['__value__']))
        if obj['__class__'] == 'datetime.datetime':
            return parse_date(obj['__value__'])
        if obj['__class__'] == 'time.struct_time':
            StructTime = namedtuple('StructTime', 'tm_year tm_mon tm_mday tm_hour tm_min tm_sec tm_wday tm_yday tm_isdst')
            return time.struct_time(StructTime(obj['__value__']['tm_year'],
                                               obj['__value__']['tm_mon'],
                                               obj['__value__']['tm_mday'],
                                               obj['__value__']['tm_hour'],
                                               obj['__value__']['tm_min'],
                                               obj['__value__']['tm_sec'],
                                               obj['__value__']['tm_wday'],
                                               obj['__value__']['tm_yday'],
                                               obj['__value__']['tm_isdst']))
    return obj


def get_cc_mapping(cc_name, element, default=None):
    '''
    Return the element mapped to cc_name in prefs:cc_mappings
    '''
    if element not in ['field', 'combobox']:
        raise ValueError("invalid element '{0}' requested for custom column '{1}'".format(
            element, cc_name))

    ans = default
    cc_mappings = plugin_prefs.get('cc_mappings', {})
    current_library = current_library_name()
    if (current_library in cc_mappings and
        cc_name in cc_mappings[current_library] and
        element in cc_mappings[current_library][cc_name]):
        ans = cc_mappings[current_library][cc_name][element]
    return ans


def isoformat(date_time, sep='T'):
    '''
    Mocks calibre.utils.date:isoformat()
    '''
    return unicode(date_time.isoformat(str(sep)))


def parse_date(date_string):
    '''
    Mocks calibre.utils.date:parse_date()
    https://labix.org/python-dateutil#head-42a94eedcff96da7fb1f77096b5a3b519c859ba9
    '''
    UNDEFINED_DATE = datetime.datetime(101,1,1, tzinfo=None)
    from dateutil.parser import parse
    if not date_string:
        return UNDEFINED_DATE
    return parse(date_string, ignoretz=True)


def set_cc_mapping(cc_name, field=None, combobox=None):
    '''
    Store element to cc_name in prefs:cc_mappings
    '''
    cc_mappings = plugin_prefs.get('cc_mappings', {})
    current_library = current_library_name()
    if current_library in cc_mappings:
        cc_mappings[current_library][cc_name] = {'field': field, 'combobox': combobox}
    else:
        cc_mappings[current_library] = {cc_name: {'field': field, 'combobox': combobox}}
    plugin_prefs.set('cc_mappings', cc_mappings)


def to_json(obj):
    '''
    Models calibre.utils.config:to_json
    Uses local isoformat()
    '''
    if isinstance(obj, bytearray):
        return {'__class__': 'bytearray',
                '__value__': base64.standard_b64encode(bytes(obj))}
    if isinstance(obj, datetime.datetime):
        return {'__class__': 'datetime.datetime',
                '__value__': isoformat(obj)}
    if isinstance(obj, time.struct_time):
        return {'__class__': 'time.struct_time',
                '__value__': {'tm_year': obj.tm_year,
                              'tm_mon': obj.tm_mon,
                              'tm_mday': obj.tm_mday,
                              'tm_hour': obj.tm_hour,
                              'tm_min': obj.tm_min,
                              'tm_sec': obj.tm_sec,
                              'tm_wday': obj.tm_wday,
                              'tm_yday': obj.tm_yday,
                              'tm_isdst': obj.tm_isdst}
               }
    raise TypeError(repr(obj) + ' is not JSON serializable')

