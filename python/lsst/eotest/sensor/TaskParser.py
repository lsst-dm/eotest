"""
@brief Class to parse common command line options for pipeline tasks.

@author J. Chiang <jchiang@slac.stanford.edu>
"""
import os
import glob
import argparse
import lsst.afw.image as afwImage
import lsst.eotest.image_utils as imutils
from ccd250_mask import ccd250_mask
from lsst.eotest.database.SensorDb import SensorDb, NullDbObject
from lsst.eotest.database.SensorGains import SensorGains

class TaskNamespace(object):
    """
    Decorator class for argparse.Namespace.  This class provides
    functions for information derived from the Namespace attributes, such
    as lists of input files based on a file pattern or file_list file,
    a SensorDb object, system gains, etc..
    """
    def __init__(self, args):
        self.args = args
    def files(self, file_pattern, file_list):
        if file_list is not None:
            my_files = [x.strip() for x in open(file_list)
                        if x[0] != '#']
        elif file_pattern is not None:
            my_pattern = file_pattern.replace('\\', '')
            my_files = glob.glob(my_pattern)
            my_files.sort()
        else:
            raise RuntimeError('You must specify a file pattern or file list.')
        return my_files
    def sensor(self):
        if self.args.db_credentials is not None:
            sensorDb = SensorDb(self.args.db_credentials)
            return sensorDb.getSensor(self.args.Vendor, self.args.sensor_id, 
                                      add=True)
        else:
            return NullDbObject()
    def system_gains(self):
        if self.args.db_credentials is not None:
            return SensorGains(vendor=self.args.Vendor,
                               vendorId=self.args.sensor_id,
                               db_credentials=self.args.db_credentials)
        else:
            md = afwImage.readMetadata(self.args.gains, 1)
            return dict([(amp, md.get('GAIN%s' % imutils.channelIds[amp]))
                         for amp in imutils.allAmps])
    def mask_files(self):
        if self.args.mask_file == 'ccd250_defects':
            my_mask_files = ('ccd250_defects.fits',)
            if not os.path.isfile(my_mask_files[0]):
                ccd250_mask(my_mask_files[0])
        elif self.args.mask_file == 'None':
            my_mask_files = ()
        elif self.args.mask_file is not None:
            my_mask_files = (self.args.mask_file,)
        return my_mask_files
    def __getattr__(self, attrname):
        return getattr(self.args, attrname)

class TaskParser(argparse.ArgumentParser):
    """
    Subclass of argparse.ArgumentParser that adds default command line
    options for all pipeline tasks.
    """
    def __init__(self, description):
        formatter_class = argparse.ArgumentDefaultsHelpFormatter
        argparse.ArgumentParser.__init__(self, description=description,
                                         formatter_class=formatter_class)
        self.add_argument('-d', '--db_credentials', type=str,
                          help='file containing database credentials')
        self.add_argument('-s', '--sensor_id', type=str,
                          help="sensor ID")
        self.add_argument('-V', '--Vendor', type=str,
                          help='CCD vendor (e.g., e2v, ITL)')
        self.add_argument('-m', '--mask_file', 
                          default='ccd250_defects', type=str,
                          help='mask file to use')
        self.add_argument('-o', '--output_dir', type=str, default='.',
                          help="output directory")
        self.add_argument('-g', '--gains', type=str, 
                          help='file of system gains')
        self.add_argument('-v', '--verbose', action='store_true',default=False,
                          help='turn verbosity on')
    def parse_args(self):
        args = argparse.ArgumentParser.parse_args(self)
        if args.output_dir is not None:
            try:
                os.makedirs(args.output_dir)
            except OSError:
                pass
        return TaskNamespace(args)

if __name__ == '__main__':
    parser = TaskParser('test task')
    args = parser.parse_args()
