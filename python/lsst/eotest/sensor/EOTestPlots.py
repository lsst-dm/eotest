import os
import sys
import glob
import copy
from collections import OrderedDict
from sets import Set
import numpy as np
import pyfits
import pylab
import matplotlib as mpl
import pylab_plotter as plot
from MaskedCCD import MaskedCCD
from rolloff_mask import rolloff_mask
from EOTestResults import EOTestResults
from Fe55GainFitter import Fe55GainFitter
from fe55_psf import psf_sigma_statistics
from DetectorResponse import DetectorResponse
from crosstalk import CrosstalkMatrix
from QE import QE_Data
from AmplifierGeometry import parse_geom_kwd
import lsst.eotest.image_utils as imutils
import lsst.afw.image as afwImage
import lsst.afw.math as afwMath
import lsst.afw.display.ds9 as ds9

def latex_minus_mean(values, format='%.2e'):
    """
    Wrapper to np.mean to handle infinities.  Returns the evaluated
    string '-\num{<%format>}' % np.mean(values) if the result is
    finite.
    """
    mean_value = np.mean(values)
    if not np.isinf(mean_value):
        if mean_value < 0:
            template = '+ \\num{' + format + '}'
        else:
            template = '- \\num{' + format + '}'
        return template % np.abs(mean_value)
    elif mean_value < 0:
        return '+ \\infty'
    else:
        return '- \\infty'

def latex_minus_max(values, format='%.2e'):
    max_value = max(values)
    if max_value < 0:
        template = '+ \\num{' + format + '}'
    else:
        template = '- \\num{' + format + '}'
    return template % np.abs(max_value)

def plot_flat(infile, nsig=3, cmap=pylab.cm.hot, win=None, subplot=(1, 1, 1),
              figsize=None, wl=None, gains=None, use_ds9=False):
    ccd = MaskedCCD(infile)
    foo = pyfits.open(infile)
    detsize = parse_geom_kwd(foo[1].header['DETSIZE'])
    nx = detsize['xmax']
    ny = detsize['ymax']
    mosaic = np.zeros((ny, nx), dtype=np.float)
    for ypos in range(2):
        for xpos in range(8):
            amp = ypos*8 + xpos + 1
            #
            # Compute subarray boundaries for this segment in the mosaicked
            # image array.
            datasec = parse_geom_kwd(foo[amp].header['DATASEC'])
            dx = np.abs(datasec['xmax'] - datasec['xmin']) + 1
            dy = np.abs(datasec['ymax'] - datasec['ymin']) + 1
            if ypos == 0:
                xmin = nx - (xpos + 1)*dx
                ymin = dy
            else:
                xmin = xpos*dx
                ymin = 0
            xmax = xmin + dx
            ymax = ymin + dy
            #
            # Extract the bias-subtracted masked image for this segment.
            segment_image = ccd.unbiased_and_trimmed_image(amp)
            subarr = segment_image.getImage().getArray()
            #
            # Determine flips in x- and y-directions in order to
            # get the (1, 1) pixel in the lower right corner.
            detsec = parse_geom_kwd(foo[amp].header['DETSEC'])
            if detsec['xmax'] > detsec['xmin']:  # flip in x-direction
                subarr = subarr[:,::-1]
            if detsec['ymax'] > detsec['ymin']:  # flip in y-direction
                subarr = subarr[::-1,:]
            #
            # Convert from ADU to e-
            if gains is not None:
                subarr *= gains[amp]
            #
            # Set the subarray in the mosaicked image.
            mosaic[ymin:ymax, xmin:xmax] = subarr
    #
    # Display the mosiacked image in ds9 using afwImage.
    if use_ds9:
        image = afwImage.ImageF(nx, ny)
        imarr = image.getArray()
        # This needs a flip in y to display properly in ds9 so that
        # amp 1 is in the lower right corner.
        imarr[:] = mosaic[::-1,:]
        ds9.mtv(image)
        ds9.ds9Cmd('zoom to fit')
        return
    #
    # Write a fits image with the mosaicked CCD data.
    #
    hdulist = pyfits.HDUList()
    hdulist.append(pyfits.PrimaryHDU())
    hdulist[0].data = mosaic[::-1,:]
    hdulist.writeto('mosaicked_flat_%04i.fits' % foo[0].header['MONOWL'],
                    clobber=True)
    #
    # Set the color map to extend over the range median +/- stdev(clipped)
    # of the pixel values.
    pixel_data = mosaic.flatten()
    stats = afwMath.makeStatistics(pixel_data,
                                   afwMath.STDEVCLIP | afwMath.MEDIAN)
    median = stats.getValue(afwMath.MEDIAN)
    stdev = stats.getValue(afwMath.STDEVCLIP)
    vmin = max(min(pixel_data), median - nsig*stdev)
    vmax = min(max(pixel_data), median + nsig*stdev)
    if win is None:
        win = plot.Window(subplot=subplot, figsize=figsize,
                          xlabel='', ylabel='')
    else:
        win.select_subplot(*subplot)
    image = win.axes[-1].imshow(mosaic, interpolation='nearest', cmap=cmap)
    norm = mpl.colors.Normalize(vmin=vmin, vmax=vmax)
    image.set_norm(norm)
    if wl is None:
        # Extract wavelength from file
        wl = foo[0].header['MONOWL']
    win.axes[-1].set_title('%i nm' % wl)
    win.fig.colorbar(image)
    # Turn off tick labels for x- and y-axes
    pylab.setp(win.axes[-1].get_xticklabels(), visible=False)
    pylab.setp(win.axes[-1].get_yticklabels(), visible=False)
    return win

