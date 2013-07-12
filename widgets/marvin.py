#!/usr/bin/env python
# coding: utf-8

from __future__ import (unicode_literals, division, absolute_import,
                        print_function)

__license__ = 'GPL v3'
__copyright__ = '2010, Gregory Riker'
__docformat__ = 'restructuredtext en'

import os, sys
from urllib2 import FileHandler

from calibre.gui2 import open_url, warning_dialog
from calibre.devices.usbms.driver import debug_print
from calibre_plugins.ios_reader_apps.config import widget_path

from PyQt4.Qt import (QAbstractItemView, QCheckBox, QIcon, QLineEdit,
                      QListWidget, QListWidgetItem, QRadioButton,
                      Qt, QUrl, QVariant, QWidget)

# Import Ui_Form from form generated dynamically during initialization
if True:
    sys.path.insert(0, widget_path)
    from marvin_ui import Ui_Form
    sys.path.remove(widget_path)


class EnabledCollectionsListWidget(QListWidget):
    def __init__(self, parent=None):
        QListWidget.__init__(self, parent)
        self.parent = parent
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.populate()

    def populate(self):
        self.clear()
        enabled_collection_fields = self.parent.prefs.get('marvin_enabled_collection_fields', [])
        for name in self.parent.eligible_custom_fields:
            item = QListWidgetItem(name, self)
            item.setData(Qt.UserRole, QVariant(name))
            if name in enabled_collection_fields:
                item.setCheckState(Qt.Checked)
            else:
                item.setCheckState(Qt.Unchecked)
            self.addItem(item)

    def get_enabled_items(self):
        enabled_items = []
        for x in xrange(self.count()):
            item = self.item(x)
            if item.checkState() == Qt.Checked:
                key = unicode(item.data(Qt.UserRole).toString()).strip()
                enabled_items.append(key)
        return enabled_items


class PluginWidget(QWidget, Ui_Form):
    LOCATION_TEMPLATE = "{cls}:{func}({arg1}) {arg2}"
    TITLE = 'Marvin Options'

    def __init__(self, parent):
        QWidget.__init__(self, parent=None)
        self.setupUi(self)
        self.gui = parent.gui
        self.parent = parent
        self.prefs = parent.prefs
        self.verbose = parent.verbose
        self._log_location()

    def initialize(self, name):
        '''
        Retrieve plugin-specific settings from general prefs store
        '''
        self.name = name
        self._log_location(name)

        # Restore saved prefs
        for pref in self.prefs:
            if pref.startswith(self.name) and hasattr(self, pref):
                opt_value = self.parent.prefs[pref]
                #self._log("setting pref '%s' to %s" % (pref, repr(opt_value)))
                if type(getattr(self, pref)) is QLineEdit:
                    getattr(self, pref).setText(', '.join(opt_value) if opt_value else '')
                elif type(getattr(self, pref)) is QCheckBox:
                    getattr(self, pref).setChecked(eval(str(opt_value)))
                elif type(getattr(self, pref)) is QRadioButton:
                    getattr(self, pref).setChecked(eval(str(opt_value)))

        # Get qualifying custom fields
        eligible_custom_fields = []
        for cf in self.gui.current_db.custom_field_keys():
            cft = self.gui.current_db.metadata_for_field(cf)['datatype']
            cfn = self.gui.current_db.metadata_for_field(cf)['name']
            #self._log("%s: %s (%s)" % (cf, cfn, cft))
            if cft in ['enumeration', 'text']:
                eligible_custom_fields.append(cfn)
        self.eligible_custom_fields = sorted(eligible_custom_fields, key=lambda s: s.lower())

        # Add collections to the layout
        self.enabled_collections_list = EnabledCollectionsListWidget(self)
        self.collections_layout.addWidget(self.enabled_collections_list)

        # Add the Help icon to the help button
        self.help_button.setIcon(QIcon(I('help.png')))
        self.help_button.clicked.connect(self.show_help)

    def options(self):
        '''
        Return a dict of current options
        '''
        self._log_location()
        opts = {}

        # Update policy: QRadioButtons
        opts['marvin_protect_rb'] = self.marvin_protect_rb.isChecked()
        opts['marvin_replace_rb'] = self.marvin_replace_rb.isChecked()
        opts['marvin_update_rb'] = self.marvin_update_rb.isChecked()

        # Enable editing Marvin collections directly
        opts['marvin_edit_collections_cb'] = self.marvin_edit_collections_cb.isChecked()

        # Enabled collections
        opts['marvin_enabled_collection_fields'] = self.enabled_collections_list.get_enabled_items()

        return opts

    def show_help(self):
        self._log_location()
        path = os.path.join(self.parent.resources_path, 'help', 'marvin.html')
        open_url(QUrl.fromLocalFile(path))

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

