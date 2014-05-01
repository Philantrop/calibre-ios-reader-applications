#!/usr/bin/env python
# coding: utf-8

from __future__ import (unicode_literals, division, absolute_import,
                        print_function)

import cStringIO, importlib, re, os, sys

from calibre.devices.usbms.driver import debug_print
from calibre.gui2 import show_restart_warning
from calibre.gui2.ui import get_gui
from calibre.utils.config import config_dir

from PyQt4.Qt import QWidget

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
        self.support_label.setOpenExternalLinks(True)
        self.version = parent.version

        # Restore the caching settings
        self.device_booklist_caching_cb.setChecked(self.prefs.get('device_booklist_caching', False))
        self.device_booklist_cache_limit_sb.setValue(self.prefs.get('device_booklist_cache_limit', 10.00))

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
        pref = self.prefs.get('preferred_reader_app', '')
        idx = self.reader_apps.findText(pref)
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

    """
    def restart_required(self, *args):
        self._log_location()
    """
    def device_booklist_caching_changed(self, enabled):
        self._log_location(bool(enabled))
        self.device_booklist_cache_limit_sb.setEnabled(bool(enabled))

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
    from PyQt4.Qt import QApplication
    from calibre.gui2.preferences import test_widget
    app = QApplication([])
    test_widget('Advanced', 'Plugins')

