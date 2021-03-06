#!/usr/bin/env python

'''

'''

#Python standard import
import os
import copy
import glob
import pathlib

#Third party import
import scipy
import numpy as np
import h5py

#Local import
from . import datetime as internal_datetime
from . import ccsds


_ptypes = [
    'file',
    'folder',
    'network',
    'ram',
]

_sources = []

def register_source(cls):
    global _sources
    _sources.append(cls)


class ObservationSource(object):
    
    dtype = [] #this is the dtype of data
    REQUIRED_META = []

    def __init__(self, path, **kwargs):
        self.kwargs = kwargs.copy()

        self.path = path

        #these are set by load()
        self.data = None
        self.meta = None
        self.index = None

        self.load()


    def avalible_data(self):
        return [x[0] for x in self.dtype] + self.REQUIRED_META


    def __str__(self):
        return '<{} located at {}>'.format(type(self).__name__, str(self.path))


    @staticmethod
    def accept(path):
        '''Verify that the path can be loaded by this source handler
        '''
        raise NotImplementedError()


    def load(self):
        '''Load all tracklets into memory as numpy arrays
        '''
        raise NotImplementedError()


    def generate_model(self, Model, **kwargs):
        
        avalible_data = [dt[0] for dt in self.dtype]
        model_args = Model.REQUIRED_DATA

        given_data = list(kwargs.keys())
        model_data = {}

        for arg in model_args:
            if arg not in given_data:
                if arg in avalible_data:
                    model_data[arg] = self.data[arg]
                elif arg in self.REQUIRED_META:
                    model_data[arg] = self.meta[arg]
                else:
                    raise ValueError('Not enough data to construct model: {} missing'.format(arg))
            else:
                model_data[arg] = kwargs[arg]
                del kwargs[arg]

        return Model(data = model_data, **kwargs)



class RadarTracklet(ObservationSource):
    
    dtype = [
        ('date', 'datetime64[us]'),
        ('r', 'float64'),
        ('v', 'float64'),
        ('r_sd', 'float64'),
        ('v_sd', 'float64'),
    ]

    REQUIRED_META = [
        'tx_ecef',
        'rx_ecef',
    ]

    def __init__(self, path, **kwargs):
        super(RadarTracklet, self).__init__(path, **kwargs)


class OpticalTracklet(ObservationSource):
    
    dtype = [
        ('date', 'datetime64[us]'),
        ('az', 'float64'),
        ('el', 'float64'),
        ('az_sd', 'float64'),
        ('el_sd', 'float64'),
    ]

    REQUIRED_META = [
        'ecef',
    ]

    def __init__(self, path, **kwargs):
        super(OpticalTracklet, self).__init__(path, **kwargs)



class SimulatedRadarTracklet(RadarTracklet):
    

    def __init__(self, path, **kwargs):
        super(SimulatedRadarTracklet, self).__init__(path, **kwargs)


    @staticmethod
    def accept(path):
        '''Verify that the path can be loaded by this source handler
        '''
        if not isinstance(path, SourcePath):
            raise TypeError('Can only check acceptance of path objects, not "{}"'.format(type(path)))
        else:
            if path.ptype == 'ram':
                bool_ = isinstance(path.data['data'], np.ndarray) and path.data['data'].dtype.names is not None
                if not bool_:
                    return False
                dt_comp = [name[0] in path.data['data'].dtype.names for name in SimulatedRadarTracklet.dtype]
                bool_ = bool_ and np.all(np.array(dt_comp, dtype=np.bool))
                return bool_
            else:
                return False

    def load(self):
        self.data = self.path.data['data']
        self.meta = self.path.data['meta']
        self.index = self.path.data['index']



class StateSource(ObservationSource):
    dtype = [
        ('date', 'datetime64[us]'),
        ('x', 'float64'),
        ('y', 'float64'),
        ('z', 'float64'),
        ('vx', 'float64'),
        ('vy', 'float64'),
        ('vz', 'float64'),
        ('cov', 'float64', (6,6)),
        ('x_sd', 'float64'),
        ('y_sd', 'float64'),
        ('z_sd', 'float64'),
        ('vx_sd', 'float64'),
        ('vy_sd', 'float64'),
        ('vz_sd', 'float64'),
    ]
    
    REQUIRED_META = [
        'frame',
    ]

    def __init__(self, path, **kwargs):
        super(StateSource, self).__init__(path, **kwargs)


class SimulatedStateSource(StateSource):


    def __init__(self, path, **kwargs):
        super(SimulatedStateSource, self).__init__(path, **kwargs)


    @staticmethod
    def accept(path):
        '''Verify that the path can be loaded by this source handler
        '''
        if not isinstance(path, SourcePath):
            raise TypeError('Can only check acceptance of path objects, not "{}"'.format(type(path)))
        else:
            if path.ptype == 'ram':
                bool_ = isinstance(path.data['data'], np.ndarray) and path.data['data'].dtype.names is not None
                if not bool_:
                    return False
                dt_comp = [name[0] in path.data['data'].dtype.names for name in SimulatedStateSource.dtype]
                bool_ = bool_ and np.all(np.array(dt_comp, dtype=np.bool))
                return bool_
            else:
                return False

    def load(self):
        self.data = self.path.data['data']
        self.meta = self.path.data['meta']
        self.index = self.path.data['index']


