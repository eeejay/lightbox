#!/usr/bin/env python

from gi.repository import Gtk, GUdev, GdkX11, GLib
import sys, signal, gst, os, re
from subprocess import Popen, PIPE
from pprint import pprint

signal.signal(signal.SIGINT, signal.SIG_DFL)

class Main:
    def __init__(self):
        settings = Gtk.Settings.get_default();
        settings.set_property("gtk-application-prefer-dark-theme", True)

        self.builder = Gtk.Builder()
        self.builder.add_from_file("lightbox.xml") 
        self.builder.connect_signals(self)
        self.devices = self.builder.get_object("devices")
        self.device_combo = self.builder.get_object("device_combo")
        self.formats = self.builder.get_object("formats")
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

        self._pop_dev_timeout = 0

    def _on_message(self, bus, message):
        t = message.type
        if t == gst.MESSAGE_STATE_CHANGED:
            oldstate, newstate, pending = message.parse_state_changed()
            if newstate == gst.STATE_PAUSED:
                formats_combo = self.builder.get_object("formats_combo")
                existing_labels = set()
                formats = []
                if not formats_combo.get_sensitive():
                    for caps in self.source.get_pad('src').get_caps():
                        structure_name = caps.get_name()
                        if structure_name.startswith('video'):
                            for fr in caps['framerate']:
                                label = '%d x %d @ %d fps' % (caps['width'], caps['height'], fr.num)
                                if label in existing_labels:
                                    continue
                                formats.append([label, caps['width'], caps['height'], fr.num, structure_name])
                                print label
                                existing_labels.add(label)
                    formats.sort(lambda x, y: cmp(x[1], y[1]) or \
                                     cmp(x[2], y[2]) or cmp(x[3], y[3]),
                                 None, True)
                    for row in formats:
                        self.formats.append(row)
                    formats_combo.set_active(0)
                    formats_combo.set_sensitive(True)
        elif t == gst.MESSAGE_EOS:
            print "MESSAGE EOS"
            self._stop()
            self.device_combo.set_active(0)
        elif t == gst.MESSAGE_ERROR:
            print "MESSAGE ERROR"
            err, debug = message.parse_error()
            print "Error: %s" % err, debug
            self.device_combo.set_active(0)

    def _on_sync_message(self, bus, message):
        if message.structure is None:
            return
        message_name = message.structure.get_name()
        if message_name == "prepare-xwindow-id":
            imagesink = message.src
            imagesink.set_property("force-aspect-ratio", True)

    def _on_format_changed(self, combobox):
        if combobox.get_active() < 0:
            return
        _, w, h, f, n  = self.formats[combobox.get_active()]
        self._stop()
        caps = gst.Caps("%s, width=%d, height=%d,framerate=(fraction)%d/1" % (n, w, h, f))
        self.filter.set_property("caps", caps)
        self._start()
        
    def _on_device_changed(self, combobox):
        self._stop()
        self.formats.clear()
        self._delete_device_widgets()
        formats_combo = self.builder.get_object("formats_combo")
        formats_combo.set_sensitive(False)
        device = self.devices[combobox.get_active()][1]
        if device:
            #GLib.idle_add(self._populate_device_widgets, open(device.get_device_file(), 'rw'))
            if self._pop_dev_timeout:
                GLib.source_remove(self._pop_dev_timeout)
            self._pop_dev_timeout = GLib.timeout_add(2000, self._populate_device_widgets, device.get_device_file())
            self.source.set_property('device', device.get_device_file())
            self._start()

    def _delete_device_widgets(self):
        for grid in [self.builder.get_object("focusgrid"), self.builder.get_object("expgrid")]:
            for child in grid.get_children():
                child.destroy()

    def _populate_device_widgets(self, dev):
        print '_populate_device_widgets'
        self._pop_dev_timeout = 0
        p = Popen(['v4l2-ctl', '--list-ctrls-menu', '-d', dev], stdout=PIPE, stderr=PIPE)
        while True:
            retcode = p.poll()
            if retcode is not None: # Process finished.
                print 'retcode:', retcode
                break
            GLib.main_context_default().iteration()

        ctrl_pattern = re.compile(r'\s*(?P<name>[a-z_]+)\s\((?P<type>\w+)\)\s*:\s(?P<params>.*?),?$')
        menu_pattern = re.compile(r'\t*(?P<value>[0-9]+):\s(?P<label>.*)')
        controls = []
        for line in p.stdout.read().split('\n'):
            if not line: continue
            match = ctrl_pattern.match(line)
            if match:
                control = match.groupdict()
                control['params'] = dict([q.split('=') for q in control['params'].split(' ')])
                controls.append(control)
                continue
            
            match = menu_pattern.match(line)
            if match:
                if not controls:
                    raise Exception('no preceding control')
                if controls[-1]['type'] != 'menu':
                    raise Exception('a menu item for a non menu control')
                menu_item = match.groupdict()
                menu_options = controls[-1].get('menu_options', [])
                menu_options.append([menu_item['value'], menu_item['label']])
                controls[-1]['menu_options'] = menu_options
                continue

            print 'Warning: line did not match anything: %s' % repr(line)

        controls_dict = {}
        for control in controls:
            controls_dict[control.pop('name')] = control
        names = controls_dict.keys()
        names.sort()
        print '\n'.join(names)

        focusgrid = self.builder.get_object("focusgrid")
        for i, cid in enumerate(['focus_auto', 'focus_absolute']):
            control = controls_dict.get(cid, None)
            print cid
            pprint (control)
            if not control:
                continue;
            print i, cid
            ctrl = V4LControl(dev, cid, control)
            print ctrl.label, ctrl.widget
            if ctrl.label and ctrl.widget:
                focusgrid.attach(Gtk.Label.new(ctrl.label), 0, i, 1, 1)
                focusgrid.attach(ctrl.widget, 1, i, 1, 1)
                ctrl.widget.set_hexpand(True)
            elif ctrl.widget:
                focusgrid.attach(ctrl.widget, 0, i, 2, 1)
        focusgrid.show_all()

        expgrid = self.builder.get_object("expgrid")
        for i, cid in enumerate(['exposure_auto', 'exposure_absolute']):
            control = controls_dict.get(cid, None)
            print cid
            pprint (control)
            if not control:
                continue;
            print i, cid
            ctrl = V4LControl(dev, cid, control)
            print ctrl.label, ctrl.widget
            if ctrl.label and ctrl.widget:
                expgrid.attach(Gtk.Label.new(ctrl.label), 0, i, 1, 1)
                expgrid.attach(ctrl.widget, 1, i, 1, 1)
                ctrl.widget.set_hexpand(True)
            elif ctrl.widget:
                focusgrid.attach(ctrl.widget, 0, i, 2, 1)
        expgrid.show_all()
        
        return False

    def _on_orientation_changed(self, combobox):
        self._stop()
        method = combobox.get_model()[combobox.get_active()][1]
        print method
        self.flipper.set_property('method', method)
        if self.device_combo.get_active():
            self._start()

    def _stop(self):
        self.player.set_state(gst.STATE_NULL)

    def _start(self):
        self.player.set_state(gst.STATE_PLAYING)

    def run(self):
        for device in self.udev_client.query_by_subsystem("video4linux"):
            self.devices.append(['%s (%s)' % (device.get_sysfs_attr('name'),
                                              device.get_device_file()), device])

        w = self.builder.get_object("main")
        w.show_all()

        Gtk.main()

