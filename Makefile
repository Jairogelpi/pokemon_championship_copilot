.PHONY: install run test check

install:
	npm ci --ignore-scripts

run:
	python scripts/dev.py

test:
	python -m unittest discover -s tests -v

check:
	python scripts/check.py