class EOTestPlots(object):
    band_pass = QE_Data.band_pass
    prnu_wls = (350, 450, 500, 620, 750, 870, 1000)
    def __init__(self, sensor_id, rootdir='.', output_dir='.',
                 interactive=False, results_file=None, xtalk_file=None):
        self.sensor_id = sensor_id
        self.rootdir = rootdir
        self.output_dir = output_dir
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        self.interactive = interactive
        plot.pylab.interactive(interactive)
        if results_file is None:
            results_file = self._fullpath('%s_eotest_results.fits' % sensor_id)
        if not os.path.exists(results_file):
            raise RuntimeError("EOTestPlots: %s not found" % results_file)
        self.results = EOTestResults(results_file)
        self._qe_data = None
        self._qe_file = self._fullpath('%s_QE.fits' % self.sensor_id)
        self.specs = CcdSpecs(results_file, plotter=self,
                              xtalk_file=xtalk_file, prnu_wls=self.prnu_wls)
#        try:
#            self.specs = CcdSpecs(results_file, plotter=self,
#                                  xtalk_file=xtalk_file)
#        except KeyError:
#            print "Error reading results file for CcdSpecs object."
#            print results_file, xtalk_file
#            print "LaTeX table generation of specs is disabled."
    @property
    def qe_data(self):
        if self._qe_data is None:
            self._qe_data = pyfits.open(self._qe_file)
        return self._qe_data
    def _save_fig(self, outfile_root):
        plot.pylab.savefig(self._outputpath('%s.png' % outfile_root))
    def _fullpath(self, basename):
        return os.path.join(self.rootdir, basename)
    def _outputpath(self, basename):
        return os.path.join(self.output_dir, basename)
    def crosstalk_matrix(self, cmap=pylab.cm.hot, xtalk_file=None):
        if xtalk_file is None:
            xtalk_file = os.path.join(self.rootdir, 
                                      '%s_xtalk_matrix.fits' % self.sensor_id)
        foo = CrosstalkMatrix(xtalk_file)