class V4LControl:
    LABELS = {'focus_auto': 'Auto',
              'focus_absolute': 'Absolute',
              'exposure_auto': 'Auto',
              'exposure_absolute': 'Absolute'}
    def __init__(self, device, control_name, control_info):
        self.name = control_name
        self.info = control_info
        self.dev = device
        self._create_widget()
        self._next_cmd = None
        self._idler = 0

    def _create_widget(self):
        params = self.info['params']
        if self.info['type'] == 'int':
            adj = Gtk.Adjustment.new(float(params['value']),
                                     float(params['min']),
                                     float(params['max']),
                                     float(params['step']),
                                     float(params['step']),
                                     1)
            self.widget = Gtk.Scale.new(Gtk.Orientation.HORIZONTAL, adj)
            self.widget.set_digits(0)
            print 'value', float(params['value'])
            self.widget.set_value(float(params['value']))
            self.widget.connect('value-changed', self._onchanged)
            self.label = self.LABELS.get(self.name, self.name)
        elif self.info['type'] == 'bool':
            self.widget = Gtk.CheckButton.new_with_label(self.LABELS.get(self.name, self.name))
            self.widget.set_active(int(params['value']) != 0)
            self.widget.connect('toggled', self._onchanged)
            self.label = None
        elif self.info['type'] == 'menu':
            self.widget = Gtk.ComboBoxText.new()
            active_option = 0
            for i, option in enumerate(self.info.get('menu_options', [])):
                if option[0] == params['value']:
                    active_option = i
                self.widget.append(*option)
            self.widget.set_active(active_option)
            self.label = self.LABELS.get(self.name, self.name)
            self.widget.connect('changed', self._onchanged)


    def _onchanged(self, w):
        value = 0
        if self.info['type'] == 'int':
            value = w.get_value()
        elif self.info['type'] == 'bool':
            value = w.get_active()
        elif self.info['type'] == 'menu':
            value = int(w.get_active_id())

        self._run_command(['v4l2-ctl', '-d', self.dev, '-c', '%s=%d' % (self.name, value)])

    def _run_command(self, cmd):
        if self._idler:
            self._next_cmd = cmd
        else:
            p = Popen(cmd, stdout=PIPE, stderr=PIPE)
            self._idler = GLib.idle_add(self._poll_process, p)

    def _poll_process(self, process):
        retcode = process.poll()
        if retcode is not None: # Process finished.
            if retcode != 0:
                raise Exception("Failed to run: %s" % process.stderr.read())
            self._idler = 0
            if self._next_cmd:
                self._run_command(self._next_cmd)
                self._next_cmd = None
            return False

        return True

if __name__ == '__main__':
    Gtk.init(sys.argv)
    main = Main()
    sys.exit(main.run())
