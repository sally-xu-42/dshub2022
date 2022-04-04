import pandas as pd
import numpy as np
import netCDF4 as nc
#import pickle
import torch
from geometry import to_xyz

def train_test_split(path, year):
    '''Load data (meta + reanalysis)
    Args:
        path: path to the pickle file where all the data are merged
        year: the year to separate train & test data set
    Returns:
        train: dictionary contianing data from the 2000 to year
        test: dictionary contianing data from the year + 1 to 2009 
    '''
    data = pd.read_pickle(path)
    filter = data['year'] > year
    train = {k: v[~filter] for k, v in data.items()}
    test = {k: v[filter] for k, v in data.items()}
    return train, test


class RawData:
    """
    This class load the raw nc files and merge them into one data structure
    """
    def __init__(self, folder_path):    
        self.folder_path = folder_path

        self.merge_data(self.folder_path)
    
    def merge_data(self, folder_path):
        """merge nc files into one dictionary
        Args:
            folder_path: path to predictors folder       
        """
        columns = ['time', 'lon', 'lat', 'id', 'U500', 'V500', 'U300', 'V300', 'T850', 'MSL', 'PV320', 'pmin']
        #months = ['01','02','03','04','05','06','07','08','09','10','11','12']
        months = ['01','02']

        self._dataset = dict()
        
        # initialize the dataset dict with empty list
        for k in columns: 
            self._dataset[k] = []

        self._dataset['year'] = []
        self._dataset['month'] = []

        # iteratively read all the data records from 2000 to 2009
        for y in range(2000, 2001):
            for m in months:
                m_data = nc.Dataset(folder_path + "/pr_" + str(y) + m + ".nc")
                for k in columns:
                    self._dataset[k] = np.append(self._dataset[k], m_data.variables[k][:].data, axis = 0) if len(self._dataset[k]) > 0 else m_data.variables[k][:].data
                self._dataset['year'] = np.append(self._dataset['year'], [y] * len(m_data.dimensions['ind']))
                self._dataset['month'] = np.append(self._dataset['month'], [int(m)] * len(m_data.dimensions['ind']))
        
        # Make the id of the first cyclone track start with 0 (currently it starts with some higher number)
        start_id = self._dataset['id'][0]
        self._dataset['id'] -= start_id

        # for every single cyclone track, build a TrackView object
        cyclone_ids = self._dataset['id'].tolist()

        # index that mark the start position of one cyclone
        start_indices = [i for i in range(len(cyclone_ids)) if cyclone_ids[i] != cyclone_ids[i-1]] 
        start_indices.append(len(cyclone_ids))

        track_list = []
        for index in range(len(start_indices) - 1):
            start = start_indices[index]
            end = start_indices[index+1]
            track_list.append(TrackView(self._dataset, list(range(start, end))))


        # Store all the TrackView objects in a list
        self._track_list = TrackViewList(track_list)

    @property
    def tracks(self):
        return self._track_list

    @property
    def raw_data(self):
        return self._dataset

class StepView:
    """
    Objects of this class contain pointers to a single time step in a single cyclone track. You can access both,
    the meta-data and the re-analysis data of this time step.
    """

    def __init__(self, raw_data, index):
        self._raw_data = raw_data
        self._index = index

    def get_ra_features(self, feature_names, shape="flat", time_step=None):
        """
        Returns the data of one or several reanalysis features.

        Args:
            feature_names: Either a string or a list of strings of reanalysis feature names. Valid strings are:
                ['U300', 'V300', 'U500', 'V500', 'T850', 'MSL', 'PV320']
            shape: Either "grid" in which case the data of each feature will be returned as a list of 11x11 grids
                or "flat" in which case the data of each feature will be returned flattened as a 1d array.

        Returns:
            A numpy matrix containing either one/several 1d arrays or one/several 11x11 shaped matrices
        """

        features = []
        if not hasattr(feature_names, "__iter__"):
            feature_names = [feature_names]

        for feature_name in feature_names:
            feature = self._raw_data[feature_name][self._index]
            if shape == "flat":
                feature = feature.flatten()
            features.append(feature)    # all features from one timestampis concatenated into one list

        if time_step is not None:
            time_steps = np.repeat(time_step, features[-1].shape[0])
            features.append(time_steps)  # add time step info into features

        if not hasattr(feature_names, "__iter__"):
            return features[0]

        features = np.array(features)

        return features

    def get_meta_features(self, feature_names, position_enc="lonlat"):
        """
        Returns the data of one or several meta-data features.

        Args:
            feature_names: Either a string or a list of strings of meta feature names. Valid strings are:
                ['id', 'time', 'center_lon', 'center_lat', 'pmin', 'pcont', 'month']

        Returns:
            A numpy 1d array containing one entry for each indicated feature
        """
        lon, lat = False, False
        lon_idx, lat_idx = -1, -1

        features = []
        if not hasattr(feature_names, "__iter__"):
            feature_names = [feature_names]

        for idx, feature_name in enumerate(feature_names):
            if feature_name == "lon":
                lon = True
                lon_idx = idx
            elif feature_name == "lat":
                lat = True
                lat_idx = idx
            feature = self._raw_data[feature_name][self._index]

            features.append(feature)


        # transform the long & lat into 3d-euclidian space
        if lon and lat and position_enc == "xyz":
            longitudes = features[lon_idx]
            latitudes = features[lat_idx]
            del features[max(lon_idx, lat_idx)]
            del features[min(lon_idx, lat_idx)]
            x, y, z = to_xyz(np.array(longitudes), np.array(latitudes))
            features.extend([x, y, z])

        features = np.array(features)
        return features

