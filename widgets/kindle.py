#!/usr/bin/env python
# coding: utf-8

from __future__ import (unicode_literals, division, absolute_import,
                        print_function)

__license__ = 'GPL v3'
__copyright__ = '2013, Gregory Riker'
__docformat__ = 'restructuredtext en'

import os, importlib, sys

from calibre.gui2 import open_url, warning_dialog
from calibre.devices.usbms.driver import debug_print
from calibre_plugins.ios_reader_apps.config import widget_path
from calibre_plugins.ios_reader_apps import (
    KINDLE_ENABLED_FORMATS, KINDLE_SUPPORTED_FORMATS)

from PyQt4.Qt import Qt, QListWidgetItem, QVariant, QWidget

# Import Ui_Form from form generated dynamically during initialization
if True:
    sys.path.insert(0, widget_path)
    from kindle_ui import Ui_Form
    sys.path.remove(widget_path)


class PluginWidget(QWidget, Ui_Form):
    LOCATION_TEMPLATE = "{cls}:{func}({arg1}) {arg2}"
    TITLE = 'Kindle options'

    def __init__(self, parent):
        QWidget.__init__(self, parent=None)
        self.setupUi(self)
        self.gui = parent.gui
        self.parent = parent
        self.prefs = parent.prefs
        self.verbose = parent.verbose

    def initialize(self, name):
        '''
        Retrieve plugin-specific settings from general prefs store
        Need to store order of all possible formats, enabled formats
        '''
        self.name = name

        # Allow for updated KINDLE_SUPPORTED_FORMATS
        all_formats = self.prefs.get('kindle_supported_formats', KINDLE_SUPPORTED_FORMATS)
        if len(all_formats) != len(KINDLE_SUPPORTED_FORMATS):
            all_formats = KINDLE_SUPPORTED_FORMATS

        enabled_formats = self.prefs.get('kindle_enabled_formats', KINDLE_ENABLED_FORMATS)

        for format in all_formats:
            item = QListWidgetItem(format, self.columns)
            item.setData(Qt.UserRole, QVariant(format))
            item.setFlags(Qt.ItemIsEnabled|Qt.ItemIsUserCheckable|Qt.ItemIsSelectable)
            item.setCheckState(Qt.Checked if format in enabled_formats else Qt.Unchecked)

        self.column_up.clicked.connect(self.up_column)
        self.column_down.clicked.connect(self.down_column)

    def down_column(self):
        idx = self.columns.currentRow()
        if idx < self.columns.count()-1:
            self.columns.insertItem(idx+1, self.columns.takeItem(idx))
            self.columns.setCurrentRow(idx+1)

    def options(self):
        '''
        Return a dict of current options
        '''
        opts = {}
        all_formats = [unicode(self.columns.item(i).data(Qt.UserRole).toString())
            for i in range(self.columns.count())]
        enabled_formats = [unicode(self.columns.item(i).data(Qt.UserRole).toString())
            for i in range(self.columns.count()) if self.columns.item(i).checkState()==Qt.Checked]
        opts['kindle_supported_formats'] = all_formats
        opts['kindle_enabled_formats'] = enabled_formats
        return opts

    def up_column(self):
        idx = self.columns.currentRow()
        if idx > 0:
            self.columns.insertItem(idx-1, self.columns.takeItem(idx))
            self.columns.setCurrentRow(idx-1)


    # ~~~~~~ Helpers ~~~~~~
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

        debug_print(self.LOCATION_TEMPLATE.format(
            #cls=self.__class__.__name__,
            cls = self.TITLE,
            func=sys._getframe(1).f_code.co_name,
            arg1=arg1, arg2=arg2))

