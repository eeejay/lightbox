#!/usr/bin/env python

from gi.repository import Gtk, GUdev, GdkX11
import sys, signal, gst

signal.signal(signal.SIGINT, signal.SIG_DFL)

class Main:
    def __init__(self):
        settings = Gtk.Settings.get_default();
        settings.set_property("gtk-application-prefer-dark-theme", True)

        self.builder = Gtk.Builder()
        self.builder.add_from_file("lightbox.xml") 
        self.builder.connect_signals(self)
        self.devices = self.builder.get_object("devices")
        self.formats = self.builder.get_object("formats")
        self.videosink = self.builder.get_object("videosink")
        self.spinner = self.builder.get_object("spinner")
        self.videowindow = self.builder.get_object("videowindow")
        self.udev_client = GUdev.Client.new(['video4linux'])

        self.player = gst.Pipeline(name='player')
        self.source = gst.element_factory_make('v4l2src', 'source')
        self.filter = gst.element_factory_make("capsfilter", "filter")
        self.flipper = gst.element_factory_make("videoflip", "flipper")
        sink = gst.element_factory_make('xvimagesink', 'sink')

        self.player.add(self.source)
        self.player.add(self.filter)
        self.player.add(self.flipper)
        self.player.add(sink)

        gst.element_link_many(self.source, self.filter, self.flipper, sink)

        bus = self.player.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_message)
        bus.enable_sync_message_emission()
        bus.connect("sync-message::element", self._on_sync_message) 

    def _on_message(self, bus, message):
        t = message.type
        if t == gst.MESSAGE_STATE_CHANGED:
            oldstate, newstate, pending = message.parse_state_changed()
            if newstate == gst.STATE_PAUSED:
                formats_combo = self.builder.get_object("formats_combo")
                existing_labels = set()
                if not formats_combo.get_sensitive():
                    for caps in self.source.get_pad('src').get_caps():
                        structure_name = caps.get_name()
                        if structure_name.startswith('video'):
                            for fr in caps['framerate']:
                                label = '%d x %d @ %d fps' % (caps['width'], caps['height'], fr.num)
                                if label in existing_labels:
                                    continue
                                self.formats.append(
                                    [label, caps['width'], caps['height'], fr.num, structure_name])
                                existing_labels.add(label)
                    formats_combo.set_sensitive(True)
        elif t == gst.MESSAGE_EOS:
            print "MESSAGE EOS"
            self.video_player.set_state(gst.STATE_NULL)
        elif t == gst.MESSAGE_ERROR:
            print "MESSAGE ERROR"
            err, debug = message.parse_error()
            print "Error: %s" % err, debug
            self.video_player.set_state(gst.STATE_NULL)

    def _on_sync_message(self, bus, message):
        print '_on_sync_message', self.videosink
        if message.structure is None:
            return
        message_name = message.structure.get_name()
        if message_name == "prepare-xwindow-id":
            #self.spinner.hide()
            imagesink = message.src
            self.videosink.set_vexpand(True)
            self.spinner.set_vexpand(False)
            imagesink.set_property("force-aspect-ratio", True)
            imagesink.set_xwindow_id(self.videosink.get_window().get_xid())

    def _on_format_changed(self, combobox):
        _, w, h, f, n  = self.formats[combobox.get_active()]
        self._stop()
        caps = gst.Caps("%s, width=%d, height=%d,framerate=(fraction)%d/1" % (n, w, h, f))
        self.filter.set_property("caps", caps)
        self._start()
        
    def _on_device_changed(self, combobox):
        self._stop()
        self.formats.clear()
        formats_combo = self.builder.get_object("formats_combo")
        formats_combo.set_sensitive(False)
        device = self.devices[combobox.get_active()][1]
        if device:
            self.source.set_property('device', device.get_device_file())
            self._start()
        else:
            self.videowindow.hide()

    def _on_orientation_changed(self, combobox):
        self._stop()
        method = combobox.get_model()[combobox.get_active()][1]
        print method
        self.flipper.set_property('method', method)
        self._start()

    def _stop(self):
        self.player.set_state(gst.STATE_NULL)
        #self.builder.get_object("spinner").show()
        self.videosink.set_vexpand(True)
        #self.spinner.set_vexpand(True)

    def _start(self):
        self.player.set_state(gst.STATE_PLAYING)
        self.videowindow.show()

    def run(self):
        for device in self.udev_client.query_by_subsystem("video4linux"):
            self.devices.append(['%s (%s)' % (device.get_sysfs_attr('name'),
                                              device.get_device_file()), device])

        w = self.builder.get_object("main")
        w.show_all()

        Gtk.main()

if __name__ == '__main__':
    Gtk.init(sys.argv)
    main = Main()
    sys.exit(main.run())
