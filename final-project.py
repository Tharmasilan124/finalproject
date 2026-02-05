import subprocess
import argparse
import shlex
import csv
import os
import sys

import numpy as np
import matplotlib.pyplot as plt

CSR_ADDR = 0x0
COEF_ADDR = 0x4
OUTCAP_ADDR = 0x8

INSTANCES = ['golden', 'impl0', 'impl1', 'impl2', 'impl3', 'impl4']

BASE_UAD_PATH = './insts'

os.makedirs('logs', exist_ok=True)
os.makedirs('plots', exist_ok=True)

# ---------------- Register Classes ---------------- #

class Csr():
    def __init__(self, csr_bin):
        self.fen   = (csr_bin >> 0) & 0x1
        self.c0en  = (csr_bin >> 1) & 0x1
        self.c1en  = (csr_bin >> 2) & 0x1
        self.c2en  = (csr_bin >> 3) & 0x1
        self.c3en  = (csr_bin >> 4) & 0x1
        self.halt  = (csr_bin >> 5) & 0x1
        self.sts   = (csr_bin >> 6) & 0x3
        self.ibcnt = (csr_bin >> 8) & 0xff
        self.ibovf = (csr_bin >> 16) & 0x1
        self.ibclr = (csr_bin >> 17) & 0x1
        self.tclr  = (csr_bin >> 18) & 0x1
        self.rnd   = (csr_bin >> 19) & 0x3
        self.icoef = (csr_bin >> 21) & 0x1
        self.icap  = (csr_bin >> 22) & 0x1

    def encode(self):
        return (
            (self.fen   << 0)  |
            (self.c0en  << 1)  |
            (self.c1en  << 2)  |
            (self.c2en  << 3)  |
            (self.c3en  << 4)  |
            (self.halt  << 5)  |
            (self.sts   << 6)  |
            (self.ibcnt << 8)  |
            (self.ibovf << 16) |
            (self.ibclr << 17) |
            (self.tclr  << 18) |
            (self.rnd   << 19) |
            (self.icoef << 21) |
            (self.icap  << 22)
        )

class Coef():
    def __init__(self, coef_bin):
        self.c0 = (coef_bin >> 0) & 0xff
        self.c1 = (coef_bin >> 8) & 0xff
        self.c2 = (coef_bin >> 16) & 0xff
        self.c3 = (coef_bin >> 24) & 0xff

    def encode(self):
        return (self.c0 | (self.c1 << 8) | (self.c2 << 16) | (self.c3 << 24))

class Outcap():
    def __init__(self, outcap_bin):
        self.hcap = (outcap_bin >> 0) & 0xff
        self.lcap = (outcap_bin >> 8) & 0xff

# ---------------- UAD Wrapper ---------------- #

class Uad():
    def __init__(self, instance):
        self.path = f'{BASE_UAD_PATH}/{instance}'

    def cmd(self, cmd):
        return subprocess.check_output(shlex.split(cmd)).decode()

    def reset(self):
        os.system(f'{self.path} com --action reset')

    def enable(self):
        os.system(f'{self.path} com --action enable')

    def disable(self):
        os.system(f'{self.path} com --action disable')

    def drive(self, val):
        return int(self.cmd(f'{self.path} sig --data {hex(val)}'), 0)

    def read(self, addr):
        return int(self.cmd(f'{self.path} cfg --address {addr}'), 0)

    def write(self, addr, data):
        os.system(f'{self.path} cfg --address {addr} --data {hex(data)}')

# ---------------- Utility ---------------- #

def twos_comp(num):
    return ((num & 0x7F) + (-128 if num & 0x80 else 0)) / 64

# ---------------- Testcases ---------------- #

def test_por(uad, por_file):
    uad.reset()
    csr = Csr(uad.read(CSR_ADDR))
    coef = Coef(uad.read(COEF_ADDR))
    outc = Outcap(uad.read(OUTCAP_ADDR))

    passed = True
    with open(por_file) as f:
        for r in csv.DictReader(f):
            reg = {'csr': csr, 'coef': coef, 'outcap': outc}[r['register']]
            if getattr(reg, r['field']) != int(r['value'], 0):
                passed = False
    return passed

def test_enable_disable(uad):
    uad.disable()
    try:
        uad.read(CSR_ADDR)
        return False
    except:
        return True

def test_buffer(uad):
    csr = Csr(uad.read(CSR_ADDR))
    csr.halt = 1
    uad.write(CSR_ADDR, csr.encode())

    for _ in range(300):
        uad.drive(0x10)

    csr = Csr(uad.read(CSR_ADDR))
    if csr.ibovf != 1 or csr.ibcnt != 255:
        return False

    csr.ibclr = 1
    uad.write(CSR_ADDR, csr.encode())
    csr = Csr(uad.read(CSR_ADDR))
    return csr.ibcnt == 0

def test_bypass(uad, vec):
    csr = Csr(uad.read(CSR_ADDR))
    csr.icoef = 1
    csr.fen = 1
    uad.write(CSR_ADDR, csr.encode())

    with open(vec) as f:
        for l in f:
            v = int(l, 0)
            if uad.drive(v) != v:
                return False
    return True

def test_signal(uad, cfg, vec, tag):
    # load config
    csr = Csr(uad.read(CSR_ADDR))
    csr.halt = 1
    uad.write(CSR_ADDR, csr.encode())

    coef = Coef(uad.read(COEF_ADDR))
    with open(cfg) as f:
        for r in csv.DictReader(f):
            setattr(csr, f'c{r["coef"]}en', int(r['en'], 0))
            setattr(coef, f'c{r["coef"]}', int(r['value'], 0))

    csr.halt = 0
    uad.write(COEF_ADDR, coef.encode())
    uad.write(CSR_ADDR, csr.encode())

    sig_in, sig_out = [], []
    with open(vec) as f:
        for l in f:
            v = int(l, 0)
            sig_in.append(twos_comp(v))
            sig_out.append(twos_comp(uad.drive(v)))

    plt.figure()
    plt.step(range(len(sig_in)), sig_in, label='Input')
    plt.step(range(len(sig_out)), sig_out, label='Output')
    plt.legend()
    plt.savefig(f'plots/{tag}_signal.png')
    plt.close()

    return True

# ---------------- Main Runner ---------------- #

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-t', '--test', choices=['full'])
    parser.add_argument('-f', '--por')
    parser.add_argument('-c', '--cfg')
    parser.add_argument('-v', '--vec')
    args = parser.parse_args()

    results = []

    for inst in INSTANCES:
        uad = Uad(inst)
        log = open(f'logs/{inst}.log', 'w')
        sys.stdout = log

        r1 = test_enable_disable(uad)
        r2 = test_por(uad, args.por)
        r3 = test_buffer(uad)
        r4 = test_bypass(uad, args.vec)
        r5 = test_signal(uad, args.cfg, args.vec, inst)

        results.append([inst, r1, r2, r3, r4, r5])

        log.close()

    sys.stdout = sys.__stdout__

    with open('results.csv', 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['Instance', 'Enable', 'POR', 'Buffer', 'Bypass', 'Signal'])
        w.writerows(results)

if __name__ == '__main__':
    main()