class StepViewList:
    """
    This is just a list wrapper for 'StepView'. It will be used to implement functions which act on all
    'StepView' objects in this list at the same time.
    """

    def __init__(self, step_list):
        self._step_list = step_list

    def __getitem__(self, index):
        if type(index) == slice:
            indices = list(range(len(self._step_list)))
            index = indices[index]
            return self._step_list[index]

        if hasattr(index, "__iter__"):
            items = []
            for element in index:
                items.append(self._step_list[element])
            return StepViewList(items)
        

    def __iter__(self):
        yield from self._step_list

    def __len__(self):
        return len(self._step_list)

class TrackView:
    """
    Objects of this class contain a list of pointers to different time-steps in the raw-data. The idea is that this allows you to
    store a cyclone track or a cyclone sub-track (for example if you extracted only every 3rd time step) as a list of pointers.
    You can access the individual time-steps of this cyclone (sub-)track as 'StepView' objects.
    """

    def __init__(self, raw_data, indices):
        self._raw_data = raw_data
        self._indices = list(indices)  # indices are all index related to one cyclone

    def drop_steps(self, indices):
        """
        Allows you to drop single pointers to time-steps. This can be useful if you want to keep only every x'th time-step.

        Args:
            indices: Either a list of indices or a single index which index the 'self._indices' array.

        Returns:
            Nothing. The indexed values in 'self._indices' will be removed.
        """
        if not hasattr(indices, "__iter__"):
            indices = [indices]
        values = [self._indices[index] for index in indices]
        for value in values:
            self._indices.remove(value)

    def drop_last(self):
        """
        A short-cut to remove the very last time-step of the stored time-steps.
        """
        self.drop_steps(-1)

    def extract_sub_track(self, start, length, stride):
        """
        Extracts a subtrack from the current track.

        Example: If 'self._indices = [0,1,2,3,4,5,6,7,8,9,10]', 'start = 2', 'length = 4' and 'stride = 2',
        then the function would return a new 'TrackView' object with the following indices: [2,4,6,8]

        Args:
            start: The start index
            length: The length of the newly generate sub-sequence
            stride: The stride with which the indices of the new sub-sequence get extracted

        Returns:
            A new 'TrackView' object with self.indices[start: end: stride] where end is chosen such that exactly
            'length' many elements get extracted
        """
        if start + (length - 1) * stride >= len(self):
            return None
        end = start + (length - 1) * stride + 1
        indices = self._indices[start: end: stride]
        return TrackView(self._raw_data, indices)

    def extract_all_sub_tracks(self, start_stride, length, step_stride):
        """
        Starting at index 0 and increasing this index by 'start_stride', extract all sub-tracks of size 
        'length' with stride 'step_stride'.

        Example: If 'self._indices = [0,1,2,3,4,5,6,7,8,9,10]', 'start_stride = 3', 'length = 3' and 'step_stride = 2',
        then the function would return a list of 'TrackView' objects with indices:
            [0,2,4], [3,5,7], [6,8,10]

        Args:
            start_stride: Denotes the offset used to start the next sub-track
            length: Denotes how many steps there should be in the sub-track
            step_stride: Denotes the offstet used to extract the single steps.

        Returns:
            A 'TrackViewList' object containing a list of extracted sub-track 'TrackView' objects.
        """
        sub_tracks = []
        start = 0

        while True:
            sub_track = self.extract_sub_track(start, length, step_stride)
            if sub_track is None:
                break
            sub_tracks.append(sub_track)
            start += start_stride

        return TrackViewList(sub_tracks)

    def __getitem__(self, index):
        if type(index) == slice:
            indices = list(range(len(self._indices)))
            index = indices[index]
        if hasattr(index, "__iter__"):
            items = []
            for element in index:
                items.append(StepView(self._raw_data, self._indices[element]))
            return StepViewList(items)
        return StepView(self._raw_data, self._indices[index])

    def __iter__(self):
        for index in self._indices:
            yield StepView(self._raw_data, index)

    def __len__(self):
        return len(self._indices)


class TrackViewList:
    """
    This is just a list wrapper for 'TrackView'. It will be used to implement functions which act on all
    'TrackView' objects in this list at the same time.
    """

    def __init__(self, track_list):
        self._track_list = track_list

    def __getitem__(self, index):
        if type(index) == slice:
            indices = list(range(len(self._track_list)))
            index = indices[index]
        if hasattr(index, "__iter__"):
            items = []
            for element in index:
                items.append(self._track_list[element])
            return TrackViewList(items)
        return self._track_list[index]

    def __iter__(self):
        yield from self._track_list

    def __len__(self):
        return len(self._track_list)


class CycloneDataset(torch.utils.data.Dataset):

    def __init__(self, data_list):
        super().__init__()
        self.data_list = data_list

    def __getitem__(self, index):
        return self.data_list[index]

    def __len__(self):
        return len(self.data_list)






