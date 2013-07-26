#!/usr/bin/env python
# coding: utf-8

from __future__ import (unicode_literals, division, absolute_import,
                        print_function)

__license__ = 'GPL v3'
__copyright__ = '2010, Gregory Riker'
__docformat__ = 'restructuredtext en'

import os, importlib, sys
from functools import partial
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


class PluginWidget(QWidget, Ui_Form):
    LOCATION_TEMPLATE = "{cls}:{func}({arg1}) {arg2}"
    TITLE = 'Marvin options'

    def __init__(self, parent):
        QWidget.__init__(self, parent=None)
        self.setupUi(self)
        self.gui = parent.gui
        self.parent = parent
        self.prefs = parent.prefs
        self.verbose = parent.verbose
        self._log_location()

    def collections_selection_changed(self, value):
        '''
        '''
        cf = str(self.collections_comboBox.currentText())
        self.prefs.set('marvin_collection_field', cf)

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
            cfim = self.gui.current_db.metadata_for_field(cf)['is_multiple']
            #self._log("%s: %s (%s)" % (cf, cfn, cft))
            if cft in ['enumeration', 'text'] and bool(cfim):
                eligible_custom_fields.append(cfn)
        self.eligible_custom_fields = sorted(eligible_custom_fields, key=lambda s: s.lower())

        # Populate Collections comboBox
        self.collections_comboBox.setToolTip("Custom column for Marvin collections")
        self.collections_comboBox.addItem('')
        self.collections_comboBox.addItems(self.eligible_custom_fields)
        cf = self.prefs.get('marvin_collection_field', None)
        if cf:
            idx = self.collections_comboBox.findText(cf)
            if idx > -1:
                self.collections_comboBox.setCurrentIndex(idx)

        # Hook changes to Collections
        self.collections_comboBox.currentIndexChanged.connect(self.collections_selection_changed)

        # Init the wizard toolbutton
        self.collections_wizard_tb.setIcon(QIcon(I('wizard.png')))
        self.collections_wizard_tb.setToolTip("Create a custom column for Collections")
        self.collections_wizard_tb.clicked.connect(partial(self.launch_cc_wizard, 'Collections'))

        # Add the Help icon to the help button
        self.help_button.setIcon(QIcon(I('help.png')))
        self.help_button.clicked.connect(self.show_help)

    def launch_cc_wizard(self, column_type):
        '''
        '''
        self._log_location()
        dialog_resources_path = os.path.join(self.parent.resources_path, 'widgets')
        klass = os.path.join(dialog_resources_path, 'cc_wizard.py')
        if os.path.exists(klass):
            #self._log("importing CC Wizard dialog from '%s'" % klass)
            sys.path.insert(0, dialog_resources_path)
            this_dc = importlib.import_module('cc_wizard')
            sys.path.remove(dialog_resources_path)
            dlg = this_dc.CustomColumnWizard(self, column_type, verbose=True)
            dlg.exec_()

            if dlg.modified_column:
                self._log("modified_column: %s" % dlg.modified_column)

                destination = dlg.modified_column['destination']
                label = dlg.modified_column['label']
                previous = dlg.modified_column['previous']
                source = dlg.modified_column['source']

                if source == 'Collections':
                    all_items = [str(self.collections_comboBox.itemText(i))
                                 for i in range(self.collections_comboBox.count())]
                    if previous and previous in all_items:
                        all_items.remove(previous)
                    all_items.append(destination)

                    self.collections_comboBox.clear()
                    self.collections_comboBox.addItems(sorted(all_items, key=lambda s: s.lower()))
                    idx = self.collections_comboBox.findText(destination)
                    if idx > -1:
                        self.collections_comboBox.setCurrentIndex(idx)

                    # Save Collection field manually in case user cancels
                    self.prefs.set('marvin_collection_field', destination)

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
        #opts['marvin_enabled_collection_fields'] = self.prefs.get('marvin_enabled_collection_fields', [])

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