class RadarTrackingDataMessage(RadarTracklet):

    ext = 'tdm'

    def __init__(self, path, **kwargs):
        super(RadarTrackingDataMessage, self).__init__(path, **kwargs)

        if not isinstance(path, SourcePath):
            raise TypeError('Can only check acceptance of path objects, not "{}"'.format(type(path)))
        if not RadarTrackingDataMessage.accept(path):
            raise TypeError('{} cannot load path of type "{}"'.format(RadarTrackingDataMessage.__name__, path.ptype))


    @staticmethod
    def accept(path):
        '''Verify that the path can be loaded by this source handler
        '''
        if not isinstance(path, SourcePath):
            raise TypeError('Can only check acceptance of path objects, not "{}"'.format(type(path)))
        else:
            if path.ptype == 'file':
                return path.data.split(os.path.sep)[-1].split('.')[-1] == RadarTrackingDataMessage.ext
            else:
                return False

    def load(self):
        path = self.path.data

        odata, ometa = ccsds.read_ccsds(path)
        sort_obs = np.argsort(odata['date'])
        odata = odata[sort_obs]

        data = np.empty(odata.shape, dtype=RadarTracklet.dtype)

        data['date'] = odata['date']
        data['r'] = odata['range']*1e3
        data['v'] = odata['doppler_instantaneous']*1e3

        #for ind in range(len(data)):
        #    lt = 0.5*data['r'][ind]/scipy.constants.c
        #    lt = np.timedelta64( int(lt*1e9),'ns')
        #    data['date'][ind] += lt

        data['r_sd'] = odata['range_err']*1e3
        data['v_sd'] = odata['doppler_instantaneous_err']*1e3

        _cm = ometa['COMMENT'].split('\n')
        for com in _cm:
            tx_ind = com.find('TX_ECEF')
            rx_ind = com.find('RX_ECEF')

            if tx_ind != -1:
                tx_ecef = com[ com.find('(')+1 : com.find(')')].split(',')
                tx_ecef = np.array([float(x) for x in tx_ecef], dtype=np.float64)
            elif rx_ind != -1:
                rx_ecef = com[ com.find('(')+1 : com.find(')')].split(',')
                rx_ecef = np.array([float(x) for x in rx_ecef], dtype=np.float64)
        

        ometa['fname'] = path.split(os.path.sep)[-1]
        ometa['tx_ecef'] = tx_ecef
        ometa['rx_ecef'] = rx_ecef

        self.index = int(float(ometa['PARTICIPANT_2']))
        self.meta = ometa
        self.data = data



class HDFSRadarTracklet(RadarTracklet):

    ext = 'h5'

    def __init__(self, path, **kwargs):
        super(HDFSRadarTracklet, self).__init__(path, **kwargs)

        if not isinstance(path, SourcePath):
            raise TypeError('Can only check acceptance of path objects, not "{}"'.format(type(path)))
        if not HDFSRadarTracklet.accept(path):
            raise TypeError('{} cannot load path of type "{}"'.format(HDFSRadarTracklet.__name__, path.ptype))


    @staticmethod
    def accept(path):
        '''Verify that the path can be loaded by this source handler
        '''
        if not isinstance(path, SourcePath):
            raise TypeError('Can only check acceptance of path objects, not "{}"'.format(type(path)))
        else:
            if path.ptype == 'file':
                return path.data.split(os.path.sep)[-1].split('.')[-1] == HDFSRadarTracklet.ext
            else:
                return False

    def load(self):
        path = self.path.data

        with h5py.File(path, 'r') as ho:

            ometa = {}
            sort_obs = np.argsort(ho["m_time"][()])

            data = np.empty((len(sort_obs),), dtype=RadarTracklet.dtype)

            data['date'] = internal_datetime.unix2npdt(ho["m_time"][()][sort_obs])
            data['r'] = ho["m_range"][()]*1e3
            data['v'] = ho["m_range_rate"][()]*1e3

            data['r_sd'] = ho["m_range_std"][()]*1e3
            data['v_sd'] = ho["m_range_rate_std"][()]*1e3

            ometa['fname'] = path.split(os.path.sep)[-1]
            ometa['tx_ecef'] = ho["tx_loc"][()]
            ometa['rx_ecef'] = ho["rx_loc"][()]

            self.index = int(ho["oid"][()])
            self.meta = ometa
            self.data = data



