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
from calibre_plugins.ios_reader_apps import get_cc_mapping, set_cc_mapping
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
    WIZARD_PROFILES = {
        'Collections': {
            'label': 'mm_collections',
            'datatype': 'text',
            'display': {u'is_names': False},
            'is_multiple': True
            },
        'Word count': {
            'label': 'mm_word_count',
            'datatype': 'int',
            'display': {u'number_format': u'{0:n}'},
            'is_multiple': False
            }
         }

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
        cf = unicode(self.collections_comboBox.currentText())
        field = None
        if cf:
            datatype = self.WIZARD_PROFILES['Collections']['datatype']
            eligible_collection_fields = self.get_eligible_custom_fields([datatype])
            field = eligible_collection_fields[cf]
        set_cc_mapping('marvin_collections', combobox=cf, field=field)

    def get_eligible_custom_fields(self, eligible_types=[], is_multiple=None):
        '''
        Discover qualifying custom fields for eligible_types[]
        '''
        #self._log_location(eligible_types)

        eligible_custom_fields = {}
        for cf in self.gui.current_db.custom_field_keys():
            cft = self.gui.current_db.metadata_for_field(cf)['datatype']
            cfn = self.gui.current_db.metadata_for_field(cf)['name']
            cfim = self.gui.current_db.metadata_for_field(cf)['is_multiple']
            #self._log("cf: %s  cft: %s  cfn: %s cfim: %s" % (cf, cft, cfn, cfim))
            if cft in eligible_types:
                if is_multiple is not None:
                    if bool(cfim) == is_multiple:
                        eligible_custom_fields[cfn] = cf
                else:
                    eligible_custom_fields[cfn] = cf
        return eligible_custom_fields

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

        # ~~~~~~ Populate/restore Collections comboBox ~~~~~~
        self.populate_collections()
        self.collections_comboBox.setToolTip("Custom column for Marvin collections")

        # Hook changes to Collections
        self.collections_comboBox.currentIndexChanged.connect(self.collections_selection_changed)

        # Init the Collections wizard toolbutton
        self.collections_wizard_tb.setIcon(QIcon(I('wizard.png')))
        self.collections_wizard_tb.setToolTip("Create a custom column for Collections")
        self.collections_wizard_tb.clicked.connect(partial(self.launch_cc_wizard, 'Collections'))

        # ~~~~~~ Populate/restore Word count comboBox ~~~~~~
        self.populate_word_count()
        self.word_count_comboBox.setToolTip("Custom column for Word count")

        # Hook changes to Word count
        self.word_count_comboBox.currentIndexChanged.connect(self.word_count_selection_changed)

        # Init the Word count wizard
        self.word_count_wizard_tb.setIcon(QIcon(I('wizard.png')))
        self.word_count_wizard_tb.setToolTip('Create a custom column for Word count')
        self.word_count_wizard_tb.clicked.connect(partial(self.launch_cc_wizard, 'Word count'))

        # ~~~~~~ Help ~~~~~~
        # Add the Help icon to the help button
        self.help_button.setIcon(QIcon(I('help.png')))
        self.help_button.clicked.connect(self.show_help)

        # Clean up JSON file < v1.3.0
        prefs_version = self.prefs.get("plugin_version", "0.0.0")
        if prefs_version < "1.3.0":
            self._log("Updating prefs to %d.%d.%d" % self.parent.version)
            for obsolete_setting in [
                'marvin_collection_field', 'marvin_collection_lookup',
                'marvin_word_count_field', 'marvin_word_count_lookup']:
                if self.prefs.get(obsolete_setting, None) is not None:
                    self._log("removing obsolete entry '{0}'".format(obsolete_setting))
                    self.prefs.__delitem__(obsolete_setting)
            self.prefs.set('plugin_version', "%d.%d.%d" % self.parent.version)

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
            dlg = this_dc.CustomColumnWizard(self,
                                             column_type,
                                             self.WIZARD_PROFILES[column_type],
                                             verbose=True)
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
                    set_cc_mapping('marvin_collections', combobox=destination, field=label)

                if source == 'Word count':
                    all_items = [str(self.word_count_comboBox.itemText(i))
                                 for i in range(self.word_count_comboBox.count())]
                    if previous and previous in all_items:
                        all_items.remove(previous)
                    all_items.append(destination)

                    self.word_count_comboBox.clear()
                    self.word_count_comboBox.addItems(sorted(all_items, key=lambda s: s.lower()))
                    idx = self.word_count_comboBox.findText(destination)
                    if idx > -1:
                        self.word_count_comboBox.setCurrentIndex(idx)

                    # Save Word count field manually in case user cancels
                    set_cc_mapping('marvin_word_count', combobox=destination, field=label)

    def options(self):
        '''
        Return a dict of current options
        Custom fields are not returned in opts, they are available in prefs
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

    def populate_collections(self):
        datatype = self.WIZARD_PROFILES['Collections']['datatype']
        eligible_collection_fields = self.get_eligible_custom_fields([datatype])
        self.collections_comboBox.addItems([''])
        ecf = sorted(eligible_collection_fields.keys(), key=lambda s: s.lower())
        self.collections_comboBox.addItems(ecf)

        # Retrieve stored value
        existing = get_cc_mapping('marvin_collections', 'combobox')
        if existing:
            ci = self.collections_comboBox.findText(existing)
            self.collections_comboBox.setCurrentIndex(ci)

    def populate_word_count(self):
        '''
        '''
        datatype = self.WIZARD_PROFILES['Word count']['datatype']
        eligible_word_count_fields = self.get_eligible_custom_fields([datatype])
        self.word_count_comboBox.addItems([''])
        ecf = sorted(eligible_word_count_fields.keys(), key=lambda s: s.lower())
        self.word_count_comboBox.addItems(ecf)

        # Retrieve stored value
        existing = get_cc_mapping('marvin_word_count', 'combobox')
        if existing:
            ci = self.word_count_comboBox.findText(existing)
            self.word_count_comboBox.setCurrentIndex(ci)

    def show_help(self):
        self._log_location()
        path = os.path.join(self.parent.resources_path, 'help', 'marvin.html')
        open_url(QUrl.fromLocalFile(path))

    def word_count_selection_changed(self, value):
        '''
        Store both the displayed field name and lookup value to prefs
        '''
        cf = unicode(self.word_count_comboBox.currentText())
        field = None
        if cf:
            datatype = self.WIZARD_PROFILES['Word count']['datatype']
            eligible_word_count_fields = self.get_eligible_custom_fields([datatype])
            field = eligible_word_count_fields[cf]
        set_cc_mapping('marvin_word_count', combobox=cf, field=field)

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

