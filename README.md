<!--This document is formatted in GitHub flavored markdown, tweaked for Github's
    presentation of the repo's README.md file. Documentation for GFM is at
    https://help.github.com/articles/github-flavored-markdown
    A semi-useful site for previewing GFM is available at
    http://tmpvar.com/markdown.html
-->

##Communication protocol for calibre and iOS reader applications##
This document provides details of the communication protocol for the _iOS reader applications_ device driver plugin for [calibre](https://github.com/kovidgoyal/calibre). The protocol was developed by Greg Riker and Kristian Guillaumier.

###General overview###
Apple’s iOS does not allow a host computer direct access to a connected iDevice’s folder structure. Various solutions are available to overcome this restriction, including jailbreaking the iDevice, or use of additional software, e.g. [iFunbox](http://www.i-funbox.com).

The _iOS reader applications_ device driver uses an open source code library  [libiMobileDevice](http://www.libimobiledevice.org) to access the iOS file system. The libiMobileDevice libraries for Linux, OS X and Windows (built from v1.1.5 source) are bundled with calibre (starting with version 0.9.31), along with glue code ([calibre.devices.idevice.libimobiledevice.py](https://github.com/kovidgoyal/calibre/blob/master/src/calibre/devices/idevice/libimobiledevice.py)). The libiMobileDevice libraries seem to work well with calibre under iOS 5.x and 6.x. Future releases of iOS may require updating the libiMobileDevice libraries.

The initial release of the _iOS reader applications_ device driver supports iBooks, [Marvin](http://marvinapp.com) and [GoodReader](http://goodreader.com/goodreader.html). iBooks support is implemented by calling calibre’s existing iTunes driver. Marvin support is implemented by loading overlays at runtime, specific to Marvin, discussed in more depth in [Device driver overlays](#device-driver-overlays). GoodReader support is included as an example of a modeless, calibre-unaware driver.

The device driver is designed to be extensible to support multiple iOS reader applications. Adding support for a new reader application requires two programming efforts:

- [Application implementation](#application-implementation) in the iOS application (written in Objective C) adds the ‘calibre connection’, polling for and responding to commands initiating in calibre.
- [Device driver overlays](#device-driver-overlays) contained in a single file (written in python) implementing calibre device driver functionality.

Alternatively, a device driver for a calibre-unaware reader application may be implemented without any application changes. This is suitable only for apps which monitor their sandbox folders for changes. GoodReader is an example of such an application. The GoodReader driver is discussed in detail in the [Calibre-unaware reader applications](#calibre-unaware-reader-applications) section.

---

###Communication protocol overview###
When calibre senses a connected USB device, it passes the USB fingerprint to each installed device driver, asking if the driver can handle the connected device. If a driver recognizes the USB fingerprint, it responds positively, and calibre connects to that device. After connection, the user can manage books installed on the device from calibre.

For ‘dumb’ devices (Kindle, Nook, Sony), the USB fingerprint is adequate to identify the device. Dumb devices are simply mounted as USB drives, and content is managed on the device by adding and deleting from the device’s document folder.

iDevices are handled as ‘smart’ devices. A user may have multiple reading applications installed on their iDevice. The driver must be able to accommodate multiple reader applications through a single USB fingerprint. The driver presents a _Preferred reader application_ combo box in its configuration dialog. When the driver initializes, it uses the selected reader application to determine which overlay methods to load.

It would also be possible to create individual calibre device drivers for each supported iOS reader application, but this would potentially require the calibre user to juggle multiple drivers all responding the to same USB fingerprints. Additionally, when Apple releases new iDevices, every driver would need to be updated with the new USB fingerprints. A unified driver is a bit more complicated for the programmer, but easier for the user.

The mechanism for sending commands from the driver to the application is implemented with a file-based command system.

- The _command staging folder_, in the application’s sandbox, is where the iOS application receives commands and reports status. All commands are written by the driver to the command staging folder. All status responses are written to the command staging folder by the application.

- The _document staging folder_, in the application’s sandbox, is where the application receives incoming books to be imported to the application’s library.

- The locations chosen for the command staging folder and the document staging folder within the application’s sandbox are determined by the implementer.

No commands will be sent by the driver until the application initiates its calibre connection mode and creates [<samp>connected.xml</samp>](#connectedxml). This signals the driver that the application is ready to respond to commands.

---

###Command files###
Commands are initiated by the driver. Status is reported by the application. Samples of all command and status files are available in the [XML command files](#xml-command-files) section.

**delete_books.xml**

- The driver writes a complete <samp>delete\_books.tmp</samp> in the command staging folder, then renames it to [<samp>delete\_books.xml</samp>](#delete_booksxml).
- The application processes the command, updating [<samp>status.xml</samp>](#statusxml) with its progress as it deletes the books in the manifest.
- The driver monitors <samp>status.xml</samp> for progress.
- The application deletes <samp>delete\_books.xml</samp> at the completion of the command.
- The driver deletes <samp>status.xml</samp> at the completion of the command.

**rebuild_collections.xml**

- _Implementation of this command is optional, and only required if the application supports collections._
- The driver writes a complete <samp>rebuild\_collections.tmp</samp> in the command staging folder, then renames it to [<samp>rebuild\_collections.xml</samp>](#rebuild_collectionsxml).
- The application processes the command, updating [<samp>status.xml</samp>](#statusxml) with its progress as it rebuilds the collection assignments.
- The driver monitors <samp>status.xml</samp> for progress.
- The application deletes <samp>rebuild\_collections.xml</samp> at the completion of the command.
- The driver deletes <samp>status.xml</samp> at the completion of the command.

**update_metadata.xml**

- The driver writes a complete <samp>update\_metadata.tmp</samp> in the command staging folder, then renames it to [<samp>update\_metadata.xml</samp>](#update_metadataxml).
- The application processes the command, updating [<samp>status.xml</samp>](#statusxml) with its progress as it updates the metadata for books in the manifest.
- The driver monitors <samp>status.xml</samp> for progress.
- The application deletes <samp>update_metadata.xml</samp> at the completion of the command.
- The driver deletes <samp>status.xml</samp> at the completion of the command.

**upload_books.xml**

- The driver copies the selected book(s) from calibre’s library to the document staging folder.
- The driver writes a complete <samp>upload\_books.tmp</samp> in the command staging folder, then renames it to [<samp>upload\_books.xml</samp>](#upload_booksxml).
- The application processes the command, updating [<samp>status.xml</samp>](#statusxml) with its progress as it imports the books listed in the manifest from the document staging folder.
- The driver monitors <samp>status.xml</samp> for progress.
- The application deletes <samp>upload_books.xml</samp> at the completion of the command.
- The driver deletes <samp>status.xml</samp> at the completion of the command.

---

###XML command files###
All XML command files are UTF8 encoded with a BOM header.

####connected.xml####
    <?xml version='1.0' encoding='utf-8?>
    <connection timestamp='1364148473.0'>
        <state>[offline|online]</state>
    </connection>

- <samp>timestamp</samp>: a Unix timestamp representing the current time, as a float number of seconds, since the Unix Epoch of January 1st, 1970 00:00:00 UTC.
- <samp>state</samp>: **online** when the application is actively polling the command staging folder.
- <samp>state</samp>: **offline** when the application is in calibre connection mode, but has been backgrounded or the device is about to sleep.
- _created by: application when initiating calibre connection_
- _updated by: application when app is being sent to background, or device is about to sleep_
- _deleted by: application when exiting calibre connection_

####delete_books.xml####
    <?xml version='1.0' encoding='utf-8?>
    <deletebooks timestamp='1364148473.0'>
        <manifest>
            <book author=''
                  filename=''
                  title=''
                  uuid='' />
            ...
            ...
        </manifest>
    </deletebooks>

- <samp>timestamp</samp>: a Unix timestamp representing the current time, as a float number of seconds, since the Unix Epoch of January 1st, 1970 00:00:00 UTC.
- <samp>book</samp> is repeated once for each book to delete, with the following attributes:
    - <samp>author</samp>: author metadata
    - <samp>filename</samp>: the filename of the epub to delete
    - <samp>title</samp>: title metadata
    - <samp>uuid</samp>: uuid metadata
- _created by: driver_
- _deleted by: application_


####rebuild_collections.xml####
    <?xml version='1.0' encoding='utf-8?>
    <rebuildcollections timestamp='1364148473.0'>
        <manifest>
            <book author=''
                  filename=''
                  title=''
                  uuid=''>
                <collections>
                    <collection>some collection name</collection>
                    ...
                <collections>
            </book>
            ...
            ...
        </manifest>
    </rebuildcollections>

- <samp>timestamp</samp>: a Unix timestamp representing the current time, as a float number of seconds, since the Unix Epoch of January 1st, 1970 00:00:00 UTC.
- <samp>book</samp> is repeated once for each book to rebuild, with the following attributes:
    - <samp>author</samp>: author metadata
    - <samp>filename</samp>: the filename of the epub to delete
    - <samp>title</samp>: title metadata
    - <samp>uuid</samp>: uuid metadata
    - <samp>collection</samp>: A collection to which the book is to be added
- _created by: driver_
- _deleted by: application_

####status.xml####
    <?xml version='1.0' encoding='utf-8?>
    <status timestamp=timestamp='1364148473.0' code='0'>
        <progress>1.0</progress>
        <messages>
            <message>A warning or error description</message>
        </messages>
    </status>

- <samp>timestamp</samp>: a Unix timestamp representing the current time, as a float number of seconds, since the Unix Epoch of January 1st, 1970 00:00:00 UTC.
- <samp>code</samp>:
   - **-1**: in progress
   - **0**: successful completion
   - **1**: success with warnings
   - **2**: failure
- <samp>progress</samp>: a float between **0.0** to **1.0** indicating overall progress of command execution as a percentage. This percentage is meaningful while <samp>code</samp> is **-1**, i.e. the command is being executed. When <samp>code</samp> is **0**, **1** or **2**, i.e. the command has been completed, <samp>progress</samp> shall be **1.0**.
If the application is processing 5 items, progress should be **0.20** after completion of the first item, **0.40** after the second, then finally **1.0** after completion of the final item when all application-side processing of the command has been completed.
- <samp>message</samp>: A text message from the application with warnings or errors to be reported the user. No messages are expected or required for successful command completion.
- _created by: application_
- _updated by: application_
- _deleted by: driver_

####update_metadata.xml####
    <?xml version='1.0' encoding='utf-8?>
    <updatemetadata timestamp=timestamp='1364148473.0' cleanupcollections='[yes|no]'>
        <manifest>
            <book author=''
                  authorsort=''
                  filename=''
                  pubdate='YYYY-MM-DD'
                  publisher=''
                  series=''
                  seriesindex=''
                  title=''
                  titlesort=''
                  uuid=''>
                <description>(escaped HTML)</description>
                <collections>
                    <collection>some collection name</collection>
                    ...
                </collections>
                <subjects>
                    <subject>some tag</subject>
                    ...
                </subjects>
                <cover hash='(md5 hash of cover bytes)' encoding='base64'>
                    (base64 encoded cover bytes)
                </cover>
            </book>
            ...
            ...
        </manifest>
    </updatemetadata>

- <samp>timestamp</samp>: a Unix timestamp representing the current time, as a float number of seconds, since the Unix Epoch of January 1st, 1970 00:00:00 UTC.
- <samp>cleanupcollections</samp>: **yes** if the application should overwrite existing epubs of the same identity, **no** if existing epubs of the same identity should be protected from overwrite.
- <samp>book</samp> is repeated once for each book to update, with the following attributes:
    - <samp>author</samp>: author metadata
    - <samp>authorsort</samp>: author sort metadata
    - <samp>filename</samp>: the filename of the epub whose metadata is being updated
    - <samp>pubdate</samp>: publication date metadata in 'YYYY-MM-DD' format
    - <samp>publisher</samp>: publisher metadata
    - <samp>series</samp>: series metadata
    - <samp>seriesindex</samp>: seriesindex metadata
    - <samp>title</samp>: title metadata
    - <samp>titlesort</samp>: title sort metadata
    - <samp>uuid</samp>: uuid
    - <samp>description</samp>: An escaped HTML description of the book
    - <samp>collection</samp>: A collection to which the book is to be added
    - <samp>subject</samp>: A genre describing the book
    - <samp>hash</samp>: an md5 hash of the included cover bytes. If the cover has not changed, there is no need to send the cover during an update.
- _created by: driver_
- _deleted by: application_

####upload_books.xml####
    <?xml version='1.0' encoding='utf-8?>
    <uploadbooks timestamp=timestamp='1364148473.0' overwrite='[yes|no]'>
        <manifest>
            <book filename='' coverhash=''>
                <collections>
                    <collection>some collection name</collection>
                <collections>
            </book>
            ...
            ...
        </manifest>
    </uploadbooks>

- <samp>timestamp</samp>: a Unix timestamp representing the current time, as a float number of seconds, since the Unix Epoch of January 1st, 1970 00:00:00 UTC.
- <samp>overwrite</samp>: **yes** if the application should overwrite existing epubs of the same identity, **no** if existing epubs of the same identity should be protected from overwrite.
- <samp>book</samp> is repeated once for each book to upload, with the following attributes:
    - <samp>filename</samp>: the filename of the epub to import, as stored to the documents staging folder
    - <samp>coverhash</samp>: an MD5 hash of the cover in calibre’s metadata
    - <samp>collection</samp>: A collection to which the book is to be added
- _created by: driver_
- _deleted by: application_

---

###Application implementation###
Implementation of the calibre connection may be modal or modeless, but the application must manage [<samp>connected.xml</samp>](#connectedxml) to accurately report the current ability of the application to poll for and respond to commands.

When the application reports its status as **online**, calibre will add the application’s icon to the main toolbar. The user may click on the icon to see a list of books installed in the application, and perform I/O tasks from the calibre GUI.

When the application reports its status as **offline**, calibre will remove the application’s icon from the main toolbar.

---

###Device driver overlays###
A calibre device driver subclasses the <samp>DevicePlugin</samp> class from [calibre.devices.interface](https://github.com/kovidgoyal/calibre/blob/master/src/calibre/devices/interface.py). The device driver constructs a shell <samp>DevicePlugin</samp> class, then merges in the following methods from the overlays:

- <samp>add\_books\_to\_metadata()</samp>
- <samp>books()</samp>
- <samp>can\_handle()</samp>
- <samp>can\_handle\_windows()</samp>
- <samp>delete\_books()</samp>
- <samp>eject()</samp>
- <samp>get_file()</samp>
- <samp>is\_usb\_connected()</samp>
- <samp>is\_usb\_connected\_windows()</samp>
- <samp>post\_yank\_cleanup()</samp>
- <samp>prepare\_addable\_books()</samp>
- <samp>remove\_books\_from\_metadata()</samp>
- <samp>sync\_booklists()</samp>
- <samp>upload\_books()</samp>

An additional overlay method <samp>\_initialize\_overlay()</samp> is called after the overlays are loaded to do any class initialization that would normally be included in the <samp>\_\_init\_\_()</samp> method.

Overlays may include local helper methods. These methods should be prefixed with an underscore to differentiate them from <samp>DevicePlugin</samp> class methods.

Calibre expects the device driver to return a list of books with metadata from <samp>books()</samp>. To fetch the metadata, the driver interrogates the application’s database, typically a sqlite store. The driver may fetch other information from the database as well, but the expectation is that the driver does not modify the contents of the database. Instead, the application updates its database as part of executing commands.

Caching will improve driver performance, and is encouraged. Refer to the Marvin driver implementation for examples, and where to store caches on the user’s machine.

Developers implementing support for a new application should refer to the Marvin overlays for examples.

In addition to the overlay file, you will also need to provide a Qt Creator <samp>.ui</samp> file for the Options dialog, and a Help file.

####Debugging device driver overlays####
The simplest calibre development environment is a text editor and a command shell. By enabling the debug logging options in the configuration dialog, you will see an informative stream of driver diagnostics when running in calibre debug mode. To launch calibre in debug mode:

    calibre-debug -g

<samp>plugins/iOS reader applications.json</samp>, stored in the user’s configuration directory, contains a variable <samp>development\_mode</samp>. Setting <samp>development_mode</samp> to **true** will print the content of all commands to the debug stream when when Marvin is the selected reader application.

---

###Calibre-unaware reader applications###
Some reader applications allow modeless interaction with their Documents folder through iTunes. For these reader applications, it may be possible to implement a driver with basic IO functionality without implementing the ‘smart’ protocol described above.

The GoodReader driver code is a good starting point for such a driver.

* GoodReader monitors its <samp>Documents</samp> folder in realtime, displaying content changes as they occur.
* GoodReader does not present any metadata to the user other than the cover, and uses the folder structure of <samp>Documents</samp> as its database.
* There is no 'GoodReader Options' tab in the config dialog, and thus no options, switches, or help file. The functionality for this driver is very similar to the driver for a Kindle or Sony hardware reader - it simply adds and deletes files.

The driver parses the <samp>Documents</samp> folder for installed books, building its own sqlite database by passing the PDFs to calibre to extract metadata. The first time the driver sees a book, it takes some time to parse it for metadata, but subsequent references to the same book in the same folder location are retrieved from the driver's cached metadata. The database is stored in the reader app's sandbox, and updated after every operation.

There are some inconsistencies between calibre's Device view and GoodReader's **My Documents** view. GoodReader supports nested folders, calibre does not. Calibre's device view shows all discovered PDFs in GoodReader in a flat list. Any books added to GoodReader are added to the top level of the **My Documents folder**. Moving them to a subfolder must be done within the GoodReader application.

---

###Developing a new driver###
Development of new driver code can be done without rebuilding the plugin. <samp>iOS reader applications.json</samp>, located in calibre's configuration directory can be modified to signal the driver of the presence of a driver overlay file under development. To find calibre's configuration directory on your machine, go to  _Preferences_ | _Advanced_ | _Miscellaneous_, then click **Open calibre configuration directory**.

Edit <samp>iOS reader applications.json</samp> to include the following lines:

    "development_mode": true,
    "development_app_id": "com.somecompany.readerappname",
    "development_overlay": "\\path\\to\\development_overlay.py",

<samp>development\_app\_id</samp> is the app id of the reader you're working with. For example, iBooks is <samp>com.apple.iBooks</samp>.

<samp>development_overlay</samp> is the full path to your overlay source file on your machine. Note that JSON data fields require escaped slashes.

During initialization, if these three fields exist in the JSON file, the specified overlay file will be loaded with the specified app_id. You can add switches to the JSON file to control the behavior of your driver. Switches should be prefaced with a unique name representing the reader app, as all reader app preferences are stored in the JSON file.

To run the driver after editing your code, restart calibre.

To see diagnostic messages, run calibre in debug mode:

    calibre-debug -g

---

Last update June 19, 2013 9:00:00 AM MDT
