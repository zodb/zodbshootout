import os

def main():
    os.environ['COVERAGE_PROCESS_START'] = os.path.abspath('.coveragerc')
    os.environ['PYTHONPATH'] = os.path.abspath(os.path.dirname(__file__))
    import zodbshootout.main
    zodbshootout.main.main()

if __name__ == '__main__':
    main()