#        foo.plot_matrix(cmap=cmap)
        win = foo.plot(title="Crosstalk, %s" % self.sensor_id)
        return foo
    def persistence(self, infile=None, figsize=(11, 8.5)):
        if infile is None:
            infile = self._fullpath('%s_persistence.fits' % self.sensor_id)
        results = pyfits.open(infile)
        times = results[1].data.field('TIME')
        win = None
        for amp in imutils.allAmps:
            subplot = (4, 4, amp)
            if amp == 1:
                win = plot.Window(subplot=subplot, figsize=figsize,
                                  xlabel=r'Time since end of flat exposure (s)',
                                  ylabel=r'Deferred charge (e-/pixel)')
                win.frameAxes.text(0.5, 1.08,
                                   'Image Persistence vs Time, %s' \
                                       % self.sensor_id,
                                   horizontalalignment='center',
                                   verticalalignment='top',
                                   transform=win.frameAxes.transAxes,
                                   size='large')
            else:
                win.select_subplot(*subplot)
            self._offset_subplot(win)
            flux = results[1].data.field('MEDIAN%02i' % amp)
            stdev = results[1].data.field('STDEV%02i' % amp)
            try:
                plot.xyplot(times, flux, yerr=stdev, xname='', yname='',
                            new_win=False)
                pylab.annotate('Amp %i' % amp, (0.5, 0.8),
                               xycoords='axes fraction', size='x-small')
            except Exception, eobj:
                print "Exception raised in generating image persistence plot for amp", amp
                print eobj
                # Continue with remaining amps
                pass
    def psf_dists(self, chiprob_min=0.1, fe55_file=None, figsize=(11, 8.5),
                  xrange=(2, 6), bins=50):
        if fe55_file is None:
            fe55_file = glob.glob(self._fullpath('%s_psf_results*.fits' 
                                                 % self.sensor_id))[0]
        fe55_catalog = pyfits.open(fe55_file)
        win = None
        for amp in imutils.allAmps:
            #print "Amp", amp
            subplot = (4, 4, amp)
            chiprob = fe55_catalog[amp].data.field('CHIPROB')
            index = np.where(chiprob > chiprob_min)
            # Read sigma values and convert from pixels to microns
            sigmax = fe55_catalog[amp].data.field('SIGMAX')[index]*10.
            sigmay = fe55_catalog[amp].data.field('SIGMAY')[index]*10.
            sigma = sorted(np.concatenate((sigmax, sigmay)))
            if amp == 1:
                win = plot.Window(subplot=subplot, figsize=figsize,
                                  xlabel=r'PSF sigma ($\mu$)',
                                  ylabel=r'entries / bin', size='large')
                win.frameAxes.text(0.5, 1.08, 
                                   'PSF from Fe55 data, %s' % self.sensor_id,
                                   horizontalalignment='center',
                                   verticalalignment='top',
                                   transform=win.frameAxes.transAxes,
                                   size='large')
            else:
                win.select_subplot(*subplot)
            self._offset_subplot(win)
            try:
                plot.histogram(sigma, xrange=xrange, bins=bins, new_win=False,
                               xname='', yname='')
                pylab.xticks(range(xrange[0], xrange[1]+1))
                # Find mode from histogram data
                mode, median, mean = psf_sigma_statistics(sigma, bins=bins, 
                                                          range=xrange)
                plot.vline(5)
                plot.vline(mode, color='r')
                pylab.annotate('Amp %i\nmode=%.2f' % (amp, mode), (0.5, 0.8),
                               xycoords='axes fraction', size='x-small')
            except Exception, eobj:
                print "Exception raised in generating PSF sigma plot for amp", amp
                print eobj
                # Skip this plot so that the rest of the plots can be
                # generated.
                pass
    def fe55_dists(self, chiprob_min=0.1, fe55_file=None, figsize=(11, 8.5)):
        if fe55_file is None:
            fe55_file = glob.glob(self._fullpath('%s_psf_results*.fits' 
                                                 % self.sensor_id))[0]
        fe55_catalog = pyfits.open(fe55_file)
        win = None
        for amp in imutils.allAmps:
            #print "Amp", amp
            chiprob = fe55_catalog[amp].data.field('CHIPROB')
            index = np.where(chiprob > chiprob_min)
            dn = fe55_catalog[amp].data.field('DN')[index]
            foo = Fe55GainFitter(dn)
            try:
                foo.fit()
            except:
                continue
            if amp == 1:
                win = foo.plot(interactive=self.interactive, 
                               subplot=(4, 4, amp),
                               figsize=figsize, frameLabels=True, amp=amp)
                win.frameAxes.text(0.5, 1.08, 'Fe55, %s' % self.sensor_id,
                                   horizontalalignment='center',
                                   verticalalignment='top',
                                   transform=win.frameAxes.transAxes,
                                   size='large')
            else:
                foo.plot(interactive=self.interactive, 
                         subplot=(4, 4, amp), win=win,
                         frameLabels=True, amp=amp)
            pylab.locator_params(axis='x', nbins=4, tight=True)
    def ptcs(self, xrange=None, yrange=None, figsize=(11, 8.5),
             ptc_file=None):
        if ptc_file is not None:
            ptc = pyfits.open(ptc_file)
        else:
            ptc = pyfits.open(self._fullpath('%s_ptc.fits' % self.sensor_id))
        for amp in imutils.allAmps:
            #print "Amp", amp
            subplot = (4, 4, amp)
            if amp == 1:
                win = plot.Window(subplot=subplot, figsize=figsize,
                                  xlabel=r'mean (ADU)',
                                  ylabel=r'variance (ADU$^2$)', size='large')
                win.frameAxes.text(0.5, 1.08,
                                   'Photon Transfer Curves, %s' \
                                       % self.sensor_id,
                                   horizontalalignment='center',
                                   verticalalignment='top',
                                   transform=win.frameAxes.transAxes,
                                   size='large')
            else:
                win.select_subplot(*subplot)
            self._offset_subplot(win)
            mean = ptc[1].data.field('AMP%02i_MEAN' % amp)
            var = ptc[1].data.field('AMP%02i_VAR' % amp)
            win = plot.xyplot(mean, var, xname='', yname='',
                              xrange=xrange, yrange=yrange,
                              xlog=1, ylog=1, new_win=False,)
            axes = pylab.gca()
            xrange = list(axes.get_xlim())
            xrange[0] = max(xrange[0], 1e-1)
            xx = np.logspace(np.log10(xrange[0]), np.log10(xrange[1]), 20)
            plot.curve(xx, xx/self.results['GAIN'][amp-1], oplot=1, color='r')
            pylab.annotate('Amp %i' % amp, (0.475, 0.8),
                           xycoords='axes fraction', size='x-small')
    def _offset_subplot(self, win, xoffset=0.025, yoffset=0.025):
        bbox = win.axes[-1].get_position()
        points = bbox.get_points()
        points[0] += xoffset
        points[1] += yoffset
        bbox.set_points(points)
        win.axes[-1].set_position(bbox)
    def gains(self, oplot=0, xoffset=0.25, width=0.5, xrange=(0, 16.5)):
        results = self.results
        gain = results['GAIN']
        error = results['GAIN_ERROR']
        ymin = max(min(gain - error), min(gain - 1))
        ymax = min(max(gain + error), max(gain + 1))
        win = plot.xyplot(results['AMP'], results['GAIN'],
                          yerr=results['GAIN_ERROR'], xname='AMP',
                          yname='gain (e-/DN)', yrange=(ymin, ymax),
                          xrange=xrange)
        win.set_title("System Gain, %s" % self.sensor_id)
    def noise(self, oplot=0, xoffset=0.2, width=0.2, color='b'):
        results = self.results
        read_noise = results['READ_NOISE']
        try:
            system_noise = results['SYSTEM_NOISE']
            total_noise = results['TOTAL_NOISE']
        except KeyError:
            system_noise = np.zeros(len(read_noise))
            total_noise= read_noise
        ymax = max(1.2*max(np.concatenate((read_noise,
                                           system_noise,
                                           total_noise))), 10)
        win = plot.bar(results['AMP'] - xoffset - xoffset/2., read_noise,
                       xname='Amp', 
                       yname='Noise (rms e-)',
                       xrange=(0, 17), color=color, width=width,
                       yrange=(0, ymax))
        plot.bar(results['AMP'] - xoffset/2., system_noise,
                 oplot=1, color='g', width=width)
        plot.bar(results['AMP'] + xoffset - xoffset/2., total_noise,
                 oplot=1, color='c', width=width)
        plot.legend('bgc', ('Read Noise', 'System Noise', 'Total Noise'))
        plot.hline(8)
        win.set_title("Read Noise, %s" % self.sensor_id)
    def linearity(self, gain_range=(1, 6), max_dev=0.02, figsize=(11, 8.5),
                  ptc_file=None, detresp_file=None):
        if ptc_file is not None:
            ptc = pyfits.open(ptc_file)
        else:
            try:
                ptc = pyfits.open(self._fullpath('%s_ptc.fits' % self.sensor_id))
            except IOError:
                ptc = None
        if detresp_file is not None:
            detresp = DetectorResponse(detresp_file, ptc=ptc,
                                       gain_range=gain_range)
        else:
            detresp = DetectorResponse(self._fullpath('%s_det_response.fits' 
                                                      % self.sensor_id),
                                       ptc=ptc, gain_range=gain_range)
        for amp in imutils.allAmps:
            try:
                maxdev, fit_pars, Ne, flux = detresp.linearity(amp)
            except Exception, eObj:
                print "EOTestPlots.linearity: amp %i" % amp
                print "  ", eObj
            f1 = np.poly1d(fit_pars)
            dNfrac = 1 - Ne/f1(flux)

            subplot = (4, 4, amp)
            if amp == 1:
                win = plot.Window(subplot=subplot, figsize=figsize,
                                  xlabel=r'pd current $\times$ exposure', 
                                  ylabel='', size='large')
                win.frameAxes.text(0.5, 1.08, 'Linearity, %s' % self.sensor_id,
                                   horizontalalignment='center',
                                   verticalalignment='top',
                                   transform=win.frameAxes.transAxes,
                                   size='large')
            else:
                win.select_subplot(*subplot)
            self._offset_subplot(win)
            # Resize subplot for plotting e-/pixel vs flux
            bbox = win.axes[-1].get_position()
            top_pts = bbox.get_points()
            bot_pts = copy.deepcopy(top_pts)
            dx, dy = top_pts[1] - top_pts[0]
            top_pts[0][1] += dy/4
            bbox.set_points(top_pts)
            win.axes[-1].set_position(bbox)
            try:
                win.axes[-1].loglog(flux, Ne, 'ko')
            except Exception, eObj:
                print "EOTestPlots.linearity: amp %i" % amp
                print "  ", eObj
            try:
                win.axes[-1].loglog(flux, f1(flux), 'r-')
            except Exception, eObj:
                print "EOTestPlots.linearity: amp %i" % amp
                print "  ", eObj
            sys.stdout.flush()

            # Plot horizontal lines showing the range of the linearity
            # spec in e-/pixel.
            xmin, xmax, ymin, ymax = pylab.axis()
            win.axes[-1].loglog([xmin, xmax], [1e3, 1e3], 'k:')
            win.axes[-1].loglog([xmin, xmax], [9e4, 9e4], 'k:')

            # Label plots by amplifier number.
            pylab.annotate('Amp %i' % amp, (0.2, 0.8),
                           xycoords='axes fraction', size='x-small')
            if amp in (1, 5, 9, 13):
                win.axes[-1].set_ylabel('e-/pixel')
            for label in win.axes[-1].get_xticklabels():
                label.set_visible(False)

            # Add fractional residuals sub-subplot.
            bot_rect = [bot_pts[0][0], bot_pts[0][1], dx, dy/4.]
            bot_ax = win.fig.add_axes(bot_rect, sharex=win.axes[-1])
            bot_ax.semilogx(flux, dNfrac, 'ko')
            bot_ax.semilogx(flux, dNfrac, 'k:')
            bot_ax.semilogx(flux, np.zeros(len(Ne)), 'r-')
            pylab.locator_params(axis='y', nbins=5, tight=True)
            plot.setAxis(yrange=(-1.5*max_dev, 1.5*max_dev))
    def qe_ratio(self, ref, amp=None, qe_file=None):
        if qe_file is not None:
            self._qe_file = qe_file
        if amp is None:
            amps = imutils.allAmps
        else:
            amps = (amp,)
        for amp in amps:
            print "Amp", amp
            wls = []
            ref_wls = ref.qe_data[1].data.field('WAVELENGTH')
            fluxes, ref_fluxes = [], []
            column = 'AMP%02i' % amp
            for i, wl in enumerate(self.qe_data[1].data.field('WAVELENGTH')):
                if wl in ref_wls and wl not in wls:
                    wls.append(wl)
                    fluxes.append(self.qe_data[1].data.field(column)[i])
            for i, wl in enumerate(ref_wls):
                if wl in wls:
                    ref_fluxes.append(ref.qe_data[1].data.field(column)[i])
            fluxes = np.array(fluxes)
            ref_fluxes = np.array(ref_fluxes)
            win = plot.xyplot(wls, fluxes/ref_fluxes, xname='wavelength (nm)',
                              yname='QE(%s) / QE(%s)' % (self.sensor_id,
                                                         ref.sensor_id))
            win.set_title('Amp %i' % amp)
            plot.hline(1)
    def qe(self, qe_file=None):
        if qe_file is not None:
            self._qe_file = qe_file
        qe_data = self.qe_data
        bands = qe_data[2].data.field('BAND')
        band_wls = np.array([sum(self.band_pass[b])/2. for b in 
                             self.band_pass.keys() if b in bands])
        band_wls_errs = np.array([(self.band_pass[b][1]-self.band_pass[b][0])/2.
                                  for b in self.band_pass.keys() if b in bands])
        wl = qe_data[1].data.field('WAVELENGTH')
        qe = {}
        for amp in imutils.allAmps:
            qe[amp] = qe_data[1].data.field('AMP%02i' % amp)
            win = plot.curve(wl, qe[amp], xname='wavelength (nm)',
                             yname='QE (% e-/photon)', oplot=amp-1,
                             xrange=(300, 1100))
            if amp == 1:
                win.set_title('QE, %s' % self.sensor_id)
            qe_band = qe_data[2].data.field('AMP%02i' % amp)
            plot.xyplot(band_wls, qe_band, xerr=band_wls_errs, 
                        oplot=1, color='g')
        plot.hline(100)
    def flat_fields(self, lambda_dir, nsig=3, cmap=pylab.cm.hot):
        glob_string = os.path.join(lambda_dir, '*_lambda_*.fits')
        #print glob_string
        flats = sorted(glob.glob(glob_string))
        flats = [x for x in flats if x.find('bias') == -1]
        wls = []
        for flat in flats:
            try:
                wl = int(float(os.path.basename(flat).split('_')[2]))
            except ValueError:
                wl = int(float(os.path.basename(flat).split('_')[3]))
            wls.append(wl)
        wls = np.array(wls)
        #print wls
        # Loop over PRNU wavelengths and generate a png for each.
        gains = dict([(amp, gain) for amp, gain 
                      in zip(self.results['AMP'], self.results['GAIN'])])
        for wl in self.prnu_wls:
            try:
                target = np.where(wls == wl)[0][0]
                win = plot_flat(flats[target], nsig=nsig, cmap=cmap, wl=wl,
                                gains=gains)
                pylab.savefig('%s_%04inm_flat.png' % (self.sensor_id, wl))
            except IndexError:
                pass
    def confluence_tables(self, outfile=False):
        if outfile:
            output = open(self._outputpath('%s_results.txt' 
                                           % self.sensor_id), 'w')
        else:
            output = sys.stdout
        # Write the per amp results.
        for name in self.results.colnames:
            output.write('|| %s' % name)
        output.write('||\n')