class OrbitEphemerisMessageSource(StateSource):

    ext = 'oem'

    def __init__(self, path, **kwargs):
        super(OrbitEphemerisMessageSource, self).__init__(path, **kwargs)

        if not isinstance(path, SourcePath):
            raise TypeError('Can only check acceptance of path objects, not "{}"'.format(type(path)))
        if not OrbitEphemerisMessageSource.accept(path):
            raise TypeError('{} cannot load path of type "{}"'.format(OrbitEphemerisMessageSource.__name__, path.ptype))


    @staticmethod
    def accept(path):
        '''Verify that the path can be loaded by this source handler
        '''
        if not isinstance(path, SourcePath):
            raise TypeError('Can only check acceptance of path objects, not "{}"'.format(type(path)))
        else:
            if path.ptype == 'file':
                return path.data.split(os.path.sep)[-1].split('.')[-1] == OrbitEphemerisMessageSource.ext
            else:
                return False


    def load(self):
        path = self.path.data

        odata, ometa = ccsds.read_oem(path)
        sort_obs = np.argsort(odata['date'])
        odata = odata[sort_obs]

        data = np.empty(odata.shape, dtype=StateSource.dtype)

        for name in odata.dtype.names:
            data[name] = odata[name]

        if 'cov' in self.kwargs:
            data['cov'] = self.kwargs['cov']
        else:
            data['cov'] = np.diag([1e3]*3 + [10.0]*3)

        #no cov is given for OEM so just assume constant or use user input
        for ind, var in enumerate(['x', 'y', 'z', 'vx', 'vy', 'vz']):
            for rowi in range(len(data)):
                data[var + '_sd'][rowi] = data['cov'][rowi][ind, ind]

        ometa['fname'] = path.split(os.path.sep)[-1]
        ometa['frame'] = ometa['REF_FRAME']

        self.index = int(float(ometa['OBJECT_ID']))
        self.meta = ometa
        self.data = data


class SourcePath(object):
    def __init__(self, data, ptype):
        if not ptype in _ptypes:
            raise TypeError('ptype "{}" not recognized'.format(ptype))

        self.ptype = ptype
        self.data = data


    def __str__(self):
        if self.ptype == 'ram':
            return self.ptype + ': ' + str(type(self.data['data']))
        else:
            return self.ptype + ': ' + str(self.data)


    @staticmethod
    def recursive_folder(folder, exts):
        if isinstance(folder, pathlib.Path):
            folder = str(folder)

        paths = []
        if folder[-1] == '/':
            _folder = folder[:-1]
        else:
            _folder = folder
        
        lst = glob.glob(_folder + '/*')
        for pth in lst:
            if os.path.isdir(pth):
                paths += SourcePath.recursive_folder(pth, exts)
            elif os.path.isfile(pth):
                _ext = pth.split(os.path.sep)[-1].split('.')[-1].lower()
                if _ext in exts:
                    paths.append(SourcePath(pth, 'file'))
        return paths


    @staticmethod
    def from_list(input_list, ptype):
        return [SourcePath(data, ptype) for data in input_list]


    @staticmethod
    def from_glob(glob_arg):
        if isinstance(glob_arg, pathlib.Path):
            glob_arg = str(glob_arg)

        return [SourcePath(str_path, 'file') for str_path in glob.glob(glob_arg)]

_sources += [
    RadarTrackingDataMessage,
    HDFSRadarTracklet,
    OrbitEphemerisMessageSource,
    SimulatedRadarTracklet,
    SimulatedStateSource,
]

class SourceCollection(list):

    def __init__(self, *args, **kwargs):
        self.paths = kwargs.get('paths', [])
        self.sources = _sources

        if 'paths' in kwargs:
            del kwargs['paths']

        super(SourceCollection, self).__init__(*args, **kwargs)

        self.load()


    def filter(self, object_id):
        for obj in self[:]:
            if obj.index != object_id:
                self.remove(obj)

    @classmethod
    def from_list(cls, lst):
        slist = cls()
        for x in lst:
            slist.paths.append(x.path)
            slist.append(x)
        return slist

    
    #implement copy and deepcopy instead of this
    def get(self, object_id):
        sub = SourceCollection()
        for path, obj in zip(self.paths, self):
            if obj.index == object_id:
                sub.paths.append(path)
                sub.append(obj)
        return sub
    

    def __str__(self):
        return 'Loaded {} items from {} paths'.format(len(self), len(self.paths))


    def details(self):
        _str = str(self)
        print('\n' + '-'*len(_str))
        print(_str)
        print('-'*len(_str))
        for obj in self:
            print(str(obj.path))
            print('{} data points '.format(len(obj.data)))
            print('-- META DATA')
            for key, val in obj.meta.items():
                print(' - {}: {}'.format(key, val))
            print('')


    def pick_source(self, path):
        for source in self.sources:
            if source.accept(path):
                return source

    def load(self):
        del self[:]
        for path in self.paths:
            source = self.pick_source(path)
            if source is None:
                raise TypeError('The path "{}" could not be used by any supported source'.format(str(path)))
            self.append(source(path=path))

