"""
@brief For pairs of flats obtain for a range of exposures, compute the
photon transfer curve (using pair_stats.py) and write as an output
file for use by later tasks, such as full_well_task.py,
linearity_task.py, etc..

@author J. Chiang <jchiang@slac.stanford.edu>
"""
import os
import glob
import pyfits
from pair_stats import pair_stats

exptime = lambda x : pyfits.open(x)[0].header['EXPTIME']

def find_flats(full_path):
    """Assume all flats are in directory full_path, have standard
    names *_flat[12].fits, and have names that are sortable by
    exposure time."""
    flat1s = glob.glob(os.path.join(full_path, '*_flat1.fits'))
    flat1s.sort()
    flats = []
    for file1 in flat1s:
        file2 = file1.replace('_flat1.fits', '_flat2.fits')
        if os.path.isfile(file1) and os.path.isfile(file2):
            flats.append(file1, file2)
    return flats

def accumulate_stats(flats, outfile='ptc_results.txt'):
    """Run pair_stats.py to find mean and variance (in units of DN) as a
    function of exposure time."""
    output = open(outfile, 'w')
    for file1, file2 in flats:
        exposure = exptime(file1)
        output.write('%12.4e' % exposure)
        for hdu in range(16):
            results, b1, b2 = pair_stats(file1, file2, hdu+2)
            output.write('  %12.4e  %12.4e'%(results.flat_mean,
                                             results.flat_var))
        output.write('\n')
        output.flush()
    output.close()

if __name__ == '__main__':
    import sys
    
    if len(sys.argv) == 3:
        full_path = sys.argv[1]
        outfile = sys.argv[2]
    else:
        #
        # For testing:
        #
        full_path = '/nfs/farm/g/lsst/u1/testData/HarvardData/112-02/ptc/logain'
        outfile = 'ptc_results.txt'
        #
        # Pipeline input
        #
        # full_path = os.environ['FLATSDIRECTORY']
        # outfile = os.environ['OUTPUTFILE']

    flats = find_flats(full_path)
    accumulate_stats(flats, outfile=outfile)