#        format = '| %i | %.2f | %.2f | %i | %.1e | %.1e | %.1e | %i | %i | %.1e | %.2f |\n'
        format = '| %i |' + (len(self.results.colnames)-1)*' %.2e |' + '\n'
        for i, amp in enumerate(self.results['AMP']):
            output.write(format % tuple([self.results[x][i] 
                                         for x in self.results.colnames]))
        output.write('\n')
        # Write the CCD-wide results.
        # PRNU:
        prnu_results = self.prnu_results
        output.write("|| wavelength || stdev of pixel values || mean || stdev/mean ||\n")
        for wl, stdev, mean in zip(prnu_results['WAVELENGTH'],
                                   prnu_results['STDEV'], prnu_results['MEAN']):
            if stdev > 0:
                output.write("| %i | %12.4e | %12.4e | %12.4e |\n" 
                             % (wl, stdev, mean, stdev/mean))
            else:
                output.write("| %i | ... | ... | ... |\n" % wl)
        if outfile:
            output.close()
    @property
    def prnu_results(self):
        my_prnu_results = pyfits.open(self.results.infile)['PRNU_RESULTS'].data
        return my_prnu_results
    def latex_table(self, outfile=None):
        lines = []
        lines.append(self.specs.latex_header())
        for spec in self.specs:
            lines.append(self.specs[spec].latex_entry())
        lines.append(self.specs.latex_footer())
        my_table = '\n'.join(lines) + '\n'
        if outfile is not None:
            output = open(outfile, 'w')
            output.write(my_table)
            output.close()
        return my_table

