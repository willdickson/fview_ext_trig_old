from __future__ import with_statement, division

import pkg_resources
import motmot.utils.config
import os, sys, pickle, warnings, time, threading, traceback
if 1:
    # https://mail.enthought.com/pipermail/enthought-dev/2008-May/014709.html
    import logging
    logger = logging.getLogger()
    logger.addHandler(logging.StreamHandler(sys.stderr))
    logger.setLevel(logging.DEBUG)

import enthought.traits.api as traits
from enthought.traits.ui.api import View, Item, Group, TextEditor

import wx
import ttrigger
from live_timestamp_modeler import LiveTimestampModelerWithAnalogInput, \
     ImpreciseMeasurementError, AnalogInputViewer
import motmot.fview.traited_plugin as traited_plugin
import numpy as np
import tables
import motmot.fview_ext_trig.data_format as data_format

LatencyEstimatedEvent = wx.NewEventType()

AnalogInputWordstreamDescription = data_format.AnalogInputWordstreamDescription
AnalogInputWordstream_dtype =  tables.Description(
    AnalogInputWordstreamDescription().columns)._v_nestedDescr

TimeDataDescription = data_format.TimeDataDescription
TimeData_dtype =  tables.Description(
    TimeDataDescription().columns)._v_nestedDescr

class FviewExtTrig(traited_plugin.HasTraits_FViewPlugin):
    plugin_name = 'FView external trigger'
    trigger_device = traits.Instance(ttrigger.DeviceModel)
    timestamp_modeler = traits.Instance(LiveTimestampModelerWithAnalogInput)
    latency_estimate_msec = traits.Float
    residual_error = traits.Float
    query_AIN_interval = traits.Range(low=10,high=1000,value=300)
    last_trigger_timestamp = traits.Any(transient=True)
    save_to_disk = traits.Bool(False,transient=True)
    streaming_filename = traits.File

    traits_view = View( Group( ( Item( 'trigger_device', style='custom',
                                       show_label=False),
                                 Group(
                                       Item('query_AIN_interval'),#show_label=False),
                                       Item( 'latency_estimate_msec',
                                             label='latency estimate (msec)',
                                             style='readonly',
                                             editor=TextEditor(evaluate=float, format_func=lambda x:
                                                               "%.5g" % x ),
                                             ),
                                 orientation='horizontal'),
                                 Item( 'timestamp_modeler', style='custom',
                                       show_label=False),
                                 Item(name='save_to_disk',
                                      ),
                                 Item(name='streaming_filename',
                                      style='readonly'),
                                 )),
                        )

    def __init__(self,*args,**kw):
        kw['wxFrame args']=(-1,self.plugin_name,wx.DefaultPosition,wx.Size(600,688))
        super(FviewExtTrig,self).__init__(*args,**kw)

        self._list_of_timestamp_data = []
        self._list_of_ain_wordstream_buffers = []
        self.stream_ain_table   = None
        self.stream_time_data_table = None
        self.last_trigger_timestamp = {}

        # load from persisted state if possible

        self.pkl_fname = motmot.utils.config.rc_fname(
            must_already_exist=False,
            filename='fview_ext_trig-trigger_device.pkl',
            dirname='.fview')
        loaded = False
        if os.path.exists(self.pkl_fname):
            try:
                self.trigger_device = pickle.load(open(self.pkl_fname))
                loaded = True
            except Exception,err:
                warnings.warn(
                    'could not open fview_ext_trig persistance file: %s'%err)

        # It not possible, create ourself anew
        if not loaded:
            try:
                self.trigger_device = ttrigger.DeviceModel()
            except Exception,err:
                formatted_error = traceback.format_exc(err)
                msg = 'While attempting to open the CamTrig USB device,\n' \
                      'FView encountered an error. The error is:\n\n' \
                      '%s\n\n' \
                      'More details:\n' \
                      '%s\n\n' \
                      'FView will now close.'%( err, formatted_error )
                dlg = wx.MessageDialog(self.frame, msg,
                                       'FView plugin error',
                                       wx.OK | wx.ICON_ERROR)
                dlg.ShowModal()
                dlg.Destroy()
                raise

        self.timestamp_modeler = LiveTimestampModelerWithAnalogInput(
            viewer=AnalogInputViewer())
        self.timestamp_modeler.set_trigger_device(self.trigger_device)

        self.gain_offset_lock = threading.Lock()
        self.gain_offset = 1,0

        self.latency_est_lock = threading.Lock()
        self.latency_est = None

        self.streaming_file = None

        ID_Timer = wx.NewId()
        self.timer = wx.Timer(self.frame, ID_Timer)
        wx.EVT_TIMER(self.frame, ID_Timer, self.OnTimer)
        self.timer.Start(500)

        ID_Timer2 = wx.NewId()
        self.timer2 = wx.Timer(self.frame, ID_Timer2)
        wx.EVT_TIMER(self.frame, ID_Timer2, self.OnFastTimer)
        self.timer2.Start(self.query_AIN_interval)

        self.frame.Connect( -1, -1,
                            LatencyEstimatedEvent, self.OnLatencyEstimated )

        self.frame_offsets = {}

    def _save_to_disk_changed(self):
        self.service_save_data()
        if self.save_to_disk:
            self.timestamp_modeler.block_activity = True

            self.streaming_filename = time.strftime('fview_analog_data_%Y%m%d_%H%M%S.h5')
            self.streaming_file = tables.openFile( self.streaming_filename, mode='w')
            self.stream_ain_table   = self.streaming_file.createTable(
                self.streaming_file.root,'ain_wordstream',AnalogInputWordstreamDescription,
                "AIN data",expectedrows=100000)
            names = self.timestamp_modeler.channel_names
            print 'saving analog channels',names
            self.stream_ain_table.attrs.channel_names = names

            self.stream_ain_table.attrs.Vcc = self.timestamp_modeler.Vcc

            self.stream_time_data_table = self.streaming_file.createTable(
                self.streaming_file.root,'time_data',TimeDataDescription,
                "time data",expectedrows=10000)
            self.stream_time_data_table.attrs.top = self.timestamp_modeler.timer3_top

            print 'saving to disk...'
        else:
            print 'closing file...'
            self.stream_ain_table   = None
            self.stream_time_data_table = None
            self.streaming_file.close()
            self.streaming_file = None
            print 'closed',repr(self.streaming_filename)
            self.streaming_filename = ''
            self.timestamp_modeler.block_activity = False

    def _timestamp_modeler_changed(self,newvalue):
        # register our handlers
        self.timestamp_modeler.on_trait_change(self.on_ain_data_raw,
                                               'ain_data_raw')
        self.timestamp_modeler.on_trait_change(self.on_timestamp_data,
                                               'timestamps_framestamps')

    def on_ain_data_raw(self,newvalue):
        self._list_of_ain_wordstream_buffers.append(newvalue)

    def on_timestamp_data(self,timestamp_framestamp_2d_array):
        if len(timestamp_framestamp_2d_array):
            last_sample = timestamp_framestamp_2d_array[-1,:]
            self._list_of_timestamp_data.append(last_sample)

    def service_save_data(self):
        # analog input data...
        bufs = self._list_of_ain_wordstream_buffers
        self._list_of_ain_wordstream_buffers = []
        if self.stream_ain_table is not None and len(bufs):
            buf = np.hstack(bufs)
            recarray = np.rec.array( [buf], dtype=AnalogInputWordstream_dtype)
            self.stream_ain_table.append( recarray )
            self.stream_ain_table.flush()

        tsfss = self._list_of_timestamp_data
        self._list_of_timestamp_data = []
        if self.stream_time_data_table is not None and len(tsfss):
            bigarr = np.vstack(tsfss)
            timestamps = bigarr[:,0]
            framestamps = bigarr[:,1]
            recarray = np.rec.array( [timestamps,framestamps], dtype=TimeData_dtype)
            self.stream_time_data_table.append( recarray )
            self.stream_time_data_table.flush()

    def _query_AIN_interval_changed(self):
        self.timer2.Start(self.query_AIN_interval)

    def OnLatencyEstimated(self,event):
        with self.latency_est_lock:
            latency_est = self.latency_est
        self.latency_estimate_msec = latency_est*1000.0

    def OnFastTimer(self,event):
        self.timestamp_modeler.update_analog_input()

    def OnTimer(self,event):
        self.service_save_data()

        try:
            self.timestamp_modeler.update()
        except ImpreciseMeasurementError:
            return

        result = self.timestamp_modeler.gain_offset_residuals
        if result is None:
            # not enough data
            return
        gain,offset,residuals = result
        if residuals is not None and len(residuals):
            self.residual_error = residuals[0]

    def process_frame(self,cam_id,buf,buf_offset,timestamp,framenumber):
        """do work on each frame

        This function gets called on every single frame capture. It is
        called within the realtime thread, NOT the wxPython
        application mainloop's thread. Therefore, be extremely careful
        (use threading locks) when sharing data with the rest of the
        class.

        """
        trigger_timestamp = self.timestamp_modeler.register_frame(
            cam_id,framenumber,timestamp)
        if trigger_timestamp is not None:

            now = time.time()
            latency_sec = now-trigger_timestamp

            with self.latency_est_lock:
                self.latency_est = latency_sec
            event = wx.CommandEvent(LatencyEstimatedEvent)
            event.SetEventObject(self.frame)

            # trigger call to self.OnDataReady
            wx.PostEvent(self.frame, event)
        self.last_trigger_timestamp[cam_id] = trigger_timestamp
        return [], []

    def get_last_trigger_timestamp(self,cam_id):
        return self.last_trigger_timestamp[cam_id]

    def quit(self):
        pickle.dump(self.trigger_device,open(self.pkl_fname,mode='w'))

