.PHONY: run test check

run:
	python scripts/dev.py

test:
	python -m unittest discover -s tests -v

check:
	python scripts/check.py