class CcdSpecs(OrderedDict):
    def __init__(self, results_file, xtalk_file=None, plotter=None,
                 prnu_wls=()):
        super(CcdSpecs, self).__init__()
        self.plotter = plotter
        self.prnu_wls = prnu_wls
        self.prnu_specs = OrderedDict()
        self._createSpecs()
        self._ingestResults(results_file, xtalk_file=xtalk_file)
    def factory(self, *args, **kwds):
        spec = CcdSpec(*args, **kwds)
        self[spec.name] = spec
        return spec
    def _createSpecs(self):
        self.factory('CCD-007', 'Read Noise', spec='$< 8$\,\electron rms')
        self.factory('CCD-008', 'Blooming Full Well',
                     spec='$<175000$\,\electron')
        self.factory('CCD-009', 'Nonlinearity', spec='$<2\\%$')
        self.factory('CCD-010', 'Serial CTE', spec='$> 1 - \\num{5e-6}$')
        self.factory('CCD-011', 'Parallel CTE', spec='$> 1 - \\num{3e-6}$')
        self.factory('CCD-012', '\\twolinecell{Active Imaging Area \\\\and Cosmetic Quality}', spec='\\twolinecell{$<0.5$\\% defective \\\\pixels}')
        self.factory('CCD-012a', 'Bright Pixels')
        self.factory('CCD-012b', 'Dark Pixels')
        self.factory('CCD-012c', 'Bright Columns')
        self.factory('CCD-012d', 'Dark Columns')
        self.factory('CCD-012e', 'Traps')
        self.factory('CCD-013', 'Crosstalk', spec='$<0.19$\\%')
        self.factory('CCD-014',
                     '\\twolinecell{Dark Current \\\\95th Percentile}',
                     spec='$<0.2$\,\electron\,s$^{-1}$')
        self.factory('CCD-021', 'u Band QE', spec='$> 41$\\%')
        self.factory('CCD-022', 'g Band QE', spec='$> 78$\\%')
        self.factory('CCD-023', 'r Band QE', spec='$> 83$\\%')
        self.factory('CCD-024', 'i Band QE', spec='$> 82$\\%')
        self.factory('CCD-025', 'z Band QE', spec='$> 75$\\%')
        self.factory('CCD-026', 'y Band QE', spec='$> 21$\\%')
        self.factory('CCD-027', 'PRNU', spec='$<5$\\%')
        for wl in self.prnu_wls:
            self.prnu_specs[wl] = CcdSpec("CCD-027 (%inm)" % wl, 'PRNU',
                                          spec='$<5$\\%')
        self.factory('CCD-028', 'Point Spread Function', spec='$\sigma < 5\mu$')
    @staticmethod
    def latex_header():
        return CcdSpec.latex_header()
    @staticmethod
    def latex_footer():
        return CcdSpec.latex_footer()
    def latex_table(self):
        output = []
        output.append(self.latex_header())
        for name, spec in self.items():
            output.append(spec.latex_entry())
        output.append(self.latex_footer())
        return '\n'.join(output) + '\n'
    def _ingestResults(self, results_file, xtalk_file=None):
        self.results = EOTestResults(results_file)
        rn = self.results['READ_NOISE']
        self['CCD-007'].measurement = '$%.2f$--$%.2f$\,\electron\,rms' % (min(rn), max(rn))
        self['CCD-007'].ok = (max(rn) < 8)
        fw = self.results['FULL_WELL']
        self['CCD-008'].measurement = '$%i$--$%i$\,\electron' % (min(fw), max(fw))
        self['CCD-008'].ok = (max(fw) < 175000)
        max_frac_dev = self.results['MAX_FRAC_DEV']
        self['CCD-009'].measurement = '\\twolinecell{max. fractional deviation \\\\from linearity: $\\num{%.1e}$}' % max(max_frac_dev)
        self['CCD-009'].ok = (max(max_frac_dev) < 0.02)
        scti = self.results['CTI_SERIAL']
        self['CCD-010'].measurement = '$1%s$ (min. value)'%latex_minus_max(scti)
        self['CCD-010'].ok = (max(scti) < 5e-6)
        pcti = self.results['CTI_PARALLEL']
        self['CCD-011'].measurement = '$1%s$ (min. value)'%latex_minus_max(pcti)
        self['CCD-011'].ok = (max(pcti) < 3e-6)
        num_bright_pixels = sum(self.results['NUM_BRIGHT_PIXELS'])
        self['CCD-012a'].measurement = '%i' % num_bright_pixels
        num_dark_pixels = sum(self.results['NUM_DARK_PIXELS'])
        self['CCD-012b'].measurement = '%i' % num_dark_pixels
        num_bright_columns = sum(self.results['NUM_BRIGHT_COLUMNS'])
        self['CCD-012c'].measurement = '%i' % num_bright_columns
        num_dark_columns = sum(self.results['NUM_DARK_COLUMNS'])
        self['CCD-012d'].measurement = '%i' % num_dark_columns
        num_traps = sum(self.results['NUM_TRAPS'])
        self['CCD-012e'].measurement = '%i' % num_traps

        try:
            num_pixels = (self.results['TOTAL_NUM_PIXELS'] 
                          - self.results['ROLLOFF_MASK_PIXELS'])
        except:
            num_pixels = 16129000
        col_size = 2002 - 9 # exclude masked edge rolloff.
        num_defects = (num_bright_pixels + num_dark_pixels + num_traps
                       + col_size*(num_bright_columns + num_dark_columns))
        self['CCD-012'].measurement = 'defective pixels: %i (%.4f\\%%)' \
            % (num_defects, 100.*float(num_defects)/float(num_pixels))
        self['CCD-012'].ok = (float(num_defects)/float(num_pixels) < 5e-3)
        if xtalk_file is not None:
            foo = CrosstalkMatrix(xtalk_file)
            for i in range(len(foo.matrix)):
                foo.matrix[i][i] = 0
            crosstalk = max(np.abs(foo.matrix.flatten()))
            self['CCD-013'].measurement = 'max. value: $\\num{%.2e}$\\%%' % (crosstalk*100.)
            self['CCD-013'].ok = (crosstalk < 1.9e-3)
        try:
            dark_current = self.results.output['AMPLIFIER_RESULTS'].header['DARK95']
        except KeyError:
            dark_current = max(self.results['DARK_CURRENT_95'])
        self['CCD-014'].measurement = '$\\num{%.2e}$\electron\,s$^{-1}$' % dark_current
        self['CCD-014'].ok = (dark_current < 0.2)
        
        bands = self.plotter.qe_data['QE_BANDS'].data.field('BAND')
        bands = OrderedDict([(band, []) for band in bands])
        for amp in imutils.allAmps:
            values = self.plotter.qe_data['QE_BANDS'].data.field('AMP%02i' % amp)
            for band, value in zip(bands, values):
                bands[band].append(value)
        for band, specnum, minQE in zip('ugrizy', range(21, 27), 
                                        (41, 78, 83, 82, 75, 21)):
            try:
                qe_mean = np.mean(bands[band])
                self['CCD-0%i' % specnum].measurement = '%.1f\\%%' % qe_mean
                self['CCD-0%i' % specnum].ok = (qe_mean > minQE)
            except KeyError:
                self['CCD-0%i' % specnum].measurement = 'No data'
        prnu_results = self.plotter.prnu_results
        target_wls = Set(EOTestPlots.prnu_wls)
        ratios = {}
        for wl, stdev, mean in zip(prnu_results['WAVELENGTH'],
                                   prnu_results['STDEV'], prnu_results['MEAN']):
            if stdev > 0:
                target_wls.remove(int(wl))
                ratios[wl] = stdev/mean
                if self.prnu_specs.has_key(wl):
                    if ratios[wl] < 0.01:
                        self.prnu_specs[wl].measurement = \
                            "\\num{%.1e}\\%%" % (ratios[wl]*100)
                    else:
                        self.prnu_specs[wl].measurement = \
                            "%.2f\\%%" % (ratios[wl]*100)
                    self.prnu_specs[wl].ok = (ratios[wl] < 5e-2)
        max_ratio = max(ratios.values())
        max_wl = ratios.keys()[np.where(ratios.values() == max_ratio)[0][0]]
        if max_ratio < 0.01:
            self['CCD-027'].measurement = 'max. variation = \\num{%.1e}\\%% at %i\\,nm' % (max_ratio*100, max_wl)
        else:
            self['CCD-027'].measurement = 'max. variation = %.2f\\%% at %i\\,nm' % (max_ratio*100, max_wl)
        if target_wls:
            measurement = self['CCD-027'].measurement + '\\\\missing wavelengths: ' \
                + ', '.join([str(x) for x in sorted(target_wls)]) + "\\,nm"
            self['CCD-027'].measurement = '\\twolinecell{%s}' % measurement
        self['CCD-027'].ok = (max_ratio < 5e-2)
        
        psf_sigma = np.mean(self.results['PSF_SIGMA'])
        self['CCD-028'].measurement = '$%.2f\,\mu$' % psf_sigma
        self['CCD-028'].ok = (psf_sigma < 5.)

