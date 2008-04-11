#!/usr/bin/env python

## Copyright (C) 2007, 2008 Tim Waugh <twaugh@redhat.com>
## Copyright (C) 2007, 2008 Red Hat, Inc.

## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation; either version 2 of the License, or
## (at your option) any later version.

## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.

## You should have received a copy of the GNU General Public License
## along with this program; if not, write to the Free Software
## Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.

import cups
import sys
import statereason
from statereason import StateReason
from debug import *
import pprint

import dbus
import dbus.glib
import dbus.service
import gobject
import pynotify
import time

APPDIR="/usr/share/system-config-printer"
DOMAIN="system-config-printer"
GLADE="applet.glade"
ICON="printer"
SEARCHING_ICON="document-print-preview"

####
#### NewPrinterNotification DBus server (the 'new' way).
####
PDS_PATH="/com/redhat/NewPrinterNotification"
PDS_IFACE="com.redhat.NewPrinterNotification"
PDS_OBJ="com.redhat.NewPrinterNotification"
class NewPrinterNotification(dbus.service.Object):
    STATUS_SUCCESS = 0
    STATUS_MODEL_MISMATCH = 1
    STATUS_GENERIC_DRIVER = 2
    STATUS_NO_DRIVER = 3

    def __init__ (self, bus):
        self.bus = bus
        self.getting_ready = 0
        bus_name = dbus.service.BusName (PDS_OBJ, bus=bus)
        dbus.service.Object.__init__ (self, bus_name, PDS_PATH)

    def wake_up (self):
        global waitloop, runloop, viewer
        import jobviewer
        if viewer == None:
            waitloop.quit ()
            runloop = gobject.MainLoop ()
            viewer = jobviewer.JobViewer(bus=bus, loop=runloop,
                                         service_running=service_running,
                                         trayicon=trayicon,
                                         suppress_icon_hide=True)

    @dbus.service.method(PDS_IFACE, in_signature='', out_signature='')
    def GetReady (self):
        self.wake_up ()
        if self.getting_ready == 0:
            viewer.set_special_statusicon (SEARCHING_ICON)

        self.getting_ready += 1
        gobject.timeout_add (60 * 1000, self.timeout_ready)

    def timeout_ready (self):
        global viewer
        if self.getting_ready > 0:
            self.getting_ready -= 1
        if self.getting_ready == 0:
            viewer.unset_special_statusicon ()

        return False

    @dbus.service.method(PDS_IFACE, in_signature='isssss', out_signature='')
    def NewPrinter (self, status, name, mfg, mdl, des, cmd):
        global viewer
        self.wake_up ()
        c = cups.Connection ()
        try:
            printer = c.getPrinters ()[name]
        except KeyError:
            return

        try:
            filename = c.getPPD (name)
        except cups.IPPError:
            return

        del c

        # Check for missing packages
        ppd = cups.PPD (filename)
        import os
        os.unlink (filename)
        import sys
        sys.path.append (APPDIR)
        import cupshelpers
        (missing_pkgs,
         missing_exes) = cupshelpers.missingPackagesAndExecutables (ppd)

        from ppds import ppdMakeModelSplit
        (make, model) = ppdMakeModelSplit (printer['printer-make-and-model'])
        driver = make + " " + model
        if status < self.STATUS_GENERIC_DRIVER:
            title = _("Printer added")
        else:
            title = _("Missing printer driver")

        if len (missing_pkgs) > 0:
            pkgs = reduce (lambda x,y: x + ", " + y, missing_pkgs)
            title = _("Install printer driver")
            text = _("`%s' requires driver installation: %s.") % (name, pkgs)
            n = pynotify.Notification (title, text)
            n.set_urgency (pynotify.URGENCY_CRITICAL)
            n.add_action ("install-driver", _("Install"),
                          lambda x, y: self.install_driver (x, y, missing_pkgs))
        elif status == self.STATUS_SUCCESS:
            text = _("`%s' is ready for printing.") % name
            n = pynotify.Notification (title, text)
            n.set_urgency (pynotify.URGENCY_NORMAL)
            n.add_action ("configure", _("Configure"),
                          lambda x, y: self.configure (x, y, name))
        else: # Model mismatch
            text = _("`%s' has been added, using the `%s' driver.") % \
                   (name, driver)
            n = pynotify.Notification (title, text, 'printer')
            n.set_urgency (pynotify.URGENCY_CRITICAL)
            n.add_action ("find-driver", _("Find driver"),
                          lambda x, y: self.find_driver (x, y, name))

        n.set_timeout (pynotify.EXPIRES_NEVER)
        viewer.notify_new_printer (name, n)
        # Set the icon back how it was.
        self.timeout_ready ()

    def run_config_tool (self, argv):
        import os
        pid = os.fork ()
        if pid == 0:
            # Child.
            cmd = "/usr/bin/system-config-printer"
            argv.insert (0, cmd)
            os.execvp (cmd, argv)
            sys.exit (1)
        elif pid == -1:
            print "Error forking process"
        
    def configure (self, notification, action, name):
        self.run_config_tool (["--configure-printer", name])

    def find_driver (self, notification, action, name):
        self.run_config_tool (["--choose-driver", name])

    def install_driver (self, notification, action, missing_pkgs):
        import os
        pid = os.fork ()
        if pid == 0:
            # Child.
            argv = ["/usr/bin/system-install-packages"]
            argv.extend (missing_pkgs)
            os.execv (argv[0], argv)
            sys.exit (1)
        elif pid == -1:
            print "Error forking process"

