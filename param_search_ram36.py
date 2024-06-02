#! encoding = utf-8

""" Search RAM36 parameters that work """

import os
import argparse
from time import time
import multiprocessing as mp
from shutil import rmtree
import signal
import subprocess

TEMP_DIR_FMT = '.multicore_pid_{:d}'
MAX_BAD_VT = 99999
PROGR = 'ram36_vt4n.exe'


def arg():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('f', nargs=1, help='input file')
    parser.add_argument('-ncore', nargs=1, type=int, default=2,
                        help='Number of cores allocated (Default 2)')
    parser.add_argument('-fix', action='store_true', help='Fix other parameters')
    parser.add_argument('-order', nargs=1, type=int, help='Test parameters of this order')
    parser.add_argument('-paramlist', nargs='+',
                        help='Specify the parameters to test, overrides the -order option.')
    return parser.parse_args()


def init_worker():
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def read_output(fobj, param):
    """ Read output file and find the RMS and WRMS """

    wrms_queue = []
    rms_queue = []
    n_bad_vt = 0
    if isinstance(fobj, str):  # a text file on disk
        with open(fobj, 'r') as f:
            for a_line in f:
                if a_line.startswith(' wrms'):
                    wrms_queue.append(a_line)
                if a_line.startswith(' rms_MHz'):
                    rms_queue.append(a_line)
                if a_line.startswith('rmscat_MHz'):
                    break
                if a_line.startswith(' the lowest vt coeff included for'):
                    n_bad_vt += 1
                    if n_bad_vt > MAX_BAD_VT:
                        break
                if a_line.startswith(' iteration number '):
                    # reset bad vt line records
                    n_bad_vt = 0
    else:  # a subprocess Pipe
        with open(param.strip().replace('*', '') + '.out', 'w') as fo:
            # also save as text, so that if we find the best fit, we don't need to re-run the fit
            is_stop_queue = False
            for a_line in fobj.stdout:
                fo.write(a_line)
                if 'NaN' in a_line:
                    fobj.terminate()
                    break
                if is_stop_queue:
                    pass
                else:
                    if a_line.startswith(' wrms'):
                        wrms_queue.append(a_line)
                    if a_line.startswith(' rms_MHz'):
                        rms_queue.append(a_line)
                if a_line.startswith(' iteration number '):
                    # reset bad vt line records
                    n_bad_vt = 0
                if a_line.startswith('rmscat_MHz'):
                    # fobj.terminate()
                    # the fit converges. Let it finish, simply do not add more things to queue
                    is_stop_queue = True
                # elif a_line.startswith(' iteration number'):
                #     n_iter = a_line.split()[2]
                #     # print('     iter {:>2s}  {:>2.0f} min {:>2.0f} s'.format(n_iter, (time()-tick)//60, (time()-tick)%60), end='  ')
                # elif a_line.startswith(' wrms='):
                #     # print(a_line.split()[1], end='  ')
                # elif a_line.startswith(' rms_MHz'):
                #     # print(a_line.split()[2])
                #     # this_wrms = float(a_line.split()[2])
                elif len(a_line) > 110 and a_line[0] == ' ':
                    # enter param gradient, check if there's NaN
                    a_list = a_line.split()
                    if a_list[8] == 'NaN':
                        fobj.terminate()
                        break
                elif a_line.startswith(' the lowest vt coeff included for'):
                    n_bad_vt += 1
                    if n_bad_vt > MAX_BAD_VT:
                        fobj.terminate()
                        break
            fobj.terminate()
            fobj.wait()

    # search upward to find rms & wrms, the second last one is the last iteration
    # the last one may be super large due to inclusion of discarded bad assignments
    try:
        if len(rms_queue) > 1:
            a_line = rms_queue[-2]
        else:
            a_line = rms_queue[-1]
        rms_mhz = a_line.split('=')[1].split()[0]
        if len(wrms_queue) > 1:
            a_line = wrms_queue[-2]
        else:
            a_line = wrms_queue[-1]
        wrms = a_line.split('=')[1].split()[0]
    except IndexError:
        wrms = 'NaN'
        rms_mhz = 'NaN'
    return wrms, rms_mhz


def compare_order(a_line, order):
    """ Compare order of parameter
    a_line: str         parameter line
    order: int          order code

    Note that order code can be specified by 1-3 digits.
    It follows the sequence of "ntr" as the same in the input file
    If only 1 digit, it is read as "n"
    If 2 digits, it is read as "nt"
    If 3 digits, it is read as "ntr"
    """
    ol = a_line[98:98 + 6].split()
    if order < 10 and len(ol) >= 1:
        return int(ol[0]) <= order
    elif 10 <= order < 100 and len(ol) >= 2:
        return int(ol[0]) * 10 + int(ol[1]) <= order
    elif len(ol) >= 3:
        return int(ol[0]) * 100 + int(ol[1]) * 10 + int(ol[2]) <= order
    else:
        return False