class CcdSpec(object):
    _latex_status = dict([(True, '\ok'), (False, '\\fail'), (None, '$\cdots$')])
    def __init__(self, name, description, spec=None, ok=None, measurement=None):
        self.name = name
        self.description = description
        self.spec = spec
        self.ok = ok
        self.measurement = measurement
    @staticmethod
    def _table_cell(value):
        if value is None:
            return '$\cdots$'
        else:
            return value
    @staticmethod
    def latex_header():
        header = """\\begin{table}[h]
\\centering
\\begin{tabular}{|c|l|l|l|l|}
\hline
\\textbf{Status} & \\textbf{Spec. ID} & \\textbf{Description} & \\textbf{Specification} & \\textbf{Measurement}  \\\\ \hline"""
        return header
    @staticmethod
    def latex_footer():
        footer = "\end{tabular}\n\end{table}"
        return footer
    def latex_entry(self):
        entry = '%s & %s & %s & %s & %s \\\\ \hline' % \
                (self._latex_status[self.ok],
                 self.name, self.description,
                 self._table_cell(self.spec), 
                 self._table_cell(self.measurement))
        return entry
    def latex_table(self):
        lines = []
        lines.append(CcdSpecs.latex_header())
        lines.append(self.latex_entry())
        lines.append(CcdSpecs.latex_footer())
        return '\n'.join(lines)

if __name__ == '__main__':
    plots = EOTestPlots('114-03')
    plots.fe55_dists()
    plots.ptcs()
    plots.linearity()
    plots.gains()
    plots.noise()
    plots.qe()
    plots.crosstalk_matrix()
    plots.confluence_table()
    plots.latex_table()
