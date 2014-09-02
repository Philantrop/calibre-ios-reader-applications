#!/usr/bin/env python
# coding: utf-8

from __future__ import (unicode_literals, division, absolute_import,
                        print_function)

import cStringIO, importlib, re, os, sys
from datetime import datetime

from calibre.devices.usbms.driver import debug_print
from calibre.gui2 import open_url, show_restart_warning
from calibre.gui2.ui import get_gui, info_dialog
from calibre.utils.config import config_dir

try:
    from PyQt5.Qt import QFont, QIcon, QUrl, QWidget
except ImportError:
    from PyQt4.Qt import QFont, QIcon, QUrl, QWidget

widget_path = os.path.join(config_dir, 'plugins',
                           'iOS_reader_applications_resources', 'widgets')

# Import Ui_Dialog from form generated dynamically during initialization
if True:
    sys.path.insert(0, widget_path)
    from main_ui import Ui_Dialog
    sys.path.remove(widget_path)

class ConfigWidget(QWidget, Ui_Dialog):
    '''
    Tabbed config dialog for iOS Reader Apps
    '''
    # Location reporting template
    LOCATION_TEMPLATE = "{cls}:{func}({arg1}) {arg2}"
    CACHING_READER_APPS = ['Marvin']

    def __init__(self, parent, app_list):
        #QDialog.__init__(self)
        QWidget.__init__(self)
        Ui_Dialog.__init__(self)
        self.current_plugin = None
        self.gui = get_gui()
        self.icon = parent.icon
        self.parent = parent
        self.prefs = parent.prefs
        self.resources_path = parent.resources_path
        self.verbose = parent.verbose
        self._log_location(app_list)
        self.setupUi(self)
        self.version = parent.version

        # Restore the caching settings
        device_caching_enabled = self.prefs.get('device_booklist_caching', False)
        self.device_booklist_caching_cb.setChecked(device_caching_enabled)
        self.device_booklist_cache_limit_sb.setEnabled(device_caching_enabled)
        self.available_space = self.parent.free_space()[0]
        self.allocation_factor = self.prefs.get('device_booklist_cache_limit', 10.00)
        self.device_booklist_cache_limit_sb.setValue(self.allocation_factor)
        if self.available_space == -1:
            self.allocated_space_label.setVisible(False)
        else:
            # If device connected, hook changes to spinbox and init display
            self.device_booklist_cache_limit_sb.valueChanged.connect(self.device_caching_allocation_changed)
            self.device_caching_allocation_changed(self.allocation_factor)

        # Restore the diagnostic settings
        self.plugin_diagnostics.setChecked(self.prefs.get('plugin_diagnostics', True))

        # Restore the debug settings
        self.debug_plugin.setChecked(self.prefs.get('debug_plugin', False))
        self.debug_libimobiledevice.setChecked(self.prefs.get('debug_libimobiledevice', False))

        # Load the widgets
        self.widgets = []
        for app_name in app_list:
            name = app_name.lower().replace(' ', '_')
            # Load dynamic tab
            klass = os.path.join(widget_path, '%s.py' % name)
            if os.path.exists(klass):
                try:
                    self._log_location("adding widget for %s" % name)
                    sys.path.insert(0, widget_path)
                    config_widget = importlib.import_module(name)
                    pw = config_widget.PluginWidget(self)
                    pw.initialize(name)
                    pw.ICON = I('forward.png')
                    self.widgets.append(pw)
                except ImportError:
                    self._log("ERROR: ImportError with %s" % name)
                    import traceback
                    traceback.print_exc()
                finally:
                    sys.path.remove(widget_path)
            else:
                self._log("no dynamic tab resources found for %s" % name)

        self.widgets = sorted(self.widgets, cmp=lambda x,y:cmp(x.TITLE.lower(), y.TITLE.lower()))

        # Callbacks when reader_app changes
        #self.reader_apps.currentIndexChanged.connect(self.restart_required)
        #self.debug_plugin.stateChanged.connect(self.restart_required)
        #self.debug_libimobiledevice.stateChanged.connect(self.restart_required)

        # Callback when reader_app changes
        self.reader_apps.currentIndexChanged.connect(self.reader_app_changed)

        # Callback when booklist_caching_cb changes - enable/disable spinbox
        self.device_booklist_caching_cb.stateChanged.connect(self.device_booklist_caching_changed)

        # Add the app_list to the dropdown
        self.reader_apps.blockSignals(True)
        self.reader_apps.addItems([''])
        self.reader_apps.addItems(sorted(app_list, key=lambda s: s.lower()))

        # Get the last-used reader_app
        pra = self.prefs.get('preferred_reader_app', '')
        idx = self.reader_apps.findText(pra)
        if idx > -1:
            self.reader_apps.setCurrentIndex(idx)

        # Initially set caching_gb visibility
        self.reader_app_changed(idx)

        self.reader_apps.blockSignals(False)

        # Init the plugin tab to currently selected reader app
        self.reader_apps.currentIndexChanged.connect(self.show_plugin_tab)
        self.show_plugin_tab(None)

        # Diagnostics opt-out
        self.diagnostic_options_gb.setVisible(False)

        # Connect the Support buttons
        self.support_pb.clicked.connect(self.support_forum)
        self.support_pb.setIcon(QIcon(I('help.png')))
        self.diagnostics_pb.setIcon(QIcon(I('dialog_information.png')))

        if (self.available_space == -1 or
            (pra == 'Marvin' and getattr(self.parent, 'cached_books', None) is None)):
            self.diagnostics_pb.setEnabled(False)
        else:
            self.diagnostics_pb.clicked.connect(self.device_diagnostics)

    """
    def restart_required(self, *args):
        self._log_location()
    """
    def device_booklist_caching_changed(self, enabled):
        self._log_location(bool(enabled))
        self.device_booklist_cache_limit_sb.setEnabled(bool(enabled))

    def device_caching_allocation_changed(self, val):
        '''
        Compute and display allocated space
        '''
        self.allocation_factor = val
        allocated_space = int(self.available_space * (self.allocation_factor / 100))
        if allocated_space > 1024 * 1024 * 1024:
            allocated_space = allocated_space / (1024 * 1024 * 1024)
            fmt_str = "({:.2f} GB)"
        else:
            allocated_space = int(allocated_space / (1024 * 1024))
            fmt_str = "({:,} MB)"
        self.allocated_space_label.setText(fmt_str.format(allocated_space))

    def device_diagnostics(self):
        '''
        prefs
        installed plugins
        device book count
        library book count
        device info
        caching
        '''
        def _add_available_space():
            available = self.available_space
            if available > 1024 * 1024 * 1024:
                available = available / (1024 * 1024 * 1024)
                fmt_str = "{:.2f} GB"
            else:
                available = int(available / (1024 * 1024))
                fmt_str = "{:,} MB"
            device_profile['available_space'] = fmt_str.format(available)

        def _add_cache_files():
            # Cache files
            # Marvin:
            #   Library/mainDb.sqlite
            #   Library/calibre.mm/booklist.db
            #   Library/calibre.mm/content_hashes.db
            # GoodReader, GoodReader 4, Kindle:
            #   Library/calibre_metadata.sqlite
            # Local:
            #   <calibre resource dir>/iOS_reader_applications_resources/booklist.db
            #   <calibre resource dir>/Marvin_XD_resources/*_cover_hashes.json
            #   <calibre resource dir>/Marvin_XD_resources/installed_books.zip

            from datetime import datetime

            def _get_ios_stats(path):
                mtime = st_size = None
                stats = self.parent.ios.stat(path)
                if stats:
                    st_size = int(stats['st_size'])
                    d = datetime.fromtimestamp(int(stats['st_mtime']))
                    mtime = d.strftime('%Y-%m-%d %H:%M:%S')
                return {'mtime': mtime, 'size': st_size}

            def _get_os_stats(path):
                mtime = st_size = None
                if os.path.exists(path):
                    stats = os.stat(path)
                    st_size = stats.st_size
                    d = datetime.fromtimestamp(stats.st_mtime)
                    mtime = d.strftime('%Y-%m-%d %H:%M:%S')
                return {'mtime': mtime, 'size': st_size}

            cache_files = {}

            if device_profile['prefs']['preferred_reader_app'] == 'Marvin':
                ''' Marvin-specific cache files '''
                cache_files['mainDb.sqlite (remote)'] = _get_ios_stats('/Library/mainDb.sqlite')
                cache_files['mainDb.sqlite (local)'] = _get_os_stats(self.parent.local_db_path)
                cache_files['booklist.db (remote)'] = _get_ios_stats('Library/calibre.mm/booklist.db')
                cache_files['mxd_content_hashes.db (remote)'] = _get_ios_stats('Library/calibre.mm/content_hashes.db')

                # booklist.db from iOSRA resources
                path = os.path.join(self.parent.resources_path, 'booklist.db')
                cache_files['booklist.db (local)'] = _get_os_stats(path)

                #installed_books.zip from MXD resources
                mxd_resources_path = os.path.join(config_dir, 'plugins', "Marvin_XD_resources")
                path = os.path.join(mxd_resources_path, 'installed_books.zip')
                cache_files['mxd_installed_books.zip'] = _get_os_stats(path)

                # Per-device cover hashes
                import glob
                pattern = mxd_resources_path + "/*_cover_hashes.json"
                for path in glob.glob(pattern):
                    ans = _get_os_stats(path)
                    name = path.rsplit(os.path.sep)[-1]
                    cache_files['mxd_{}'.format(name)] = ans

            elif device_profile['prefs']['preferred_reader_app'] in ['GoodReader', 'GoodReader 4', 'Kindle']:
                cache_files['calibre_metadata.sqlite (remote)'] = _get_ios_stats('Library/calibre_metadata.sqlite')
                cache_files['calibre_metadata.sqlite (local)'] = _get_os_stats(self.parent.local_metadata)

            device_profile['cache_files'] = cache_files

        def _add_caching():
            device_caching = {}
            device_caching_enabled = self.prefs.get('device_booklist_caching')
            allocation_factor = self.prefs.get('device_booklist_cache_limit')
            device_caching['enabled'] = device_caching_enabled
            device_caching['allocation_factor'] = allocation_factor

            allocated_space = int(self.available_space * (allocation_factor / 100))
            if allocated_space > 1024 * 1024 * 1024:
                allocated_space = allocated_space / (1024 * 1024 * 1024)
                fmt_str = "{:.2f} GB"
            else:
                allocated_space = int(allocated_space / (1024 * 1024))
                fmt_str = "{:,} MB"
            device_caching['allocated_space'] = fmt_str.format(allocated_space)
            device_profile['device_caching'] = device_caching

        def _add_installed_plugins():
            # installed plugins
            from calibre.customize.ui import initialized_plugins
            user_installed_plugins = {}
            for plugin in initialized_plugins():
                path = getattr(plugin, 'plugin_path', None)
                if path is not None:
                    name = getattr(plugin, 'name', None)
                    if name == self.parent.name:
                        continue
                    author = getattr(plugin, 'author', None)
                    version = getattr(plugin, 'version', None)
                    user_installed_plugins[name] = {'author': author, 'version': "{0}.{1}.{2}".format(*version)}
            device_profile['user_installed_plugins'] = user_installed_plugins

        def _add_device_book_count():
            # Device book count
            try:
                device_profile['device_book_count'] = len(self.parent.cached_books)
            except:
                device_profile['device_book_count'] = "parent.cached_books not found"

        def _add_device_info():
            cdp = self.parent.device_profile
            device_info = {}
            all_fields = ['DeviceClass', 'DeviceColor', 'DeviceName', 'FSBlockSize',
                          'FSFreeBytes', 'FSTotalBytes', 'FirmwareVersion', 'HardwareModel',
                          'ModelNumber', 'PasswordProtected', 'ProductType', 'ProductVersion',
                          'SerialNumber', 'TimeIntervalSince1970', 'TimeZone',
                          'TimeZoneOffsetFromUTC', 'UniqueDeviceID']
            superfluous = ['DeviceClass', 'DeviceColor', 'FSBlockSize', 'HardwareModel',
                           'SerialNumber', 'TimeIntervalSince1970', 'TimeZoneOffsetFromUTC',
                           'UniqueDeviceID', 'ModelNumber']
            for item in sorted(cdp):
                if item in superfluous:
                    continue
                if item in ['FSTotalBytes', 'FSFreeBytes']:
                    device_info[item] = int(cdp[item])
                else:
                    device_info[item] = cdp[item]
            device_profile['device_info'] = device_info

        def _add_library_profile():
            library_profile = {}
            cdb = self.gui.current_db
            library_profile['epubs'] = len(cdb.search_getting_ids('formats:EPUB', ''))
            library_profile['pdfs'] = len(cdb.search_getting_ids('formats:PDF', ''))
            library_profile['mobis'] = len(cdb.search_getting_ids('formats:MOBI', ''))

            device_profile['library_profile'] = library_profile

        def _add_load_time():
            elapsed = _seconds_to_time(self.parent.load_time)
            formatted = "{0:02d}:{1:02d}".format(int(elapsed['mins']), int(elapsed['secs']))
            device_profile['load_time'] = formatted

        def _add_iOSRA_version():
            device_profile['iOSRA_version'] = "{0}.{1}.{2}".format(*self.parent.version)

        def _add_platform_profile():
            # Platform info
            import platform
            from calibre.constants import (__appname__, get_version, isportable, isosx,
                                           isfrozen, is64bit, iswindows)
            calibre_profile = "{0} {1}{2} isfrozen:{3} is64bit:{4}".format(
                __appname__, get_version(),
                ' Portable' if isportable else '', isfrozen, is64bit)
            device_profile['CalibreProfile'] = calibre_profile

            platform_profile = "{0} {1} {2}".format(
                platform.platform(), platform.system(), platform.architecture())
            device_profile['PlatformProfile'] = platform_profile

            try:
                if iswindows:
                    os_profile = "Windows {0}".format(platform.win32_ver())
                    if not is64bit:
                        try:
                            import win32process
                            if win32process.IsWow64Process():
                                os_profile += " 32bit process running on 64bit windows"
                        except:
                            pass
                elif isosx:
                    os_profile = "OS X {0}".format(platform.mac_ver()[0])
                else:
                    os_profile = "Linux {0}".format(platform.linux_distribution())
            except:
                import traceback
                self._log(traceback.format_exc())
                os_profile = "unknown"

            device_profile['OSProfile'] = os_profile

        def _add_prefs():
            prefs = {'created_under': self.prefs.get('plugin_version')}
            for pref in sorted(self.prefs.keys()):
                if pref == 'plugin_version':
                    continue
                prefs[pref] = self.prefs.get(pref, None)
            device_profile['prefs'] = prefs

        def _format_cache_files_info():
            max_fs_width = max([len(v) for v in device_profile['cache_files'].keys()])
            max_size_width = 12
            max_ts_width = 20

            args = {'subtitle': " Caches ",
                    'separator_width': separator_width,
                    'report_time_label': "report generated",
                    'report_time': report_time,
                    'fs_width': max_fs_width + 1,
                    'ts_width': max_ts_width + 1}
            for cache_fs in device_profile['cache_files']:
                args[cache_fs] = "{%s}" % cache_fs

            TEMPLATE = ('\n{subtitle:-^{separator_width}}\n'
                        ' {report_time_label:{fs_width}} {report_time:{ts_width}}\n')

            # iOSRA cache files
            if device_profile['prefs']['preferred_reader_app'] == 'Marvin':

                ans = TEMPLATE.format(**args)
                # iOSRA cache files
                TEMPLATE = 'iOSRA\n'
                for cache_fs, d in sorted(device_profile['cache_files'].iteritems(), key=lambda item: item[0].lower()):
                    if cache_fs.startswith('mxd_'):
                        continue
                    try:
                        TEMPLATE += " {0:{1}} {2:{3}} {4:{5},}\n".format(
                            cache_fs, max_fs_width + 1,
                            d['mtime'], max_ts_width + 1,
                            d['size'], max_size_width + 1)
                    except:
                        TEMPLATE += " {0:{1}} {2:{3}} {4:{5},}\n".format(
                            cache_fs, max_fs_width + 1,
                            d['mtime'], max_ts_width + 1,
                            0, max_size_width + 1)

                ans += TEMPLATE.format(**args)
                ans += '\n'

                # MXD cache files
                TEMPLATE = 'MXD\n'
                for cache_fs, d in sorted(device_profile['cache_files'].iteritems(), key=lambda item: item[0].lower()):
                    if not cache_fs.startswith('mxd_'):
                        continue
                    try:
                        TEMPLATE += " {0:{1}} {2:{3}} {4:{5},}\n".format(
                            cache_fs[len('mxd_'):], max_fs_width + 1,
                            d['mtime'], max_ts_width + 1,
                            d['size'], max_size_width + 1)
                    except:
                        TEMPLATE += " {0:{1}} {2:{3}} {4:{5},}\n".format(
                            cache_fs[len('mxd_'):], max_fs_width + 1,
                            d['mtime'], max_ts_width + 1,
                            0, max_size_width + 1)
                ans += TEMPLATE.format(**args)

            else:
                # Kindle, GoodReader, GoodReader4
                ans = TEMPLATE.format(**args)
                TEMPLATE = ''
                for cache_fs, d in sorted(device_profile['cache_files'].iteritems(), key=lambda item: item[0].lower()):
                    TEMPLATE += " {0:{1}} {2:{3}} {4:{5},}\n".format(
                        cache_fs, max_fs_width + 1,
                        d['mtime'], max_ts_width + 1,
                        d['size'], max_size_width + 1)
                ans += TEMPLATE.format(**args)
            return ans

        def _format_caching_info():
            args = {'subtitle': " Device booklist caching ",
                    'separator_width': separator_width,
                    'enabled': device_profile['device_caching']['enabled'],
                    'available_space': device_profile['available_space'],
                    'allocation_factor': device_profile['device_caching']['allocation_factor'],
                    'allocated_space': device_profile['device_caching']['allocated_space']
                    }
            TEMPLATE = (
                '\n{subtitle:-^{separator_width}}\n'
                ' enabled: {enabled}\n'
                ' available space: {available_space}\n'
                ' allocation factor: {allocation_factor}%\n'
                ' allocated space: {allocated_space}\n'
                )
            return TEMPLATE.format(**args)

        def _format_device_info():
            args = {'subtitle': " iDevice ",
                    'separator_width': separator_width,
                    'iOSRA_version': device_profile['iOSRA_version'],
                    'iOS_version': device_profile['device_info']['ProductVersion'],
                    'ProductType': device_profile['device_info']['ProductType'],
                    'FSTotalBytes': device_profile['device_info']['FSTotalBytes'],
                    'FSFreeBytes': device_profile['device_info']['FSFreeBytes'],
                    'PasswordProtected': device_profile['device_info']['PasswordProtected']
                    }
            TEMPLATE = (
                '\n{subtitle:-^{separator_width}}\n'
                ' iOSRA version: {iOSRA_version}\n'
                ' iOS version: {iOS_version}\n'
                ' model: {ProductType}\n'
                ' FSTotalBytes: {FSTotalBytes:,}\n'
                ' FSFreeBytes: {FSFreeBytes:,}\n'
                ' PasswordProtected: {PasswordProtected}\n'
                )
            return TEMPLATE.format(**args)

        def _format_installed_plugins_info():

            args = {'subtitle': " Installed plugins ",
                    'separator_width': separator_width,
                    }
            ans = '\n{subtitle:-^{separator_width}}\n'.format(**args)

            if device_profile['user_installed_plugins']:
                for plugin in device_profile['user_installed_plugins']:
                    args[plugin] = "{{{0}}}".format(plugin)
                max_name_width = max([len(v) for v in device_profile['user_installed_plugins'].keys()])
                max_author_width = max([len(d['author']) for d in device_profile['user_installed_plugins'].values()])
                max_version_width = max([len(d['version']) for d in device_profile['user_installed_plugins'].values()])
                TEMPLATE = ''
                for plugin, d in sorted(device_profile['user_installed_plugins'].iteritems(), key=lambda item: item[0].lower()):
                    TEMPLATE += " {0:{1}} {2:{3}}\n".format(
                        plugin, max_name_width + 1,
                        d['version'], max_version_width + 1)
                ans += TEMPLATE.format(**args)

            return ans

        def _format_prefs_info():
            args = {'subtitle': " Prefs ",
                    'separator_width': separator_width}
            for pref in device_profile['prefs']:
                args[pref] = device_profile['prefs'][pref]

            TEMPLATE = '\n{subtitle:-^{separator_width}}\n'
            for pref in sorted(device_profile['prefs'].keys()):
                TEMPLATE += " {0}: {{{1}}}\n".format(pref, pref)

            return TEMPLATE.format(**args)

        def _format_reader_app_info():
            args = {'subtitle': " {} ".format(device_profile['prefs']['preferred_reader_app']),
                    'separator_width': separator_width,
                    'device_books': device_profile['device_book_count'],
                    'load_time': device_profile['load_time']
                    }
            TEMPLATE = (
                '\n{subtitle:-^{separator_width}}\n'
                ' device books: {device_books}\n'
                ' initialization time: {load_time}\n'
                )
            return TEMPLATE.format(**args)

        def _format_system_info():
            # System information
            args = {'subtitle': " {} ".format(report_time),
                    'separator_width': separator_width,
                    'CalibreProfile': device_profile['CalibreProfile'],
                    'OSProfile': device_profile['OSProfile'],
                    'library_books': "{0:,} EPUBs, {1:,} MOBIs, {2:,} PDFs".format(
                        device_profile['library_profile']['epubs'],
                        device_profile['library_profile']['mobis'],
                        device_profile['library_profile']['pdfs'])
                    }

            TEMPLATE = (
                '{subtitle:-^{separator_width}}\n'
                ' {CalibreProfile}\n'
                ' {OSProfile}\n'
                ' library: {library_books}\n')
            return TEMPLATE.format(**args)

        def _seconds_to_time(s):
            years, s = divmod(s, 31556952)
            min, s = divmod(s, 60)
            h, min = divmod(min, 60)
            d, h = divmod(h, 24)
            ans = {'days': d, 'hours': h, 'mins': min, 'secs': s}
            return ans

        # ~~~ Entry point ~~~
        self._log_location()
        report_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Collect the diagnostic information
        device_profile = {}
        _add_platform_profile()
        _add_iOSRA_version()
        _add_prefs()
        _add_installed_plugins()
        _add_device_book_count()
        _add_load_time()
        _add_library_profile()
        _add_device_info()
        _add_available_space()
        _add_caching()
        _add_cache_files()

        # Format for printing
        det_msg = ''
        separator_width = 80

        det_msg += _format_system_info()
        det_msg += _format_device_info()
        det_msg += _format_reader_app_info()
        det_msg += _format_prefs_info()
        det_msg += _format_installed_plugins_info()
        det_msg += _format_caching_info()
        det_msg += _format_cache_files_info()

        # Present the results
        title = "Device diagnostics"
        msg = (
               '<p>Device diagnostics for {}.</p>'
              ).format(self.parent.ios.device_name)

        # Set dialog det_msg to monospace
        dialog = info_dialog(self.gui, title, msg, det_msg=det_msg)
        font = QFont('monospace')
        font.setFixedPitch(True)
        dialog.det_msg.setFont(font)
        dialog.exec_()

    def reader_app_changed(self, index):
        '''
        If the selected app supports caching app, show caching controls
        '''
        preferred = str(self.reader_apps.itemText(index))
        self._log_location(preferred)
        self.caching_gb.setVisible(preferred in self.CACHING_READER_APPS)

    def save_settings(self):
        self._log_location()

        # Save cache settings
        self.prefs.set('device_booklist_caching', self.device_booklist_caching_cb.isChecked())
        self.prefs.set('device_booklist_cache_limit', self.device_booklist_cache_limit_sb.value())

        # Save general settings
        self.prefs.set('plugin_diagnostics', self.plugin_diagnostics.isChecked())
        self.prefs.set('debug_plugin', self.debug_plugin.isChecked())
        self.prefs.set('debug_libimobiledevice', self.debug_libimobiledevice.isChecked())
        self.prefs.set('preferred_reader_app', str(self.reader_apps.currentText()))

        for pw in self.widgets:
            opts = pw.options()
            self._log_location("%s: %s" % (pw.name, opts))
            for opt in opts:
                #self._log_location("saving '%s' as %s" % (opt, repr(opts[opt])))
                self.prefs.set(opt, opts[opt])

    def show_plugin_tab(self, idx):
        self._log_location(idx)
        cf = unicode(self.reader_apps.currentText()).lower()
        while self.tabs.count() > 1:
            self.tabs.removeTab(1)
        for pw in self.widgets:
            if cf == pw.name:
                self._log("adding '%s' tab" % pw.TITLE)
                self.tabs.addTab(pw, pw.TITLE)
                self.current_plugin = pw
                break

    def support_forum(self):
        '''
        Open iOSRA support thread at MobileRead
        http://www.mobileread.com/forums/showthread.php?t=241143
        '''
        support_thread = "http://www.mobileread.com/forums/showthread.php?t=241143"
        open_url(QUrl(support_thread))

    def validate(self):
        '''
        '''
        self._log_location()

        return True

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


# For testing ConfigWidget, run from command line:
# cd ~/Documents/calibredev/iOS_reader_applications
# calibre-debug config.py 2> >(grep -v 'CoreAnimation\|CoreText\|modalSession' 1>&2)
# Search 'iOS Reader Apps'
if __name__ == '__main__':
    try:
        from PyQt5.Qt import QApplication
    except ImportError:
        from PyQt4.Qt import QApplication
    from calibre.gui2.preferences import test_widget
    app = QApplication([])
    test_widget('Advanced', 'Plugins')

