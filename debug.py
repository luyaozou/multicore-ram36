from param_search_ram36 import read_user_param_list
import os
import argparse
import subprocess

if __name__ == '__main__':
    # parser = argparse.ArgumentParser(description=__doc__)
    # parser.add_argument('f', nargs=1, help='input file')
    # parser.add_argument('-paramlist', nargs='+',
    #                     help='Specify the parameters to test, overrides the -order option.')
    # arg = parser.parse_args()

    fi='../../Postdoc_Lille/SPFIT/TFA_PSE/2023/input.txt'
    #param_list, cache_input_line = read_user_param_list(arg.f[0], arg.paramlist, True)
    param_list, cache_input_line = read_user_param_list(fi, ['Dbc', 'Fbc'], True)
    print(param_list)