PROGRAM_NAME="system-config-printer-applet"
def show_help ():
    print "usage: %s [--no-tray-icon]" % PROGRAM_NAME

def show_version ():
    import config
    print "%s %s" % (PROGRAM_NAME, config.VERSION)
    
####
#### Main program entry
####

global waitloop, runloop, viewer

trayicon = True
service_running = False
waitloop = runloop = None
viewer = None

if __name__ == '__main__':
    import sys, getopt
    try:
        opts, args = getopt.gnu_getopt (sys.argv[1:], '',
                                        ['no-tray-icon',
                                         'debug',
                                         'help',
                                         'version'])
    except getopt.GetoptError:
        show_help ()
        sys.exit (1)

    for opt, optarg in opts:
        if opt == "--help":
            show_help ()
            sys.exit (0)
        if opt == "--version":
            show_version ()
            sys.exit (0)
        if opt == "--no-tray-icon":
            trayicon = False
        elif opt == "--debug":
            set_debugging (True)

    # Must be done before connecting to D-Bus (for some reason).
    if not pynotify.init (PROGRAM_NAME):
        print >> sys.stderr, ("%s: unable to initialize pynotify" %
                              PROGRAM_NAME)

    if trayicon:
        # Stop running when the session ends.
        def monitor_session (*args):
            pass

        try:
            bus = dbus.SessionBus()
            bus.add_signal_receiver (monitor_session)
        except:
            print >> sys.stderr, "%s: failed to connect to session D-Bus" % \
                PROGRAM_NAME
            sys.exit (1)

    try:
        bus = dbus.SystemBus()
    except:
        print >> sys.stderr, ("%s: failed to connect to system D-Bus" %
                              PROGRAM_NAME)
        sys.exit (1)

    if trayicon:
        try:
            NewPrinterNotification(bus)
            service_running = True
        except:
            print >> sys.stderr, \
                "%s: failed to start NewPrinterNotification service" % \
                PROGRAM_NAME

    if trayicon and get_debugging () == False:
        # Start off just waiting for print jobs.
        def any_jobs ():
            try:
                c = cups.Connection ()
                if len (c.getJobs (my_jobs=True)):
                    return True
            except:
                pass

            return False

        if not any_jobs ():

            ###
            class WaitForJobs:
                def handle_dbus_signal (self, *args):
                    self.received_any_dbus_signals = True
                    gobject.source_remove (self.timer)
                    self.timer = gobject.timeout_add (200, self.check_for_jobs)

                def check_for_jobs (self, *args):
                    debugprint ("checking for jobs")
                    if any_jobs ():
                        waitloop.quit ()

                    # Don't run this timer again.
                    return False
            ###

            jobwaiter = WaitForJobs()
            bus.add_signal_receiver (jobwaiter.check_for_jobs,
                                     path="/com/redhat/PrinterSpooler",
                                     dbus_interface="com.redhat.PrinterSpooler")
            waitloop = gobject.MainLoop ()
            waitloop.run()
            waitloop = None
            bus.remove_signal_receiver (jobwaiter.check_for_jobs,
                                        path="/com/redhat/PrinterSpooler",
                                        dbus_interface="com.redhat.PrinterSpooler")

    if viewer == None:
        import jobviewer
        runloop = gobject.MainLoop ()
        viewer = jobviewer.JobViewer(bus=bus, loop=runloop,
                                     service_running=service_running,
                                     trayicon=trayicon)

    try:
        runloop.run()
    except KeyboardInterrupt:
        pass
    viewer.cleanup ()
