"""
@brief Find dark pixels and dark columns above a threshold specified
in units of e- per second per pixel.
"""
import os
import numpy as np
import pyfits
from lsst.eotest.pyfitsTools import pyfitsWriteto

import lsst.afw.detection as afwDetect
import lsst.afw.image as afwImage
import lsst.afw.math as afwMath
import lsst.daf.base as dafBase

import lsst.eotest.image_utils as imutils
from MaskedCCD import MaskedCCD
from AmplifierGeometry import makeAmplifierGeometry
from fits_headers import fits_headers

class DarkPixels(object):
    """
    Find dark pixels and dark columns based on a threshold of
    frac_thresh of the segment mean.  A dark column has at least
    colthresh dark pixels.  The dark pixels that are returned are
    exclusive of the dark columns.  The mask that is generated will
    be identified as mask_plane.
    """
    def __init__(self, ccd, amp, frac_thresh=0.8, colthresh=100,
                 mask_plane='BAD'):
        self.ccd = ccd
        self.amp = amp
        self.raw_image = ccd[amp]
        self.frac_thresh = frac_thresh
        self.colthresh = colthresh
        self.mask_plane = mask_plane
        self.dark_pixels = None
        self.dark_columns = None
    def generate_mask(self, outfile):
        """
        Find dark pixels and columns, and write the mask for this
        amplifier to a FITS file using the afwImage.MaskU.writeFits
        method.
        """
        if self.dark_pixels is None or self.dark_columns is None:
            self.find()
        self.mask = afwImage.MaskU(self.raw_image.getDimensions())
        self.fp_set.setMask(self.mask, self.mask_plane)
        self._write_fits(outfile)
    def _write_fits(self, outfile):
        amp_geom = makeAmplifierGeometry(self.ccd.imfile)
        hdrs = fits_headers()
        if not os.path.isfile(outfile):
            # We are writing the first extension, most likely, so create
            # the output file.
            output = pyfits.HDUList()
            output.append(pyfits.PrimaryHDU())
            output[0].header['MASKTYPE'] = 'DARK_PIXELS'
            output[0].header['FTHRESH'] = self.frac_thresh
            output[0].header['CTHRESH'] = self.colthresh
            pyfitsWriteto(output, outfile, clobber=True)
        imaging = self.ccd.amp_geom.imaging
        ihdr = hdrs[hdrs.keys()[self.amp]]
        md = dafBase.PropertySet()
        md.set('EXTNAME', 'SEGMENT%s' % imutils.channelIds[self.amp])
        md.set('DETSIZE', amp_geom[self.amp]['DETSIZE'])
        md.set('DETSEC', amp_geom[self.amp]['DETSEC'])
        md.set('DATASEC', amp_geom[self.amp]['DATASEC'])
        md.set('NDARKPIX', len(self.dark_pixels))
        md.set('NDARKCOL', len(self.dark_columns))
        self.mask.writeFits(outfile, md, 'a')
    def _inverted_image(self, offset=None):
        """
        Return a masked image which is the trimmed and unbiased amp
        image subtracted from an offset value.  This enables regions
        below a specified threshold value to be found using afwDetect.
        If offset is None, then use the median of the unmasked pixels.
        """
        my_image = self.ccd.unbiased_and_trimmed_image(self.amp).clone()
        if offset is None:
            median = afwMath.makeStatistics(my_image, afwMath.MEDIAN,
                                            self.ccd.stat_ctrl).getValue()
        else:
            median = offset
        imarr = my_image.getImage().getArray()  # a view into the image data
        imarr[:] = median - imarr[:]
        return my_image, median
    def find(self):
        """
        Find and return the dark pixels and dark columns.
        """
        image, median = self._inverted_image()
        #
        # For the inverted image, the dark pixel threshold is
        # (1 - frac)*median.
        #
        threshold = afwDetect.Threshold((1. - self.frac_thresh)*median)
        #
        # Apply footprint detection code.
        #
        self.fp_set = afwDetect.FootprintSet(image, threshold)
        #
        # Organize dark pixels by column.
        #
        columns = dict([(x, []) for x in range(0, self.raw_image.getWidth())])
        for footprint in self.fp_set.getFootprints():
            for span in footprint.getSpans():
                y = span.getY()
                for x in range(span.getX0(), span.getX1()+1):
                    columns[x].append(y)
        #
        # Divide into dark columns (with # dark pixels > self.colthresh)
        # and remaining dark pixels.
        #
        dark_pixs = []
        dark_cols = []
        x0 = image.getX0()
        y0 = image.getY0()
        for x in columns:
            if len(columns[x]) > self.colthresh:
                dark_cols.append(x - x0)
            else:
                dark_pixs.extend([(x - x0, y - y0) for y in columns[x]])
        #
        # Sort the output.
        #
        dark_cols.sort()
        dark_pixs = sorted(dark_pixs)
        self.dark_pixels = dark_pixs
        self.dark_columns = dark_cols
        return dark_pixs, dark_cols
if __name__ == '__main__':
    import sim_tools
    def write_test_image(outfile, emin=10, dark_curr=2e-3, exptime=10,
                         gain=5, ccdtemp=-100, bias_level=1e2,
                         bias_sigma=4, ncols=2, npix=100):
        ccd = sim_tools.CCD(exptime=exptime, gain=gain, ccdtemp=ccdtemp)
        ccd.add_bias(bias_level, bias_sigma)
        ccd.add_dark_current(level=dark_curr)
        #
        # Simulation code sets dark pixel values by nsig*seg.sigma(),
        # but we want to set using e- per sec, so need to compute
        # equivalent nsig.
        #
        nsig = (emin*exptime/gain)/ccd.segments[imutils.allAmps[0]].sigma()
        #
        columns = ccd.generate_dark_cols(ncols)
        ccd.add_dark_cols(columns, nsig=nsig)
        pixels = ccd.generate_dark_pix(npix)
        ccd.add_dark_pix(pixels, nsig=nsig)
        pyfitsWriteto(ccd, outfile)

    def remove_file(filename):
        try:
            os.remove(filename)
        except OSError:
            pass

    dark_file = 'dark_pix_test.fits'
    mask_file = 'dark_pix_mask.fits'
    remove_file(mask_file)

    gain = 5
    write_test_image(dark_file, emin=10, gain=5, npix=1000)

    ccd = MaskedCCD(dark_file)
    for amp in imutils.allAmps:
        dark_pixels = DarkPixels(ccd, amp, ccd.md.get('EXPTIME'), gain)
        pixels, columns = dark_pixels.find()
        dark_pixels.generate_mask(mask_file)
        print imutils.channelIds[amp], len(pixels), columns
