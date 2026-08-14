.PHONY: install demo test report clean

install:
	pip install -e '.[dev]'

demo:
	python -m autopsy demo --videos 24

test:
	python -m pytest tests/ -q

report:
	python -m autopsy report

clean:
	rm -rf out .pytest_cache **/__pycache__
