"""统一入口：python tests/run_tests.py [-v]"""
import os, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

if __name__ == '__main__':
    argv = [sys.argv[0], 'discover', '-s', HERE, '-t', ROOT]
    argv += [a for a in sys.argv[1:] if a in ('-v', '-q')]
    unittest.main(module=None, argv=argv)