def opt(root_dir, cache_input_file, param, is_float, is_fix):
    # create temporary directory
    sub_dir = TEMP_DIR_FMT.format(os.getpid())
    this_dir = os.path.join(root_dir, sub_dir)
    if os.path.isdir(this_dir):
        pass
    else:
        os.mkdir(this_dir)
    # go to that directory
    os.chdir(this_dir)

    # if param is None, the it's the initial value which means we simply calculate
    # the result of the orignial input file
    if not param:
        with open('input.txt', 'w') as f:
            for a_line in cache_input_file:
                if 'Number of iterations' in a_line:
                    f.write('0                       !Number of iterations (negative number means robust weighting fit)\n')
                else:
                    f.write(a_line)
        # run ram36
        param = 'Initial Ref'
        try:
            fout = subprocess.Popen([PROGR, "input.txt"], stdout=subprocess.PIPE, text=True)
            wrms, rms_mhz = read_output(fout, param)
        except ValueError:
            wrms, rms_mhz = 'NaN', 'NaN'
    elif is_float == 0:
        # loop into the par list, each time change one parameter which is_float = 0 -> 1
        # run the ram36 fit again, and read the wrms, rms
        # write input file with this line is_float replaced to 1
        with open('input.txt', 'w') as f:
            for a_line in cache_input_file:
                if a_line.startswith(param):
                    # double check
                    if int(a_line.split(',')[10]) == 0:
                        new_line = a_line[:93] + '1' + a_line[94:]
                        f.write(new_line)
                    else:
                        f.write(a_line)
                elif a_line.split(',')[0] in param:
                    if is_fix:
                        # fix the other varying parameters in the template code
                        if int(a_line.split(',')[10]) == 1:
                            new_line = a_line[:92] + ' 0' + a_line[94:]
                            f.write(new_line)
                        else:
                            f.write(a_line)
                    else:
                        f.write(a_line)
                else:
                    f.write(a_line)
        # run ram36
        try:
            fout = subprocess.Popen([PROGR, "input.txt"], stdout=subprocess.PIPE, text=True)
            wrms, rms_mhz = read_output(fout, param)
        except ValueError:
            wrms, rms_mhz = 'NaN', 'NaN'
        # print(param, '  ', wrms, rms_mhz)
    else:  # the parameters that have been included already in the initial input, skip them
        wrms = ''
        rms_mhz = ''
    print('{:<13s} {:>2d} {:s} {:s}'.format(param, int(is_float), wrms, rms_mhz), flush=True)
    # copy the output file out
    _ = subprocess.run(['copy', '*.out', '..\\outputs'], shell=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # get back to root dir
    os.chdir(root_dir)


def read_param_list(fin, order):
    """ Read parameter list to be tested """
    # this is the initial value (which does not run any other parameters
    param_list = [(None, False)]

    with open(fin, 'r') as f:
        cache_input_file = f.readlines()

    for a_line in cache_input_file[12:]:
        if a_line.startswith('&&&END,'):
            break
        else:
            a_list = a_line.split(',')
            is_float = int(a_list[10])
            # check if order is specified
            # here, a_list[0] has white spaces, they need to be kept !
            # so that eg Dab != DabJ (because later we check with .startswith())
            if isinstance(order, int):
                if compare_order(a_line, order):
                    param_list.append((a_list[0], is_float))
                else:
                    pass
            else:
                param_list.append((a_list[0], is_float))
    return param_list, cache_input_file


def read_user_param_list(fin, user_param_list):
    """ Read user defined parameter list to be tested """
    # this is the initial value (which does not run any other parameters
    param_list = [(None, False)]

    with open(fin, 'r') as f:
        cache_input_file = f.readlines()

    for a_line in cache_input_file[12:]:
        if a_line.startswith('&&&END,'):
            break
        else:
            a_list = a_line.split(',')
            is_float = int(a_list[10])
            this_param = a_list[0].strip()
            # here, a_list[0] has white spaces, they need to be kept !
            # so that eg Dab != DabJ (because later we check with .startswith())
            if this_param in user_param_list:
                param_list.append((a_list[0], is_float))
            else:
                pass
    return param_list, cache_input_file


def run(fin, ncore, is_fix, user_param_list, order):
    """ Run the multi-core code """
    print('-' * 11, 'RAM36 Parameter Exploration', '-' * 11, flush=True)
    print('{:<16s} {:^16s} {:^16s}'.format('Parameter', 'wrms', 'rms_MHz'), flush=True)
    print('-' * 51, flush=True)
    root_dir = os.getcwd()
    if user_param_list:
        param_list, cache_input_line = read_user_param_list(fin, user_param_list)
    else:
        param_list, cache_input_line = read_param_list(fin, order)
    # single core version
    # for (param, is_float) in param_list:
    #     print(param, flush=True)
    #     opt(root_dir, cache_input_line, param, is_float, is_fix)
    if os.path.isdir(os.path.join(root_dir, 'outputs')):
        pass
    else:
        os.mkdir(os.path.join(root_dir, 'outputs'))
    pool = mp.Pool(processes=ncore, initializer=init_worker)
    try:
        pool.starmap(
            opt, list((root_dir, cache_input_line, param, is_float, is_fix) for (param, is_float) in param_list))
        # get all pids and remove all these temporary folder
        for child in mp.active_children():
            sub_dir = TEMP_DIR_FMT.format(child.pid)
            rmtree(os.path.join(root_dir, sub_dir), ignore_errors=True)
        pool.close()
        pool.join()
    except KeyboardInterrupt:
        pool.terminate()
        for child in mp.active_children():
            sub_dir = TEMP_DIR_FMT.format(child.pid)
            rmtree(os.path.join(root_dir, sub_dir), ignore_errors=True)
        pool.join()


if __name__ == '__main__':
    args = arg()
    order = args.order[0] if args.order else None
    user_param_list = args.paramlist if args.paramlist else None
    ncore = args.ncore if isinstance(args.ncore, int) else args.ncore[0]
    tick = time()
    run(args.f[0], ncore, args.fix, user_param_list, order)
    t = (time() - tick) / 60
    print('-' * 9, 'Total elapsed time: {:.0f} h {:.1f} min'.format(t // 60, t % 60), '-' * 9)
    print('')
