.PHONY: test compile check

PY ?= python3

compile:
	$(PY) -m compileall -q mi tests

test:
	$(PY) -m unittest discover -s tests -p 'test_*.py'

check: compile test
