"""
@brief Compute QE curves from the wavelength scan dataset.

@author J. Chiang <jchiang@slac.stanford.edu>
"""
import os
from qe.QE import QE_Data
from pipeline.TaskParser import TaskParser

if __name__ == '__main__':
    parser = TaskParser('Compute QE curves')
    parser.add_argument('-f', '--qe_files', type=str,
                        help='wavelength scan file pattern')
    parser.add_argument('-F', '--qe_file_list', type=str,
                        help='list of wavelength scan files')
    parser.add_argument('-c', '--ccd_cal_file', type=str,
                        help='calibration file for photodiode at CCD location')
    parser.add_argument('-i', '--int_sph_cal_file', type=str,
                        help='calibration file for photodiode at integrating sphere')
    parser.add_argument('-w', '--wavelength_scan_file', type=str,
                        help='uncorrected wavelength scan file')
    args = parser.parse_args()

    gains = args.system_gains()
    sensor = args.sensor()
    sensor_id = args.sensor_id
    
    medians_file = os.path.join(args.output_dir, '%s_QE.txt' % sensor_id)
    fits_outfile = os.path.join(args.output_dir, '%s_QE.fits' % sensor_id)

    ccd_cal_file = args.ccd_cal_file
    sph_cal_file = args.int_sph_cal_file
    wlscan_file = args.wavelength_scan_file

    infiles = args.files(args.qe_files, args.qe_file_list)

    qe_data = QE_Data()
#    qe_data.calculate_medians(infiles, medians_file,
#                              mask_files=args.mask_files())
    qe_data.read_medians(medians_file)
    qe_data.calculate_QE(ccd_cal_file, sph_cal_file, wlscan_file, gains)
    qe_data.write_fits_tables(fits_outfile)